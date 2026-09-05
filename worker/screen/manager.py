"""ScreenManager 统一管理器。"""

import io
import logging
import threading
import time
from collections.abc import Callable
from queue import Empty, Queue
from typing import TYPE_CHECKING

import numpy
from PIL import Image

from worker.screen.frame_source import FrameSource

if TYPE_CHECKING:
    from worker.screen.streamer import WebSocketStreamer

logger = logging.getLogger(__name__)

# 全局缓存
_screen_managers: dict[str, "ScreenManager"] = {}
_screen_managers_lock = threading.Lock()

# 帧捕获失败回调（全局）
_on_capture_failed: Callable[[str], None] | None = None


def set_capture_failed_callback(callback: Callable[[str], None]) -> None:
    """设置帧捕获失败回调（由 Worker 初始化时调用）。"""
    global _on_capture_failed
    _on_capture_failed = callback


def get_screen_manager(device_id: str, frame_source: FrameSource) -> "ScreenManager":
    """获取或创建 ScreenManager（按设备 ID 缓存，线程安全）。"""
    with _screen_managers_lock:
        manager = _screen_managers.get(device_id)
        if manager is None:
            manager = ScreenManager(frame_source, device_id)
            manager.start_capture()
            _screen_managers[device_id] = manager
            logger.info(f"ScreenManager created for device: {device_id}")
        return manager


def close_screen_manager(device_id: str) -> None:
    """关闭指定设备的 ScreenManager。

    注意：只关闭 HTTP 流连接和后台线程，不清理端口转发进程。
    端口转发进程的生命周期由 iOSPlatformManager 管理，与设备连接状态绑定。
    """
    with _screen_managers_lock:
        manager = _screen_managers.pop(device_id, None)
    if manager is not None:
        manager.stop()
        logger.info(f"ScreenManager closed for device: {device_id}")


def close_all_screen_managers() -> None:
    """关闭所有 ScreenManager（Worker 停止时调用）。"""
    with _screen_managers_lock:
        device_ids = list(_screen_managers.keys())
    for device_id in device_ids:
        close_screen_manager(device_id)
    logger.info("All ScreenManagers closed")


def get_existing_screen_manager(device_id: str) -> "ScreenManager | None":
    """返回已缓存的 ScreenManager（不创建，线程安全）。"""
    with _screen_managers_lock:
        return _screen_managers.get(device_id)


class ScreenManager:
    """统一管理截图/录屏/推流。"""

    def __init__(self, frame_source: FrameSource, device_id: str = ""):
        self._frame_source = frame_source
        self._device_id = device_id  # 用于失败通知
        self._frame_queue: Queue[bytes] = Queue(maxsize=10)
        self._bgra_queue: Queue[bytearray] = Queue(maxsize=2)  # 录制只需要 1 帧当前 + 1 帧缓冲
        self._capture_thread: threading.Thread | None = None
        self._running: bool = False
        self._streamer: WebSocketStreamer | None = None
        # 保护 _running/_capture_thread 状态迁移，避免并发 start/stop
        # 出现"看到线程在跑却被对方 join 掉"的竞态
        self._state_lock: threading.Lock = threading.Lock()

    def start_capture(self) -> None:
        """启动后台截图线程（已在运行时幂等返回）。"""
        with self._state_lock:
            if self._running and self._capture_thread and self._capture_thread.is_alive():
                return
            self._running = True
            self._capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
            self._capture_thread.start()
        logger.info("Frame capture thread started")

    def stop(self) -> None:
        """停止所有资源（截图线程、推流）。"""
        with self._state_lock:
            self._running = False
            capture_thread = self._capture_thread
            self._capture_thread = None
        if capture_thread:
            # 如果是当前线程调用 stop（如帧捕获线程检测失败后），则不 join（避免死锁）
            if capture_thread != threading.current_thread():
                capture_thread.join(timeout=5)
        if self._streamer:
            self._streamer.stop()
        self._frame_source.stop()
        logger.info("ScreenManager stopped")

    def get_frame(self, timeout: float = 1.0) -> bytes:
        """获取单帧（供录屏和推流共享）。"""
        try:
            frame = self._frame_queue.get(timeout=timeout)
            if self._frame_source.prefers_latest_frame:
                # 官方鸿蒙帧源已维护最新帧槽位，不能因为通用 FIFO 再把旧帧
                # 交给 WebSocket。队列中若有更新帧，直接丢弃旧帧直到最新一张。
                while True:
                    try:
                        frame = self._frame_queue.get_nowait()
                    except Empty:
                        return frame
            return frame
        except Empty:
            if self._frame_source.prefers_latest_frame:
                return self._frame_source.get_frame()
            # 队列空时返回空白帧
            return self._frame_source.get_blank_frame()

    def get_frame_bgra(self, max_retries: int = 3) -> bytearray:
        """获取 BGRA 原始帧（从队列获取）。

        Args:
            max_retries: 最大重试次数

        Returns:
            bytearray: BGRA 格式的原始像素数据
        """
        for attempt in range(max_retries):
            try:
                return self._bgra_queue.get(timeout=1.0)
            except Empty:
                if attempt == max_retries - 1:
                    logger.error("BGRA queue empty after retries, falling back to direct capture")
                    return self._frame_source.get_frame_bgra()
        return self._frame_source.get_frame_bgra()

    def get_frame_jpeg(self) -> bytes:
        """从队列获取一帧并返回 JPEG。

        BGRA 队列仅对支持 BGRA 的帧源（如 MacFrameSource）有意义；
        其它帧源（MJPEG/minicap/鸿蒙）直接输出 JPEG，走帧队列兜底，
        不能因缺少 BGRA 支持而抛 NotImplementedError。
        """
        try:
            bgra = self.get_frame_bgra()
        except NotImplementedError:
            return self.get_frame()
        if not bgra:
            return self._frame_source.get_blank_frame()

        # 从 FrameSource 获取屏幕尺寸以还原 BGRA 数组形状
        width, height = self._frame_source.get_screen_size()
        bgra_array = numpy.frombuffer(bgra, dtype=numpy.uint8).reshape(height, width, 4)
        # BGRA -> RGB
        rgb_array = bgra_array[:, :, 2::-1]  # 取 B,G,R 通道并反转为 R,G,B
        img = Image.fromarray(rgb_array)
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=80)
        return buffer.getvalue()

    def _capture_loop(self) -> None:
        """后台截图循环（队列满时丢弃旧帧，带帧率控制）。

        优化：MacFrameSource 每次循环只截屏一次，同时放入两个队列。
        Windows 使用 WindowsSidecarScreenManager，不走此路径。
        """

        consecutive_errors = 0
        max_consecutive_errors = 10  # 连续错误阈值
        default_capture_fps = 15  # 默认帧率（高于录制帧率以保证流畅）
        frame_interval = 1.0 / default_capture_fps
        last_frame_time = time.time()

        # 是否共享单次截屏（MacFrameSource 支持）
        use_shared_capture = type(self._frame_source).__name__ == "MacFrameSource"

        # BGRA 是否不受支持（如 HarmonyFrameSource）——首次探测后置位，避免每轮刷 ERROR
        bgra_unsupported = False

        while self._running:
            try:
                capture_fps = default_capture_fps

                # 帧率控制
                current_time = time.time()
                elapsed = current_time - last_frame_time
                new_interval = 1.0 / capture_fps
                if abs(frame_interval - new_interval) > 0.001:
                    # fps 变了，重新计算间隔并重置时间
                    frame_interval = new_interval
                    last_frame_time = current_time
                    elapsed = 0

                if elapsed < frame_interval:
                    time.sleep(frame_interval - elapsed)

                if use_shared_capture:
                    # 共享一次截屏，同时获取 RGB 和 BGRA
                    bgra = self._frame_source.get_frame_bgra()
                    # BGRA 转 RGB 用于 _frame_queue（JPEG）
                    if bgra:
                        width, height = self._frame_source.get_screen_size()
                        bgra_array = numpy.frombuffer(bytes(bgra), dtype=numpy.uint8).reshape(height, width, 4)
                        rgb_array = bgra_array[:, :, 2::-1]  # BGRA -> RGB
                        img = Image.fromarray(rgb_array)
                        buffer = io.BytesIO()
                        img.save(buffer, format="JPEG", quality=80)
                        frame = buffer.getvalue()
                    else:
                        frame = self._frame_source.get_blank_frame()
                        bgra = None
                else:
                    frame = self._frame_source.get_frame()
                    bgra = None  # 非共享路径，后续单独获取

                consecutive_errors = 0  # 成功后重置计数
                last_frame_time = time.time()

                if use_shared_capture and bgra:
                    # 共享截屏：BGRA 已获取，直接放入 _bgra_queue
                    if self._bgra_queue.full():
                        try:
                            self._bgra_queue.get_nowait()
                        except Empty:
                            pass
                    self._bgra_queue.put(bgra, timeout=1)

                if self._frame_queue.full():
                    # 队列满时丢弃最旧的帧
                    try:
                        self._frame_queue.get_nowait()
                    except Empty:
                        pass
                self._frame_queue.put(frame, timeout=1)

                # 非共享路径：单独获取 BGRA（不支持 BGRA 的帧源跳过，仅首次记录）
                if not use_shared_capture and not bgra_unsupported:
                    try:
                        bgra = self._frame_source.get_frame_bgra()
                        if self._bgra_queue.full():
                            try:
                                self._bgra_queue.get_nowait()
                            except Empty:
                                pass
                        self._bgra_queue.put(bgra, timeout=1)
                    except NotImplementedError:
                        # FrameSource 不支持 BGRA：置位跳过后续尝试，避免每轮刷 ERROR
                        bgra_unsupported = True
                        logger.info(
                            f"{type(self._frame_source).__name__} 不支持 BGRA 帧，跳过 BGRA 采集"
                        )

            except Exception as e:
                consecutive_errors += 1
                # 连续错误时只打印一次摘要，避免刷屏
                if consecutive_errors == 1:
                    logger.warning(f"Frame capture error: {e}")
                elif consecutive_errors == max_consecutive_errors:
                    logger.error(f"Frame capture failed {max_consecutive_errors} times, stopping capture")
                    # 通知设备监控
                    if _on_capture_failed and self._device_id:
                        _on_capture_failed(self._device_id)
                    break

                # 连续错误时增加延迟，避免快速循环
                if consecutive_errors >= 3:
                    time.sleep(0.5)

    def start_streaming(
        self,
        codec: str = "jpeg",
        bitrate: int = 4_000_000,
        profile: int = 66,
    ) -> "WebSocketStreamer":
        """启动 WebSocket 推流。

        Args:
            codec: 推流编码格式 (jpeg/h264/mjpeg)
            bitrate: H.264 平均码率（仅 Windows hard-encode 推流生效，其它平台忽略）
            profile: H.264 profile（仅 Windows hard-encode 推流生效，其它平台忽略）
        """
        from worker.screen.streamer import WebSocketStreamer

        # 确保截图线程运行（幂等）
        self.start_capture()

        # 检测 codec 切换：如果 codec 发生变化，需要重新创建 streamer
        if self._streamer:
            current_codec = getattr(self._streamer, 'codec', None)
            if current_codec != codec:
                logger.info(f"Codec changed from {current_codec} to {codec}, recreating streamer")
                self._streamer.stop()
                self._streamer = None

        if not self._streamer:
            self._streamer = WebSocketStreamer(self, codec=codec)
            self._streamer.start(codec=codec)
            logger.info(f"WebSocket streaming started (codec={codec})")
        return self._streamer

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

# 帧捕获失败回调（全局）
_on_capture_failed: Callable[[str], None] | None = None


def set_capture_failed_callback(callback: Callable[[str], None]) -> None:
    """设置帧捕获失败回调（由 Worker 初始化时调用）。"""
    global _on_capture_failed
    _on_capture_failed = callback


def get_screen_manager(device_id: str, frame_source: FrameSource) -> "ScreenManager":
    """获取或创建 ScreenManager（按设备 ID 缓存）。"""
    if device_id not in _screen_managers:
        manager = ScreenManager(frame_source, device_id)
        manager.start_capture()
        _screen_managers[device_id] = manager
        logger.info(f"ScreenManager created for device: {device_id}")
    return _screen_managers[device_id]


def close_screen_manager(device_id: str) -> None:
    """关闭指定设备的 ScreenManager。

    注意：只关闭 HTTP 流连接和后台线程，不清理端口转发进程。
    端口转发进程的生命周期由 iOSPlatformManager 管理，与设备连接状态绑定。
    """
    if device_id in _screen_managers:
        manager = _screen_managers[device_id]
        manager.stop()
        del _screen_managers[device_id]
        logger.info(f"ScreenManager closed for device: {device_id}")


def close_all_screen_managers() -> None:
    """关闭所有 ScreenManager（Worker 停止时调用）。"""
    for device_id in list(_screen_managers.keys()):
        close_screen_manager(device_id)
    logger.info("All ScreenManagers closed")


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
        # 消费者计数与延迟释放
        self._active_consumers: int = 0
        self._consumers_lock: threading.Lock = threading.Lock()
        self._release_timer: threading.Timer | None = None
        self._release_delay: float = 60.0

    def start_capture(self) -> None:
        """启动后台截图线程。"""
        if self._running:
            return

        self._running = True
        self._capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._capture_thread.start()
        logger.info("Frame capture thread started")

    def stop(self) -> None:
        """停止所有资源（截图线程、推流）。"""
        self._running = False
        if self._capture_thread:
            # 如果是当前线程调用 stop（如帧捕获线程检测失败后），则不 join（避免死锁）
            if self._capture_thread != threading.current_thread():
                self._capture_thread.join(timeout=5)
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
        """从 BGRA 队列获取帧并转换为 JPEG。

        Returns:
            bytes: JPEG 格式的图像数据
        """
        bgra = self.get_frame_bgra()
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

    def _ensure_capture_running(self) -> None:
        """确保截图线程正在运行（消费者模式）。

        取消待执行的释放定时器，增加消费者计数。
        如果是第一个消费者，启动截图线程。
        """
        with self._consumers_lock:
            # 取消待执行的释放定时器
            if self._release_timer is not None:
                self._release_timer.cancel()
                self._release_timer = None
                logger.debug("Release timer cancelled")

            self._active_consumers += 1
            logger.debug(f"Consumer added, active_consumers={self._active_consumers}")

            if self._active_consumers == 1 and not self._running:
                self.start_capture()
                logger.info("Capture started by first consumer")

    def _release_capture(self) -> None:
        """释放消费者引用，无消费者时延迟释放截图资源。

        消费者计数 -1，如果计数归零，启动 60 秒延迟释放定时器。
        """
        with self._consumers_lock:
            if self._active_consumers > 0:
                self._active_consumers -= 1
            logger.debug(f"Consumer released, active_consumers={self._active_consumers}")

            if self._active_consumers == 0:
                self._schedule_release()

    def _schedule_release(self) -> None:
        """启动延迟释放定时器（60 秒后无新消费者则释放截图资源）。"""
        if self._release_timer is not None:
            self._release_timer.cancel()

        self._release_timer = threading.Timer(self._release_delay, self._do_release)
        self._release_timer.daemon = True
        self._release_timer.start()
        logger.info(f"Release scheduled in {self._release_delay}s (no active consumers)")

    def _do_release(self) -> None:
        """延迟释放定时器回调：停止截图线程，释放 MSS 等资源。"""
        with self._consumers_lock:
            # 再次确认没有新消费者加入
            if self._active_consumers > 0:
                logger.debug("Release cancelled: new consumer joined")
                return
            self._release_timer = None

        logger.info("Release timer fired, stopping capture and releasing resources")
        self._running = False
        if self._capture_thread and self._capture_thread != threading.current_thread():
            self._capture_thread.join(timeout=5)
        self._capture_thread = None
        self._frame_source.stop()
        # 清空队列
        while not self._bgra_queue.empty():
            try:
                self._bgra_queue.get_nowait()
            except Empty:
                break
        while not self._frame_queue.empty():
            try:
                self._frame_queue.get_nowait()
            except Empty:
                break

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

        # 确保截图线程运行
        self._ensure_capture_running()

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

"""ScreenManager 统一管理器。"""

import io
import logging
import threading
import time
from queue import Queue, Empty
from typing import Callable, Optional, TYPE_CHECKING

import numpy
from PIL import Image

from worker.screen.frame_source import FrameSource

if TYPE_CHECKING:
    from worker.screen.recorder import ScreenRecorder
    from worker.screen.streamer import WebSocketStreamer

logger = logging.getLogger(__name__)

# 全局缓存
_screen_managers: dict[str, "ScreenManager"] = {}

# 帧捕获失败回调（全局）
_on_capture_failed: Optional[Callable[[str], None]] = None


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
        self._frame_queue: Queue[bytes] = Queue(maxsize=30)
        self._bgra_queue: Queue[bytearray] = Queue(maxsize=30)
        self._capture_thread: Optional[threading.Thread] = None
        self._running: bool = False
        self._is_recording: bool = False
        self._recording_lock: threading.Lock = threading.Lock()
        self._recorder: Optional["ScreenRecorder"] = None
        self._streamer: Optional["WebSocketStreamer"] = None
        # 消费者计数与延迟释放
        self._active_consumers: int = 0
        self._consumers_lock: threading.Lock = threading.Lock()
        self._release_timer: Optional[threading.Timer] = None
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
        """停止所有资源（截图线程、录屏、推流）。"""
        self._running = False
        if self._capture_thread:
            # 如果是当前线程调用 stop（如帧捕获线程检测失败后），则不 join（避免死锁）
            if self._capture_thread != threading.current_thread():
                self._capture_thread.join(timeout=5)
        self.stop_recording()
        if self._streamer:
            self._streamer.stop()
        self._frame_source.stop()
        logger.info("ScreenManager stopped")

    def get_frame(self, timeout: float = 1.0) -> bytes:
        """获取单帧（供录屏和推流共享）。"""
        try:
            return self._frame_queue.get(timeout=timeout)
        except Empty:
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
                    logger.warning("BGRA queue empty after retries, falling back to direct capture")
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
        """后台截图循环（队列满时丢弃旧帧）。"""
        consecutive_errors = 0
        max_consecutive_errors = 10  # 连续错误阈值

        # WindowsFrameSource 使用本地 MSS 截屏，不需要重连逻辑
        # 直接调用 get_frame() 避免重连阻塞导致无法快速响应停止信号
        use_direct_get_frame = type(self._frame_source).__name__ in ("WindowsFrameSource", "WebFrameSource")

        while self._running:
            try:
                if use_direct_get_frame:
                    frame = self._frame_source.get_frame()
                else:
                    frame = self._frame_source.get_frame_with_reconnect()
                consecutive_errors = 0  # 成功后重置计数

                if self._frame_queue.full():
                    # 队列满时丢弃最旧的帧
                    try:
                        self._frame_queue.get_nowait()
                    except Empty:
                        pass
                self._frame_queue.put(frame, timeout=1)

                # 同时获取 BGRA 帧放入 _bgra_queue
                try:
                    bgra = self._frame_source.get_frame_bgra()
                    if self._bgra_queue.full():
                        try:
                            self._bgra_queue.get_nowait()
                        except Empty:
                            pass
                    self._bgra_queue.put(bgra, timeout=1)
                except NotImplementedError:
                    # FrameSource 不支持 BGRA 时跳过
                    pass

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

    def start_recording(self, output_path: str, fps: int = 10,
                        timeout_ms: int = 7200000, audio: bool = False,
                        monitor: int = 1) -> bool:
        """启动录屏。

        Args:
            output_path: 输出文件路径
            fps: 帧率
            timeout_ms: 超时时间（毫秒），默认 2 小时
            audio: 是否录制音频（仅 win-recorder 支持）
            monitor: 显示器选择（仅 win-recorder 支持）

        Returns:
            bool: 是否成功启动（False 表示已有录屏进行中）
        """
        from worker.screen.recorder import ScreenRecorder

        with self._recording_lock:
            if self._is_recording:
                logger.warning("Recording already in progress")
                return False

            timeout_sec = timeout_ms // 1000
            self._recorder = ScreenRecorder(self, output_path, fps, timeout_sec, audio, monitor)
            self._recorder.start()
            self._is_recording = True
            logger.info(f"Recording started: {output_path}")
            return True

    def stop_recording(self) -> str:
        """停止录屏，返回文件路径。"""
        with self._recording_lock:
            if not self._is_recording or not self._recorder:
                return ""

            output_path = self._recorder.stop()
            self._recorder = None
            self._is_recording = False
            logger.info(f"Recording stopped: {output_path}")
            return output_path

    def start_streaming(self, codec: str = "jpeg") -> "WebSocketStreamer":
        """启动 WebSocket 推流。

        Args:
            codec: 推流编码格式 (jpeg/h264/mjpeg)
        """
        from worker.screen.streamer import WebSocketStreamer

        if not self._streamer:
            self._streamer = WebSocketStreamer(self, codec=codec)
            self._streamer.start()
            logger.info(f"WebSocket streaming started (codec={codec})")
        return self._streamer
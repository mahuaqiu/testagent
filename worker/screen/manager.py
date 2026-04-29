"""ScreenManager 统一管理器。"""

import logging
import threading
import time
from queue import Queue, Empty
from typing import Callable, Optional, TYPE_CHECKING

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
    """关闭指定设备的 ScreenManager。"""
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
        self._capture_thread: Optional[threading.Thread] = None
        self._running: bool = False
        self._is_recording: bool = False
        self._recording_lock: threading.Lock = threading.Lock()
        self._recorder: Optional["ScreenRecorder"] = None
        self._streamer: Optional["WebSocketStreamer"] = None

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

    def _capture_loop(self) -> None:
        """后台截图循环（队列满时丢弃旧帧）。"""
        consecutive_errors = 0
        max_consecutive_errors = 10  # 连续错误阈值

        while self._running:
            try:
                frame = self._frame_source.get_frame_with_reconnect()
                consecutive_errors = 0  # 成功后重置计数

                if self._frame_queue.full():
                    # 队列满时丢弃最旧的帧
                    try:
                        self._frame_queue.get_nowait()
                    except Empty:
                        pass
                self._frame_queue.put(frame, timeout=1)
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
                        timeout_ms: int = 7200000) -> bool:
        """启动录屏。

        Args:
            output_path: 输出文件路径
            fps: 帧率
            timeout_ms: 超时时间（毫秒），默认 2 小时

        Returns:
            bool: 是否成功启动（False 表示已有录屏进行中）
        """
        from worker.screen.recorder import ScreenRecorder

        with self._recording_lock:
            if self._is_recording:
                logger.warning("Recording already in progress")
                return False

            timeout_sec = timeout_ms // 1000
            self._recorder = ScreenRecorder(self, output_path, fps, timeout_sec)
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

    def start_streaming(self) -> "WebSocketStreamer":
        """启动 WebSocket 推流。"""
        from worker.screen.streamer import WebSocketStreamer

        if not self._streamer:
            self._streamer = WebSocketStreamer(self)
            self._streamer.start()
            logger.info("WebSocket streaming started")
        return self._streamer
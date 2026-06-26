"""ScreenRecorder 录屏器（基于 win-recorder 硬件编码）。"""

import logging
import sys
import threading
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from worker.screen.manager import ScreenManager

logger = logging.getLogger(__name__)

# 尝试导入 win_recorder
_win_recorder_available = False
try:
    import win_recorder

    _win_recorder_available = True
    logger.info("win_recorder module available")
except ImportError:
    logger.error("win_recorder not available, recording will fail. Please install win-recorder wheel.")


class ScreenRecorder:
    """录屏器（基于 win-recorder 硬件编码）。"""

    def __init__(
        self,
        screen_manager: "ScreenManager",
        output_path: str,
        fps: int = 10,
        timeout_sec: int = 7200,
        audio: bool = False,
        monitor: int = 1,
        watermark: bool = True,
    ):
        """
        Args:
            screen_manager: ScreenManager 实例
            output_path: 输出文件路径
            fps: 帧率
            timeout_sec: 超时时间（秒）
            audio: 是否录制音频
            monitor: 显示器选择
            watermark: 是否开启时间水印（默认 True）
        """
        self.screen_manager = screen_manager
        self.output_path = output_path
        self.fps = fps
        self.timeout_sec = timeout_sec
        self.audio = audio
        self.monitor = monitor
        self.watermark = watermark
        self._stop_event = threading.Event()
        self._timeout_timer: threading.Timer | None = None
        self._write_thread: threading.Thread | None = None
        self._win_recorder: Optional["win_recorder.WinRecorder"] = None

        if not _win_recorder_available:
            raise RuntimeError("win_recorder not available, cannot start recording")

    def start(self) -> None:
        """启动录屏。"""
        if not _win_recorder_available:
            raise RuntimeError("win_recorder not available")

        # 启动超时定时器
        self._timeout_timer = threading.Timer(self.timeout_sec, self.stop)
        self._timeout_timer.start()

        # 初始化 win-recorder
        self._win_recorder = win_recorder.WinRecorder(
            output_path=self.output_path,
            fps=self.fps,
            audio=self.audio,
            monitor=self.monitor,
            watermark=self.watermark,
        )
        self._win_recorder.start()

        # 获取对齐后的分辨率，同步给 FrameSource
        aligned_width = self._win_recorder.width
        aligned_height = self._win_recorder.height
        self.screen_manager.set_frame_aligned_size(aligned_width, aligned_height)

        # 启动写入线程
        self._write_thread = threading.Thread(target=self._write_loop, daemon=True)
        self._write_thread.start()

        logger.info(f"Recording started: {self.output_path}, fps={self.fps}, monitor={self.monitor}, audio={self.audio}")

    def _write_loop(self) -> None:
        """编码写入循环。

        按录制帧率从队列取帧写入，确保帧率稳定。
        """
        import time

        frame_interval = 1.0 / self.fps

        try:
            while not self._stop_event.is_set():
                # 从队列获取帧
                frame = self.screen_manager.get_frame_bgra()
                if frame and self._win_recorder:
                    self._win_recorder.write_frame(frame)
                # 按录制帧率控制速度
                time.sleep(frame_interval)
        except Exception as e:
            logger.error(f"Recording write loop error: {e}")
        finally:
            logger.debug("Recording write loop ended")

    def stop(self) -> str:
        """停止录屏，返回文件路径。幂等操作：可多次调用。"""
        # 幂等处理：已经停止或从未开始，直接返回
        if self._win_recorder is None:
            logger.info("Recording already stopped or never started")
            return self.output_path

        # 标记停止事件（防止重复调用）
        if self._stop_event.is_set():
            logger.info("Recording stop already called")
            return self.output_path

        self._stop_event.set()

        # 取消超时定时器
        if self._timeout_timer:
            self._timeout_timer.cancel()
            self._timeout_timer = None

        # 停止 win-recorder
        if self._win_recorder:
            try:
                self._win_recorder.stop()
                logger.info("WinRecorder stopped")
            except Exception as e:
                logger.warning(f"WinRecorder stop error: {e}")
            finally:
                self._win_recorder = None

        # 等待写入线程结束
        if self._write_thread:
            self._write_thread.join(timeout=5)
            self._write_thread = None

        logger.info(f"Recording stopped: {self.output_path}")
        return self.output_path
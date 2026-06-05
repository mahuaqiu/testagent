"""ScreenRecorder 录屏器（win-recorder + FFmpeg 双模式）。"""

import logging
import subprocess
import sys
import threading
from typing import TYPE_CHECKING, Optional

from common.utils import SUBPROCESS_HIDE_WINDOW

if TYPE_CHECKING:
    from worker.screen.manager import ScreenManager

logger = logging.getLogger(__name__)

# 尝试导入 win_recorder
_win_recorder_available = False
try:
    import win_recorder

    _win_recorder_available = True
    logger.info("win_recorder module available, will use hardware encoding")
except ImportError:
    logger.info("win_recorder not available, will use FFmpeg")


class ScreenRecorder:
    """录屏器（win-recorder 硬件编码 或 FFmpeg 软件编码）。"""

    def __init__(
        self,
        screen_manager: "ScreenManager",
        output_path: str,
        fps: int = 10,
        timeout_sec: int = 7200,
        audio: bool = False,
        monitor: int = 1,
    ):
        """
        Args:
            screen_manager: ScreenManager 实例
            output_path: 输出文件路径
            fps: 帧率
            timeout_sec: 超时时间（秒）
            audio: 是否录制音频（仅 win-recorder 支持）
            monitor: 显示器选择（仅 win-recorder 支持）
        """
        self.screen_manager = screen_manager
        self.output_path = output_path
        self.fps = fps
        self.timeout_sec = timeout_sec
        self.audio = audio
        self.monitor = monitor
        self._stop_event = threading.Event()
        self._timeout_timer: threading.Timer | None = None
        self._write_thread: threading.Thread | None = None

        # 录制器实例（win-recorder 或 None）
        self._win_recorder: Optional["win_recorder.WinRecorder"] = None
        self._ffmpeg_process: subprocess.Popen | None = None

        # 判断使用哪种模式
        self._use_win_recorder = _win_recorder_available and sys.platform == "win32"

    def start(self) -> None:
        """启动录屏。"""
        # 启动超时定时器
        self._timeout_timer = threading.Timer(self.timeout_sec, self.stop)
        self._timeout_timer.start()

        if self._use_win_recorder:
            self._start_win_recorder()
        else:
            self._start_ffmpeg()

        # 启动写入线程
        self._write_thread = threading.Thread(target=self._write_loop, daemon=True)
        self._write_thread.start()

        mode = "win-recorder (hardware)" if self._use_win_recorder else "FFmpeg (software)"
        logger.info(f"Recording started: {self.output_path}, fps={self.fps}, mode={mode}")

    def _start_win_recorder(self) -> None:
        """使用 win-recorder 启动录屏。"""
        try:
            self._win_recorder = win_recorder.WinRecorder(
                output_path=self.output_path,
                fps=self.fps,
                audio=self.audio,
                monitor=self.monitor,
            )
            self._win_recorder.start()
            logger.info(f"WinRecorder started: {self.output_path}")
        except Exception as e:
            logger.error(f"Failed to start WinRecorder: {e}, falling back to FFmpeg")
            self._use_win_recorder = False
            self._start_ffmpeg()

    def _start_ffmpeg(self) -> None:
        """使用 FFmpeg 启动录屏。"""
        width, height = self.screen_manager._frame_source.get_screen_size()

        ffmpeg_cmd = [
            "ffmpeg",
            "-y",
            "-f", "image2pipe",
            "-vcodec", "mjpeg",
            "-r", str(self.fps),
            "-i", "-",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-pix_fmt", "yuv420p",
            "-s", f"{width}x{height}",
            self.output_path,
        ]

        self._ffmpeg_process = subprocess.Popen(
            ffmpeg_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=SUBPROCESS_HIDE_WINDOW,
        )

    def _write_loop(self) -> None:
        """编码写入循环。"""
        try:
            while not self._stop_event.is_set():
                if self._use_win_recorder and self._win_recorder:
                    # win-recorder 模式：使用 BGRA 原始帧
                    frame = self.screen_manager.get_frame_bgra()
                    if frame:
                        self._win_recorder.write_frame(frame)
                else:
                    # FFmpeg 模式：使用 JPEG 帧
                    frame = self.screen_manager.get_frame()
                    if frame and self._ffmpeg_process and self._ffmpeg_process.stdin:
                        try:
                            self._ffmpeg_process.stdin.write(frame)
                        except BrokenPipeError:
                            logger.warning("FFmpeg stdin pipe broken")
                            break

        except Exception as e:
            logger.error(f"Recording write loop error: {e}")
        finally:
            logger.debug("Recording write loop ended")

    def stop(self) -> str:
        """停止录屏，返回文件路径。"""
        self._stop_event.set()

        # 取消超时定时器
        if self._timeout_timer:
            self._timeout_timer.cancel()
            self._timeout_timer = None

        # 停止录制器
        if self._use_win_recorder and self._win_recorder:
            try:
                self._win_recorder.stop()
                logger.info("WinRecorder stopped")
            except Exception as e:
                logger.warning(f"WinRecorder stop error: {e}")
            finally:
                self._win_recorder = None
        else:
            # 关闭 FFmpeg 进程
            if self._ffmpeg_process:
                try:
                    if self._ffmpeg_process.stdin:
                        self._ffmpeg_process.stdin.close()
                    self._ffmpeg_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._ffmpeg_process.terminate()
                    self._ffmpeg_process.wait(timeout=2)
                except Exception as e:
                    logger.warning(f"FFmpeg cleanup error: {e}")
                finally:
                    self._ffmpeg_process = None

        # 等待写入线程结束
        if self._write_thread:
            self._write_thread.join(timeout=5)
            self._write_thread = None

        logger.info(f"Recording stopped: {self.output_path}")
        return self.output_path
"""ScreenRecorder FFmpeg 录屏器。"""

import logging
import subprocess
import threading
from typing import TYPE_CHECKING

from common.utils import SUBPROCESS_HIDE_WINDOW

if TYPE_CHECKING:
    from worker.screen.manager import ScreenManager

logger = logging.getLogger(__name__)


class ScreenRecorder:
    """FFmpeg 录屏器（队列缓冲 + 双线程）。"""

    def __init__(
        self,
        screen_manager: "ScreenManager",
        output_path: str,
        fps: int = 10,
        timeout_sec: int = 7200,
    ):
        """
        Args:
            screen_manager: ScreenManager 实例
            output_path: 输出文件路径
            fps: 帧率
            timeout_sec: 超时时间（秒）
        """
        self.screen_manager = screen_manager
        self.output_path = output_path
        self.fps = fps
        self.timeout_sec = timeout_sec
        self._stop_event = threading.Event()
        self._timeout_timer: threading.Timer | None = None
        self._ffmpeg_process: subprocess.Popen | None = None
        self._write_thread: threading.Thread | None = None

    def start(self) -> None:
        """启动录屏。"""
        # 启动超时定时器
        self._timeout_timer = threading.Timer(self.timeout_sec, self.stop)
        self._timeout_timer.start()

        # 启动 FFmpeg 写入线程
        self._write_thread = threading.Thread(target=self._write_loop, daemon=True)
        self._write_thread.start()

        logger.info(f"Recording started: {self.output_path}, fps={self.fps}, timeout={self.timeout_sec}s")

    def _write_loop(self) -> None:
        """FFmpeg 编码写入线程。"""
        width, height = self.screen_manager._frame_source.get_screen_size()

        # FFmpeg 命令：使用 image2pipe 输入 JPEG 序列
        ffmpeg_cmd = [
            "ffmpeg",
            "-y",  # 覆盖输出文件
            "-f", "image2pipe",
            "-vcodec", "mjpeg",
            "-r", str(self.fps),
            "-i", "-",  # 从 stdin 读取
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-pix_fmt", "yuv420p",
            "-s", f"{width}x{height}",
            self.output_path,
        ]

        try:
            self._ffmpeg_process = subprocess.Popen(
                ffmpeg_cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=SUBPROCESS_HIDE_WINDOW,
            )

            while not self._stop_event.is_set():
                frame = self.screen_manager.get_frame()
                if frame and self._ffmpeg_process and self._ffmpeg_process.stdin:
                    try:
                        self._ffmpeg_process.stdin.write(frame)
                    except BrokenPipeError:
                        logger.warning("FFmpeg stdin pipe broken")
                        break

        except FileNotFoundError:
            logger.error("FFmpeg not found, please install ffmpeg and add to PATH")
        except Exception as e:
            logger.error(f"FFmpeg error: {e}")
        finally:
            logger.debug("Write loop ended")

    def stop(self) -> str:
        """停止录屏，返回文件路径。"""
        self._stop_event.set()

        # 取消超时定时器
        if self._timeout_timer:
            self._timeout_timer.cancel()
            self._timeout_timer = None

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
            self._ffmpeg_process = None

        # 等待写入线程结束
        if self._write_thread:
            self._write_thread.join(timeout=5)
            self._write_thread = None

        logger.info(f"Recording stopped: {self.output_path}")
        return self.output_path
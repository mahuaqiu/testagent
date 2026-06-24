"""WebSocketStreamer 推流器。"""

import asyncio
import logging
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from worker.screen.manager import ScreenManager

logger = logging.getLogger(__name__)


class WebSocketStreamer:
    """WebSocket 屏幕推流器。"""

    def __init__(self, screen_manager: "ScreenManager", codec: str = "jpeg"):
        self.screen_manager = screen_manager
        self.codec = codec
        self._running = False
        self._h264_streamer = None
        self._h264_info = None  # 保存 H.264 编码器初始化信息

    def start(self, codec: str = "jpeg", on_fallback: Optional[Callable[[], None]] = None) -> None:
        """启动推流。"""
        self.codec = codec
        self._running = True
        self._h264_info = None

        # H.264 模式需要初始化编码器
        if self.codec == "h264":
            try:
                from worker.screen.h264_streamer import H264Streamer
                self._h264_streamer = H264Streamer(
                    self.screen_manager._frame_source,
                    fps=10
                )
                self._h264_streamer.set_fallback_callback(on_fallback or self._default_fallback)
                self._h264_info = self._h264_streamer.start()
                logger.info("H.264 encoder started for streaming")
            except Exception as e:
                logger.error(f"Failed to start H.264 encoder: {e}, falling back to JPEG")
                self.codec = "jpeg"

        logger.info(f"WebSocket streamer started (codec={self.codec})")

    def _default_fallback(self) -> None:
        """默认降级处理：切换到 JPEG。"""
        logger.warning("Falling back to JPEG mode")
        self.codec = "jpeg"

    def stop(self) -> None:
        """停止推流。"""
        self._running = False
        if self._h264_streamer:
            # stop() 会自动重置 H264 相关的降级状态
            self._h264_streamer.stop()
            self._h264_streamer = None
        logger.info("WebSocket streamer stopped")

    async def get_frame_async(self) -> Optional[bytes]:
        """异步获取帧（避免阻塞 WebSocket）。"""
        if self.codec == "h264" and self._h264_streamer:
            # H.264 模式：直接获取编码帧
            return await asyncio.to_thread(self._h264_streamer.get_frame)
        else:
            # JPEG 模式：从队列获取帧
            return await asyncio.to_thread(self.screen_manager.get_frame)

    def is_running(self) -> bool:
        """检查是否正在运行。"""
        return self._running

    def get_h264_info(self) -> Optional[dict]:
        """获取 H.264 编码器信息（SPS/PPS）。"""
        if self._h264_info:
            return self._h264_info
        return None
"""WebSocketStreamer 推流器。"""

import asyncio
import logging
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from worker.screen.manager import ScreenManager

logger = logging.getLogger(__name__)


class WebSocketStreamer:
    """WebSocket 屏幕推流器。"""

    def __init__(self, screen_manager: "ScreenManager", codec: str = "jpeg"):
        self.screen_manager = screen_manager
        self.codec = codec
        self._running = False

    def start(self, codec: str = "jpeg") -> None:
        """启动推流。"""
        # 非 Windows 平台不支持 H.264 硬编推流（Windows 走 sidecar RSM1 通道），降级 JPEG
        if codec == "h264":
            logger.warning("H.264 streaming not supported on this path, falling back to JPEG")
            codec = "jpeg"
        self.codec = codec
        self._running = True
        logger.info(f"WebSocket streamer started (codec={self.codec})")

    def stop(self) -> None:
        """停止推流。"""
        self._running = False
        logger.info("WebSocket streamer stopped")

    async def get_frame_async(self) -> Optional[bytes]:
        """异步获取帧（避免阻塞 WebSocket）。"""
        # 从队列获取 JPEG 帧
        return await asyncio.to_thread(self.screen_manager.get_frame)

    def is_running(self) -> bool:
        """检查是否正在运行。"""
        return self._running

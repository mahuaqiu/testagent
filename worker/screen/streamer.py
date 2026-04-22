"""WebSocketStreamer 推流器。"""

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from worker.screen.manager import ScreenManager

logger = logging.getLogger(__name__)


class WebSocketStreamer:
    """WebSocket 屏幕推流器。"""

    def __init__(self, screen_manager: "ScreenManager"):
        self.screen_manager = screen_manager
        self._running = False

    def start(self) -> None:
        """启动推流。"""
        self._running = True
        logger.info("WebSocket streamer started")

    def stop(self) -> None:
        """停止推流。"""
        self._running = False
        logger.info("WebSocket streamer stopped")

    async def get_frame_async(self) -> bytes:
        """异步获取帧（避免阻塞 WebSocket）。"""
        return await asyncio.to_thread(self.screen_manager.get_frame)

    def is_running(self) -> bool:
        """检查是否正在运行。"""
        return self._running
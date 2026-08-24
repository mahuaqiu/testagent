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
        # Windows 走 sidecar RSM1；鸿蒙官方帧源可直接输出 H.264。其它旧帧源
        # 没有编码器，继续安全降级为 JPEG，避免误把 JPEG 送进前端 MSE。
        frame_source = getattr(self.screen_manager, "_frame_source", None)
        if codec == "h264" and not getattr(frame_source, "supports_h264", False):
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

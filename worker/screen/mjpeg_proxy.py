"""MJPEG HTTP→WebSocket 代理。"""

import asyncio
import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class MJPEGProxy:
    """MJPEG HTTP→WebSocket 代理。"""

    def __init__(self, host: str, port: int = 9100):
        self.host = host
        self.port = port
        self._response = None
        self._iterator = None
        self._running = False

    def start(self):
        """启动 MJPEG 流连接。"""
        mjpeg_url = f"http://{self.host}:{self.port}"
        try:
            self._response = requests.get(mjpeg_url, stream=True, timeout=30)
            self._iterator = self._response.iter_content(chunk_size=8192)
            self._running = True
            logger.info(f"MJPEG proxy started: {mjpeg_url}")
        except Exception as e:
            logger.error(f"Failed to start MJPEG proxy: {e}")
            raise

    async def proxy_to_websocket(self, websocket):
        """透传到 WebSocket。"""
        try:
            while self._running:
                try:
                    chunk = next(self._iterator)
                    await websocket.send_bytes(chunk)
                except StopIteration:
                    # 流结束，重新连接
                    logger.warning("MJPEG stream ended, reconnecting...")
                    self.start()
                except Exception as e:
                    logger.error(f"MJPEG proxy error: {e}")
                    break
        finally:
            self.stop()

    def stop(self):
        """停止透传。"""
        self._running = False
        if self._response:
            try:
                self._response.close()
            except Exception:
                pass
            self._response = None
        logger.info("MJPEG proxy stopped")
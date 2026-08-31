"""MJPEG HTTP→WebSocket 代理。"""

import asyncio
import logging
import re
import threading

import requests

logger = logging.getLogger(__name__)


def _is_expected_websocket_close(exc: BaseException) -> bool:
    """判断异常是否表示 MJPEG WebSocket 已由客户端或 ASGI 层关闭。"""
    if isinstance(
        exc,
        (
            ConnectionAbortedError,
            ConnectionResetError,
            BrokenPipeError,
        ),
    ):
        return True
    if isinstance(exc, RuntimeError):
        message = str(exc)
        return (
            "Unexpected ASGI message" in message
            and "websocket.send" in message
            and (
                "websocket.close" in message
                or "response already completed" in message
            )
        )
    return False


class MJPEGProxy:
    """MJPEG HTTP→WebSocket 代理。"""

    def __init__(self, host: str, port: int = 9100):
        self.host = host
        self.port = port
        self._response = None
        self._iterator = None
        self._running = False
        self._response_lock = threading.Lock()
        self._boundary = b"--BoundaryString"
        self._stream_buffer = b""

    def start(self):
        """启动 MJPEG 流连接。"""
        mjpeg_url = f"http://{self.host}:{self.port}"
        try:
            self._close_response()
            response = requests.get(
                mjpeg_url,
                stream=True,
                timeout=(10, 2),
            )
            iterator = response.iter_content(chunk_size=8192)
            content_type = response.headers.get("Content-Type", "")
            boundary_match = re.search(r"boundary=\"?([^;\"]+)", content_type, re.IGNORECASE)
            boundary = (
                boundary_match.group(1).encode("ascii", errors="ignore")
                if boundary_match
                else b"BoundaryString"
            )
            if not boundary.startswith(b"--"):
                boundary = b"--" + boundary
            with self._response_lock:
                self._response = response
                self._iterator = iterator
                self._boundary = boundary
                self._stream_buffer = b""
                self._running = True
            logger.info(f"MJPEG proxy started: {mjpeg_url}")
        except Exception as e:
            self._close_response()
            logger.error(f"Failed to start MJPEG proxy: {e}")
            raise

    def _close_response(self) -> None:
        """关闭当前响应，解除可能阻塞在 iter_content 的读取。"""
        with self._response_lock:
            response = self._response
            self._response = None
            self._iterator = None
            self._stream_buffer = b""
        if response:
            try:
                response.close()
            except Exception:
                pass

    def _read_frame(self):
        """从 multipart 流中读取一个完整 JPEG 帧。"""
        with self._response_lock:
            iterator = self._iterator
            response = self._response
            boundary = self._boundary
        if iterator is None:
            return None

        while True:
            with self._response_lock:
                # stop()/start() 可能在读线程阻塞期间替换响应，旧响应的数据不能
                # 再写入新响应的缓冲区。
                if response is not self._response or iterator is not self._iterator:
                    return None
                boundary_pos = self._stream_buffer.find(boundary)
                if boundary_pos >= 0:
                    if boundary_pos > 0:
                        self._stream_buffer = self._stream_buffer[boundary_pos:]
                    header_end = self._stream_buffer.find(b"\r\n\r\n", len(boundary))
                    if header_end >= 0:
                        header = self._stream_buffer[len(boundary):header_end]
                        length_match = re.search(
                            rb"(?:^|\r\n)Content-Length:\s*(\d+)",
                            header,
                            re.IGNORECASE,
                        )
                        data_start = header_end + 4
                        if length_match:
                            content_length = int(length_match.group(1))
                            data_end = data_start + content_length
                            if len(self._stream_buffer) >= data_end:
                                frame = self._stream_buffer[data_start:data_end]
                                self._stream_buffer = self._stream_buffer[data_end:]
                                return frame
                        else:
                            next_boundary = self._stream_buffer.find(boundary, data_start)
                            if next_boundary >= 0:
                                frame = self._stream_buffer[data_start:next_boundary].rstrip(
                                    b"\r\n"
                                )
                                self._stream_buffer = self._stream_buffer[next_boundary:]
                                if frame:
                                    return frame

            try:
                chunk = next(iterator)
            except StopIteration:
                return None
            with self._response_lock:
                if response is not self._response or iterator is not self._iterator:
                    return None
                self._stream_buffer += chunk

    async def proxy_to_websocket(self, websocket, stop_event: asyncio.Event | None = None):
        """透传到 WebSocket。"""
        try:
            while self._running and not (stop_event and stop_event.is_set()):
                try:
                    frame = await asyncio.to_thread(self._read_frame)
                    if frame is None:
                        if not self._running or (stop_event and stop_event.is_set()):
                            return
                        logger.warning("MJPEG stream ended, reconnecting...")
                        await asyncio.to_thread(self.start)
                        continue
                    if stop_event and stop_event.is_set():
                        return
                    await websocket.send_bytes(frame)
                except requests.RequestException as e:
                    if stop_event and stop_event.is_set():
                        return
                    logger.warning(f"MJPEG stream read failed, reconnecting: {e}")
                    try:
                        await asyncio.to_thread(self.start)
                    except Exception:
                        await asyncio.sleep(0.5)
                except Exception as e:
                    if _is_expected_websocket_close(e):
                        logger.debug("MJPEG WebSocket 已关闭，停止透传: %s", e)
                    else:
                        logger.error(f"MJPEG proxy error: {e}")
                    break
        finally:
            self.stop()

    def stop(self):
        """停止透传。"""
        self._running = False
        self._close_response()
        logger.info("MJPEG proxy stopped")

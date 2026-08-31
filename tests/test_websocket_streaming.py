"""WebSocket 屏幕推流关闭竞态测试。"""

import asyncio

import pytest

from worker.server import (
    _WebSocketClosed,
    _is_expected_websocket_close,
    _send_websocket_message,
)


ASGI_CLOSE_RACE = (
    "Unexpected ASGI message 'websocket.send', after sending "
    "'websocket.close' or response already completed."
)


def test_asgi_send_after_close_is_expected_disconnect() -> None:
    assert _is_expected_websocket_close(RuntimeError(ASGI_CLOSE_RACE)) is True


def test_unrelated_runtime_error_is_not_expected_disconnect() -> None:
    assert _is_expected_websocket_close(RuntimeError("frame encoder failed")) is False


def test_safe_websocket_send_converts_close_race() -> None:
    class FakeWebSocket:
        async def send_bytes(self, _data: bytes) -> None:
            raise RuntimeError(ASGI_CLOSE_RACE)

        async def send_text(self, _data: str) -> None:
            raise AssertionError("send_text should not be called")

    with pytest.raises(_WebSocketClosed):
        asyncio.run(_send_websocket_message(FakeWebSocket(), b"frame"))


def test_safe_websocket_send_stops_before_sending() -> None:
    class FakeWebSocket:
        def __init__(self) -> None:
            self.sent = False

        async def send_bytes(self, _data: bytes) -> None:
            self.sent = True

        async def send_text(self, _data: str) -> None:
            self.sent = True

    async def run() -> FakeWebSocket:
        websocket = FakeWebSocket()
        stop_event = asyncio.Event()
        stop_event.set()
        with pytest.raises(_WebSocketClosed):
            await _send_websocket_message(
                websocket,
                b"frame",
                stop_event=stop_event,
            )
        return websocket

    websocket = asyncio.run(run())

    assert websocket.sent is False

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


def test_windows_h264_stream_uses_sidecar_manager(monkeypatch):
    """回归测试：Windows 推流必须走 sidecar 分支，不能再落入通用
    ScreenManager 分支调用 _create_frame_source（后者对 windows 直接抛错，
    曾导致 Windows 推流连接建立后立即被关闭）。"""
    from unittest.mock import Mock

    from fastapi.testclient import TestClient

    import worker.screen.windows_sidecar as sidecar_module
    import worker.server as server_module
    from worker.config import WorkerConfig

    calls = {"sidecar": 0, "create_frame_source": 0}

    def fake_get_sidecar_manager(key, **kwargs):
        calls["sidecar"] += 1
        manager = Mock()
        manager.start_streaming.side_effect = RuntimeError("sentinel: end streaming early")
        return manager

    def fake_create_frame_source(*args, **kwargs):
        calls["create_frame_source"] += 1
        raise AssertionError("_create_frame_source must not be called for windows")

    monkeypatch.setattr(server_module, "_create_frame_source", fake_create_frame_source)
    monkeypatch.setattr(sidecar_module, "get_windows_sidecar_manager", fake_get_sidecar_manager)

    fake_worker = Mock()
    fake_worker.config = WorkerConfig()
    monkeypatch.setattr(server_module, "worker", fake_worker)

    client = TestClient(server_module.app, raise_server_exceptions=False)
    # 连接成功（accept 完成、with 块正常进出）即证明走到了 sidecar 分支；
    # 不读帧——sidecar 打桩会在 start_streaming 处结束推流，handler
    # 捕获异常后正常返回，不会发送 close 帧，读帧会无限阻塞。
    with client.websocket_connect(
        "/ws/screen/windows/windows_screen?monitor=1&codec=h264"
    ) as websocket:
        pass

    assert calls["sidecar"] == 1
    assert calls["create_frame_source"] == 0

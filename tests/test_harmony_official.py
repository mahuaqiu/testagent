"""鸿蒙官方 HOScrcpy 集成的无设备单元测试。"""

from __future__ import annotations

import io
import threading
import time

import pytest

from worker.platforms.harmony_official.decoder import LatestFrameCache
from worker.platforms.harmony_official.protocol import (
    BridgeMessageType,
    BridgeProtocolError,
    command_mouse_move,
    command_touch_down,
    encode_message,
    iter_messages,
)
from worker.platforms.harmony_official.session import (
    HarmonyOfficialError,
    HarmonyOfficialSession,
    HarmonyOfficialSessionManager,
)


def test_hos1_round_trip() -> None:
    payload = encode_message(BridgeMessageType.READY, b"serial=test")
    payload += encode_message(BridgeMessageType.H264, b"\x00\x00\x00\x01\x67")

    messages = list(iter_messages(io.BytesIO(payload)))

    assert [message.message_type for message in messages] == [
        BridgeMessageType.READY,
        BridgeMessageType.H264,
    ]
    assert messages[0].text == "serial=test"


def test_hos1_rejects_truncated_payload() -> None:
    payload = encode_message(BridgeMessageType.H264, b"123")[:-1]

    with pytest.raises(BridgeProtocolError, match="被截断"):
        list(iter_messages(io.BytesIO(payload)))


def test_latest_frame_cache_keeps_newest_frame() -> None:
    cache = LatestFrameCache()
    first = cache.update(b"first", 100, 200)
    second = cache.update(b"second", 100, 200)

    latest = cache.get()

    assert latest is not None
    assert latest.jpeg == b"second"
    assert latest.sequence == second.sequence
    assert first.sequence < second.sequence


def test_latest_frame_cache_can_wait_for_newer_frame() -> None:
    cache = LatestFrameCache()
    first = cache.update(b"first", 100, 200)

    def update_later() -> None:
        time.sleep(0.05)
        cache.update(b"second", 300, 400)

    thread = threading.Thread(target=update_later)
    thread.start()
    latest = cache.get(timeout=1.0, after_sequence=first.sequence)
    thread.join()

    assert latest is not None
    assert latest.jpeg == b"second"
    assert (latest.width, latest.height) == (300, 400)


def test_input_commands_and_gesture_step_limits() -> None:
    assert command_touch_down(10, 20) == b"TOUCH_DOWN 10 20\n"
    assert command_mouse_move(None, 10, 20) == b"MOUSE_MOVE NONE 10 20\n"
    assert HarmonyOfficialSession._gesture_steps(500, None) == 10
    assert HarmonyOfficialSession._gesture_steps(5_000, None) == 20
    assert HarmonyOfficialSession._gesture_steps(100, 100) == 40


def test_auto_mode_returns_none_when_hdc_is_unavailable() -> None:
    manager = HarmonyOfficialSessionManager("harmony_mobile", {"mode": "auto"})

    assert manager.get_or_start("device-1") is None


def test_official_mode_rejects_missing_hdc() -> None:
    manager = HarmonyOfficialSessionManager("harmony_mobile", {"mode": "official"})

    with pytest.raises(HarmonyOfficialError, match="HDC"):
        manager.get_or_start("device-1")


def test_session_lease_stops_bridge_after_last_consumer() -> None:
    class FakeSession:
        is_running = True

        def __init__(self) -> None:
            self.stop_count = 0

        def stop(self) -> None:
            self.stop_count += 1

    manager = HarmonyOfficialSessionManager("harmony_mobile", {"mode": "auto"})
    manager.set_hdc_path("hdc.exe")
    fake = FakeSession()
    manager._sessions["device-1"] = fake  # type: ignore[assignment]

    assert manager.acquire("device-1", "task:1") is fake
    assert manager.acquire("device-1", "stream:1") is fake

    manager.release("device-1", "task:1")
    assert fake.stop_count == 0
    manager.release("device-1", "stream:1")
    manager._release_idle_session("device-1")
    assert fake.stop_count == 1
    assert manager.get("device-1") is None
    manager.stop_all()

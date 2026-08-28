"""鸿蒙官方 HOScrcpy 集成的无设备单元测试。"""

from __future__ import annotations

import io
import time
from queue import Empty, Queue
from types import SimpleNamespace

import pytest

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
    _h264_websocket_packets,
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


def test_h264_websocket_packets_split_config_and_idr() -> None:
    payload = (
        b"\x00\x00\x00\x01\x67\x01"
        b"\x00\x00\x00\x01\x68\x02"
        b"\x00\x00\x00\x01\x65\x03"
    )

    packets = _h264_websocket_packets(payload)

    assert packets == [
        b"\x01\x00\x00\x00\x01\x67\x01\x00\x00\x00\x01\x68\x02",
        b"\x02\x00\x00\x00\x01\x65\x03",
    ]


def test_h264_websocket_packets_classify_p_frame() -> None:
    payload = b"\x00\x00\x01\x41\x09"

    assert _h264_websocket_packets(payload) == [b"\x03" + payload]


def test_h264_first_subscription_starts_from_a_fresh_keyframe() -> None:
    sent_commands: list[bytes] = []
    session = HarmonyOfficialSession(
        serial="device-1",
        device_type="mobile",
        hdc_path="hdc.exe",
        settings=dict(HarmonyOfficialSessionManager.DEFAULTS),
    )
    session._bridge = SimpleNamespace(  # type: ignore[assignment]
        is_running=True,
        send=sent_commands.append,
        stop=lambda timeout=5.0: None,
    )
    session._latest_h264_config = b"\x01\x00\x00\x01\x67"
    session._latest_h264_keyframe = b"\x02\x00\x00\x01\x65"

    queue = session.subscribe_h264("subscriber-1")

    with pytest.raises(Empty):
        queue.get_nowait()
    assert sent_commands == [b"WAKE_STREAM\n", b"REQUEST_IDR\n"]
    assert session._latest_h264_config is None
    assert session._latest_h264_keyframe is None

    # 新的参数集/关键帧到达后才允许后续 P 帧进入订阅队列。
    session._broadcast_h264(
        b"\x00\x00\x01\x67\x01\x00\x00\x01\x68\x02"
        b"\x00\x00\x01\x65\x03"
    )
    assert queue.get_nowait()[0] == 0x01
    assert queue.get_nowait()[0] == 0x02
    session._broadcast_h264(b"\x00\x00\x01\x41\x09")
    assert queue.get_nowait() == b"\x03\x00\x00\x01\x41\x09"
    session.stop()


def test_h264_second_subscription_can_replay_active_keyframe() -> None:
    sent_commands: list[bytes] = []
    session = HarmonyOfficialSession(
        serial="device-1",
        device_type="mobile",
        hdc_path="hdc.exe",
        settings=dict(HarmonyOfficialSessionManager.DEFAULTS),
    )
    session._bridge = SimpleNamespace(  # type: ignore[assignment]
        is_running=True,
        send=sent_commands.append,
        stop=lambda timeout=5.0: None,
    )
    session._latest_h264_config = b"\x01\x00\x00\x01\x67"
    session._latest_h264_keyframe = b"\x02\x00\x00\x01\x65"
    session._h264_subscribers["subscriber-1"] = SimpleNamespace(  # type: ignore[assignment]
        subscriber_id="subscriber-1",
        queue=Queue(),
        has_config=True,
        waiting_for_keyframe=False,
        enqueued_packets=0,
        dropped_packets=0,
        dropped_p_packets=0,
        skipped_p_packets=0,
        queue_full_events=0,
        peak_queue_size=0,
    )

    queue = session.subscribe_h264("subscriber-2")

    assert queue.get_nowait() == session._latest_h264_config
    assert queue.get_nowait() == session._latest_h264_keyframe
    assert sent_commands == []
    session.stop()


def test_h264_subscription_waits_for_idr_when_only_config_is_cached() -> None:
    sent_commands: list[bytes] = []
    session = HarmonyOfficialSession(
        serial="device-1",
        device_type="mobile",
        hdc_path="hdc.exe",
        settings=dict(HarmonyOfficialSessionManager.DEFAULTS),
    )
    session._bridge = SimpleNamespace(  # type: ignore[assignment]
        is_running=True,
        send=sent_commands.append,
        stop=lambda timeout=5.0: None,
    )
    session._latest_h264_config = b"\x01\x00\x00\x01\x67"
    session._last_idr_request_at = time.monotonic()
    session._h264_subscribers["subscriber-1"] = SimpleNamespace(  # type: ignore[assignment]
        subscriber_id="subscriber-1",
        queue=Queue(),
        has_config=True,
        waiting_for_keyframe=False,
        enqueued_packets=0,
        dropped_packets=0,
        dropped_p_packets=0,
        skipped_p_packets=0,
        queue_full_events=0,
        peak_queue_size=0,
    )

    queue = session.subscribe_h264("subscriber-2")
    assert queue.get_nowait() == session._latest_h264_config

    session._broadcast_h264(b"\x00\x00\x01\x41\x09")

    with pytest.raises(Empty):
        queue.get_nowait()
    session.stop()


def test_h264_slow_subscriber_drops_only_its_p_frame_and_requests_idr() -> None:
    sent_commands: list[bytes] = []
    session = HarmonyOfficialSession(
        serial="device-1",
        device_type="mobile",
        hdc_path="hdc.exe",
        settings={**HarmonyOfficialSessionManager.DEFAULTS, "frame_queue_capacity": 1},
    )
    session._bridge = SimpleNamespace(  # type: ignore[assignment]
        is_running=True,
        send=sent_commands.append,
        stop=lambda timeout=5.0: None,
    )

    slow_queue = Queue(maxsize=1)
    slow_queue.put(b"occupied")
    session._h264_subscribers["slow"] = SimpleNamespace(  # type: ignore[assignment]
        subscriber_id="slow",
        queue=slow_queue,
        has_config=True,
        waiting_for_keyframe=False,
        enqueued_packets=0,
        dropped_packets=0,
        dropped_p_packets=0,
        skipped_p_packets=0,
        queue_full_events=0,
        peak_queue_size=0,
    )
    fast_queue = Queue(maxsize=4)
    session._h264_subscribers["fast"] = SimpleNamespace(  # type: ignore[assignment]
        subscriber_id="fast",
        queue=fast_queue,
        has_config=True,
        waiting_for_keyframe=False,
        enqueued_packets=0,
        dropped_packets=0,
        dropped_p_packets=0,
        skipped_p_packets=0,
        queue_full_events=0,
        peak_queue_size=0,
    )
    session._broadcast_h264(b"\x00\x00\x01\x41\x09")

    slow = session._h264_subscribers["slow"]
    assert slow.dropped_p_packets == 1
    assert slow.waiting_for_keyframe is True
    assert fast_queue.get_nowait() == b"\x03\x00\x00\x01\x41\x09"
    assert sent_commands == [b"REQUEST_IDR\n"]
    session.stop()


def test_h264_slow_subscriber_clears_stale_packets_and_keeps_config() -> None:
    session = HarmonyOfficialSession(
        serial="device-1",
        device_type="mobile",
        hdc_path="hdc.exe",
        settings={**HarmonyOfficialSessionManager.DEFAULTS, "frame_queue_capacity": 1},
    )
    session._latest_h264_config = b"\x01config"
    queue = Queue(maxsize=4)
    queue.put(b"\x01old-config")
    queue.put(b"\x03old-p-1")
    queue.put(b"\x03old-p-2")
    queue.put(b"\x03old-p-3")
    session._h264_subscribers["slow"] = SimpleNamespace(  # type: ignore[assignment]
        subscriber_id="slow",
        queue=queue,
        has_config=True,
        waiting_for_keyframe=False,
        enqueued_packets=0,
        dropped_packets=0,
        dropped_p_packets=0,
        skipped_p_packets=0,
        queue_full_events=0,
        peak_queue_size=0,
    )

    session._broadcast_h264(b"\x00\x00\x01\x41new-p")

    subscriber = session._h264_subscribers["slow"]
    assert subscriber.waiting_for_keyframe is True
    assert subscriber.has_config is True
    assert subscriber.dropped_p_packets == 4
    assert list(queue.queue) == [b"\x01config"]
    session.stop()


def test_h264_subscription_does_not_schedule_idr_retry() -> None:
    sent_commands: list[bytes] = []
    session = HarmonyOfficialSession(
        serial="device-1",
        device_type="mobile",
        hdc_path="hdc.exe",
        settings=dict(HarmonyOfficialSessionManager.DEFAULTS),
    )
    session._bridge = SimpleNamespace(  # type: ignore[assignment]
        is_running=True,
        send=sent_commands.append,
        stop=lambda timeout=5.0: None,
    )
    queue = session.subscribe_h264("subscriber-1")
    assert queue.empty()

    session._broadcast_h264(
        b"\x00\x00\x01\x67\x01\x00\x00\x01\x68\x02"
        b"\x00\x00\x01\x65\x03"
    )

    session.stop()


def test_h264_packets_are_dropped_without_subscribers() -> None:
    session = HarmonyOfficialSession(
        serial="device-1",
        device_type="mobile",
        hdc_path="hdc.exe",
        settings=dict(HarmonyOfficialSessionManager.DEFAULTS),
    )
    session._broadcast_h264(b"\x00\x00\x01\x67\x01\x00\x00\x01\x65\x02")

    assert session._latest_h264_config is None
    assert session._latest_h264_keyframe is None


def test_last_h264_unsubscribe_clears_startup_cache() -> None:
    session = HarmonyOfficialSession(
        serial="device-1",
        device_type="mobile",
        hdc_path="hdc.exe",
        settings=dict(HarmonyOfficialSessionManager.DEFAULTS),
    )
    session._h264_subscribers["subscriber-1"] = SimpleNamespace(queue=Queue())  # type: ignore[assignment]
    session._latest_h264_config = b"\x01config"
    session._latest_h264_keyframe = b"\x02idr"

    session.unsubscribe_h264("subscriber-1")

    assert session._latest_h264_config is None
    assert session._latest_h264_keyframe is None


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


def test_locked_device_does_not_start_official_java() -> None:
    manager = HarmonyOfficialSessionManager("harmony_mobile", {"mode": "auto"})
    manager.set_hdc_path("hdc.exe")
    manager.set_device_lock_checker(lambda serial: True)

    assert manager.get_or_start("device-1") is None
    assert manager.get("device-1") is None


def test_locked_device_is_rejected_in_official_mode() -> None:
    manager = HarmonyOfficialSessionManager("harmony_mobile", {"mode": "official"})
    manager.set_hdc_path("hdc.exe")
    manager.set_device_lock_checker(lambda serial: True)

    with pytest.raises(HarmonyOfficialError, match="锁屏"):
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


def test_repeated_prewarm_does_not_extend_idle_timer() -> None:
    class FakeSession:
        is_running = True

        def stop(self) -> None:
            pass

    manager = HarmonyOfficialSessionManager(
        "harmony_mobile",
        {"mode": "auto", "idle_timeout_seconds": 600},
    )
    manager.set_hdc_path("hdc.exe")
    manager._sessions["device-1"] = FakeSession()  # type: ignore[assignment]

    manager.prewarm("device-1")
    first_timer = manager._idle_timers["device-1"]
    manager.prewarm("device-1")

    assert manager._idle_timers["device-1"] is first_timer
    manager.stop_all()

"""鸿蒙官方 HOScrcpy 集成的无设备单元测试。"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import subprocess
import threading
import time
from queue import Empty, Queue
from types import SimpleNamespace

import pytest
from PIL import Image

import common.utils as common_utils
from common.ocr_client import _prepare_ocr_reference, _prepare_ocr_source
from worker.config import PlatformConfig
from worker.platforms.harmony import HarmonyPlatformManager
from worker.platforms.harmony_capture import HarmonyScreenCapture
from worker.platforms.harmony_official.bridge import JavaBridgeProcess
from worker.platforms.harmony_official.protocol import (
    BridgeMessage,
    BridgeMessageType,
    BridgeProtocolError,
    command_mouse_down,
    command_mouse_move,
    command_mouse_up,
    command_touch_down,
    command_touch_move,
    command_touch_up,
    encode_message,
    iter_messages,
)
from worker.platforms.harmony_official.session import (
    HarmonyOfficialSession,
    HarmonyOfficialSessionManager,
    _find_residual_device_pids,
    _h264_websocket_packets,
    _parse_fport_rules,
)
from worker.screen.frame_source import HarmonyOfficialFrameSource
from worker.screen.mjpeg_proxy import MJPEGProxy


def test_prepare_ocr_source_preserves_existing_jpeg() -> None:
    image = b"\xff\xd8\xff\xe0existing-jpeg"

    assert _prepare_ocr_source(image) is image


def test_prepare_ocr_source_keeps_non_jpeg_conversion(monkeypatch) -> None:
    calls = []

    def fake_compress(image_bytes: bytes, quality: int) -> bytes:
        calls.append((image_bytes, quality))
        return b"converted-jpeg"

    monkeypatch.setattr("common.ocr_client.compress_image_to_jpeg", fake_compress)

    assert _prepare_ocr_source(b"png-data") == b"converted-jpeg"
    assert calls == [(b"png-data", 80)]


def test_prepare_ocr_reference_passes_through_jpeg_without_recompression(monkeypatch) -> None:
    calls = []

    def spy_compress(image_bytes: bytes, quality: int) -> bytes:
        calls.append((image_bytes, quality))
        return b"converted-reference"

    monkeypatch.setattr("common.ocr_client.compress_image_to_jpeg", spy_compress)

    image = b"\xff\xd8\xff\xe0small-jpeg"

    assert _prepare_ocr_reference(image) is image
    assert calls == []


def test_prepare_ocr_reference_passes_through_png() -> None:
    image = Image.new("RGBA", (3, 2), (20, 40, 60, 128))
    output = io.BytesIO()
    image.save(output, format="PNG")
    png_bytes = output.getvalue()

    assert _prepare_ocr_reference(png_bytes) == png_bytes


def test_java_bridge_uses_hidden_window_process_launcher(tmp_path, monkeypatch) -> None:
    """Java Bridge 在 Windows 打包程序中启动时不能创建可见控制台。"""
    jar_path = tmp_path / "hoscrcpy.jar"
    jar_path.touch()
    class_path = tmp_path / "bridge"
    class_path.mkdir()
    hdc_path = tmp_path / "hdc.exe"
    hdc_path.touch()
    java_path = tmp_path / "java.exe"
    java_path.touch()
    calls = []

    def fake_popen(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(stdout=io.BytesIO(), stderr=io.BytesIO())

    # 只替换底层 Popen，让测试仍然实际经过 common.utils.popen_cmd。
    monkeypatch.setattr(common_utils.subprocess, "Popen", fake_popen)
    closed = []
    bridge = JavaBridgeProcess(
        serial="device-1",
        device_type="mobile",
        java_path=str(java_path),
        jar_path=jar_path,
        class_path=class_path,
        hdc_path=str(hdc_path),
        temp_dir=tmp_path / "tmp",
        image_scale_size=720,
        frame_rate=10,
        bit_rate=2_000_000,
        on_message=lambda message: None,
        on_closed=closed.append,
    )

    bridge.start()
    for thread in bridge._reader_threads:
        thread.join(timeout=1)

    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command[0] == str(java_path)
    assert kwargs["stdin"] is subprocess.PIPE
    assert kwargs["stdout"] is subprocess.PIPE
    assert kwargs["stderr"] is subprocess.PIPE
    if os.name == "nt":
        assert kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW
    assert closed


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


class _ShortReadStream:
    """模拟 bufsize=0 管道的任意短读：每次 read 最多返回 chunk_size 字节。"""

    def __init__(self, data: bytes, chunk_size: int) -> None:
        self._data = data
        self._pos = 0
        self._chunk_size = max(1, chunk_size)

    def read(self, size: int) -> bytes:
        if size <= 0 or self._pos >= len(self._data):
            return b""
        end = min(self._pos + self._chunk_size, self._pos + size, len(self._data))
        chunk = self._data[self._pos : end]
        self._pos = end
        return chunk


def test_hos1_resyncs_past_native_stdout_pollution() -> None:
    """复现现场故障：SDK native 库在 JVM 极早期直写 fd 1 的诊断文本混入流首。

    现场 magic 无效报错为 b'[0.0'，即 [0.000s] 时间戳行的前 4 字节。
    """
    stream = _ShortReadStream(
        b"[0.000s] HOScrcpy native diag\n" + encode_message(BridgeMessageType.READY, b"serial=test"),
        chunk_size=7,
    )

    messages = list(iter_messages(stream))

    assert [message.message_type for message in messages] == [BridgeMessageType.READY]
    assert messages[0].text == "serial=test"


def test_hos1_resync_between_messages() -> None:
    """流中段混入诊断文本时同样跳过并重新对齐，两条消息都完整解析。"""
    ready = encode_message(BridgeMessageType.READY, b"serial=test")
    h264 = encode_message(BridgeMessageType.H264, b"\x00\x00\x00\x01\x67")
    stream = _ShortReadStream(ready + b"[0.123s] mid-stream noise\n" + h264, chunk_size=13)

    messages = list(iter_messages(stream))

    assert [message.message_type for message in messages] == [
        BridgeMessageType.READY,
        BridgeMessageType.H264,
    ]


def test_hos1_resync_partial_magic_across_reads() -> None:
    """MAGIC 前缀被拆到多次短读中时，滑动窗口不得误丢对齐点。"""
    ready = encode_message(BridgeMessageType.READY, b"serial=test")
    stream = _ShortReadStream(b"noise noise HO" + ready, chunk_size=3)

    messages = list(iter_messages(stream))

    assert [message.message_type for message in messages] == [BridgeMessageType.READY]


def test_hos1_resync_magic_inside_consumed_header() -> None:
    """MAGIC 整个落在已消费的 10 字节失败 header 内时仍可恢复对齐。"""
    ready = encode_message(BridgeMessageType.READY, b"serial=test")
    stream = _ShortReadStream(b"[0.0" + ready, chunk_size=4)

    messages = list(iter_messages(stream))

    assert [message.message_type for message in messages] == [BridgeMessageType.READY]


def test_hos1_resync_eof_without_magic_raises() -> None:
    """流中只有非协议数据且已 EOF 时必须报错，不得误判为正常 EOF。"""
    stream = _ShortReadStream(b"[0.000s] fatal native crash\n", chunk_size=100)

    with pytest.raises(BridgeProtocolError, match="magic 无效"):
        list(iter_messages(stream))


def test_hos1_resync_logs_skipped_data(caplog) -> None:
    """重新对齐必须留下 WARNING 日志，含跳过字节与内容预览供现场取证。"""
    stream = _ShortReadStream(
        b"[0.000s] HOScrcpy native diag\n" + encode_message(BridgeMessageType.READY, b"serial=test"),
        chunk_size=64,
    )

    with caplog.at_level(logging.WARNING, logger="worker.platforms.harmony_official.protocol"):
        list(iter_messages(stream))

    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert warnings, "重新对齐事件必须留下 WARNING 日志"
    assert "非协议数据" in warnings[0].getMessage()
    assert "[0.000s]" in warnings[0].getMessage()


def test_hos1_version_check_after_resync_still_enforced() -> None:
    """对齐后仍严格校验版本：文本里的伪 HOS1 或协议漂移不得被静默跳过。"""
    bad = b"HOS1" + bytes([9, 1]) + b"\x00\x00\x00\x00"
    stream = _ShortReadStream(b"[0.0s] noise\n" + bad, chunk_size=16)

    with pytest.raises(BridgeProtocolError, match="协议版本"):
        list(iter_messages(stream))


def test_bridge_runtime_error_is_logged_once(caplog) -> None:
    session = HarmonyOfficialSession(
        serial="device-1",
        device_type="mobile",
        hdc_path="hdc.exe",
        settings=dict(HarmonyOfficialSessionManager.DEFAULTS),
    )
    session._ready_event.set()

    session._on_bridge_message(BridgeMessage(BridgeMessageType.ERROR, b"H264_EXCEPTION encoder failed"))

    errors = [record.getMessage() for record in caplog.records if "H264_EXCEPTION" in record.getMessage()]
    assert errors == ["鸿蒙官方 Java Bridge 运行时报错(device-1): H264_EXCEPTION encoder failed"]


def test_packaged_java_runtime_overrides_stale_development_path(tmp_path, monkeypatch) -> None:
    import worker.platforms.harmony_official.session as session_module

    bundled_java = tmp_path / "tools" / "jre" / "bin" / "java.exe"
    bundled_java.parent.mkdir(parents=True)
    bundled_java.touch()
    monkeypatch.setattr(session_module, "get_base_dir", lambda: str(tmp_path))
    monkeypatch.setattr(session_module, "is_packaged", lambda: True)

    manager = HarmonyOfficialSessionManager(
        "harmony_pc",
        {"java_path": "D:/Program Files/jdk/bin/java.exe"},
    )

    assert manager._settings["java_path"] == str(bundled_java)


def test_h264_websocket_packets_split_config_and_idr() -> None:
    payload = b"\x00\x00\x00\x01\x67\x01" b"\x00\x00\x00\x01\x68\x02" b"\x00\x00\x00\x01\x65\x03"

    packets = _h264_websocket_packets(payload)

    assert packets == [
        b"\x01\x00\x00\x00\x01\x67\x01\x00\x00\x00\x01\x68\x02",
        b"\x02\x00\x00\x00\x01\x65\x03",
    ]


def test_h264_websocket_packets_classify_p_frame() -> None:
    payload = b"\x00\x00\x01\x41\x09"

    assert _h264_websocket_packets(payload) == [b"\x03" + payload]


def test_h264_first_subscription_replays_retained_cache() -> None:
    """空闲后首订：立即回放缓存出画面，同时唤醒+IDR 刷新，缓存保留供下次重订。"""
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

    assert queue.get_nowait() == b"\x01\x00\x00\x01\x67"
    assert queue.get_nowait() == b"\x02\x00\x00\x01\x65"
    assert sent_commands == [b"WAKE_STREAM\n", b"REQUEST_IDR\n"]
    session.unsubscribe_h264("subscriber-1")
    assert session._latest_h264_config is not None
    assert session._latest_h264_keyframe is not None
    session.stop()


def test_h264_first_subscription_without_cache_waits_for_fresh_keyframe() -> None:
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

    with pytest.raises(Empty):
        queue.get_nowait()
    assert sent_commands == [b"WAKE_STREAM\n", b"REQUEST_IDR\n"]

    # 新的参数集/关键帧到达后才允许后续 P 帧进入订阅队列。
    session._broadcast_h264(b"\x00\x00\x01\x67\x01\x00\x00\x01\x68\x02" b"\x00\x00\x01\x65\x03")
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


def test_h264_subscription_stops_idr_retry_after_success(monkeypatch) -> None:
    sent_commands: list[bytes] = []
    monkeypatch.setattr(
        "worker.platforms.harmony_official.session.H264_IDR_RETRY_DELAYS_SECONDS",
        (0.01, 0.01, 0.01),
    )
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
    assert sent_commands == [b"WAKE_STREAM\n", b"REQUEST_IDR\n"]

    session._broadcast_h264(b"\x00\x00\x01\x67\x01\x00\x00\x01\x68\x02" b"\x00\x00\x01\x65\x03")

    time.sleep(0.05)
    assert sent_commands == [b"WAKE_STREAM\n", b"REQUEST_IDR\n"]
    session.stop()


def test_h264_subscription_retries_idr_for_static_screen(monkeypatch) -> None:
    sent_commands: list[bytes] = []
    monkeypatch.setattr(
        "worker.platforms.harmony_official.session.H264_IDR_RETRY_DELAYS_SECONDS",
        (0.01, 0.01, 0.01),
    )
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

    session.subscribe_h264("subscriber-1")

    time.sleep(0.08)
    assert sent_commands == [
        b"WAKE_STREAM\n",
        b"REQUEST_IDR\n",
        b"REQUEST_IDR\n",
        b"REQUEST_IDR\n",
        b"REQUEST_IDR\n",
    ]
    session.stop()


def test_h264_broadcast_without_subscribers_updates_startup_cache() -> None:
    """无消费者（预热/空闲）时也必须维护起播缓存。

    编码器启动后的首组 SPS/PPS+IDR 几乎必然落在首个订阅者之前，丢弃会导致
    之后订阅者只能收 P 帧（has_config=False 全部跳过）的起播死锁。
    """
    session = HarmonyOfficialSession(
        serial="device-1",
        device_type="mobile",
        hdc_path="hdc.exe",
        settings=dict(HarmonyOfficialSessionManager.DEFAULTS),
    )
    session._broadcast_h264(b"\x00\x00\x00\x01\x67sps\x00\x00\x00\x01\x68pps")
    session._broadcast_h264(b"\x00\x00\x00\x01\x65idrdata")
    assert session._latest_h264_config == (b"\x01\x00\x00\x00\x01\x67sps\x00\x00\x00\x01\x68pps")
    assert session._latest_h264_keyframe == b"\x02\x00\x00\x00\x01\x65idrdata"

    # P 帧不进缓存
    session._broadcast_h264(b"\x00\x00\x00\x01\x41pdata")
    assert session._latest_h264_keyframe == b"\x02\x00\x00\x00\x01\x65idrdata"

    # 跨参数集时旧 IDR 作废
    session._broadcast_h264(b"\x00\x00\x00\x01\x67newsps")
    assert session._latest_h264_config == b"\x01\x00\x00\x00\x01\x67newsps"
    assert session._latest_h264_keyframe is None
    session.stop()


def test_mjpeg_proxy_stops_when_websocket_stop_event_is_set() -> None:
    class FakeResponse:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    class FakeWebSocket:
        def __init__(self, stop_event: asyncio.Event) -> None:
            self.stop_event = stop_event
            self.frames: list[bytes] = []

        async def send_bytes(self, frame: bytes) -> None:
            self.frames.append(frame)
            self.stop_event.set()

    async def run_proxy() -> tuple[FakeResponse, FakeWebSocket]:
        stop_event = asyncio.Event()
        response = FakeResponse()
        websocket = FakeWebSocket(stop_event)
        proxy = MJPEGProxy("127.0.0.1")
        proxy._response = response
        proxy._iterator = iter(
            [
                b"--BoundaryString\r\nContent-Length: 10\r\n\r\njpeg-",
                b"frame\r\n",
            ]
        )
        proxy._running = True

        await proxy.proxy_to_websocket(websocket, stop_event)
        return response, websocket

    response, websocket = asyncio.run(run_proxy())

    assert response.closed is True
    assert websocket.frames == [b"jpeg-frame"]


def test_last_h264_unsubscribe_retains_startup_cache() -> None:
    """退订保留起播缓存：关窗后快速重开立即回放，不依赖唤醒产出新帧。"""
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

    assert session._latest_h264_config == b"\x01config"
    assert session._latest_h264_keyframe == b"\x02idr"
    session.stop()
    # stop() 清空缓存，防止跨桥接会话（新编码器）复用
    assert session._latest_h264_config is None
    assert session._latest_h264_keyframe is None


def test_input_commands_and_gesture_step_limits() -> None:
    assert command_touch_down(10, 20) == b"TOUCH_DOWN 10 20\n"
    assert command_mouse_move(None, 10, 20) == b"MOUSE_MOVE NONE 10 20\n"
    assert HarmonyOfficialSession._gesture_steps(500, None) == 10
    assert HarmonyOfficialSession._gesture_steps(5_000, None) == 20
    assert HarmonyOfficialSession._gesture_steps(100, 100) == 40


def _make_input_session(device_type: str) -> tuple[HarmonyOfficialSession, list[bytes]]:
    sent: list[bytes] = []
    session = HarmonyOfficialSession(
        serial="device-1",
        device_type=device_type,
        hdc_path="hdc.exe",
        settings=dict(HarmonyOfficialSessionManager.DEFAULTS),
    )
    session._ready_event.set()
    session._bridge = SimpleNamespace(is_running=True, send=sent.append)
    return session, sent


def test_official_pc_swipe_uses_touch_sequence(monkeypatch) -> None:
    session, sent = _make_input_session("pc")
    monkeypatch.setattr(session, "_wait_input_ready", lambda: None)
    monkeypatch.setattr("worker.platforms.harmony_official.session.time.sleep", lambda _: None)

    session.swipe(10, 20, 30, 40, duration_ms=100, steps=2)

    assert sent == [
        command_touch_down(10, 20),
        command_touch_move(20, 30),
        command_touch_move(30, 40),
        command_touch_up(30, 40),
    ]


def test_official_pc_drag_uses_mouse_sequence(monkeypatch) -> None:
    session, sent = _make_input_session("pc")
    monkeypatch.setattr(session, "_wait_input_ready", lambda: None)
    monkeypatch.setattr("worker.platforms.harmony_official.session.time.sleep", lambda _: None)

    session.drag(10, 20, 30, 40, duration_ms=100, steps=2)

    assert sent == [
        command_mouse_down("LEFT", 10, 20),
        command_mouse_move("LEFT", 20, 30),
        command_mouse_move("LEFT", 30, 40),
        command_mouse_up("LEFT", 30, 40),
    ]


def test_default_mode_returns_none_when_hdc_is_unavailable() -> None:
    manager = HarmonyOfficialSessionManager("harmony_mobile")

    assert manager.get_or_start("device-1") is None


def test_start_runtime_error_returns_none_for_hdc_fallback(monkeypatch) -> None:
    manager = HarmonyOfficialSessionManager(
        "harmony_mobile",
        {"reconnect_attempts": 1},
    )
    manager.set_hdc_path("hdc.exe")

    def fail_start(self) -> None:
        raise OSError("java executable failed")

    monkeypatch.setattr(HarmonyOfficialSession, "start", fail_start)

    assert manager.get_or_start("device-1") is None
    assert "device-1" not in manager._sessions


def test_legacy_mode_settings_cannot_disable_official_fallback() -> None:
    manager = HarmonyOfficialSessionManager(
        "harmony_mobile",
        {
            "enabled": False,
            "mode": "legacy",
            "fallback_to_legacy": False,
        },
    )

    assert "enabled" not in manager._settings
    assert "mode" not in manager._settings
    assert "fallback_to_legacy" not in manager._settings
    assert manager.get_or_start("device-1") is None


def test_locked_device_does_not_start_official_java() -> None:
    manager = HarmonyOfficialSessionManager("harmony_mobile")
    manager.set_hdc_path("hdc.exe")
    manager.set_device_lock_checker(lambda serial: True)

    assert manager.get_or_start("device-1") is None
    assert manager.get("device-1") is None


def test_parse_fport_rules_extracts_forward_rules() -> None:
    output = (
        "[Forward]\n"
        "tcp:59275 localabstract:scrcpy_grpc_socket    [Forward]\n"
        "tcp:50000 tcp:8012    [Forward]\n"
        "\n"
        "tcp:60001 localabstract:uitest_socket    [Forward]\n"
        "tcp:1234 tcp:5678    [Reverse]\n"
        "[Empty]\n"
    )

    assert _parse_fport_rules(output) == [
        ("tcp:59275", "localabstract:scrcpy_grpc_socket"),
        ("tcp:50000", "tcp:8012"),
        ("tcp:60001", "localabstract:uitest_socket"),
    ]


def test_find_residual_device_pids_matches_sdk_daemon_lines() -> None:
    output = (
        "UID            PID  PPID ELAPSED ARGS\n"
        "root          1234     1 1-02:00 uitest start-daemon singleness &\n"
        "root          5678     1 1-02:00"
        " /data/local/tmp/libscrcpy/libscrcpy_server_emulator.z.so\n"
        "root          9999     1 1-02:00"
        " snapshot_display -f /data/local/tmp/screen.jpeg\n"
    )

    assert _find_residual_device_pids(output) == ["1234", "5678"]


def test_clean_up_hdc_fport_removes_enumerated_rules(tmp_path, monkeypatch) -> None:
    hdc_path = tmp_path / "hdc.exe"
    hdc_path.write_bytes(b"fake-hdc")
    manager = HarmonyOfficialSessionManager("harmony_mobile")
    manager.set_hdc_path(str(hdc_path))
    executed: list[list[str]] = []

    def fake_run_cmd(cmd, timeout=None, **kwargs):
        executed.append(list(cmd))
        if cmd[-1] == "ls":
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    "tcp:50000 tcp:8012    [Forward]\n"
                    "tcp:59275 localabstract:scrcpy_grpc_socket    [Forward]\n"
                ),
                stderr="",
            )
        return SimpleNamespace(
            returncode=0,
            stdout=f"Remove forward ruler success, ruler:{cmd[-2]} {cmd[-1]}",
            stderr="",
        )

    import worker.platforms.harmony_official.session as session_module

    monkeypatch.setattr(session_module, "run_cmd", fake_run_cmd)

    manager._clean_up_hdc_fport("device-1")

    assert executed == [
        [str(hdc_path), "-t", "device-1", "fport", "ls"],
        [str(hdc_path), "-t", "device-1", "fport", "rm", "tcp:50000", "tcp:8012"],
        [
            str(hdc_path),
            "-t",
            "device-1",
            "fport",
            "rm",
            "tcp:59275",
            "localabstract:scrcpy_grpc_socket",
        ],
    ]


def test_clean_up_device_processes_kills_residual_pids(tmp_path, monkeypatch) -> None:
    hdc_path = tmp_path / "hdc.exe"
    hdc_path.write_bytes(b"fake-hdc")
    manager = HarmonyOfficialSessionManager("harmony_mobile")
    manager.set_hdc_path(str(hdc_path))
    executed: list[list[str]] = []

    def fake_run_cmd(cmd, timeout=None, **kwargs):
        executed.append(list(cmd))
        if cmd[-1] == "ps -ef":
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    "UID            PID  PPID ELAPSED ARGS\n"
                    "root          1234     1 1-02:00 uitest start-daemon singleness &\n"
                    "root          9999     1 1-02:00"
                    " snapshot_display -f /data/local/tmp/x.jpeg\n"
                ),
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    import worker.platforms.harmony_official.session as session_module

    monkeypatch.setattr(session_module, "run_cmd", fake_run_cmd)

    manager._clean_up_device_processes("device-1")

    assert executed == [
        [str(hdc_path), "-t", "device-1", "shell", "ps -ef"],
        [str(hdc_path), "-t", "device-1", "shell", "kill -9 1234"],
    ]


def test_clean_up_methods_tolerate_hdc_failures(tmp_path, monkeypatch) -> None:
    hdc_path = tmp_path / "hdc.exe"
    hdc_path.write_bytes(b"fake-hdc")
    manager = HarmonyOfficialSessionManager("harmony_mobile")
    manager.set_hdc_path(str(hdc_path))

    import worker.platforms.harmony_official.session as session_module

    def raising_run_cmd(cmd, timeout=None, **kwargs):
        raise OSError("hdc disappeared")

    monkeypatch.setattr(session_module, "run_cmd", raising_run_cmd)

    manager._clean_up_hdc_fport("device-1")
    manager._clean_up_device_processes("device-1")


def test_get_or_start_cleans_residue_before_each_start_attempt(monkeypatch) -> None:
    manager = HarmonyOfficialSessionManager("harmony_mobile")
    # _build_settings 只消费 java_path，重试参数直接改运行时配置。
    manager._settings["reconnect_attempts"] = 2
    manager.set_hdc_path("hdc.exe")
    events: list[tuple[str, str]] = []
    monkeypatch.setattr(
        manager, "_clean_up_hdc_fport", lambda serial: events.append(("fport", serial))
    )
    monkeypatch.setattr(
        manager,
        "_clean_up_device_processes",
        lambda serial: events.append(("processes", serial)),
    )

    def fail_start(self) -> None:
        raise OSError("java executable failed")

    monkeypatch.setattr(HarmonyOfficialSession, "start", fail_start)
    monkeypatch.setattr(time, "sleep", lambda seconds: None)

    assert manager.get_or_start("device-1") is None
    assert events == [
        ("fport", "device-1"),
        ("processes", "device-1"),
        ("fport", "device-1"),
        ("processes", "device-1"),
    ]


def test_get_or_start_reuses_running_session_without_cleanup(monkeypatch) -> None:
    manager = HarmonyOfficialSessionManager("harmony_mobile")
    manager.set_hdc_path("hdc.exe")
    running = SimpleNamespace(is_running=True)
    manager._sessions["device-1"] = running
    events: list[str] = []
    monkeypatch.setattr(
        manager, "_clean_up_hdc_fport", lambda serial: events.append("fport")
    )
    monkeypatch.setattr(
        manager, "_clean_up_device_processes", lambda serial: events.append("processes")
    )

    assert manager.get_or_start("device-1") is running
    assert events == []


def test_get_or_start_aborts_remaining_attempts_when_cancelled(monkeypatch) -> None:
    """取消标志置位后，重试循环在 attempt 边界放弃，不再发起新启动。"""
    manager = HarmonyOfficialSessionManager("harmony_mobile")
    manager._settings["reconnect_attempts"] = 3
    manager._settings["reconnect_backoff_seconds"] = 0.01
    manager.set_hdc_path("hdc.exe")
    monkeypatch.setattr(manager, "_clean_up_hdc_fport", lambda serial: None)
    monkeypatch.setattr(manager, "_clean_up_device_processes", lambda serial: None)
    monkeypatch.setattr(time, "sleep", lambda seconds: None)

    cancel = threading.Event()
    attempts: list[int] = []

    def fail_start(self) -> None:
        attempts.append(len(attempts) + 1)
        cancel.set()
        raise OSError("java executable failed")

    monkeypatch.setattr(HarmonyOfficialSession, "start", fail_start)

    assert manager.get_or_start("device-1", cancel_event=cancel) is None
    assert attempts == [1]


def test_get_or_start_cancelled_while_waiting_start_lock(monkeypatch) -> None:
    """前一个启动占住 per-serial 锁时，取消能让等待方尽快退出而不是无限排队。"""
    manager = HarmonyOfficialSessionManager("harmony_mobile")
    manager.set_hdc_path("hdc.exe")
    holder = manager._start_locks.setdefault("device-1", threading.Lock())
    assert holder.acquire()

    cancel = threading.Event()
    entered = threading.Event()
    results: list = []

    def waiting_get_or_start() -> None:
        entered.set()
        results.append(manager.get_or_start("device-1", cancel_event=cancel))

    thread = threading.Thread(target=waiting_get_or_start, daemon=True)
    thread.start()
    assert entered.wait(1.0)
    time.sleep(0.3)
    cancel.set()
    thread.join(timeout=3.0)

    assert not thread.is_alive()
    assert results == [None]
    holder.release()
    manager.stop_all()


def test_official_frame_source_subscribe_passes_cancel_event() -> None:
    """帧源把取消标志透传给会话管理器的 acquire。"""
    fake_session = SimpleNamespace(
        subscribe_h264=lambda subscriber_id: "queue-1",
    )
    captured: list = []

    class Manager:
        def acquire_official_session(self, device_id, owner, cancel_event=None):
            captured.append(cancel_event)
            return fake_session

        def is_device_locked(self, device_id) -> bool:
            return False

    source = HarmonyOfficialFrameSource("device-1", Manager())
    cancel = threading.Event()

    assert source.subscribe_h264("subscriber-1", cancel) == "queue-1"
    assert captured == [cancel]


def test_teardown_harmony_h264_source_unsubscribes_and_stops() -> None:
    events: list[tuple] = []
    frame_source = SimpleNamespace(
        unsubscribe_h264=lambda subscriber_id: events.append(("unsub", subscriber_id)),
        stop=lambda: events.append(("stop",)),
    )

    from worker.server import _teardown_harmony_h264_source

    _teardown_harmony_h264_source(frame_source, "sub-1", True)
    assert events == [("unsub", "sub-1"), ("stop",)]

    events.clear()
    _teardown_harmony_h264_source(frame_source, "sub-1", False)
    assert events == [("unsub", "sub-1")]


async def test_subscribe_h264_with_deadline_returns_queue_on_success() -> None:
    import asyncio as asyncio_module

    from worker.server import _subscribe_h264_with_deadline

    stop_event = asyncio_module.Event()
    cancel = threading.Event()
    seen: list = []

    def subscribe(subscriber_id, cancel_event):
        seen.append((subscriber_id, cancel_event))
        return "queue-1"

    frame_source = SimpleNamespace(subscribe_h264=subscribe)

    queue = await _subscribe_h264_with_deadline(
        frame_source, "sub-1", cancel, stop_event
    )

    assert queue == "queue-1"
    assert seen == [("sub-1", cancel)]
    assert not cancel.is_set()


async def test_subscribe_h264_with_deadline_times_out_and_cancels(monkeypatch) -> None:
    import asyncio as asyncio_module

    from worker import server as server_module

    monkeypatch.setattr(
        server_module, "DEFAULT_HARMONY_H264_SUBSCRIBE_TIMEOUT", 0.05
    )
    stop_event = asyncio_module.Event()
    cancel = threading.Event()
    release = threading.Event()

    def slow_subscribe(subscriber_id, cancel_event):
        release.wait(2.0)
        return "queue-1"

    frame_source = SimpleNamespace(subscribe_h264=slow_subscribe)

    with pytest.raises(server_module.HarmonyH264SubscribeTimeoutError):
        await server_module._subscribe_h264_with_deadline(
            frame_source, "sub-1", cancel, stop_event
        )

    assert cancel.is_set()
    release.set()


async def test_subscribe_h264_with_deadline_raises_closed_on_disconnect() -> None:
    import asyncio as asyncio_module

    from worker import server as server_module

    stop_event = asyncio_module.Event()
    stop_event.set()
    cancel = threading.Event()
    release = threading.Event()

    def subscribe(subscriber_id, cancel_event):
        release.wait(2.0)
        return "queue-1"

    frame_source = SimpleNamespace(subscribe_h264=subscribe)

    with pytest.raises(server_module.WebSocketClosedError):
        await server_module._subscribe_h264_with_deadline(
            frame_source, "sub-1", cancel, stop_event
        )

    # 取消标志由调用方的 WebSocketClosedError 分支置位，helper 本身不置位。
    assert not cancel.is_set()
    release.set()


def test_harmony_action_skips_official_session_and_uses_hdc(monkeypatch) -> None:
    """用例动作统一走 HDC：移动端不再启动/租借官方会话。"""
    manager = HarmonyPlatformManager(PlatformConfig(), device_type="harmony_mobile")
    hdc_calls: list[tuple[int, int]] = []
    client = SimpleNamespace(
        serial="device-1",
        tap=lambda x, y: hdc_calls.append((x, y)) or True,
    )

    def fail_acquire(*args, **kwargs):
        raise AssertionError("task actions must not acquire the official session")

    monkeypatch.setattr(manager, "acquire_official_session", fail_acquire)

    manager.click(10, 20, context=client)

    assert hdc_calls == [(10, 20)]


def test_official_frame_source_stop_releases_lease_when_unsubscribe_fails() -> None:
    released: list[tuple[str, str]] = []

    class FailingSession:
        is_running = True

        def unsubscribe_h264(self, subscriber_id: str) -> None:
            raise RuntimeError(f"unsubscribe failed: {subscriber_id}")

    manager = SimpleNamespace(
        release_official_session=lambda device_id, owner: released.append((device_id, owner)),
    )
    source = HarmonyOfficialFrameSource("device-1", manager)
    source._session = FailingSession()
    source._h264_subscribers.add("subscriber-1")

    source.stop()

    assert released == [("device-1", source._owner)]
    assert source._session is None
    assert source._h264_subscribers == set()


def test_harmony_screen_capture_stop_closes_socket_before_waiting_for_receiver() -> None:
    events: list[str] = []

    class FakeSocket:
        def sendall(self, payload: bytes) -> None:
            events.append("send")

        def shutdown(self, how: int) -> None:
            events.append("shutdown")

        def close(self) -> None:
            events.append("close")

    class FakeHdc:
        serial = "device-1"

        def fport_rm(self, local_port: int, remote_port: int) -> bool:
            events.append("fport_rm")
            return True

    capture = HarmonyScreenCapture(FakeHdc())
    capture.sock = FakeSocket()
    capture.local_port = 49001
    capture._recv_thread = threading.current_thread()

    capture.stop()

    assert events[:2] == ["send", "shutdown"]
    assert events.index("close") < events.index("fport_rm")
    assert capture.sock is None
    assert capture.local_port is None
    assert capture._recv_thread is None


def test_session_lease_stops_bridge_after_last_consumer() -> None:
    class FakeSession:
        is_running = True

        def __init__(self) -> None:
            self.stop_count = 0

        def stop(self) -> None:
            self.stop_count += 1

    manager = HarmonyOfficialSessionManager("harmony_mobile")
    manager.set_hdc_path("hdc.exe")
    # 空闲释放只响应 release() 登记的真实计时器线程（_release_idle_session
    # 校验 current_thread 身份），这里把空闲超时压到下限后等待计时器触发。
    manager._settings["idle_timeout_seconds"] = 0
    fake = FakeSession()
    manager._sessions["device-1"] = fake  # type: ignore[assignment]

    assert manager.acquire("device-1", "task:1") is fake
    assert manager.acquire("device-1", "stream:1") is fake

    manager.release("device-1", "task:1")
    assert fake.stop_count == 0
    manager.release("device-1", "stream:1")
    deadline = time.monotonic() + 2.0
    while fake.stop_count == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert fake.stop_count == 1
    assert manager.get("device-1") is None
    manager.stop_all()


def test_stop_session_if_idle_stops_session_without_owners() -> None:
    class FakeSession:
        is_running = True

        def __init__(self) -> None:
            self.stop_count = 0

        def stop(self) -> None:
            self.stop_count += 1

    manager = HarmonyOfficialSessionManager("harmony_mobile")
    manager.set_hdc_path("hdc.exe")
    fake = FakeSession()
    manager._sessions["device-1"] = fake  # type: ignore[assignment]
    # 模拟 WS 断开后的空闲保活计时器已挂起。
    with manager._lock:
        manager._schedule_idle_release_locked("device-1")
    assert "device-1" in manager._idle_timers

    result = manager.stop_session_if_idle("device-1", wait_idle_seconds=0)

    assert result == {"stopped": True, "owners": []}
    assert fake.stop_count == 1
    assert manager.get("device-1") is None
    assert "device-1" not in manager._idle_timers


def test_stop_session_if_idle_waits_for_stream_owner_release() -> None:
    """HTTP 释放请求可能先于 WS 关闭的租约释放到达，需要短暂等待。"""

    class FakeSession:
        is_running = True

        def __init__(self) -> None:
            self.stop_count = 0

        def stop(self) -> None:
            self.stop_count += 1

    manager = HarmonyOfficialSessionManager("harmony_mobile")
    manager.set_hdc_path("hdc.exe")
    fake = FakeSession()
    manager._sessions["device-1"] = fake  # type: ignore[assignment]
    manager.acquire("device-1", "stream:1")

    def release_soon() -> None:
        time.sleep(0.1)
        manager.release("device-1", "stream:1")

    thread = threading.Thread(target=release_soon)
    thread.start()
    result = manager.stop_session_if_idle("device-1", wait_idle_seconds=2.0)
    thread.join()

    assert result == {"stopped": True, "owners": []}
    assert fake.stop_count == 1


def test_stop_session_if_idle_skips_when_task_owner_active() -> None:
    class FakeSession:
        is_running = True

        def __init__(self) -> None:
            self.stop_count = 0

        def stop(self) -> None:
            self.stop_count += 1

    manager = HarmonyOfficialSessionManager("harmony_mobile")
    manager.set_hdc_path("hdc.exe")
    fake = FakeSession()
    manager._sessions["device-1"] = fake  # type: ignore[assignment]
    manager.acquire("device-1", "task:1")

    result = manager.stop_session_if_idle("device-1", wait_idle_seconds=0.05)

    assert result["stopped"] is False
    assert result["owners"] == ["task:1"]
    assert fake.stop_count == 0
    assert manager.get("device-1") is fake
    manager.release("device-1", "task:1")
    manager.stop_all()


def test_h264_startup_diagnosis_reports_last_access_unit_age() -> None:
    """首帧超时诊断必须能区分设备静默与仍在产帧。"""
    session = HarmonyOfficialSession(
        serial="device-1",
        device_type="mobile",
        hdc_path="hdc.exe",
        settings={"startup_timeout_seconds": 1},
    )
    diag = session.h264_startup_diagnosis()
    assert diag["received_access_units"] == 0
    assert diag["last_access_unit_age_seconds"] is None

    session._broadcast_h264(b"\x67\x64\x00\x1f")
    diag = session.h264_startup_diagnosis()
    assert diag["received_access_units"] == 1
    assert diag["last_access_unit_age_seconds"] is not None
    assert diag["last_access_unit_age_seconds"] < 5.0

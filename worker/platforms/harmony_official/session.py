"""鸿蒙官方 HOScrcpy 会话、输入和 H.264 订阅。"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Full, Queue
from typing import Any

from common.packaging import get_base_dir, is_packaged
from common.utils import run_cmd
from worker.constants import (
    DEFAULT_HARMONY_BRIDGE_REL_PATH,
    DEFAULT_HARMONY_BUNDLED_JAVA_REL_PATH,
    DEFAULT_HARMONY_JAR_REL_PATH,
)
from worker.platforms.harmony_official.bridge import JavaBridgeError, JavaBridgeProcess
from worker.platforms.harmony_official.protocol import (
    BridgeMessage,
    BridgeMessageType,
    command_mouse_down,
    command_mouse_move,
    command_mouse_up,
    command_request_idr,
    command_touch_down,
    command_touch_move,
    command_touch_up,
    command_wake_stream,
    command_wheel,
)

logger = logging.getLogger(__name__)

H264_IDR_RETRY_DELAYS_SECONDS = (0.8, 2.0, 4.0)

H264_BACKPRESSURE_LOG_INTERVAL_SECONDS = 600.0

# Java Bridge 被 taskkill /F 强杀（或 Worker 进程直接退出）时，SDK 的
# stopCaptureScreen 没机会执行：宿主机会残留 fport 转发规则，设备端会残留
# singleness/uitest 采集进程（scrcpy 服务的宿主），下次 startCaptureScreen
# 可能端口冲突报 "can not find scrcpy pid"。兜底清理按这些命令行特征识别残留。
_HDC_CLEANUP_TIMEOUT_SECONDS = 10.0
_HDC_DEVICE_KILL_PATTERNS = ("uitest start-daemon", "libscrcpy", "libscreen_casting")


def _parse_fport_rules(output: str) -> list[tuple[str, str]]:
    """解析 ``hdc fport ls`` 输出，返回 (本地, 远端) 转发规则列表。

    每条规则形如 ``tcp:50000 tcp:8012    [Forward]``：取行内含冒号的两个
    token（tcp:/localabstract:/localfilesystem: 等）。[Empty]、[Reverse]
    和无法解析的行忽略。
    """
    rules: list[tuple[str, str]] = []
    for raw_line in (output or "").splitlines():
        if "[Reverse]" in raw_line:
            continue
        tokens = [
            token
            for token in raw_line.split()
            if ":" in token and not token.startswith("[")
        ]
        if len(tokens) == 2:
            rules.append((tokens[0], tokens[1]))
    return rules


def _find_residual_device_pids(output: str) -> list[str]:
    """从设备端 ``ps -ef`` 输出中找出残留采集进程的 PID。

    命令行命中 _HDC_DEVICE_KILL_PATTERNS 即视为残留；PID 取第二列，与
    HarmonyScreenCapture 的 daemon 清理保持一致。
    """
    pids: list[str] = []
    for raw_line in (output or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if not any(pattern in line for pattern in _HDC_DEVICE_KILL_PATTERNS):
            continue
        columns = line.split()
        if len(columns) > 1 and columns[1].isdigit() and columns[1] not in pids:
            pids.append(columns[1])
    return pids


@dataclass
class _H264Subscriber:
    """一个鸿蒙 H.264 WebSocket 订阅者及其有界发送队列。"""

    subscriber_id: str
    queue: Queue[bytes]
    has_config: bool = False
    waiting_for_keyframe: bool = True
    enqueued_packets: int = 0
    dropped_packets: int = 0
    dropped_p_packets: int = 0
    skipped_p_packets: int = 0
    queue_full_events: int = 0
    peak_queue_size: int = 0


def _find_annexb_start_code(payload: bytes, offset: int = 0) -> tuple[int, int] | None:
    """查找 Annex-B 起始码，返回位置和长度。"""
    start3 = payload.find(b"\x00\x00\x01", offset)
    start4 = payload.find(b"\x00\x00\x00\x01", offset)
    if start3 < 0:
        return (start4, 4) if start4 >= 0 else None
    if start4 >= 0 and start4 <= start3:
        return start4, 4
    return start3, 3


def _split_annexb_nals(payload: bytes) -> list[tuple[int, bytes]]:
    """拆分一段完整的 Annex-B access unit，保留每个 NAL 的起始码。"""
    nals: list[tuple[int, bytes]] = []
    first = _find_annexb_start_code(payload)
    if first is None:
        return nals

    start, start_code_length = first
    while start >= 0:
        next_start = _find_annexb_start_code(payload, start + start_code_length)
        end = next_start[0] if next_start else len(payload)
        nal = payload[start + start_code_length:end]
        if nal:
            nals.append((nal[0] & 0x1F, payload[start:end]))
        if next_start is None:
            break
        start, start_code_length = next_start
    return nals


def _h264_websocket_packets(payload: bytes) -> list[bytes]:
    """把官方 H.264 access unit 转成现有前端 WebSocket 帧协议。

    每个输出帧首字节为：0x01 参数集、0x02 IDR、0x03 P 帧；后面保持
    Annex-B 原始数据。官方 SDK 可能把 SPS/PPS 和 IDR 放在同一次回调中，
    这里拆成两个 WebSocket 帧，确保浏览器先收到参数集再收到关键帧。
    """
    nals = _split_annexb_nals(payload)
    if not nals:
        return []

    config_nals = [data for nal_type, data in nals if nal_type in (7, 8)]
    video_nals = [data for nal_type, data in nals if nal_type not in (7, 8)]
    has_idr = any(nal_type == 5 for nal_type, _ in nals)
    has_slice = any(nal_type in (1, 5) for nal_type, _ in nals)

    packets: list[bytes] = []
    if config_nals:
        packets.append(b"\x01" + b"".join(config_nals))
    if video_nals and has_slice:
        prefix = b"\x02" if has_idr else b"\x03"
        packets.append(prefix + b"".join(video_nals))
    return packets


class HarmonyOfficialError(RuntimeError):
    """官方 HOScrcpy 会话不可用或执行失败。"""


class HarmonyOfficialPartialActionError(HarmonyOfficialError):
    """官方手势已开始执行（至少 down 事件送达设备）后的失败。

    平台层收到此异常时不得回退 HDC 重放同一动作，否则同一手势会被执行
    两次；会话通道已不可信，调用方应停用会话，由上层决定是否整体重试。
    """


class HarmonyOfficialSession:
    """单台鸿蒙设备的官方视频、触摸或鼠标会话。"""

    def __init__(
        self,
        *,
        serial: str,
        device_type: str,
        hdc_path: str,
        settings: dict[str, Any],
    ) -> None:
        self.serial = serial
        self.device_type = device_type
        self.hdc_path = hdc_path
        self.settings = settings
        self._h264_subscribers: dict[str, _H264Subscriber] = {}
        self._h264_idr_retry_timers: dict[str, threading.Timer] = {}
        self._h264_idr_retry_counts: dict[str, int] = {}
        self._latest_h264_config: bytes | None = None
        self._latest_h264_keyframe: bytes | None = None
        self._bridge: JavaBridgeProcess | None = None
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._state_lock = threading.RLock()
        self._input_lock = threading.Lock()
        self._startup_error = ""
        self._closed_reason = ""
        self._input_ready_at = 0.0
        self._last_idr_request_at = 0.0
        self._h264_video_logged = False
        self._h264_unrecognized_logged = False
        self._h264_received_access_units = 0
        self._h264_forwarded_packets = 0
        self._h264_idr_requests = 0
        # 最近一次收到设备 H.264 access unit 的单调时钟；0 表示尚未收到。
        # 首帧超时时用它区分"设备静默（息屏/编码器停）"与"本地链路丢帧"。
        self._last_h264_access_unit_at = 0.0
        self._h264_monitor_thread: threading.Thread | None = None
        self._h264_monitor_stop = threading.Event()

    @property
    def is_running(self) -> bool:
        bridge = self._bridge
        return bool(bridge and bridge.is_running and not self._stop_event.is_set())

    def start(self) -> None:
        """启动 Java 采集会话，等待 SDK READY。"""
        with self._state_lock:
            if self.is_running:
                return
            self._reset_start_state()
            self._bridge = JavaBridgeProcess(
                serial=self.serial,
                device_type=self.device_type,
                java_path=str(self.settings["java_path"]),
                jar_path=Path(str(self.settings["jar_path"])),
                class_path=Path(str(self.settings["bridge_class_path"])),
                hdc_path=self.hdc_path,
                temp_dir=Path(str(self.settings["temp_dir"])),
                image_scale_size=int(self.settings["image_scale_size"]),
                frame_rate=int(self.settings["frame_rate"]),
                bit_rate=int(self.settings["bit_rate"]),
                on_message=self._on_bridge_message,
                on_closed=self._on_bridge_closed,
            )
            try:
                self._bridge.start()
            except Exception as exc:
                self._stop_event.set()
                raise HarmonyOfficialError(str(exc)) from exc
            self._start_h264_monitor()

        timeout = float(self.settings["startup_timeout_seconds"])
        if not self._ready_event.wait(timeout):
            self.stop()
            detail = self._startup_error or self._closed_reason or "未收到 READY"
            raise HarmonyOfficialError(f"等待鸿蒙官方 SDK READY 超时({timeout}s): {detail}")
        if self._startup_error:
            detail = self._startup_error
            self.stop()
            raise HarmonyOfficialError(f"鸿蒙官方 SDK 启动失败: {detail}")

        logger.info("鸿蒙官方会话已就绪: %s/%s", self.device_type, self.serial)

    def stop(self, timeout: float = 5.0) -> None:
        """停止 Java Bridge 并清理 H.264 订阅状态。"""
        with self._state_lock:
            self._stop_event.set()
            self._h264_monitor_stop.set()
            bridge = self._bridge
            self._bridge = None
            self._h264_subscribers.clear()
            retry_timers = list(self._h264_idr_retry_timers.values())
            self._h264_idr_retry_timers.clear()
            self._h264_idr_retry_counts.clear()
            self._latest_h264_config = None
            self._latest_h264_keyframe = None
        for timer in retry_timers:
            timer.cancel()
        monitor_thread = self._h264_monitor_thread
        if monitor_thread and monitor_thread is not threading.current_thread():
            monitor_thread.join(timeout=1.0)
        self._h264_monitor_thread = None
        if bridge:
            bridge.stop(timeout=timeout)

    def subscribe_h264(self, subscriber_id: str) -> Queue[bytes]:
        """订阅原始 H.264 WebSocket 帧，并请求一组新的参数集/关键帧。"""
        if not self.is_running:
            raise HarmonyOfficialError("鸿蒙官方会话未运行")
        subscriber = _H264Subscriber(
            subscriber_id=subscriber_id,
            queue=Queue(maxsize=max(4, int(self.settings["frame_queue_capacity"]) * 4))
        )
        with self._state_lock:
            has_existing_subscribers = bool(self._h264_subscribers)
            self._h264_subscribers[subscriber_id] = subscriber
            self._h264_idr_retry_counts[subscriber_id] = 0
            cached_config = self._latest_h264_config
            cached_keyframe = self._latest_h264_keyframe
            queued_packets = 0
            if cached_config is not None:
                # 起播缓存对空闲后重开窗口同样回放：静态桌面下设备编码器可能
                # 长时间不出帧，唤醒+IDR 不保证在首帧预算内产出，先回放最近
                # 一组参数集/关键帧立即出画面（内容即断开前最后一帧），新帧
                # 由后续唤醒+IDR 刷新。缓存只在桥接存活期间保留（stop/重建时
                # 清空），编码器会话不变则旧 IDR 始终可解码。
                self._enqueue_h264_packet(subscriber, cached_config)
                subscriber.has_config = True
                queued_packets += 1
                if cached_keyframe is not None:
                    self._enqueue_h264_packet(subscriber, cached_keyframe)
                    queued_packets += 1
                    subscriber.waiting_for_keyframe = False
        logger.info(
            "鸿蒙 H.264 订阅建立: serial=%s, subscriber=%s, cached_config_bytes=%d, "
            "cached_idr_bytes=%d, had_existing_subscribers=%s, queued_packets=%d",
            self.serial,
            subscriber_id,
            len(cached_config) - 1 if cached_config else 0,
            len(cached_keyframe) - 1 if cached_keyframe else 0,
            has_existing_subscribers,
            queued_packets,
        )
        # 首个订阅从空闲状态恢复时，先唤醒静止画面，再请求一次新的 IDR（无论
        # 是否已回放缓存：回放解决"出画"，唤醒+IDR 解决"画面新鲜度"）。
        if not has_existing_subscribers:
            self.wake_stream()
            self.request_idr(force=True)
            logger.info(
                "鸿蒙 H.264 起播请求已发送: serial=%s, subscriber=%s, wake_stream=1, request_idr=1, 缓存回放=%d包, 重试节奏=%s",
                self.serial,
                subscriber_id,
                queued_packets,
                H264_IDR_RETRY_DELAYS_SECONDS,
            )
        elif cached_config is None or cached_keyframe is None:
            # 活动流尚未形成完整起播缓存时补请求；缓存完整时直接复用，
            # 避免新增查看页面打断现有订阅者。
            self.wake_stream()
            self.request_idr(force=True)
            logger.info(
                "鸿蒙 H.264 起播补请求已发送: serial=%s, subscriber=%s, wake_stream=1, request_idr=1, 重试节奏=%s",
                self.serial,
                subscriber_id,
                H264_IDR_RETRY_DELAYS_SECONDS,
            )
        self._schedule_h264_idr_retry(subscriber_id)
        return subscriber.queue

    def unsubscribe_h264(self, subscriber_id: str) -> None:
        """取消一个原始 H.264 订阅。"""
        with self._state_lock:
            self._h264_subscribers.pop(subscriber_id, None)
            retry_timer = self._h264_idr_retry_timers.pop(subscriber_id, None)
            self._h264_idr_retry_counts.pop(subscriber_id, None)
            # 保留最近一组参数集/关键帧作为起播缓存：关窗后快速重开时立即
            # 回放出画面，避免静态桌面下唤醒+IDR 超时导致 H264→JPEG 回退。
            # 缓存随桥接 stop()/会话重建清空，不会跨编码器会话复用。
        if retry_timer:
            retry_timer.cancel()

    def request_idr(self, force: bool = False) -> None:
        """请求设备端发送关键帧，节流避免连续请求。"""
        now = time.monotonic()
        with self._state_lock:
            if not force and now - self._last_idr_request_at < 0.5:
                return
            self._last_idr_request_at = now
            self._h264_idr_requests += 1
        try:
            self._send(command_request_idr())
        except HarmonyOfficialError:
            pass

    def h264_startup_diagnosis(self) -> dict[str, Any]:
        """首帧超时诊断快照：区分设备静默与本地链路丢帧。

        last_access_unit_age_seconds 为 None 表示本会话从未收到设备回调
        （设备侧静默，典型原因是息屏/AOD）；数值很小则说明设备仍在产帧，
        问题在起播握手或订阅链路。
        """
        with self._state_lock:
            last_at = self._last_h264_access_unit_at
            return {
                "bridge_running": self.is_running,
                "received_access_units": self._h264_received_access_units,
                "forwarded_packets": self._h264_forwarded_packets,
                "idr_requests": self._h264_idr_requests,
                "last_access_unit_age_seconds": (
                    round(time.monotonic() - last_at, 1) if last_at else None
                ),
            }

    def wake_stream(self) -> None:
        """请求官方 SDK 让静止画面产生一次新的 H.264 回调。"""
        try:
            self._send(command_wake_stream())
        except HarmonyOfficialError:
            # 静止画面唤醒失败时仍保留原始 H.264 会话，后续帧或 IDR 请求可继续恢复。
            logger.debug("鸿蒙官方 H.264 静止画面唤醒失败: serial=%s", self.serial)

    def tap(self, x: int, y: int, duration_ms: int = 0) -> None:
        """执行移动端触摸点击或长按。"""
        self._wait_input_ready()
        if self.device_type == "mobile":
            self._send(command_touch_down(x, y))
            try:
                time.sleep(max(duration_ms, 80) / 1000.0)
            finally:
                # down 已送达：up 失败时手势可能已作用于设备，必须以部分执行
                # 异常上报，禁止调用方回退 HDC 重放同一动作。
                try:
                    self._send(command_touch_up(x, y))
                except HarmonyOfficialError as exc:
                    raise HarmonyOfficialPartialActionError(
                        f"touch_up 发送失败（touch_down 已送达）: {exc}"
                    ) from exc
            return
        self._mouse_click("LEFT", x, y, duration_ms)

    def double_click(self, x: int, y: int) -> None:
        """执行双击。"""
        self.tap(x, y)
        time.sleep(0.08)
        self.tap(x, y)

    def right_click(self, x: int, y: int) -> None:
        """执行鸿蒙 PC 官方鼠标右键。"""
        self._require_pc()
        self._wait_input_ready()
        self._mouse_click("RIGHT", x, y, 0)

    def move_mouse(self, x: int, y: int, button: str | None = None) -> None:
        """移动鸿蒙 PC 鼠标；拖拽期间携带按键类型。"""
        self._require_pc()
        self._wait_input_ready()
        self._send(command_mouse_move(button, x, y))

    def input_ready(self) -> bool:
        """输入通道是否已就绪（含 READY 后的稳定窗口），非阻塞。

        供实时指针注入逐事件快速判定；脚本式手势仍走阻塞的 _wait_input_ready。
        """
        return (
            self.is_running
            and self._ready_event.is_set()
            and time.monotonic() >= self._input_ready_at
        )

    def mouse_down(self, button: str, x: int, y: int) -> None:
        """按下鸿蒙 PC 鼠标按键（实时指针流原语，逐事件调用）。"""
        self._require_pc()
        self._wait_input_ready()
        self._send(command_mouse_down(button, x, y))

    def mouse_up(self, button: str, x: int, y: int) -> None:
        """抬起鸿蒙 PC 鼠标按键。down 已送达后 up 失败按部分执行上报。"""
        self._require_pc()
        self._wait_input_ready()
        try:
            self._send(command_mouse_up(button, x, y))
        except HarmonyOfficialError as exc:
            raise HarmonyOfficialPartialActionError(
                f"mouse_up 发送失败（mouse_down 已送达）: {exc}"
            ) from exc

    def touch_down(self, x: int, y: int) -> None:
        """触摸按下（移动端实时触摸流原语）。"""
        self._wait_input_ready()
        self._send(command_touch_down(x, y))

    def touch_move(self, x: int, y: int) -> None:
        """触摸移动。"""
        self._wait_input_ready()
        self._send(command_touch_move(x, y))

    def touch_up(self, x: int, y: int) -> None:
        """触摸抬起。down 已送达后 up 失败按部分执行上报。"""
        self._wait_input_ready()
        try:
            self._send(command_touch_up(x, y))
        except HarmonyOfficialError as exc:
            raise HarmonyOfficialPartialActionError(
                f"touch_up 发送失败（touch_down 已送达）: {exc}"
            ) from exc

    def swipe(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        duration_ms: int,
        steps: int | None = None,
    ) -> None:
        """发送官方默认 swipe 手势，即 Touch down/move/up。"""
        self._wait_input_ready()
        duration_ms = max(1, int(duration_ms))
        event_count = self._gesture_steps(duration_ms, steps)
        interval = duration_ms / event_count / 1000.0
        self._send(command_touch_down(start_x, start_y))
        # touch_down 已送达：此后任何失败都意味着手势已部分执行于设备，
        # 必须以部分执行异常上报，禁止调用方回退 HDC 重放同一动作。
        try:
            for index in range(1, event_count + 1):
                time.sleep(interval)
                x, y = self._interpolate(start_x, start_y, end_x, end_y, index, event_count)
                self._send(command_touch_move(x, y))
            time.sleep(0.02)
        except HarmonyOfficialError as exc:
            raise HarmonyOfficialPartialActionError(
                f"滑动手势中断（touch_down 已送达）: {exc}"
            ) from exc
        finally:
            try:
                self._send(command_touch_up(end_x, end_y))
            except HarmonyOfficialError as exc:
                raise HarmonyOfficialPartialActionError(
                    f"touch_up 发送失败（手势已开始）: {exc}"
                ) from exc

    def drag(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        duration_ms: int,
        steps: int | None = None,
    ) -> None:
        """发送官方 PC 鼠标拖拽：左键按下、带键移动、左键抬起。"""
        self._require_pc()
        self._wait_input_ready()
        duration_ms = max(1, int(duration_ms))
        event_count = self._gesture_steps(duration_ms, steps)
        interval = duration_ms / event_count / 1000.0
        self._send(command_mouse_down("LEFT", start_x, start_y))
        # mouse_down 已送达：此后任何失败都意味着拖拽已部分执行。
        try:
            for index in range(1, event_count + 1):
                time.sleep(interval)
                x, y = self._interpolate(start_x, start_y, end_x, end_y, index, event_count)
                self._send(command_mouse_move("LEFT", x, y))
            time.sleep(0.02)
        except HarmonyOfficialError as exc:
            raise HarmonyOfficialPartialActionError(
                f"拖拽手势中断（mouse_down 已送达）: {exc}"
            ) from exc
        finally:
            try:
                self._send(command_mouse_up("LEFT", end_x, end_y))
            except HarmonyOfficialError as exc:
                raise HarmonyOfficialPartialActionError(
                    f"mouse_up 发送失败（拖拽已开始）: {exc}"
                ) from exc

    def wheel(self, direction: str, x: int, y: int) -> None:
        """发送鸿蒙 PC 官方滚轮事件。"""
        self._require_pc()
        self._wait_input_ready()
        self._send(command_wheel(direction, x, y))

    def _reset_start_state(self) -> None:
        self._stop_event.clear()
        self._ready_event.clear()
        self._startup_error = ""
        self._closed_reason = ""
        self._input_ready_at = 0.0
        self._last_idr_request_at = 0.0
        self._h264_video_logged = False
        self._h264_unrecognized_logged = False
        self._h264_received_access_units = 0
        self._h264_forwarded_packets = 0
        self._h264_idr_requests = 0
        self._last_h264_access_unit_at = 0.0
        self._h264_monitor_stop.clear()
        with self._state_lock:
            self._h264_subscribers.clear()
            retry_timers = list(self._h264_idr_retry_timers.values())
            self._h264_idr_retry_timers.clear()
            self._h264_idr_retry_counts.clear()
            self._latest_h264_config = None
            self._latest_h264_keyframe = None
        for timer in retry_timers:
            timer.cancel()

    def _schedule_h264_idr_retry(self, subscriber_id: str) -> None:
        """为未完成起播的订阅者安排有限次数的 IDR 补请求。"""
        with self._state_lock:
            subscriber = self._h264_subscribers.get(subscriber_id)
            if (
                subscriber is None
                or (subscriber.has_config and not subscriber.waiting_for_keyframe)
                or self._stop_event.is_set()
            ):
                return
            retry_index = self._h264_idr_retry_counts.get(subscriber_id, 0)
            if retry_index >= len(H264_IDR_RETRY_DELAYS_SECONDS):
                return
            delay = H264_IDR_RETRY_DELAYS_SECONDS[retry_index]
            old_timer = self._h264_idr_retry_timers.pop(subscriber_id, None)

            def _retry() -> None:
                with self._state_lock:
                    current_timer = self._h264_idr_retry_timers.get(subscriber_id)
                    subscriber = self._h264_subscribers.get(subscriber_id)
                    if current_timer is not threading.current_thread():
                        return
                    self._h264_idr_retry_timers.pop(subscriber_id, None)
                    if (
                        subscriber is None
                        or (subscriber.has_config and not subscriber.waiting_for_keyframe)
                        or self._stop_event.is_set()
                    ):
                        self._h264_idr_retry_counts.pop(subscriber_id, None)
                        return
                    self._h264_idr_retry_counts[subscriber_id] = retry_index + 1

                if self.is_running:
                    logger.info(
                        "鸿蒙 H.264 起播补请求 IDR: serial=%s, subscriber=%s, attempt=%d/%d",
                        self.serial,
                        subscriber_id,
                        retry_index + 1,
                        len(H264_IDR_RETRY_DELAYS_SECONDS),
                    )
                    self.request_idr(force=True)
                    self._schedule_h264_idr_retry(subscriber_id)

            timer = threading.Timer(delay, _retry)
            timer.daemon = True
            self._h264_idr_retry_timers[subscriber_id] = timer
        if old_timer:
            old_timer.cancel()
        timer.start()

    def _on_bridge_message(self, message: BridgeMessage) -> None:
        if message.message_type == BridgeMessageType.H264:
            self._broadcast_h264(message.payload)
        elif message.message_type == BridgeMessageType.READY:
            self._input_ready_at = time.monotonic() + float(self.settings["input_ready_delay_seconds"])
            self._ready_event.set()
        elif message.message_type == BridgeMessageType.ERROR:
            detail = message.text
            if not self._ready_event.is_set():
                self._startup_error = detail
                self._ready_event.set()
                logger.warning("鸿蒙官方 Java Bridge 启动时报错(%s): %s", self.serial, detail)
            else:
                logger.warning("鸿蒙官方 Java Bridge 运行时报错(%s): %s", self.serial, detail)
        elif message.message_type == BridgeMessageType.EOF:
            self._closed_reason = message.text
        elif message.message_type == BridgeMessageType.STATS:
            logger.debug("鸿蒙官方 Java Bridge 统计(%s): %s", self.serial, message.text)

    def _on_bridge_closed(self, reason: str) -> None:
        self._closed_reason = reason
        if not self._ready_event.is_set() and not self._stop_event.is_set():
            self._startup_error = reason
            self._ready_event.set()
        if not self._stop_event.is_set():
            # stdout 读取结束即视为 Bridge 已失效，避免管理器继续复用一个
            # 已经无法发送视频的会话；后续真实请求会重新建立官方链路。
            self._stop_event.set()
            logger.warning("鸿蒙官方 Java Bridge 已关闭(%s): %s", self.serial, reason)

    def _broadcast_h264(self, payload: bytes) -> None:
        """将官方 H.264 access unit 广播给所有 WebSocket 订阅者。"""
        with self._state_lock:
            self._h264_received_access_units += 1
            self._last_h264_access_unit_at = time.monotonic()
            if not self._h264_subscribers:
                # 无消费者时仍必须维护起播缓存：编码器启动后的首组 SPS/PPS+IDR
                # 几乎必然落在预热/空闲窗口（首个订阅者出现之前），若在此丢弃，
                # 之后订阅者只能收到 P 帧——P 帧因 has_config=False 被全部跳过，
                # 静态桌面下再也没有新 IDR，形成起播死锁（首帧超时→JPEG 回退）。
                self._update_h264_startup_cache(payload)
                return
        packets = _h264_websocket_packets(payload)
        if not packets:
            if not self._h264_unrecognized_logged:
                self._h264_unrecognized_logged = True
                logger.warning(
                    "鸿蒙官方 H.264 回调未识别为 Annex-B 数据: serial=%s, bytes=%d",
                    self.serial,
                    len(payload),
                )
            return

        request_idr = False
        cancel_retry_timers: list[threading.Timer] = []
        with self._state_lock:
            # 解析期间订阅可能已经断开，重新取当前订阅者。
            subscribers = list(self._h264_subscribers.values())
            if not subscribers:
                return
            for packet in packets:
                frame_type = packet[0]
                if frame_type in (0x02, 0x03) and not self._h264_video_logged:
                    self._h264_video_logged = True
                    logger.info(
                        "鸿蒙官方 H.264 已收到可转发视频帧: serial=%s, frame_type=%s, bytes=%d",
                        self.serial,
                        "IDR" if frame_type == 0x02 else "P",
                        len(packet) - 1,
                    )
                if frame_type == 0x01:
                    previous_config = self._latest_h264_config
                    self._latest_h264_config = packet
                    if previous_config is not None and previous_config != packet:
                        # 已有参数集发生变化时，旧 IDR 可能无法用新参数集解码。
                        self._latest_h264_keyframe = None
                    # 首组 SPS/PPS 可能晚于 SDK 首个 IDR 回调；此时保留此前的
                    # IDR，下面会在参数集之后补发，避免静态画面等待下一个 IDR。
                    pending_keyframe = (
                        self._latest_h264_keyframe if previous_config is None else None
                    )
                elif frame_type == 0x02:
                    self._latest_h264_keyframe = packet
                    pending_keyframe = None
                else:
                    pending_keyframe = None
                for subscriber in subscribers:
                    if frame_type == 0x03 and (
                        not subscriber.has_config or subscriber.waiting_for_keyframe
                    ):
                        subscriber.skipped_p_packets += 1
                        continue
                    if frame_type == 0x02 and not subscriber.has_config:
                        # IDR 不携带 SPS/PPS 时，单独发送也无法让浏览器起播。
                        # 等待参数集后再接收后续关键帧；起播请求已在订阅建立时发送。
                        continue
                    try:
                        self._enqueue_h264_packet(subscriber, packet)
                    except Full:
                        subscriber.queue_full_events += 1
                        was_waiting_for_keyframe = subscriber.waiting_for_keyframe
                        recovered_keyframe = self._recover_h264_subscriber(
                            subscriber,
                            frame_type,
                            packet,
                        )
                        if frame_type == 0x03 or not recovered_keyframe:
                            # 慢客户端清掉旧视频链路后，必须从下一个 IDR 重新起播。
                            # 当前 P 或缺少参数集的 IDR 都不能继续发送。
                            # 只在刚进入等待状态时请求一次，避免设备端持续收到 IDR 请求。
                            request_idr = request_idr or not was_waiting_for_keyframe
                        elif frame_type == 0x02:
                            subscriber.waiting_for_keyframe = False
                            retry_timer = self._h264_idr_retry_timers.pop(
                                subscriber.subscriber_id, None
                            )
                            self._h264_idr_retry_counts.pop(subscriber.subscriber_id, None)
                            if retry_timer:
                                cancel_retry_timers.append(retry_timer)
                    else:
                        if frame_type == 0x01:
                            subscriber.has_config = True
                            subscriber.waiting_for_keyframe = True
                            if pending_keyframe is not None:
                                try:
                                    self._enqueue_h264_packet(subscriber, pending_keyframe)
                                except Full:
                                    subscriber.queue_full_events += 1
                                    was_waiting_for_keyframe = subscriber.waiting_for_keyframe
                                    recovered_keyframe = self._recover_h264_subscriber(
                                        subscriber,
                                        0x02,
                                        pending_keyframe,
                                    )
                                    if recovered_keyframe:
                                        retry_timer = self._h264_idr_retry_timers.pop(
                                            subscriber.subscriber_id, None
                                        )
                                        self._h264_idr_retry_counts.pop(
                                            subscriber.subscriber_id, None
                                        )
                                        if retry_timer:
                                            cancel_retry_timers.append(retry_timer)
                                    else:
                                        request_idr = (
                                            request_idr or not was_waiting_for_keyframe
                                        )
                                else:
                                    subscriber.waiting_for_keyframe = False
                                    logger.info(
                                        "鸿蒙 H.264 首组 SPS/PPS 晚于 IDR，已按解码顺序补发: "
                                        "serial=%s, subscriber=%s, idr_bytes=%d",
                                        self.serial,
                                        subscriber.subscriber_id,
                                        len(pending_keyframe) - 1,
                                    )
                                    retry_timer = self._h264_idr_retry_timers.pop(
                                        subscriber.subscriber_id, None
                                    )
                                    self._h264_idr_retry_counts.pop(
                                        subscriber.subscriber_id, None
                                    )
                                    if retry_timer:
                                        cancel_retry_timers.append(retry_timer)
                        elif frame_type == 0x02:
                            subscriber.waiting_for_keyframe = False
                            retry_timer = self._h264_idr_retry_timers.pop(
                                subscriber.subscriber_id, None
                            )
                            self._h264_idr_retry_counts.pop(subscriber.subscriber_id, None)
                            if retry_timer:
                                cancel_retry_timers.append(retry_timer)

        for timer in cancel_retry_timers:
            timer.cancel()
        if request_idr:
            self.request_idr()

    def _update_h264_startup_cache(self, payload: bytes) -> None:
        """从 access unit 维护起播缓存（参数集/关键帧），供订阅建立时回放。

        仅存两组小包（SPS/PPS 与最近 IDR），不缓存 P 帧；跨参数集时旧 IDR
        作废，与订阅路径的缓存语义一致。
        """
        packets = _h264_websocket_packets(payload)
        if not packets:
            return
        with self._state_lock:
            for packet in packets:
                frame_type = packet[0]
                if frame_type == 0x01:
                    previous_config = self._latest_h264_config
                    self._latest_h264_config = packet
                    if previous_config is not None and previous_config != packet:
                        # 仅在已有参数集确实变化时作废 IDR；首组参数集可能晚于
                        # 首个关键帧到达，不能把同一会话的起播 IDR 一并清掉。
                        self._latest_h264_keyframe = None
                elif frame_type == 0x02:
                    self._latest_h264_keyframe = packet

    def _enqueue_h264_packet(self, subscriber: _H264Subscriber, packet: bytes) -> None:
        """入队一个完整的 WebSocket H.264 包并更新监控统计。"""
        subscriber.queue.put_nowait(packet)
        subscriber.enqueued_packets += 1
        subscriber.peak_queue_size = max(
            subscriber.peak_queue_size,
            subscriber.queue.qsize(),
        )
        self._h264_forwarded_packets += 1

    def _recover_h264_subscriber(
        self,
        subscriber: _H264Subscriber,
        frame_type: int,
        packet: bytes,
    ) -> bool:
        """清理慢订阅者的旧链路，并尽量恢复到可解码的起播状态。

        返回值表示当前包是否已经和参数集一起成功入队。P 帧溢出时不保留
        队列中的旧 P 帧，避免慢页面恢复后先播放过期画面；如果当前会话有
        最新参数集，则先补回参数集，随后等待新的 IDR。
        """
        drained_packets = 0
        drained_p_packets = 0
        while True:
            try:
                old_packet = subscriber.queue.get_nowait()
            except Empty:
                break
            drained_packets += 1
            if old_packet and old_packet[0] == 0x03:
                drained_p_packets += 1

        subscriber.dropped_packets += drained_packets
        subscriber.dropped_p_packets += drained_p_packets
        subscriber.has_config = False
        subscriber.waiting_for_keyframe = True

        recovery_packets: list[bytes] = []
        if frame_type == 0x01:
            # 当前参数集就是最新参数集，直接保留它。
            recovery_packets.append(packet)
        elif self._latest_h264_config is not None:
            recovery_packets.append(self._latest_h264_config)
            if frame_type == 0x02:
                recovery_packets.append(packet)

        recovered_keyframe = False
        for recovery_packet in recovery_packets:
            try:
                self._enqueue_h264_packet(subscriber, recovery_packet)
            except Full:
                # frame_queue_capacity 最小为 4；这里仅作为防御性处理，避免
                # 极端配置下错误地把订阅者标记为可解码。
                subscriber.dropped_packets += 1
                if recovery_packet and recovery_packet[0] == 0x03:
                    subscriber.dropped_p_packets += 1
                continue
            if recovery_packet[0] == 0x01:
                subscriber.has_config = True
            elif recovery_packet[0] == 0x02 and subscriber.has_config:
                subscriber.waiting_for_keyframe = False
                recovered_keyframe = True

        if frame_type == 0x03:
            subscriber.dropped_packets += 1
            subscriber.dropped_p_packets += 1
        elif frame_type == 0x02 and not recovered_keyframe:
            # 没有参数集时，当前 IDR 也不能单独交给浏览器。
            subscriber.dropped_packets += 1
        return recovered_keyframe

    def _start_h264_monitor(self) -> None:
        """每分钟记录官方 H.264 下游积压，便于评估丢帧策略。"""
        monitor_thread = self._h264_monitor_thread
        if monitor_thread and monitor_thread.is_alive():
            return

        interval = max(
            1.0,
            float(self.settings.get(
                "backpressure_log_interval_seconds",
                H264_BACKPRESSURE_LOG_INTERVAL_SECONDS,
            )),
        )

        def _monitor() -> None:
            while not self._h264_monitor_stop.wait(interval):
                with self._state_lock:
                    subscribers = tuple(self._h264_subscribers.values())
                    details = ";".join(
                        f"{subscriber.subscriber_id}:queue={subscriber.queue.qsize()}"
                        f",peak={subscriber.peak_queue_size}"
                        f",enqueued={subscriber.enqueued_packets}"
                        f",dropped={subscriber.dropped_packets}"
                        f",dropped_p={subscriber.dropped_p_packets}"
                        f",skipped_p={subscriber.skipped_p_packets}"
                        f",queue_full={subscriber.queue_full_events}"
                        f",waiting_idr={subscriber.waiting_for_keyframe}"
                        for subscriber in subscribers
                    ) or "none"
                    received = self._h264_received_access_units
                    forwarded = self._h264_forwarded_packets
                    idr_requests = self._h264_idr_requests
                logger.info(
                    "鸿蒙 H.264 下游阻塞监控: serial=%s, subscribers=%d, "
                    "received_access_units=%d, forwarded_packets=%d, idr_requests=%d, %s",
                    self.serial,
                    len(subscribers),
                    received,
                    forwarded,
                    idr_requests,
                    details,
                )

        self._h264_monitor_thread = threading.Thread(
            target=_monitor,
            name=f"harmony-official-monitor-{self.serial}",
            daemon=True,
        )
        self._h264_monitor_thread.start()

    def _wait_input_ready(self) -> None:
        if not self.is_running or not self._ready_event.is_set():
            raise HarmonyOfficialError("鸿蒙官方输入会话未就绪")
        delay = self._input_ready_at - time.monotonic()
        if delay > 0:
            time.sleep(delay)

    def _send(self, command: bytes) -> None:
        bridge = self._bridge
        if bridge is None:
            raise HarmonyOfficialError("鸿蒙官方 Java Bridge 未启动")
        try:
            with self._input_lock:
                bridge.send(command)
        except JavaBridgeError as exc:
            raise HarmonyOfficialError(str(exc)) from exc

    def _mouse_click(self, button: str, x: int, y: int, duration_ms: int) -> None:
        self._require_pc()
        self._send(command_mouse_down(button, x, y))
        try:
            time.sleep(max(duration_ms, 80) / 1000.0)
        finally:
            # 同 tap：down 已送达后 up 失败视为部分执行。
            try:
                self._send(command_mouse_up(button, x, y))
            except HarmonyOfficialError as exc:
                raise HarmonyOfficialPartialActionError(
                    f"mouse_up 发送失败（mouse_down 已送达）: {exc}"
                ) from exc

    def _require_pc(self) -> None:
        if self.device_type != "pc":
            raise HarmonyOfficialError("当前官方会话不是鸿蒙 PC")

    @staticmethod
    def _gesture_steps(duration_ms: int, steps: int | None) -> int:
        if steps is not None:
            return max(1, min(int(steps), 40))
        return max(1, min(20, duration_ms // 50))

    @staticmethod
    def _interpolate(
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        index: int,
        count: int,
    ) -> tuple[int, int]:
        ratio = index / count
        return (
            round(start_x + (end_x - start_x) * ratio),
            round(start_y + (end_y - start_y) * ratio),
        )


class HarmonyOfficialSessionManager:
    """按设备复用官方会话，官方链路失败时回退 HDC。"""

    DEFAULTS: dict[str, Any] = {
        "java_path": "java",
        "jar_path": DEFAULT_HARMONY_JAR_REL_PATH,
        "bridge_class_path": DEFAULT_HARMONY_BRIDGE_REL_PATH,
        "startup_timeout_seconds": 35,
        "idle_timeout_seconds": 60,
        "input_ready_delay_seconds": 1.5,
        "reconnect_attempts": 3,
        "reconnect_backoff_seconds": 0.5,
        "frame_queue_capacity": 2,
        "backpressure_log_interval_seconds": H264_BACKPRESSURE_LOG_INTERVAL_SECONDS,
        "image_scale_size": 720,
        "frame_rate": 10,
        "bit_rate": 4_000_000,
    }

    def __init__(self, device_type: str, config: dict[str, Any] | None = None) -> None:
        self.device_type = "mobile" if device_type == "harmony_mobile" else "pc"
        self._settings = self._build_settings(config or {})
        self._hdc_path: str | None = None
        self._sessions: dict[str, HarmonyOfficialSession] = {}
        # 同一设备可能同时被 HTTP 任务和 WebSocket 使用；只有最后一个租约释放后，
        # 才开始计算空闲释放时间，避免截图任务误杀正在推流的会话。
        self._owners: dict[str, set[str]] = {}
        self._idle_timers: dict[str, threading.Timer] = {}
        # 管理器停机代次：stop_all 递增，作废所有正在进行的冷启动，
        # 防止停机/升级期间启动完成的会话又被装回会话表。
        self._stop_generation = 0
        # 单设备代次用于作废正在启动的会话；显式断开时，未完成的冷启动不得
        # 在断开接口返回后又被装回会话表。
        self._session_generations: dict[str, int] = {}
        self._stopping_sessions: set[str] = set()
        # per-serial 启动锁：start()（最长 startup_timeout×重试次数）在锁外执行，
        # 只串行同一设备的并发启动，不阻塞其他设备的 acquire/订阅。
        self._start_locks: dict[str, threading.Lock] = {}
        self._device_lock_checker: Callable[[str], bool] | None = None
        self._lock = threading.RLock()

    def set_hdc_path(self, hdc_path: str | None) -> None:
        self._hdc_path = hdc_path

    def set_device_lock_checker(self, checker: Callable[[str], bool] | None) -> None:
        """设置设备锁屏状态查询器，锁屏时不启动官方视频会话。"""
        self._device_lock_checker = checker

    def _is_device_locked(self, serial: str) -> bool:
        checker = self._device_lock_checker
        if checker is None:
            return False
        try:
            return bool(checker(serial))
        except Exception as exc:
            # 无法确认状态时宁可跳过官方启动，避免锁屏设备反复拉起 Java。
            logger.warning("查询鸿蒙设备锁屏状态失败，按锁屏处理: serial=%s, error=%s", serial, exc)
            return True

    def is_device_locked(self, serial: str) -> bool:
        """返回设备当前是否处于锁屏状态，供 H.264 等待逻辑使用。"""
        return self._is_device_locked(serial)

    @staticmethod
    def _acquire_start_lock(
        start_lock: threading.Lock,
        cancel_event: threading.Event | None,
    ) -> bool:
        """获取 per-serial 启动锁；等待期间周期性检查取消标志。

        前一个启动方（如已断开的 WebSocket 订阅）可能在锁内执行最长
        startup_timeout 的冷启动；后到的 get_or_start 若不响应取消，
        会在锁上无反馈地排队同样长的时间，阻塞新连接。
        """
        while True:
            if cancel_event is not None and cancel_event.is_set():
                return False
            if start_lock.acquire(timeout=0.25):
                return True

    def get_or_start(
        self,
        serial: str,
        *,
        retry_attempts: int | None = None,
        owner: str | None = None,
        cancel_event: threading.Event | None = None,
    ) -> HarmonyOfficialSession | None:
        """返回可用官方会话；失败时返回 ``None``，由调用方回退 HDC。

        全局锁只保护会话字典的读写；耗时的 ``session.start()`` 在 per-serial
        启动锁下锁外执行，一台设备的启动（最长 startup_timeout×重试次数）不再
        阻塞其他设备的 acquire/订阅操作。

        ``cancel_event`` 提供协作式取消：在 attempt 边界和启动锁等待期间检查，
        已取消的调用尽快返回 ``None``。正在执行的 ``session.start()``（单次
        startup_timeout）无法中断，因此取消后锁至多再被占用一次启动预算。
        """
        try:
            with self._lock:
                generation = self._stop_generation
                serial_generation = self._session_generations.get(serial, 0)
                if serial in self._stopping_sessions:
                    return None
            if not self._hdc_path:
                return self._handle_start_failure("HDC 路径不可用")
            if self._is_device_locked(serial):
                logger.debug("鸿蒙设备处于锁屏状态，等待解锁后启动官方会话: serial=%s", serial)
                return None

            with self._lock:
                current = self._sessions.get(serial)
                if (
                    generation == self._stop_generation
                    and serial_generation == self._session_generations.get(serial, 0)
                    and serial not in self._stopping_sessions
                    and current
                    and current.is_running
                ):
                    # 已运行的会话只需 Bridge 仍在运行即可复用；视频数据由真实的
                    # WebSocket 订阅消费，不能在这里阻塞等待消费者。
                    self._claim_owner_locked(serial, owner)
                    return current

            with self._lock:
                start_lock = self._start_locks.setdefault(serial, threading.Lock())
            if not self._acquire_start_lock(start_lock, cancel_event):
                logger.info(
                    "鸿蒙官方会话启动在等待 per-serial 启动锁时被取消: serial=%s", serial
                )
                return None
            try:
                with self._lock:
                    if (
                        generation != self._stop_generation
                        or serial_generation != self._session_generations.get(serial, 0)
                    ):
                        return None
                    current = self._sessions.get(serial)
                    if current and current.is_running:
                        self._claim_owner_locked(serial, owner)
                        return current
                    if current:
                        self._sessions.pop(serial, None)
                        try:
                            current.stop(timeout=1.0)
                        except Exception as exc:
                            logger.warning(
                                "清理失效的鸿蒙官方会话失败，继续尝试 HDC 回退: serial=%s, error=%s",
                                serial,
                                exc,
                            )

                attempts = max(
                    1,
                    int(
                        retry_attempts
                        if retry_attempts is not None
                        else self._settings["reconnect_attempts"]
                    ),
                )
                last_error = ""
                for attempt in range(1, attempts + 1):
                    if cancel_event is not None and cancel_event.is_set():
                        logger.info(
                            "鸿蒙官方会话启动被取消: serial=%s, attempt=%d/%d",
                            serial,
                            attempt,
                            attempts,
                        )
                        return None
                    # 上次 Java Bridge 可能被 taskkill /F 强杀，SDK 的
                    # stopCaptureScreen 没机会执行；启动前主动兜底清理残留的
                    # fport 规则和设备端 singleness/uitest 进程，避免
                    # startCaptureScreen 端口冲突报 "can not find scrcpy pid"。
                    self._clean_up_hdc_fport(serial)
                    self._clean_up_device_processes(serial)
                    session = HarmonyOfficialSession(
                        serial=serial,
                        device_type=self.device_type,
                        hdc_path=self._hdc_path,
                        settings=self._settings_for_serial(serial),
                    )
                    try:
                        # start() 持 per-serial 启动锁在全局锁外执行。
                        session.start()
                    except Exception as exc:
                        last_error = str(exc)
                        if not last_error:
                            last_error = type(exc).__name__
                        try:
                            session.stop(timeout=1.0)
                        except Exception as stop_exc:
                            logger.warning(
                                "清理启动失败的鸿蒙官方会话失败: serial=%s, error=%s",
                                serial,
                                stop_exc,
                            )
                        logger.warning(
                            "启动鸿蒙官方会话失败: serial=%s, attempt=%d/%d, error=%s",
                            serial,
                            attempt,
                            attempts,
                            exc,
                        )
                        with self._lock:
                            invalidated = (
                                generation != self._stop_generation
                                or serial_generation
                                != self._session_generations.get(serial, 0)
                            )
                        if invalidated:
                            return None
                        if attempt < attempts:
                            time.sleep(float(self._settings["reconnect_backoff_seconds"]))
                        continue
                    with self._lock:
                        existing = self._sessions.get(serial)
                        if existing and existing.is_running:
                            # 另一线程已抢先装入新会话，丢弃本次重复启动。
                            logger.debug(
                                "鸿蒙官方会话已被并发启动，丢弃重复实例: serial=%s", serial
                            )
                            try:
                                session.stop(timeout=1.0)
                            except Exception as stop_exc:
                                logger.warning(
                                    "清理重复启动的鸿蒙官方会话失败: serial=%s, error=%s",
                                    serial,
                                    stop_exc,
                                )
                            self._claim_owner_locked(serial, owner)
                            return existing
                        if (
                            generation != self._stop_generation
                            or serial_generation != self._session_generations.get(serial, 0)
                        ):
                            # 会话代次失效后，不把新会话装回字典。
                            logger.info(
                                "鸿蒙官方会话启动完成但管理器已停机，直接释放: serial=%s",
                                serial,
                            )
                            try:
                                session.stop(timeout=1.0)
                            except Exception as stop_exc:
                                logger.warning(
                                    "清理停机后启动的鸿蒙官方会话失败: serial=%s, error=%s",
                                    serial,
                                    stop_exc,
                                )
                            return self._handle_start_failure("会话管理器已停机")
                        self._sessions[serial] = session
                        # 会话复用、租约登记和空闲计时器取消必须在同一把锁内完成，
                        # 否则空闲计时器可能先停掉刚刚被 acquire 返回的会话。
                        self._claim_owner_locked(serial, owner)
                        return session
                return self._handle_start_failure(last_error)
            finally:
                start_lock.release()
        except Exception as exc:
            # 官方链路是可选加速路径，任何启动阶段异常都必须交给调用方回退 HDC。
            detail = str(exc) or type(exc).__name__
            return self._handle_start_failure(detail)

    def get(self, serial: str) -> HarmonyOfficialSession | None:
        with self._lock:
            session = self._sessions.get(serial)
            return session if session and session.is_running else None

    def acquire(
        self,
        serial: str,
        owner: str,
        cancel_event: threading.Event | None = None,
    ) -> HarmonyOfficialSession | None:
        """获取一份官方会话租约；同一 owner 重复获取不会增加引用。

        get_or_start 可能耗时数十秒（冷启动/重试），期间不能持有全局锁，
        否则会阻塞其他设备的所有会话操作（H-2）。``cancel_event`` 用于
        推流订阅方在预算耗尽或客户端断开后放弃仍在进行的冷启动。
        """
        return self.get_or_start(serial, owner=owner, cancel_event=cancel_event)

    def release(self, serial: str, owner: str) -> None:
        """释放官方会话租约；没有活动租约时启动空闲释放倒计时。"""
        with self._lock:
            owners = self._owners.get(serial)
            if not owners:
                return
            owners.discard(owner)
            if owners:
                return
            self._owners.pop(serial, None)
            self._schedule_idle_release_locked(serial)

    def stop_session(self, serial: str) -> None:
        with self._lock:
            self._session_generations[serial] = self._session_generations.get(serial, 0) + 1
            self._stopping_sessions.add(serial)
            self._cancel_idle_timer_locked(serial)
            self._owners.pop(serial, None)
            start_lock = self._start_locks.setdefault(serial, threading.Lock())
        # 与该设备的冷启动串行化，避免旧会话 stop 与新 SDK 启动重叠。
        with start_lock:
            with self._lock:
                session = self._sessions.pop(serial, None)
            try:
                if session:
                    session.stop()
            finally:
                with self._lock:
                    self._stopping_sessions.discard(serial)

    def stop_session_if_idle(self, serial: str, wait_idle_seconds: float = 0.0) -> dict[str, Any]:
        """立即停止没有租约的官方会话，供平台调试页显式断开使用。

        与空闲保活的分工：保活兜底的是 WS 意外断开、任务间歇等隐式空闲；
        这里是用户主动"断开"，期望设备侧投屏立刻终止。wait_idle_seconds
        给 WebSocket 关闭后的 stream 租约释放留时间；仍被任务租约占用时
        放弃立即停止，交回空闲保活兜底。
        """
        deadline = time.monotonic() + max(0.0, wait_idle_seconds)
        owners: list[str] = []
        timer = None
        while True:
            with self._lock:
                owners_set = self._owners.get(serial)
                if not owners_set:
                    # 使正在进行的冷启动失效；它完成后会自行 stop，而不会成为
                    # 一个没有租约、但仍留在设备上的常驻会话。
                    self._session_generations[serial] = self._session_generations.get(serial, 0) + 1
                    self._stopping_sessions.add(serial)
                    timer = self._idle_timers.pop(serial, None)
                    start_lock = self._start_locks.setdefault(serial, threading.Lock())
                    break
                owners = sorted(owners_set)
            if time.monotonic() >= deadline:
                logger.info(
                    "平台请求断开，但官方会话仍有租约，交给空闲保活: serial=%s, owners=%s",
                    serial,
                    owners,
                )
                return {"stopped": False, "owners": owners}
            time.sleep(0.05)
        if timer:
            timer.cancel()
        remaining = max(0.0, deadline - time.monotonic())
        acquired = (
            start_lock.acquire(timeout=remaining)
            if remaining > 0
            else start_lock.acquire(blocking=False)
        )
        if not acquired:
            # 冷启动仍在 SDK 内部等待；代次已作废，启动线程完成后会自行清理。
            with self._lock:
                self._stopping_sessions.discard(serial)
            return {"stopped": False, "owners": [], "pending_start": True}
        try:
            with self._lock:
                # 冷启动完成与断开并发时，新的 acquire 可能先登记租约；尊重该租约。
                owners_set = self._owners.get(serial)
                if owners_set:
                    return {"stopped": False, "owners": sorted(owners_set)}
                session = self._sessions.pop(serial, None)
            if session:
                logger.info("平台调试页断开，立即停止鸿蒙官方会话: serial=%s", serial)
                session.stop()
            return {"stopped": True, "owners": []}
        finally:
            with self._lock:
                self._stopping_sessions.discard(serial)
            start_lock.release()

    def stop_all(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
            timers = list(self._idle_timers.values())
            self._stop_generation += 1
            self._sessions.clear()
            self._owners.clear()
            self._idle_timers.clear()
        for timer in timers:
            timer.cancel()
        for session in sessions:
            session.stop()

    def _cancel_idle_timer_locked(self, serial: str) -> None:
        timer = self._idle_timers.pop(serial, None)
        if timer:
            timer.cancel()

    def _claim_owner_locked(self, serial: str, owner: str | None) -> None:
        """在持有会话锁时原子登记租约并取消空闲释放计时器。"""
        if owner is None:
            return
        self._cancel_idle_timer_locked(serial)
        self._owners.setdefault(serial, set()).add(owner)

    def _schedule_idle_release_locked(self, serial: str) -> None:
        if serial not in self._sessions or self._owners.get(serial):
            return
        if serial in self._idle_timers:
            return
        self._cancel_idle_timer_locked(serial)
        timeout = max(0.1, float(self._settings["idle_timeout_seconds"]))
        timer = threading.Timer(timeout, self._release_idle_session, args=(serial,))
        timer.daemon = True
        self._idle_timers[serial] = timer
        timer.start()
        logger.info("鸿蒙官方会话进入空闲保活: serial=%s, timeout=%.1fs", serial, timeout)

    def _release_idle_session(self, serial: str) -> None:
        timer_thread = threading.current_thread()
        with self._lock:
            start_lock = self._start_locks.setdefault(serial, threading.Lock())
        with start_lock:
            with self._lock:
                if self._idle_timers.get(serial) is not timer_thread:
                    return
                self._idle_timers.pop(serial, None)
                if self._owners.get(serial) or serial in self._stopping_sessions:
                    return
                session = self._sessions.pop(serial, None)
            if session:
                logger.info("鸿蒙官方会话空闲超时，退出 Java Bridge: %s", serial)
                session.stop()

    def _handle_start_failure(self, detail: str) -> None:
        message = f"鸿蒙官方 HOScrcpy 不可用: {detail}"
        logger.warning("%s；将回退 HDC 路径", message)
        return None

    def _clean_up_hdc_fport(self, serial: str) -> None:
        """清理 Java Bridge 被强杀后遗留的 fport 转发规则（best-effort）。

        SDK 正常停止时会自行 fport rm；taskkill /F 绕过 stopCaptureScreen 后，
        残留规则（如 tcp:<随机端口> -> localabstract:scrcpy_grpc_socket）会随
        每次冷启动累积。这里全量清理该设备的 forward 规则，失败不阻断启动。
        """
        hdc_path = self._hdc_path
        if not hdc_path or not Path(hdc_path).is_file():
            return
        try:
            result = run_cmd(
                [hdc_path, "-t", serial, "fport", "ls"],
                timeout=_HDC_CLEANUP_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            logger.warning(
                "查询鸿蒙 fport 规则失败，跳过残留清理: serial=%s, error=%s", serial, exc
            )
            return
        rules = _parse_fport_rules(result.stdout or "")
        logger.debug("鸿蒙冷启动兜底清理 fport: serial=%s, rules=%d", serial, len(rules))
        for local, remote in rules:
            try:
                removed = run_cmd(
                    [hdc_path, "-t", serial, "fport", "rm", local, remote],
                    timeout=_HDC_CLEANUP_TIMEOUT_SECONDS,
                )
            except Exception as exc:
                logger.warning(
                    "清理鸿蒙 fport 规则异常: serial=%s, rule=%s %s, error=%s",
                    serial,
                    local,
                    remote,
                    exc,
                )
                continue
            output = (removed.stdout or removed.stderr or "").strip()
            if removed.returncode == 0 and "success" in output.lower():
                logger.info(
                    "已清理鸿蒙残留 fport 规则: serial=%s, rule=%s %s", serial, local, remote
                )
            else:
                logger.warning(
                    "清理鸿蒙 fport 规则失败（继续启动）: serial=%s, rule=%s %s, output=%s",
                    serial,
                    local,
                    remote,
                    output,
                )

    def _clean_up_device_processes(self, serial: str) -> None:
        """清理 Java Bridge 被强杀后设备端残留的 singleness/uitest 进程。

        残留的 ``uitest start-daemon singleness``（scrcpy 服务宿主）会让下次
        startCaptureScreen 端口冲突报 "can not find scrcpy pid"。清理方式与
        SDK 自身停止路径一致（ps -ef 后 kill -9），best-effort，失败不阻断启动。
        """
        hdc_path = self._hdc_path
        if not hdc_path or not Path(hdc_path).is_file():
            return
        try:
            result = run_cmd(
                [hdc_path, "-t", serial, "shell", "ps -ef"],
                timeout=_HDC_CLEANUP_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            logger.warning(
                "查询鸿蒙设备进程失败，跳过残留进程清理: serial=%s, error=%s", serial, exc
            )
            return
        pids = _find_residual_device_pids(result.stdout or "")
        logger.debug(
            "鸿蒙冷启动兜底清理设备进程: serial=%s, residual_pids=%s", serial, pids
        )
        for pid in pids:
            try:
                killed = run_cmd(
                    [hdc_path, "-t", serial, "shell", f"kill -9 {pid}"],
                    timeout=_HDC_CLEANUP_TIMEOUT_SECONDS,
                )
            except Exception as exc:
                logger.warning(
                    "终止鸿蒙设备残留进程异常: serial=%s, pid=%s, error=%s",
                    serial,
                    pid,
                    exc,
                )
                continue
            if killed.returncode == 0:
                logger.info("已终止鸿蒙设备残留进程: serial=%s, pid=%s", serial, pid)
            else:
                logger.warning(
                    "终止鸿蒙设备残留进程失败（继续启动）: serial=%s, pid=%s, output=%s",
                    serial,
                    pid,
                    (killed.stdout or killed.stderr or "").strip(),
                )

    def _build_settings(self, config: dict[str, Any]) -> dict[str, Any]:
        settings = dict(self.DEFAULTS)
        # 除开发环境 Java 路径外，其余运行参数和工具路径都固定由代码管理。
        base_dir = Path(get_base_dir())
        if is_packaged():
            # 打包版忽略配置中的开发机 Java 路径，只使用安装目录随附的运行时。
            settings["java_path"] = str(base_dir / DEFAULT_HARMONY_BUNDLED_JAVA_REL_PATH)
        else:
            # 开发环境可配置本机 Java；相对路径按项目根目录解析，java 命令走 PATH。
            configured_java = config.get("java_path") or self.DEFAULTS["java_path"]
            java_path = Path(str(configured_java))
            settings["java_path"] = (
                str(java_path)
                if str(java_path) == "java" or java_path.is_absolute()
                else str(base_dir / java_path)
            )

        settings["jar_path"] = str(base_dir / DEFAULT_HARMONY_JAR_REL_PATH)
        settings["bridge_class_path"] = str(base_dir / DEFAULT_HARMONY_BRIDGE_REL_PATH)
        return settings

    def _settings_for_serial(self, serial: str) -> dict[str, Any]:
        settings = dict(self._settings)
        temp_root = Path(get_base_dir()) / "data" / "harmony_official"
        safe_serial = re.sub(r"[^A-Za-z0-9_.-]", "_", serial)
        settings["temp_dir"] = str(temp_root / safe_serial / "java_tmp")
        return settings

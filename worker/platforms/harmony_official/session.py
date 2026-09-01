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

from common.packaging import get_base_dir
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
            if has_existing_subscribers and cached_config is not None:
                self._enqueue_h264_packet(subscriber, cached_config)
                subscriber.has_config = True
                queued_packets += 1
                if cached_keyframe is not None:
                    self._enqueue_h264_packet(subscriber, cached_keyframe)
                    queued_packets += 1
                    subscriber.waiting_for_keyframe = False
            elif not has_existing_subscribers:
                # 预热或空闲保活期间不保留视频数据。首个订阅必须从本次播放
                # 起点开始，避免把旧页面的参数集、关键帧或 P 帧带入解码链。
                self._latest_h264_config = None
                self._latest_h264_keyframe = None
                cached_config = None
                cached_keyframe = None
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
        # 首个订阅从空闲状态恢复时，先唤醒静止画面，再请求一次新的 IDR。
        # 活动流已有完整起播缓存时，直接回放给新订阅者，不打断已有订阅者。
        if not has_existing_subscribers:
            self.wake_stream()
            self.request_idr(force=True)
        elif cached_config is None or cached_keyframe is None:
            # 活动流尚未形成完整起播缓存时补请求；缓存完整时直接复用，
            # 避免新增查看页面打断现有订阅者。
            self.wake_stream()
            self.request_idr(force=True)
        self._schedule_h264_idr_retry(subscriber_id)
        return subscriber.queue

    def unsubscribe_h264(self, subscriber_id: str) -> None:
        """取消一个原始 H.264 订阅。"""
        with self._state_lock:
            self._h264_subscribers.pop(subscriber_id, None)
            retry_timer = self._h264_idr_retry_timers.pop(subscriber_id, None)
            self._h264_idr_retry_counts.pop(subscriber_id, None)
            if not self._h264_subscribers:
                # 没有消费者时丢弃所有 H.264 数据，下一次订阅重新请求 IDR。
                self._latest_h264_config = None
                self._latest_h264_keyframe = None
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

    def wake_stream(self) -> None:
        """请求官方 SDK 让静止画面产生一次新的 H.264 回调。"""
        try:
            self._send(command_wake_stream())
        except HarmonyOfficialError:
            # 静止画面唤醒失败时仍保留原始 H.264 会话，后续帧或 IDR 请求可继续恢复。
            logger.debug("鸿蒙官方 H.264 静止画面唤醒失败: serial=%s", self.serial)

    def tap(self, x: int, y: int, duration_ms: int = 0) -> None:
        """执行移动端触摸点击或长按，PC 使用左键点击。"""
        self._wait_input_ready()
        if self.device_type == "mobile":
            self._send(command_touch_down(x, y))
            try:
                time.sleep(max(duration_ms, 80) / 1000.0)
            finally:
                self._send(command_touch_up(x, y))
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

    def swipe(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        duration_ms: int,
        steps: int | None = None,
    ) -> None:
        """移动端发送触摸轨迹，PC 发送官方左键拖拽。"""
        self._wait_input_ready()
        duration_ms = max(1, int(duration_ms))
        event_count = self._gesture_steps(duration_ms, steps)
        interval = duration_ms / event_count / 1000.0
        if self.device_type == "mobile":
            self._send(command_touch_down(start_x, start_y))
            try:
                for index in range(1, event_count + 1):
                    time.sleep(interval)
                    x, y = self._interpolate(start_x, start_y, end_x, end_y, index, event_count)
                    self._send(command_touch_move(x, y))
                time.sleep(0.02)
            finally:
                self._send(command_touch_up(end_x, end_y))
            return

        self._send(command_mouse_down("LEFT", start_x, start_y))
        try:
            for index in range(1, event_count + 1):
                time.sleep(interval)
                x, y = self._interpolate(start_x, start_y, end_x, end_y, index, event_count)
                self._send(command_mouse_move("LEFT", x, y))
            time.sleep(0.02)
        finally:
            self._send(command_mouse_up("LEFT", end_x, end_y))

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
                    logger.debug(
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
            logger.warning("鸿蒙官方 Java Bridge 报错(%s): %s", self.serial, detail)
            if not self._ready_event.is_set():
                self._startup_error = detail
                self._ready_event.set()
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
            if not self._h264_subscribers:
                # 预热只保持 Java 和设备采集会话，不保存或解码无消费者的视频。
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
                    self._latest_h264_config = packet
                    # 参数集变化后，旧 IDR 可能与新的 SPS/PPS 不匹配，不能跨参数集回放。
                    self._latest_h264_keyframe = None
                elif frame_type == 0x02:
                    self._latest_h264_keyframe = packet
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
            self._send(command_mouse_up(button, x, y))

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
        "jar_path": "tools/harmony/hosScrcpy-1.0.15-beta.jar",
        "bridge_class_path": "tools/harmony/bridge",
        "startup_timeout_seconds": 35,
        "idle_timeout_seconds": 600,
        "prewarm_failure_cooldown_seconds": 60,
        "input_ready_delay_seconds": 1.5,
        "reconnect_attempts": 3,
        "reconnect_backoff_seconds": 0.5,
        "prewarm_on_device_ready": True,
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
        self._prewarm_threads: dict[str, threading.Thread] = {}
        self._prewarm_failure_until: dict[str, float] = {}
        self._prewarm_generation = 0
        self._device_lock_checker: Callable[[str], bool] | None = None
        self._lock = threading.RLock()

    @property
    def prewarm_on_device_ready(self) -> bool:
        """设备服务上线后是否后台预热官方 Java 会话。"""
        return bool(self._settings["prewarm_on_device_ready"])

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

    def get_or_start(
        self,
        serial: str,
        *,
        retry_attempts: int | None = None,
    ) -> HarmonyOfficialSession | None:
        """返回可用官方会话；失败时返回 ``None``，由调用方回退 HDC。"""
        try:
            if not self._hdc_path:
                return self._handle_start_failure("HDC 路径不可用")
            if self._is_device_locked(serial):
                logger.debug("鸿蒙设备处于锁屏状态，等待解锁后启动官方会话: serial=%s", serial)
                return None

            with self._lock:
                current = self._sessions.get(serial)
                if current and current.is_running:
                    # 预热会话只需 Bridge 仍在运行即可复用；视频数据由真实的
                    # WebSocket 订阅消费，不能在这里阻塞等待消费者。
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
                    session = HarmonyOfficialSession(
                        serial=serial,
                        device_type=self.device_type,
                        hdc_path=self._hdc_path,
                        settings=self._settings_for_serial(serial),
                    )
                    try:
                        session.start()
                        self._sessions[serial] = session
                        return session
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
                        if attempt < attempts:
                            time.sleep(float(self._settings["reconnect_backoff_seconds"]))
                return self._handle_start_failure(last_error)
        except Exception as exc:
            # 官方链路是可选加速路径，任何启动阶段异常都必须交给调用方回退 HDC。
            detail = str(exc) or type(exc).__name__
            return self._handle_start_failure(detail)

    def get(self, serial: str) -> HarmonyOfficialSession | None:
        with self._lock:
            session = self._sessions.get(serial)
            return session if session and session.is_running else None

    def prewarm(self, serial: str, owner: str | None = None) -> None:
        """后台预热官方会话，不阻塞当前鸿蒙动作。"""
        if not self._hdc_path:
            return
        if self._is_device_locked(serial):
            logger.debug("鸿蒙设备处于锁屏状态，跳过官方会话预热: serial=%s", serial)
            return

        with self._lock:
            retry_after = self._prewarm_failure_until.get(serial, 0.0)
            now = time.monotonic()
            if retry_after > now:
                logger.debug(
                    "鸿蒙官方会话预热处于失败冷却，跳过本次启动: serial=%s, retry_after=%.1fs",
                    serial,
                    retry_after - now,
                )
                return
            if owner:
                self._owners.setdefault(serial, set()).add(owner)
                self._cancel_idle_timer_locked(serial)
            current = self._sessions.get(serial)
            if current and current.is_running:
                if not self._owners.get(serial):
                    # 设备监控会周期性调用预热检查；已有空闲计时器时不能重复
                    # 续期，否则“10 分钟无调用释放”会永远不会触发。
                    if serial not in self._idle_timers:
                        self._schedule_idle_release_locked(serial)
                return
            thread = self._prewarm_threads.get(serial)
            if thread and thread.is_alive():
                return
            generation = self._prewarm_generation

            def _run() -> None:
                try:
                    with self._lock:
                        if generation != self._prewarm_generation:
                            return
                    # 预热只把 Java Bridge 启动到 SDK_READY。没有消费者时仍保持
                    # 采集并丢弃视频数据，真正订阅到来时再从新的 IDR 起播。
                    session = self.get_or_start(
                        serial,
                        retry_attempts=1,
                    )
                    if session is not None:
                        stale_session = False
                        with self._lock:
                            self._prewarm_failure_until.pop(serial, None)
                            if generation != self._prewarm_generation:
                                stale_session = self._sessions.get(serial) is session
                                if stale_session:
                                    self._sessions.pop(serial, None)
                                    self._owners.pop(serial, None)
                            elif not self._owners.get(serial):
                                self._schedule_idle_release_locked(serial)
                        if stale_session:
                            session.stop()
                            return
                        logger.info("鸿蒙官方会话后台预热完成: %s", serial)
                    else:
                        cooldown = max(
                            0.0,
                            float(self._settings["prewarm_failure_cooldown_seconds"]),
                        )
                        with self._lock:
                            self._prewarm_failure_until[serial] = time.monotonic() + cooldown
                        logger.warning(
                            "鸿蒙官方会话后台预热未建立，%.0fs 内不再自动重试: serial=%s",
                            cooldown,
                            serial,
                        )
                        if owner:
                            self.release(serial, owner)
                except Exception as exc:
                    # 预热失败不影响当前动作；短暂冷却避免设备维护重复拉起 Java。
                    cooldown = max(
                        0.0,
                        float(self._settings["prewarm_failure_cooldown_seconds"]),
                    )
                    with self._lock:
                        self._prewarm_failure_until[serial] = time.monotonic() + cooldown
                    logger.warning(
                        "鸿蒙官方会话后台预热失败，%.0fs 内不再自动重试: serial=%s, error=%s",
                        cooldown,
                        serial,
                        exc,
                    )
                    if owner:
                        self.release(serial, owner)
                finally:
                    with self._lock:
                        if self._prewarm_threads.get(serial) is threading.current_thread():
                            self._prewarm_threads.pop(serial, None)

            thread = threading.Thread(
                target=_run,
                name=f"harmony-official-prewarm-{serial}",
                daemon=True,
            )
            self._prewarm_threads[serial] = thread
            thread.start()

    def acquire(
        self,
        serial: str,
        owner: str,
    ) -> HarmonyOfficialSession | None:
        """获取一份官方会话租约；同一 owner 重复获取不会增加引用。"""
        with self._lock:
            self._cancel_idle_timer_locked(serial)
            session = self.get_or_start(serial)
            if session is None:
                return None
            self._owners.setdefault(serial, set()).add(owner)
            return session

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
            self._cancel_idle_timer_locked(serial)
            self._owners.pop(serial, None)
            self._prewarm_failure_until.pop(serial, None)
            session = self._sessions.pop(serial, None)
        if session:
            session.stop()

    def stop_all(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
            timers = list(self._idle_timers.values())
            self._prewarm_generation += 1
            self._sessions.clear()
            self._owners.clear()
            self._idle_timers.clear()
            self._prewarm_threads.clear()
            self._prewarm_failure_until.clear()
        for timer in timers:
            timer.cancel()
        for session in sessions:
            session.stop()

    def _cancel_idle_timer_locked(self, serial: str) -> None:
        timer = self._idle_timers.pop(serial, None)
        if timer:
            timer.cancel()

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
        with self._lock:
            self._idle_timers.pop(serial, None)
            if self._owners.get(serial):
                return
            session = self._sessions.pop(serial, None)
        if session:
            logger.info("鸿蒙官方会话空闲超时，退出 Java Bridge: %s", serial)
            session.stop()

    def _handle_start_failure(self, detail: str) -> None:
        message = f"鸿蒙官方 HOScrcpy 不可用: {detail}"
        logger.warning("%s；将回退 HDC 路径", message)
        return None

    def _build_settings(self, config: dict[str, Any]) -> dict[str, Any]:
        settings = dict(self.DEFAULTS)
        # 仅读取已支持的运行参数，历史配置中的模式和回退开关不再生效。
        settings.update({
            key: value
            for key, value in config.items()
            if key in self.DEFAULTS and value is not None
        })
        base_dir = Path(get_base_dir())
        for key in ("java_path", "jar_path", "bridge_class_path"):
            value = Path(str(settings[key]))
            # ``java`` 仅是 PATH 命令，不应拼接项目目录；显式相对路径则用于
            # 打包后的 tools/jre/bin/java.exe。
            if key == "java_path" and str(value) == "java":
                continue
            settings[key] = str(value if value.is_absolute() else base_dir / value)
        return settings

    def _settings_for_serial(self, serial: str) -> dict[str, Any]:
        settings = dict(self._settings)
        temp_root = Path(get_base_dir()) / "data" / "harmony_official"
        safe_serial = re.sub(r"[^A-Za-z0-9_.-]", "_", serial)
        settings["temp_dir"] = str(temp_root / safe_serial / "java_tmp")
        return settings

"""官方 HOScrcpy H.264 数据的最新帧解码缓存。"""

from __future__ import annotations

import io
import threading
import time
from dataclasses import dataclass
from typing import Iterable


class H264DecodeError(RuntimeError):
    """H.264 解码器不可用或解码失败。"""


def _annexb_nal_types(payload: bytes) -> list[int]:
    """提取 Annex-B 码流中的 NAL 类型，供解码前过滤配置包。"""
    types: list[int] = []
    offset = 0
    length = len(payload)
    while offset < length:
        start3 = payload.find(b"\x00\x00\x01", offset)
        start4 = payload.find(b"\x00\x00\x00\x01", offset)
        if start3 < 0 and start4 < 0:
            break
        if start4 >= 0 and (start3 < 0 or start4 <= start3):
            start, prefix_length = start4, 4
        else:
            start, prefix_length = start3, 3
        nal_start = start + prefix_length
        if nal_start < length:
            types.append(payload[nal_start] & 0x1F)
        offset = nal_start
    return types


def _annexb_config_payload(payload: bytes) -> bytes:
    """提取 Annex-B 码流中的 SPS/PPS，供后续视频帧补齐解码参数。"""
    config_parts: list[bytes] = []
    offset = 0
    length = len(payload)
    while offset < length:
        start3 = payload.find(b"\x00\x00\x01", offset)
        start4 = payload.find(b"\x00\x00\x00\x01", offset)
        if start3 < 0 and start4 < 0:
            break
        if start4 >= 0 and (start3 < 0 or start4 <= start3):
            start, prefix_length = start4, 4
        else:
            start, prefix_length = start3, 3
        nal_start = start + prefix_length
        next3 = payload.find(b"\x00\x00\x01", nal_start)
        next4 = payload.find(b"\x00\x00\x00\x01", nal_start)
        next_start = next4 if next3 < 0 else next3 if next4 < 0 else min(next3, next4)
        end = next_start if next_start >= 0 else length
        if nal_start < end and (payload[nal_start] & 0x1F) in (7, 8):
            config_parts.append(payload[start:end])
        if next_start < 0:
            break
        offset = next_start
    return b"".join(config_parts)


@dataclass(frozen=True)
class LatestFrame:
    """最新的解码 JPEG 帧。"""

    jpeg: bytes
    width: int
    height: int
    sequence: int
    captured_at: float


class LatestFrameCache:
    """线程安全的单槽最新帧缓存，避免消费者积压旧画面。"""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._latest: LatestFrame | None = None
        self._sequence = 0

    def update(self, jpeg: bytes, width: int, height: int) -> LatestFrame:
        """覆盖缓存为最新帧并唤醒等待的消费者。"""
        with self._condition:
            self._sequence += 1
            self._latest = LatestFrame(
                jpeg=jpeg,
                width=width,
                height=height,
                sequence=self._sequence,
                captured_at=time.monotonic(),
            )
            self._condition.notify_all()
            return self._latest

    def get(self, timeout: float = 0.0, after_sequence: int | None = None) -> LatestFrame | None:
        """获取最新帧；指定序号时等待更新后的帧。"""
        deadline = time.monotonic() + max(timeout, 0.0)
        with self._condition:
            while self._latest is None or (
                after_sequence is not None and self._latest.sequence <= after_sequence
            ):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)
            return self._latest

    def clear(self) -> None:
        with self._condition:
            self._latest = None
            self._condition.notify_all()


class H264Decoder:
    """基于 PyAV 的 Annex-B H.264 解码器。"""

    def __init__(self, jpeg_quality: int = 90) -> None:
        try:
            import av
        except ImportError as exc:
            raise H264DecodeError("未安装 PyAV，无法解码鸿蒙官方 H.264 推流") from exc

        self._codec = av.CodecContext.create("h264", "r")
        self._jpeg_quality = max(1, min(int(jpeg_quality), 100))
        self.decoded_frames = 0
        self._config_payload = b""

    def decode_to_jpegs(self, data: bytes) -> Iterable[tuple[bytes, int, int]]:
        """解码一段 H.264 数据，产生其中所有完整视频帧。"""
        if not data:
            return []

        results: list[tuple[bytes, int, int]] = []
        try:
            nal_types = _annexb_nal_types(data)
            config_payload = _annexb_config_payload(data)
            if config_payload:
                self._config_payload = config_payload
            if nal_types and not any(nal_type in (1, 5) for nal_type in nal_types):
                # SPS/PPS 只用于配置解码器，单独送入解码器不会产生图像。
                return []
            decode_data = data
            if (
                nal_types
                and any(nal_type in (1, 5) for nal_type in nal_types)
                and not config_payload
                and self._config_payload
            ):
                decode_data = self._config_payload + data
            # 官方回调是 Annex-B 原始码流，必须经过 FFmpeg parser，不能直接
            # 包装成 AVPacket。每次回调结束后 flush parser，避免静止画面没有
            # 下一段 P 帧时，首个 IDR 一直滞留在 parser 内部。
            packets = list(self._codec.parse(decode_data))
            packets.extend(self._codec.parse(b""))
            for packet in packets:
                for frame in self._codec.decode(packet):
                    image = frame.to_image()
                    buffer = io.BytesIO()
                    image.save(buffer, format="JPEG", quality=self._jpeg_quality)
                    width, height = image.size
                    results.append((buffer.getvalue(), width, height))
                    self.decoded_frames += 1
        except Exception as exc:
            raise H264DecodeError(f"H.264 解码失败: {exc}") from exc
        return results

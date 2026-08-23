"""官方 HOScrcpy H.264 数据的最新帧解码缓存。"""

from __future__ import annotations

import io
import threading
import time
from dataclasses import dataclass
from typing import Iterable


class H264DecodeError(RuntimeError):
    """H.264 解码器不可用或解码失败。"""


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

    def decode_to_jpegs(self, data: bytes) -> Iterable[tuple[bytes, int, int]]:
        """解码一段 H.264 数据，产生其中所有完整视频帧。"""
        if not data:
            return []

        results: list[tuple[bytes, int, int]] = []
        try:
            packets = self._codec.parse(data)
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

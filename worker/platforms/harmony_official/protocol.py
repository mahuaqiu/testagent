"""HOScrcpy Java Bridge 的 HOS1 二进制协议。"""

from __future__ import annotations

import logging
import struct
from collections.abc import Iterator
from dataclasses import dataclass
from enum import IntEnum
from typing import BinaryIO

MAGIC = b"HOS1"
VERSION = 1
HEADER = struct.Struct(">4sBBI")
MAX_PAYLOAD = 32 * 1024 * 1024

# 重对齐时每次最多读入 64KB 参与 magic 搜索；诊断文本通常只有几百字节。
RESYNC_CHUNK_SIZE = 64 * 1024
RESYNC_PREVIEW_BYTES = 200
_RESYNC_LOG_CAP = 4096
_RESYNC_WARNING_INTERVAL = 100

logger = logging.getLogger(__name__)


class BridgeMessageType(IntEnum):
    """Java Bridge stdout 消息类型。"""

    READY = 1
    H264 = 2
    ERROR = 4
    STATS = 5
    EOF = 6


class BridgeProtocolError(ValueError):
    """Bridge 输出不符合 HOS1 协议。"""


@dataclass(frozen=True)
class BridgeMessage:
    """一条 Java Bridge 输出消息。"""

    message_type: BridgeMessageType
    payload: bytes

    @property
    def text(self) -> str:
        """状态类 payload 的 UTF-8 表示。"""
        return self.payload.decode("utf-8", errors="replace")


class ProtocolStreamReader:
    """HOS1 流读取器，支持跳过混入 stdout 的非协议数据并重新对齐 magic。

    官方 SDK 的 native 库可能在 JVM 启动极早期绕过 ``System.setOut`` 直写
    stdout fd 1（现场表现为 ``[0.000s]`` 诊断行），污染 HOS1 二进制流。
    读取器维护内部缓冲：magic 校验失败时把已消费的 header 放回搜索范围，
    以滑动窗口向前查找下一个 ``HOS1``，丢弃之前的非协议字节后继续解析。
    """

    def __init__(self, stream: BinaryIO) -> None:
        self._stream = stream
        self._buf = bytearray()
        self.resync_count = 0
        self.skipped_bytes = 0

    def read_exact(self, size: int) -> bytes:
        """读取恰好 ``size`` 字节；流提前 EOF 时返回不足的字节。"""
        while len(self._buf) < size:
            chunk = self._stream.read(size - len(self._buf))
            if not chunk:
                break
            self._buf.extend(chunk)
        data = bytes(memoryview(self._buf)[:size])
        del self._buf[:size]
        return data

    def resync(self, discarded: bytes) -> bool:
        """丢弃 ``discarded`` 与缓冲中的非协议字节，滑动对齐到下一个 MAGIC。

        搜索从缓冲偏移 1 开始：MAGIC 可能整个落在已消费的失败 header 内。
        找不到时仅保留尾部 3 字节（可能是被截断的 MAGIC 前缀）继续读流；
        流 EOF 仍未对齐则返回 ``False``。跳过的字节全程累积（封顶
        ``_RESYNC_LOG_CAP``）用于日志取证，污染开头的 ``[0.000s]`` 前缀
        正是定位 native 直写 fd 1 的关键证据。
        """
        self._buf[:0] = discarded
        skipped_log = bytearray()
        while True:
            index = self._buf.find(MAGIC, 1)
            if index >= 0:
                skipped_log.extend(self._buf[:index])
                self.skipped_bytes += index
                del self._buf[:index]
                self.resync_count += 1
                self._log_resync(bytes(skipped_log[:_RESYNC_LOG_CAP]))
                return True
            keep = max(len(self._buf) - (len(MAGIC) - 1), 0)
            self.skipped_bytes += keep
            if len(skipped_log) < _RESYNC_LOG_CAP:
                skipped_log.extend(self._buf[:keep])
                del skipped_log[_RESYNC_LOG_CAP:]
            del self._buf[:keep]
            chunk = self._stream.read(RESYNC_CHUNK_SIZE)
            if not chunk:
                return False
            self._buf.extend(chunk)

    def _log_resync(self, skipped: bytes) -> None:
        preview = skipped[:RESYNC_PREVIEW_BYTES]
        details = (
            len(skipped),
            preview,
            self.skipped_bytes,
            self.resync_count,
        )
        if self.resync_count == 1 or self.resync_count % _RESYNC_WARNING_INTERVAL == 0:
            logger.warning(
                "HOS1 流混入非协议数据，已滑动重新对齐 magic: 本次跳过=%d 字节, "
                "preview=%r, 累计跳过=%d 字节, 累计次数=%d",
                *details,
            )
        else:
            logger.debug(
                "HOS1 流混入非协议数据已重新对齐: 本次跳过=%d 字节, " "preview=%r, 累计跳过=%d 字节, 累计次数=%d",
                *details,
            )


def read_message(stream: BinaryIO | ProtocolStreamReader) -> BridgeMessage | None:
    """读取一条 HOS1 消息；正常 EOF 时返回 ``None``。

    magic 不匹配时先尝试滑动重新对齐（见 ``ProtocolStreamReader.resync``），
    仅当流 EOF 仍找不到 MAGIC 才报错，避免 native 诊断文本杀死整条会话。
    """
    reader = stream if isinstance(stream, ProtocolStreamReader) else ProtocolStreamReader(stream)
    header = reader.read_exact(HEADER.size)
    if not header:
        return None
    if len(header) != HEADER.size:
        raise BridgeProtocolError("HOS1 header 被截断")

    fields = HEADER.unpack(header)
    if fields[0] != MAGIC:
        if not reader.resync(header):
            raise BridgeProtocolError(
                f"HOS1 magic 无效: {fields[0]!r}"
                f"（重新对齐失败，已跳过 {reader.skipped_bytes} 字节非协议数据，流已 EOF）"
            )
        header = reader.read_exact(HEADER.size)
        if len(header) != HEADER.size:
            raise BridgeProtocolError("HOS1 header 被截断")
        fields = HEADER.unpack(header)

    magic, version, raw_type, payload_length = fields
    if version != VERSION:
        raise BridgeProtocolError(f"不支持的 HOS1 协议版本: {version}")
    if payload_length > MAX_PAYLOAD:
        raise BridgeProtocolError(f"HOS1 payload 过大: {payload_length}")
    try:
        message_type = BridgeMessageType(raw_type)
    except ValueError as exc:
        raise BridgeProtocolError(f"未知 HOS1 消息类型: {raw_type}") from exc

    payload = reader.read_exact(payload_length)
    if len(payload) != payload_length:
        raise BridgeProtocolError("HOS1 payload 被截断")
    return BridgeMessage(message_type, payload)


def iter_messages(stream: BinaryIO) -> Iterator[BridgeMessage]:
    """持续读取 HOS1 消息，直到 stdout EOF。"""
    reader = stream if isinstance(stream, ProtocolStreamReader) else ProtocolStreamReader(stream)
    while True:
        message = read_message(reader)
        if message is None:
            return
        yield message


def encode_message(message_type: BridgeMessageType, payload: bytes = b"") -> bytes:
    """编码消息，仅供协议单元测试使用。"""
    if len(payload) > MAX_PAYLOAD:
        raise BridgeProtocolError(f"HOS1 payload 过大: {len(payload)}")
    return HEADER.pack(MAGIC, VERSION, int(message_type), len(payload)) + payload


def command_touch_down(x: int, y: int) -> bytes:
    return _command("TOUCH_DOWN", x, y)


def command_touch_move(x: int, y: int) -> bytes:
    return _command("TOUCH_MOVE", x, y)


def command_touch_up(x: int, y: int) -> bytes:
    return _command("TOUCH_UP", x, y)


def command_mouse_down(button: str, x: int, y: int) -> bytes:
    return _command("MOUSE_DOWN", button, x, y)


def command_mouse_move(button: str | None, x: int, y: int) -> bytes:
    return _command("MOUSE_MOVE", button or "NONE", x, y)


def command_mouse_up(button: str, x: int, y: int) -> bytes:
    return _command("MOUSE_UP", button, x, y)


def command_wheel(direction: str, x: int, y: int) -> bytes:
    direction = direction.upper()
    if direction not in {"UP", "DOWN", "STOP"}:
        raise ValueError(f"不支持的滚轮方向: {direction}")
    return _command(f"WHEEL_{direction}", x, y)


def command_request_idr() -> bytes:
    return b"REQUEST_IDR\n"


def command_wake_stream() -> bytes:
    return b"WAKE_STREAM\n"


def _command(name: str, *parts: object) -> bytes:
    return (" ".join((name, *(str(part) for part in parts))) + "\n").encode("utf-8")

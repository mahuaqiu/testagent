"""HOScrcpy Java Bridge 的 HOS1 二进制协议。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
import struct
from typing import BinaryIO, Iterator


MAGIC = b"HOS1"
VERSION = 1
HEADER = struct.Struct(">4sBBI")
MAX_PAYLOAD = 32 * 1024 * 1024


class BridgeMessageType(IntEnum):
    """Java Bridge stdout 消息类型。"""

    READY = 1
    H264 = 2
    SIZE = 3
    ERROR = 4
    STATS = 5
    EOF = 6
    IMAGE = 7


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


def read_message(stream: BinaryIO) -> BridgeMessage | None:
    """读取一条 HOS1 消息；正常 EOF 时返回 ``None``。"""
    header = _read_exact(stream, HEADER.size)
    if not header:
        return None
    if len(header) != HEADER.size:
        raise BridgeProtocolError("HOS1 header 被截断")

    magic, version, raw_type, payload_length = HEADER.unpack(header)
    if magic != MAGIC:
        raise BridgeProtocolError(f"HOS1 magic 无效: {magic!r}")
    if version != VERSION:
        raise BridgeProtocolError(f"不支持的 HOS1 协议版本: {version}")
    if payload_length > MAX_PAYLOAD:
        raise BridgeProtocolError(f"HOS1 payload 过大: {payload_length}")
    try:
        message_type = BridgeMessageType(raw_type)
    except ValueError as exc:
        raise BridgeProtocolError(f"未知 HOS1 消息类型: {raw_type}") from exc

    payload = _read_exact(stream, payload_length)
    if len(payload) != payload_length:
        raise BridgeProtocolError("HOS1 payload 被截断")
    return BridgeMessage(message_type, payload)


def iter_messages(stream: BinaryIO) -> Iterator[BridgeMessage]:
    """持续读取 HOS1 消息，直到 stdout EOF。"""
    while True:
        message = read_message(stream)
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


def command_stop() -> bytes:
    return b"STOP\n"


def _command(name: str, *parts: object) -> bytes:
    return (" ".join((name, *(str(part) for part in parts))) + "\n").encode("utf-8")


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    if size == 0:
        return b""
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)

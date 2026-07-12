"""Windows 屏幕侧车进程客户端。"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import shutil
import socket
import struct
import time
import subprocess
import threading
from urllib.parse import urlparse
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common.packaging import get_base_dir
from common.utils import popen_cmd

logger = logging.getLogger(__name__)

_shared_client: WindowsSidecarClient | None = None
_shared_client_lock = threading.Lock()
_windows_managers: dict[str, WindowsSidecarScreenManager] = {}
_windows_managers_lock = threading.Lock()


def _candidate_paths() -> list[str]:
    base_dir = Path(get_base_dir())
    candidates = [
        base_dir / "tools" / "windows-screen-sidecar.exe",
        base_dir / "rust" / "windows-screen-sidecar" / "target" / "release" / "windows-screen-sidecar.exe",
        base_dir / "rust" / "windows-screen-sidecar" / "target" / "debug" / "windows-screen-sidecar.exe",
    ]
    return [str(path) for path in candidates if path.exists()]


@dataclass
class _CommandResult:
    ok: bool
    data: dict[str, Any] | None = None
    error: str | None = None


class WindowsSidecarClient:
    """负责启动和访问 Rust sidecar 进程。"""

    def __init__(self) -> None:
        self._proc: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._request_id = 1
        self._ref_count = 0
        self._stderr_thread: threading.Thread | None = None
        self._stderr_stop_event = threading.Event()  # 用于立即停止 stderr 线程
        self._closed = False
        self._restart_count = 0
        self._max_restarts = 3
        # stderr 归属：True 表示日志态（_drain_stderr 记日志），False 表示推流态（行投递给推流回调）
        # 关键：stderr 始终由单一 _stderr_pump 线程读取，避免两个 readline 抢读导致行被瓜分截断。
        # 切换归属只切下游分发，不切读取线程。
        self._stderr_owner_drain = True
        # 推流态下的行处理回调（由 PushFrameReader 注册）
        self._push_line_handler: Callable[[str], None] | None = None

    def acquire(self) -> None:
        with self._lock:
            self._ref_count += 1
        self._ensure_started()

    def release(self) -> None:
        should_close = False
        with self._lock:
            if self._ref_count > 0:
                self._ref_count -= 1
            should_close = self._ref_count == 0
        if should_close:
            self.shutdown()

    def _resolve_command(self) -> list[str]:
        candidates = _candidate_paths()
        if candidates:
            return [candidates[0]]

        cargo = shutil.which("cargo")
        manifest = Path(get_base_dir()) / "rust" / "windows-screen-sidecar" / "Cargo.toml"
        if cargo and manifest.exists():
            return [cargo, "run", "--quiet", "--manifest-path", str(manifest)]

        raise FileNotFoundError("未找到 windows-screen-sidecar 可执行文件，也没有可用的 cargo 构建入口")

    def _try_start(self) -> bool:
        """尝试启动 sidecar，返回是否成功"""
        try:
            command = self._resolve_command()
            logger.info("启动 Windows sidecar: %s", " ".join(command))
            proc = popen_cmd(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )

            if not proc.stdin or not proc.stdout or not proc.stderr:
                return False

            self._proc = proc
            self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
            self._stderr_thread.start()

            # 健康检查
            health = self.request("health", {})
            if health.get("status") == "ok":
                self._restart_count = 0
                return True

            # 健康检查失败，清理进程
            proc.terminate()
            proc.wait(timeout=5)
            return False
        except Exception as e:
            logger.warning("启动 sidecar 失败: %s", e)
            return False

    def _ensure_started(self) -> None:
        with self._lock:
            if self._proc and self._proc.poll() is None:
                return
            if self._closed:
                self._closed = False

        # 最多重试 max_restarts 次
        for attempt in range(self._max_restarts):
            if self._try_start():
                return
            logger.warning("sidecar 启动失败，尝试第 %d/%d 次", attempt + 1, self._max_restarts)
            self._restart_count += 1

        raise RuntimeError(f"无法启动 sidecar，已尝试 {self._max_restarts} 次")

    def _drain_stderr(self) -> None:
        """stderr 单消费者：唯一调用 _proc.stderr.readline() 的线程。

        根据当前归属把每一行分发到日志或推流回调，避免多线程抢读同一管道。
        """
        if not self._proc or not self._proc.stderr:
            return
        stream = self._proc.stderr
        while not self._stderr_stop_event.is_set():
            try:
                line = stream.readline()
            except Exception:
                break
            if not line:
                # readline 返回空表示 EOF（进程已退出）
                break
            line = line.rstrip("\n")
            if not line:
                continue
            # 切换归属只切分发，不切读取：始终由本线程独占读取
            if self._stderr_owner_drain:
                logger.info("[windows-sidecar] %s", line)
            else:
                handler = self._push_line_handler
                if handler is not None:
                    try:
                        handler(line)
                    except Exception as exc:
                        # 打印异常类型/repr + 行原文前缀，便于定位（不再打完整 traceback 降噪）
                        line_preview = line[:60]
                        logger.warning("推流行处理异常: %r | 行原文前缀=%r", exc, line_preview)

    # 重试无意义的确定性错误关键词：这些错误反映当前业务状态，重试不会改变结果
    _NON_RETRYABLE_KEYWORDS = (
        "not running",
        "already running",
        "not found",
        "invalid request",
    )

    def request(self, cmd: str, params: dict[str, Any] | None = None, max_retries: int = 2) -> dict[str, Any]:
        """发送请求到 sidecar，支持失败重试

        对于确定性错误（如 recording not running、session not found），不会重试，直接抛出。
        """
        last_error = None
        for attempt in range(max_retries + 1):
            try:
                # 每次尝试前确保进程存活
                self._ensure_started()

                with self._lock:
                    self._ensure_alive()
                    if not self._proc or not self._proc.stdin or not self._proc.stdout:
                        raise RuntimeError("sidecar 进程未启动")

                    request_id = self._request_id
                    self._request_id += 1
                    payload = json.dumps(
                        {"id": request_id, "cmd": cmd, "params": params or {}},
                        ensure_ascii=False,
                    )
                    logger.debug(f"sidecar request: cmd={cmd}, id={request_id}")
                    self._proc.stdin.write(payload + "\n")
                    self._proc.stdin.flush()

                    response_line = self._proc.stdout.readline()
                    if not response_line:
                        raise RuntimeError("sidecar 进程已退出")

                    logger.debug(f"sidecar response: {response_line[:200] if response_line else 'empty'}")
                    response = json.loads(response_line)
                if not response.get("ok"):
                    error_msg = response.get("error") or f"sidecar 命令失败: {cmd}"
                    raise RuntimeError(error_msg)

                data = response.get("data")
                if isinstance(data, dict):
                    return data
                return {}
            except Exception as e:
                last_error = e
                # 确定性错误：重试无意义，直接抛出
                if self._is_non_retryable_error(e):
                    logger.warning("sidecar 确定性错误，不重试: %s", e)
                    raise
                logger.warning("sidecar 请求失败 (attempt %d/%d): %s", attempt + 1, max_retries + 1, e)
                # 重试前清理可能已损坏的进程
                with self._lock:
                    if self._proc and self._proc.poll() is not None:
                        self._proc = None
                # 短暂等待后重试
                if attempt < max_retries:
                    import time
                    time.sleep(0.5)

        raise RuntimeError(f"sidecar 请求失败，已重试 {max_retries + 1} 次: {last_error}")

    @classmethod
    def _is_non_retryable_error(cls, exc: Exception) -> bool:
        """判断是否为确定性错误（重试无意义）"""
        msg = str(exc).lower()
        return any(kw in msg for kw in cls._NON_RETRYABLE_KEYWORDS)

    def _ensure_alive(self) -> None:
        if self._proc and self._proc.poll() is not None:
            raise RuntimeError(f"sidecar 进程已退出，退出码={self._proc.returncode}")

    def get_process(self):
        """暴露底层 subprocess 引用，供 PushFrameReader 使用"""
        with self._lock:
            return self._proc

    def is_alive(self) -> bool:
        """检查 sidecar 进程是否存活"""
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    def write_command(self, cmd: str) -> None:
        """发送控制命令到 stdin（不等待响应）"""
        with self._lock:
            if not self._proc or not self._proc.stdin:
                raise RuntimeError("sidecar 进程未启动")
            self._proc.stdin.write(cmd + "\n")
            self._proc.stdin.flush()

    def set_stderr_drain(self, enabled: bool, push_handler: Callable[[str], None] | None = None) -> None:
        """切换 stderr 归属（始终由 _drain_stderr 线程独占读取，只切下游分发）。

        enabled=True：行记日志（正常态）。
        enabled=False：行投递给 push_handler（推流态）。push_handler 必须非空。
        """
        with self._lock:
            if not enabled:
                self._push_line_handler = push_handler
            else:
                self._push_line_handler = None
            self._stderr_owner_drain = enabled
            # 确保 pump 线程在运行
            self._stderr_stop_event.clear()
            if self._stderr_thread is None or not self._stderr_thread.is_alive():
                self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
                self._stderr_thread.start()
            logger.debug("stderr drain owner=drain? %s", enabled)

    def get_monitors(self) -> list[dict]:
        """获取所有显示器配置"""
        result = self.request("get_monitors", {})
        return result.get("monitors", [])

    def shutdown(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True

            proc = self._proc
            if not proc or proc.poll() is not None:
                self._proc = None
                return

            try:
                if proc.stdin and proc.stdout:
                    request_id = self._request_id
                    self._request_id += 1
                    payload = json.dumps({"id": request_id, "cmd": "shutdown", "params": {}}, ensure_ascii=False)
                    proc.stdin.write(payload + "\n")
                    proc.stdin.flush()
                    _ = proc.stdout.readline()
            except Exception as exc:
                logger.warning("关闭 sidecar 时失败: %s", exc)
            finally:
                try:
                    proc.terminate()
                    proc.wait(timeout=5)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                self._proc = None


def get_shared_windows_sidecar_client() -> WindowsSidecarClient:
    global _shared_client
    with _shared_client_lock:
        if _shared_client is None:
            _shared_client = WindowsSidecarClient()
        return _shared_client


def get_windows_sidecar_manager(
    session_id: str,
    monitor: int = 1,
    idle_fps: int = 1,
    active_fps: int = 15,
) -> WindowsSidecarScreenManager:
    with _windows_managers_lock:
        manager = _windows_managers.get(session_id)
        if manager is None:
            manager = WindowsSidecarScreenManager(
                session_id=session_id,
                monitor=monitor,
                idle_fps=idle_fps,
                active_fps=active_fps,
            )
            _windows_managers[session_id] = manager
        return manager


def close_windows_sidecar_manager(session_id: str) -> None:
    with _windows_managers_lock:
        manager = _windows_managers.pop(session_id, None)
    if manager:
        manager.stop()


class WindowsSidecarStreamer:
    """Windows 侧车推流适配器。"""

    def __init__(
        self,
        client: WindowsSidecarClient,
        session_id: str,
        codec: str,
        fps: int,
        bitrate: int = 4_000_000,
        profile: int = 66,
        binary: bool = False,
    ):
        self._client = client
        self._session_id = session_id
        self.codec = codec
        self._fps = fps
        self._bitrate = bitrate
        self._profile = profile  # H.264 profile: 66=Baseline, 77=Main, 100=High
        self._binary = binary
        self._running = False
        self._h264_info: dict[str, Any] | None = None
        self._media_reader: MediaPacketReader | None = None
        self._on_fallback: Callable[[], None] | None = None

    def start(self, codec: str = "jpeg", on_fallback: Callable[[], None] | None = None) -> None:
        self._on_fallback = on_fallback
        self.codec = codec
        self._running = True
        if codec == "h264":
            try:
                data = self._client.request(
                    "stream_start",
                    {
                        "session_id": self._session_id,
                        "fps": self._fps,
                        "bitrate": self._bitrate,
                        "profile": self._profile,
                        "binary": self._binary,
                    },
                )
                self._h264_info = data
                if self._binary:
                    endpoint = data.get("binary_media_endpoint")
                    if not endpoint:
                        raise RuntimeError("sidecar 未返回二进制媒体 endpoint")
                    self._media_reader = MediaPacketReader(endpoint)
            except Exception as e:
                try:
                    self._client.request("stream_stop", {"session_id": self._session_id})
                except Exception:
                    pass
                if self._media_reader:
                    self._media_reader.close()
                    self._media_reader = None
                logger.error(f"Failed to start H.264 stream: {e}, falling back to JPEG")
                self._trigger_fallback()
        else:
            self._h264_info = None

    def _trigger_fallback(self) -> None:
        """触发降级回调并切换到 JPEG。"""
        logger.warning("Falling back to JPEG mode")
        self.codec = "jpeg"
        if self._on_fallback:
            self._on_fallback()

    def stop(self) -> None:
        if self.codec == "h264" and self._running:
            try:
                self._client.request("stream_stop", {"session_id": self._session_id})
            except Exception as exc:
                logger.warning("停止 Windows H264 推流失败: %s", exc)
        self._running = False
        if self._media_reader:
            self._media_reader.close()
            self._media_reader = None

    async def get_frame_async(self) -> bytes | None:
        if not self._running:
            return None

        if self.codec == "h264":
            if self._media_reader:
                packet = await asyncio.to_thread(self._media_reader.read_packet)
                return packet["payload"] if packet else None
            data = self._client.request("stream_next", {"session_id": self._session_id})
            frame_b64 = data.get("frame_b64")
            if not frame_b64:
                return None
            return base64.b64decode(frame_b64)

        data = self._client.request(
            "snapshot",
            {
                "session_id": self._session_id,
                "format": "jpeg",
                "quality": 80,
                "max_age_ms": 100,
            },
        )
        image_b64 = data.get("image_b64")
        if not image_b64:
            return None
        return base64.b64decode(image_b64)

    async def get_media_packet_async(self) -> dict[str, Any] | None:
        """读取一个完整 RSM1 媒体包，保留逻辑时间和关键帧元数据。"""
        if not self._running or self.codec != "h264" or not self._media_reader:
            return None
        try:
            return await asyncio.to_thread(self._media_reader.read_packet)
        except (EOFError, OSError, TimeoutError) as exc:
            # Rust 二进制输出支持新客户端重新接入。这里不立即结束 WebSocket，
            # 而是先重连一次；Rust 端会对新 TCP 连接重新执行首个关键帧门控。
            logger.warning("Windows RSM1 通道断开，尝试重连: %s", exc)
            try:
                await asyncio.to_thread(self._media_reader.reconnect)
            except (OSError, TimeoutError, ValueError) as reconnect_exc:
                logger.error("Windows RSM1 通道重连失败: %s", reconnect_exc)
                self._running = False
            return None

    def is_running(self) -> bool:
        return self._running

    def get_h264_info(self) -> dict[str, Any] | None:
        return self._h264_info

    @property
    def uses_binary_media(self) -> bool:
        """是否已成功建立二进制媒体通道。"""
        return self._media_reader is not None and self.codec == "h264"

    @property
    def binary_enabled(self) -> bool:
        """是否请求使用二进制媒体通道。"""
        return self._binary


class MediaPacketReader:
    """读取 Rust RSM1 二进制媒体通道，处理 TCP 半包和粘包。"""

    HEADER = struct.Struct("<4sBBH Q q q I I I")
    MAGIC = b"RSM1"
    VERSION = 1
    MAX_PAYLOAD_BYTES = 64 * 1024 * 1024

    def __init__(self, endpoint: str, sock: socket.socket | Any | None = None) -> None:
        parsed = urlparse(endpoint)
        if parsed.scheme != "tcp" or not parsed.hostname or not parsed.port:
            raise ValueError(f"不支持的二进制媒体 endpoint: {endpoint}")
        self._endpoint = (parsed.hostname, parsed.port)
        self._buffer = bytearray()
        self._socket = sock or self._connect()
        self._connected_monotonic = time.monotonic()
        self._packet_count = 0

    def _connect(self) -> socket.socket:
        """建立一个新的 TCP 媒体连接。"""
        return socket.create_connection(self._endpoint, timeout=5.0)

    def reconnect(self) -> None:
        """断线后重新连接 Rust 二进制媒体端点，并丢弃旧连接残留数据。"""
        self.close()
        self._buffer.clear()
        self._socket = self._connect()
        self._connected_monotonic = time.monotonic()
        self._packet_count = 0

    def close(self) -> None:
        try:
            self._socket.close()
        except Exception:
            pass

    def _recv_exact(self, size: int) -> None:
        while len(self._buffer) < size:
            chunk = self._socket.recv(max(4096, size - len(self._buffer)))
            if not chunk:
                raise EOFError("二进制媒体通道已断开")
            self._buffer.extend(chunk)

    def read_packet(self) -> dict[str, Any]:
        read_started = time.monotonic()
        self._recv_exact(self.HEADER.size)
        header = bytes(self._buffer[: self.HEADER.size])
        (
            magic,
            version,
            message_type,
            flags,
            sequence,
            pts_100ns,
            duration_100ns,
            width,
            height,
            payload_length,
        ) = self.HEADER.unpack(header)
        if magic != self.MAGIC:
            raise ValueError("媒体 packet magic 不匹配")
        if version != self.VERSION:
            raise ValueError(f"不支持的媒体 packet 版本: {version}")
        if payload_length > self.MAX_PAYLOAD_BYTES:
            raise ValueError(f"媒体 packet payload 过大: {payload_length}")

        packet_length = self.HEADER.size + payload_length
        self._recv_exact(packet_length)
        payload = bytes(self._buffer[self.HEADER.size : packet_length])
        del self._buffer[:packet_length]
        received_monotonic = time.monotonic()
        self._packet_count += 1
        self.last_diagnostics = {
            "_received_monotonic": received_monotonic,
            "_read_ms": (received_monotonic - read_started) * 1000,
            "_connected_ms": (received_monotonic - self._connected_monotonic) * 1000,
            "_packet_count": self._packet_count,
            "_buffered_bytes": len(self._buffer),
        }
        return {
            "sequence": sequence,
            "pts_100ns": pts_100ns,
            "duration_100ns": duration_100ns,
            "width": width,
            "height": height,
            "flags": flags,
            "message_type": message_type,
            "payload": payload,
        }


def media_packet_to_websocket_frame(packet: dict[str, Any]) -> bytes:
    """将 RSM1 媒体包转换为现有 WebSocket 帧协议。

    WebSocket 兼容协议使用一个字节标识帧类型：0x01 表示配置数据，
    0x02 表示关键帧，0x03 表示普通 P 帧。配置包同时携带关键帧时，
    必须优先使用关键帧前缀，避免前端只更新解码器参数而不触发解码。
    """
    flags = int(packet.get("flags", 0))
    payload = packet.get("payload", b"")
    if not isinstance(payload, bytes):
        raise TypeError("媒体 packet payload 必须是 bytes")

    if flags & 0x01:
        prefix = b"\x02"
    elif flags & 0x02:
        prefix = b"\x01"
    else:
        prefix = b"\x03"
    return prefix + payload


def media_packet_to_websocket_frames(packet: dict[str, Any]) -> list[bytes]:
    """将一个 RSM1 媒体包展开为一个或多个现有 WebSocket 帧。

    编码器通常会把 SPS、PPS 和 IDR 合并到同一个 RSM1 包中，
    但旧 WebSocket 协议要求配置帧先于关键帧发送。这里仅拆分转发帧，
    不改变 WebSocket 帧格式或 sidecar 的 RSM1 协议。
    """
    flags = int(packet.get("flags", 0))
    payload = packet.get("payload", b"")
    if not isinstance(payload, bytes):
        raise TypeError("媒体 packet payload 必须是 bytes")

    has_config = bool(flags & 0x02)
    has_keyframe = bool(flags & 0x01)
    if not (has_config and has_keyframe):
        return [media_packet_to_websocket_frame(packet)]

    config_payload, keyframe_payload = _split_h264_config_payload(payload)
    if not config_payload or not keyframe_payload:
        return [media_packet_to_websocket_frame(packet)]

    return [b"\x01" + config_payload, b"\x02" + keyframe_payload]


def _split_h264_config_payload(payload: bytes) -> tuple[bytes, bytes]:
    """拆分 Annex-B payload 中的 SPS/PPS 与其它 NAL。"""
    nals: list[tuple[bytes, int]] = []
    index = 0
    while index < len(payload):
        short_start = payload.find(b"\x00\x00\x01", index)
        long_start = payload.find(b"\x00\x00\x00\x01", index)
        if long_start >= 0 and (short_start < 0 or long_start <= short_start):
            start = long_start
            start_code_length = 4
        else:
            start = short_start
            start_code_length = 3
        if start < 0:
            break
        nal_start = start + start_code_length
        next_short = payload.find(b"\x00\x00\x01", nal_start)
        next_long = payload.find(b"\x00\x00\x00\x01", nal_start)
        candidates = [candidate for candidate in (next_short, next_long) if candidate >= 0]
        next_start = min(candidates) if candidates else len(payload)
        if nal_start < next_start:
            nal = payload[start:next_start]
            nals.append((nal, payload[nal_start] & 0x1F))
        index = next_start

    config = b"".join(nal for nal, nal_type in nals if nal_type in (7, 8))
    keyframe = b"".join(nal for nal, nal_type in nals if nal_type not in (7, 8))
    return config, keyframe

class WindowsSidecarScreenManager:
    """Windows 屏幕管理器的新实现，直接连接 Rust sidecar。"""

    def __init__(self, session_id: str, monitor: int = 1, idle_fps: int = 1, active_fps: int = 15):
        self._client = get_shared_windows_sidecar_client()
        self._client.acquire()
        try:
            self._session_id = session_id
            self._monitor = monitor
            self._idle_fps = idle_fps
            self._active_fps = active_fps
            # 保存创建参数，用于 session 丢失后幂等重建（sidecar 重启场景）
            self._open_monitor = monitor
            self._open_idle_fps = idle_fps
            self._open_active_fps = active_fps
            self._streamer: WindowsSidecarStreamer | None = None
            self._closed = False
            self._aligned_width: int | None = None
            self._aligned_height: int | None = None

            self._client.request(
                "session_open",
                {
                    "session_id": self._session_id,
                    "monitor": self._open_monitor,
                    "idle_fps": self._open_idle_fps,
                    "active_fps": self._open_active_fps,
                },
            )
        except Exception:
            self._client.release()
            raise

    def _is_session_lost_error(self, exc: Exception) -> bool:
        """判断异常是否为 session 丢失（sidecar 重启后 session 表清空）。"""
        msg = str(exc).lower()
        return "session not found" in msg or ("not found" in msg and "session" in msg)

    def _reopen_session(self) -> bool:
        """幂等重建 session。session_open 在 Rust 侧 get_or_create_session 不存在即创建。

        Returns:
            True=重建成功，False=重建失败。
        """
        try:
            self._client.request(
                "session_open",
                {
                    "session_id": self._session_id,
                    "monitor": self._open_monitor,
                    "idle_fps": self._open_idle_fps,
                    "active_fps": self._open_active_fps,
                },
            )
            logger.warning("session 重建成功: session_id=%s", self._session_id)
            return True
        except Exception as e:
            logger.error("session 重建失败: session_id=%s err=%s", self._session_id, e)
            return False

    def _call_with_session_recovery(self, op_name: str, do_call):
        """执行一次 client 请求；命中 session not found 时重建 session 后重试一次。

        用于截图/录制等命令,使 sidecar 重启场景能自愈,不再永久 fallback 到 pyautogui。
        """
        try:
            return do_call()
        except Exception as e:
            if not self._is_session_lost_error(e):
                raise
            logger.warning("检测到 session 丢失(%s): %s，尝试重建后重试", op_name, e)
            if not self._reopen_session():
                raise
            return do_call()  # 重建后重试一次,失败则抛出由上层处理

    def start_capture(self) -> None:
        return

    def stop(self) -> None:
        if self._closed:
            return
        self._closed = True

        if self._streamer:
            self._streamer.stop()
            self._streamer = None

        try:
            self._client.request("session_close", {"session_id": self._session_id})
        except Exception as exc:
            logger.warning("关闭 Windows sidecar session 失败: %s", exc)
        finally:
            self._client.release()

    def get_frame(self, timeout: float = 1.0) -> bytes:
        return self.get_frame_jpeg()

    def get_frame_bgra(self) -> bytearray:
        data = self._call_with_session_recovery(
            "snapshot_raw",
            lambda: self._client.request(
                "snapshot",
                {
                    "session_id": self._session_id,
                    "format": "raw",
                    "max_age_ms": 100,
                },
            ),
        )
        bgra_b64 = data.get("bgra_b64")
        if not bgra_b64:
            return bytearray()

        bgra_bytes = base64.b64decode(bgra_b64, validate=True)
        width = int(data.get("width", 0))
        height = int(data.get("height", 0))

        # 如果设置了对齐尺寸，且需要扩展到对齐尺寸
        logger.info(f"get_frame_bgra: actual={width}x{height}, aligned={self._aligned_width}x{self._aligned_height}")
        if self._aligned_width and self._aligned_height and (width != self._aligned_width or height != self._aligned_height):
            import numpy as np

            logger.info(f"Padding frame: actual={width}x{height}, expected={self._aligned_width}x{self._aligned_height}")
            # 创建对齐尺寸的空白 BGRA（全黑）
            aligned_bgra = np.zeros((self._aligned_height, self._aligned_width, 4), dtype=np.uint8)
            # 填充原图数据到左上角
            orig_array = np.frombuffer(bgra_bytes, dtype=np.uint8).reshape(height, width, 4)
            aligned_bgra[:height, :width] = orig_array
            result = bytearray(aligned_bgra.tobytes())
            logger.info(f"Padded frame size: {len(result)} bytes")
            return result

        return bytearray(bgra_bytes)

    def get_frame_raw_with_meta(self) -> dict:
        """获取 raw 全屏帧及元信息（bgra_b64/width/height），支持 session 自愈。

        供 windows.py 窗口级截图使用：先取全屏 raw，再按 window_rect 裁剪。
        """
        return self._call_with_session_recovery(
            "snapshot_raw_meta",
            lambda: self._client.request(
                "snapshot",
                {
                    "session_id": self._session_id,
                    "format": "raw",
                    "max_age_ms": 100,
                },
            ),
        )

    def get_frame_jpeg(self) -> bytes:
        data = self._call_with_session_recovery(
            "snapshot_jpeg",
            lambda: self._client.request(
                "snapshot",
                {
                    "session_id": self._session_id,
                    "format": "jpeg",
                    "quality": 80,
                    "max_age_ms": 100,
                },
            ),
        )
        image_b64 = data.get("image_b64")
        if not image_b64:
            raise RuntimeError("sidecar JPEG snapshot is empty")
        image_bytes = base64.b64decode(image_b64, validate=True)
        if not image_bytes:
            raise RuntimeError("sidecar JPEG snapshot decoded to empty data")
        return image_bytes

    def get_screen_size(self) -> tuple[int, int]:
        data = self._call_with_session_recovery(
            "snapshot_size",
            lambda: self._client.request(
                "snapshot",
                {
                    "session_id": self._session_id,
                    "format": "raw",
                    "max_age_ms": 100,
                },
            ),
        )
        width = int(data.get("width", 0))
        height = int(data.get("height", 0))
        return width, height

    def get_blank_frame(self) -> bytes:
        return b""

    def set_frame_aligned_size(self, width: int, height: int) -> None:
        """设置对齐后的分辨率（由 ScreenRecorder 调用）。

        Args:
            width: 对齐后的宽度
            height: 对齐后的高度
        """
        self._aligned_width = width
        self._aligned_height = height
        logger.info(f"Aligned size set: {width}x{height}")

    def start_recording(
        self,
        output_path: str,
        fps: int = 10,
        timeout_ms: int = 7_200_000,
        audio: bool = False,
        monitor: int = 1,
        watermark: bool = True,
    ) -> bool:
        try:
            data = self._call_with_session_recovery(
                "recording_start",
                lambda: self._client.request(
                    "recording_start",
                    {
                        "session_id": self._session_id,
                        "output_path": output_path,
                        "fps": fps,
                        "audio": audio,
                        "watermark": watermark,
                    },
                ),
            )
            # 获取对齐后的尺寸并设置，用于帧填充
            aligned_width = data.get("aligned_width")
            aligned_height = data.get("aligned_height")
            logger.info(f"Recording start response: aligned_width={aligned_width}, aligned_height={aligned_height}")
            if aligned_width and aligned_height:
                self.set_frame_aligned_size(aligned_width, aligned_height)
                logger.info(f"Recording aligned size: {aligned_width}x{aligned_height}")
            return True
        except Exception as exc:
            logger.error("启动 Windows 录制失败: %s", exc)
            return False

    def stop_recording(self) -> str:
        try:
            data = self._call_with_session_recovery(
                "recording_stop",
                lambda: self._client.request("recording_stop", {"session_id": self._session_id}),
            )
            return str(data.get("output_path") or "")
        except Exception as exc:
            logger.error("停止 Windows 录制失败: %s", exc)
            return ""

    def start_streaming(
        self,
        codec: str = "jpeg",
        bitrate: int = 4_000_000,
        profile: int = 66,
        binary: bool = False,
    ) -> WindowsSidecarStreamer:
        if self._streamer and (
            self._streamer.codec != codec
            or (codec == "h264" and self._streamer.binary_enabled != binary)
        ):
            self._streamer.stop()
            self._streamer = None

        if not self._streamer:
            self._streamer = WindowsSidecarStreamer(
                self._client,
                self._session_id,
                codec=codec,
                fps=self._active_fps,
                bitrate=bitrate,
                profile=profile,
                binary=binary,
            )
            self._streamer.start(codec=codec)
        return self._streamer


class PushFrameReader:
    """推模式帧读取器 - 通过 client 的 stderr 单消费者分发接收 Rust 推送的帧数据"""

    def __init__(self, client: WindowsSidecarClient, session_id: str = "windows/1"):
        self._client = client
        self._session_id = session_id
        self._running = False
        self._fps = 20
        self._frame_queue: asyncio.Queue | None = None
        # 诊断测点2：记录推流启动时刻 + IDR 到达计数与时间戳
        self._push_start_time: float | None = None
        self._idr_count: int = 0

    def set_fps(self, fps: int):
        """动态配置帧率"""
        self._fps = fps
        self._client.write_command(f"@FPS={fps}")

    def is_running(self) -> bool:
        """检查推流是否仍在运行"""
        return self._running and self._client.is_alive()

    def start_push(self, fps: int = 20):
        """启动推流模式"""
        logger.info("PushFrameReader.start_push: session_id=%s, fps=%d", self._session_id, fps)
        self._fps = fps
        # 缓冲约 1 秒帧量：足够吸收 Rust 编码突发（bursty producer），
        # 又不至于累积过多延迟。满时由 _enqueue_frame 丢最旧保最新。
        self._frame_queue = asyncio.Queue(maxsize=30)
        self._running = True
        # 诊断测点2：记录推流启动时刻，用于计算 IDR 首次到达延迟与 IDR 间隔
        import time as _time
        self._push_start_time = _time.monotonic()
        self._idr_count = 0
        logger.info("PushFrameReader.start_push: 推流启动时刻已记录（测点2基准）")

        # 关键：把 stderr 归属切换为推流态，并注册自己的行处理回调。
        # _drain_stderr 线程仍独占读取 stderr，但把每一行投递给 _handle_line，
        # 不再有第二个线程抢读同一管道。
        self._client.set_stderr_drain(False, push_handler=self._handle_line)
        logger.info("PushFrameReader.start_push: stderr 归属已切换给推流回调")

        # 通知 Rust 启动推送
        logger.info("PushFrameReader.start_push: 发送 stream_push_start 请求, session_id=%s", self._session_id)
        try:
            result = self._client.request("stream_push_start", {"session_id": self._session_id, "fps": fps})
            logger.info("PushFrameReader.start_push: stream_push_start 响应: %s", result)
        except Exception as e:
            logger.error("PushFrameReader.start_push: stream_push_start 请求失败: %s", e)
            self._running = False
            self._client.set_stderr_drain(True)
            raise

    def stop_push(self):
        """停止推流模式"""
        self._running = False
        self._client.write_command("@PUSH_STOP")
        # 恢复 stderr 归属为日志态
        self._client.set_stderr_drain(True)

    def _handle_line(self, line: str | bytes):
        """处理接收到的行（由 client 的 _drain_stderr 线程调用，已去除换行）

        line 为 str（stderr 是 text 模式）。
        """
        if isinstance(line, bytes):
            line = line.decode("utf-8", errors="replace")
        if not line or not self._frame_queue:
            return
        prefix = line[:1]
        content = line[1:].strip()

        if prefix == '@':
            self._handle_command(content)
        elif prefix == '0':
            data = base64.b64decode(content)
            self._enqueue_frame('sps', data)
        elif prefix == '1':
            data = base64.b64decode(content)
            self._enqueue_frame('pps', data)
        elif prefix == '2':
            data = base64.b64decode(content)
            # 诊断测点2：IDR 到达时间戳——距推流启动多久、距上一个 IDR 多久、累计第几个
            import time as _time
            now = _time.monotonic()
            self._idr_count += 1
            t0 = self._push_start_time
            if t0 is not None:
                since_start = (now - t0) * 1000.0
                logger.info("[测点2] IDR#%d 到达: 距推流启动 %.0fms, size=%d",
                            self._idr_count, since_start, len(data))
            self._enqueue_frame('idr', data)
        elif prefix == '3':
            data = base64.b64decode(content)
            self._enqueue_frame('p', data)
        elif prefix == 'H':
            # 心跳，忽略
            pass
        elif prefix == 'E':
            logger.error("[Rust] %s", content)
        else:
            # 其他行（Rust 调试日志等）记录用于调试
            logger.info("[rust-stderr] %s", line)

    def _enqueue_frame(self, frame_type: str, data: bytes) -> None:
        """关键帧友好的入队：队列满时丢最旧帧保最新，避免 QueueFull 崩掉推流。

        实时推流容忍丢中间 P 帧（客户端等到下个 IDR 自动恢复），
        但绝不能让生产侧因消费侧瞬时跟不上而阻塞或崩溃。
        """
        q = self._frame_queue
        if q is None:
            return
        try:
            q.put_nowait((frame_type, data))
        except asyncio.QueueFull:
            # 队列满：丢最旧一帧，给最新帧腾位（保最新，实时性优先于完整性）
            try:
                old_type, _old_data = q.get_nowait()
                q.put_nowait((frame_type, data))
                logger.info("[push] 队列满，丢旧 %s 帧入新 %s 帧 size=%d", old_type, frame_type, len(data))
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                # 竞态：被并发取空 / 再次满，尽力而为直接放；仍失败即放弃此帧
                try:
                    q.put_nowait((frame_type, data))
                except asyncio.QueueFull:
                    logger.warning("[push] 丢帧(队列仍满) %s size=%d", frame_type, len(data))

    def _handle_command(self, cmd: str):
        """处理控制命令"""
        if isinstance(cmd, bytes):
            cmd = cmd.decode("utf-8", errors="replace")
        if cmd.startswith("FPS="):
            logger.info("帧率已设置为: %s", cmd)

    async def get_frame(self):
        """获取一帧（异步）

        Returns:
            tuple: (frame_type, data) - 帧类型和二进制数据
                   frame_type: 'sps' | 'pps' | 'idr' | 'p'
        """
        if not self._frame_queue:
            return ('', None)
        try:
            frame_type, data = await asyncio.wait_for(
                self._frame_queue.get(),
                timeout=0.5
            )
            return (frame_type, data)
        except asyncio.TimeoutError:
            return ('', None)

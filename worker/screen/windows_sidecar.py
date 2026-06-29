"""Windows 屏幕侧车进程客户端。"""

from __future__ import annotations

import base64
import json
import logging
import os
import queue
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from common.packaging import get_base_dir
from common.utils import popen_cmd

logger = logging.getLogger(__name__)

_shared_client: "WindowsSidecarClient | None" = None
_shared_client_lock = threading.Lock()
_windows_managers: dict[str, "WindowsSidecarScreenManager"] = {}
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
        self._closed = False
        self._restart_count = 0
        self._max_restarts = 3

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
        if not self._proc or not self._proc.stderr:
            return
        for line in self._proc.stderr:
            line = line.rstrip("\n")
            if line:
                logger.info("[windows-sidecar] %s", line)

    def request(self, cmd: str, params: Optional[dict[str, Any]] = None, max_retries: int = 2) -> dict[str, Any]:
        """发送请求到 sidecar，支持失败重试"""
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
                    self._proc.stdin.write(payload + "\n")
                    self._proc.stdin.flush()

                    response_line = self._proc.stdout.readline()
                    if not response_line:
                        raise RuntimeError("sidecar 进程已退出")

                response = json.loads(response_line)
                if not response.get("ok"):
                    raise RuntimeError(response.get("error") or f"sidecar 命令失败: {cmd}")

                data = response.get("data")
                if isinstance(data, dict):
                    return data
                return {}
            except Exception as e:
                last_error = e
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

    def _ensure_alive(self) -> None:
        if self._proc and self._proc.poll() is not None:
            raise RuntimeError(f"sidecar 进程已退出，退出码={self._proc.returncode}")

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
) -> "WindowsSidecarScreenManager":
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

    def __init__(self, client: WindowsSidecarClient, session_id: str, codec: str, fps: int, bitrate: int = 2_000_000):
        self._client = client
        self._session_id = session_id
        self.codec = codec
        self._fps = fps
        self._bitrate = bitrate
        self._running = False
        self._h264_info: dict[str, Any] | None = None

    def start(self, codec: str = "jpeg", on_fallback: Optional[Any] = None) -> None:
        self.codec = codec
        self._running = True
        if codec == "h264":
            data = self._client.request(
                "stream_start",
                {
                    "session_id": self._session_id,
                    "fps": self._fps,
                    "bitrate": self._bitrate,
                    "profile": 66,
                },
            )
            self._h264_info = data
        else:
            self._h264_info = None

    def stop(self) -> None:
        if self.codec == "h264" and self._running:
            try:
                self._client.request("stream_stop", {"session_id": self._session_id})
            except Exception as exc:
                logger.warning("停止 Windows H264 推流失败: %s", exc)
        self._running = False

    async def get_frame_async(self) -> Optional[bytes]:
        if not self._running:
            return None

        if self.codec == "h264":
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

    def is_running(self) -> bool:
        return self._running

    def get_h264_info(self) -> Optional[dict[str, Any]]:
        return self._h264_info


class WindowsSidecarScreenManager:
    """Windows 屏幕管理器的新实现，直接连接 Rust sidecar。"""

    def __init__(self, session_id: str, monitor: int = 1, idle_fps: int = 1, active_fps: int = 15):
        self._client = get_shared_windows_sidecar_client()
        self._client.acquire()
        self._session_id = session_id
        self._monitor = monitor
        self._idle_fps = idle_fps
        self._active_fps = active_fps
        self._streamer: WindowsSidecarStreamer | None = None
        self._closed = False
        self._aligned_width: int | None = None
        self._aligned_height: int | None = None

        self._client.request(
            "session_open",
            {
                "session_id": self._session_id,
                "monitor": self._monitor,
                "idle_fps": self._idle_fps,
                "active_fps": self._active_fps,
            },
        )

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
        data = self._client.request(
            "snapshot",
            {
                "session_id": self._session_id,
                "format": "raw",
                "max_age_ms": 100,
            },
        )
        bgra_b64 = data.get("bgra_b64")
        if not bgra_b64:
            return bytearray()

        bgra_bytes = base64.b64decode(bgra_b64)
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

    def get_frame_jpeg(self) -> bytes:
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
            return b""
        return base64.b64decode(image_b64)

    def get_screen_size(self) -> tuple[int, int]:
        data = self._client.request(
            "snapshot",
            {
                "session_id": self._session_id,
                "format": "raw",
                "max_age_ms": 100,
            },
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
            data = self._client.request(
                "recording_start",
                {
                    "session_id": self._session_id,
                    "output_path": output_path,
                    "fps": fps,
                    "audio": audio,
                    "watermark": watermark,
                },
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
            data = self._client.request("recording_stop", {"session_id": self._session_id})
            return str(data.get("output_path") or "")
        except Exception as exc:
            logger.error("停止 Windows 录制失败: %s", exc)
            return ""

    def start_streaming(self, codec: str = "jpeg") -> WindowsSidecarStreamer:
        if self._streamer and self._streamer.codec != codec:
            self._streamer.stop()
            self._streamer = None

        if not self._streamer:
            self._streamer = WindowsSidecarStreamer(
                self._client,
                self._session_id,
                codec=codec,
                fps=self._active_fps,
            )
            self._streamer.start(codec=codec)
        return self._streamer

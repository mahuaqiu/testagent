"""
鸿蒙 uitest daemon 投屏帧流客户端。

agent.so 来自华为官方 Hypium（DevEco Testing）投屏插件，协议参考
hmdriver2/hmnextauto 的实现（不集成其代码）：
1. 推送 agent.so 到设备（MD5 比对，按需更新；新版不兼容时自动回退旧版）；
2. 重启 uitest start-daemon singleness 守护进程（监听设备 8012 端口）；
3. hdc fport 转发本地空闲端口到设备 8012；
4. socket 发送 Captures/startCaptureScreen 请求，成功后同一连接持续
   收取 JPEG 字节流，按 FFD8/FFD9 魔数切帧（约 10fps）。
"""

import hashlib
import json
import logging
import os
import socket
import threading
import time
from datetime import datetime
from typing import List, Optional, Tuple

from worker.platforms.harmony_hdc import HarmonyHdcWrapper

logger = logging.getLogger(__name__)

# uitest daemon 在设备侧监听的固定端口
UITEST_SERVICE_PORT = 8012
# agent.so 在设备上的固定部署路径
REMOTE_AGENT_PATH = "/data/local/tmp/agent.so"
# 内置 agent.so 资产目录（取自官方 Hypium 投屏插件）
AGENT_ASSETS_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "harmony_assets"
)
# 候选 agent 版本，新版优先；启动失败时自动回退旧版（两版协议一致）
AGENT_CANDIDATES = ("uitest_agent_v1.2.2.so", "uitest_agent_v1.1.0.so")
SOCKET_TIMEOUT = 20
# 单次 recv 的缓冲大小（JPEG 帧较大，用大缓冲减少系统调用）
RECV_BUFF_SIZE = 4096 * 1024

# JPEG 帧起止魔数
JPEG_START = b"\xff\xd8"
JPEG_END = b"\xff\xd9"


class HarmonyCaptureError(Exception):
    """uitest 帧流启动或运行异常。"""

    pass


def split_jpeg_frames(buffer: bytearray) -> Tuple[List[bytes], bytearray]:
    """
    按 JPEG 魔数从字节流缓冲区切出完整帧。

    Args:
        buffer: 累积的原始字节流缓冲区

    Returns:
        Tuple[List[bytes], bytearray]: (完整 JPEG 帧列表, 剩余未完整的缓冲)
    """
    frames: List[bytes] = []
    start_idx = buffer.find(JPEG_START)
    end_idx = buffer.find(JPEG_END, start_idx + 2 if start_idx != -1 else 0)
    while start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        frames.append(bytes(buffer[start_idx:end_idx + 2]))
        buffer = buffer[end_idx + 2:]
        start_idx = buffer.find(JPEG_START)
        end_idx = buffer.find(JPEG_END, start_idx + 2 if start_idx != -1 else 0)
    # 丢弃帧头之前的脏数据，避免缓冲无限膨胀
    if start_idx > 0:
        buffer = buffer[start_idx:]
    return frames, buffer


def find_free_port() -> int:
    """在本机分配一个空闲 TCP 端口。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class HarmonyScreenCapture:
    """
    鸿蒙 uitest daemon 帧流客户端。

    用法：
        capture = HarmonyScreenCapture(hdc)
        capture.start()               # 失败抛 HarmonyCaptureError
        frame = capture.get_frame(1)  # 最新 JPEG 帧
        capture.stop()
    """

    def __init__(self, hdc: HarmonyHdcWrapper, agent_path: Optional[str] = None):
        self.hdc = hdc
        # 显式指定 agent 路径时只用该版本，否则按 AGENT_CANDIDATES 依次尝试
        self.agent_path = agent_path
        self.local_port: Optional[int] = None
        self.sock: Optional[socket.socket] = None

        self._recv_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        # 只保留最新一帧，避免消费慢时积压导致画面延迟
        self._latest_frame: Optional[bytes] = None
        self._frame_cond = threading.Condition()
        self._frame_seq = 0

    @property
    def is_running(self) -> bool:
        """帧流接收线程是否仍在工作。"""
        return (
            self._recv_thread is not None
            and self._recv_thread.is_alive()
            and not self._stop_event.is_set()
        )

    # ------------------------------------------------------------------
    # 启动/停止
    # ------------------------------------------------------------------

    def start(self) -> None:
        """
        建立帧流。逐个尝试候选 agent 版本，全部失败抛
        HarmonyCaptureError（调用方降级轮询）。
        """
        if self.agent_path:
            agent_paths = [self.agent_path]
        else:
            agent_paths = [
                os.path.join(AGENT_ASSETS_DIR, name) for name in AGENT_CANDIDATES
            ]

        last_error: Optional[Exception] = None
        for agent_path in agent_paths:
            try:
                self._setup_device_agent(agent_path)
                self._restart_uitest_daemon()
                self._setup_fport()
                self._connect_sock()
                self._start_capture_screen()
                break
            except Exception as exc:
                self._cleanup()
                last_error = exc
                logger.warning(
                    f"agent {os.path.basename(agent_path)} 启动帧流失败: {exc}"
                )
        else:
            raise HarmonyCaptureError(
                f"启动 uitest 帧流失败（已尝试 {len(agent_paths)} 个 agent 版本）: {last_error}"
            ) from last_error

        self._stop_event.clear()
        self._recv_thread = threading.Thread(
            target=self._recv_worker,
            name=f"harmony-capture-{self.hdc.serial}",
            daemon=True,
        )
        self._recv_thread.start()
        logger.info(f"鸿蒙 uitest 帧流已启动: {self.hdc.serial} (fport tcp:{self.local_port})")

    def stop(self) -> None:
        """停止帧流并清理 socket/fport 资源。"""
        self._stop_event.set()
        if self.sock is not None:
            try:
                self._send_captures_msg("stopCaptureScreen")
            except Exception:
                pass
        if self._recv_thread is not None:
            self._recv_thread.join(timeout=3)
            self._recv_thread = None
        self._cleanup()
        logger.info(f"鸿蒙 uitest 帧流已停止: {self.hdc.serial}")

    def get_frame(self, timeout: float = 2.0) -> Optional[bytes]:
        """
        获取最新 JPEG 帧。

        Args:
            timeout: 等待新帧的超时时间（秒）

        Returns:
            Optional[bytes]: JPEG 帧数据，超时返回 None
        """
        with self._frame_cond:
            seq = self._frame_seq
            if self._latest_frame is not None:
                return self._latest_frame
            self._frame_cond.wait_for(
                lambda: self._frame_seq != seq or self._stop_event.is_set(),
                timeout=timeout,
            )
            return self._latest_frame

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _setup_device_agent(self, agent_path: str) -> None:
        """按 MD5 比对推送 agent.so 并赋执行权限。"""
        if not os.path.isfile(agent_path):
            raise HarmonyCaptureError(f"agent.so 资产不存在: {agent_path}")

        exists = "exists" in self.hdc.shell(
            f"[ -f {REMOTE_AGENT_PATH} ] && echo exists || echo missing"
        ).output
        if exists:
            local_md5 = self._local_md5(agent_path)
            remote_output = self.hdc.shell(f"md5sum {REMOTE_AGENT_PATH}").output.strip()
            remote_md5 = remote_output.split()[0] if remote_output else ""
            if local_md5 == remote_md5:
                self.hdc.shell(f"chmod +x {REMOTE_AGENT_PATH}")
                return
            self.hdc.shell(f"rm -f {REMOTE_AGENT_PATH}")

        if not self.hdc.push_file(agent_path, REMOTE_AGENT_PATH):
            raise HarmonyCaptureError("推送 agent.so 到设备失败")
        self.hdc.shell(f"chmod +x {REMOTE_AGENT_PATH}")
        logger.info(
            f"agent 已更新到设备: {self.hdc.serial} ({os.path.basename(agent_path)})"
        )

    @staticmethod
    def _local_md5(file_path: str) -> str:
        hash_md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()

    def _restart_uitest_daemon(self) -> None:
        """kill 残留 daemon 后重启（单例进程，残留会导致连接失败）。"""
        ps_output = self.hdc.shell("ps -ef").output
        for line in ps_output.splitlines():
            if "uitest start-daemon singleness" not in line:
                continue
            columns = line.split()
            if len(columns) > 1 and columns[1].isdigit():
                self.hdc.shell(f"kill -9 {columns[1]}")
                logger.debug(f"已终止残留 uitest daemon 进程: {columns[1]}")

        result = self.hdc.shell("uitest start-daemon singleness")
        if result.exit_code != 0:
            raise HarmonyCaptureError(
                f"启动 uitest daemon 失败: {result.output or result.error}"
            )
        time.sleep(0.5)

    def _setup_fport(self) -> None:
        """分配本地空闲端口并建立到设备 8012 的转发。"""
        self.local_port = find_free_port()
        if not self.hdc.fport(self.local_port, UITEST_SERVICE_PORT):
            self.local_port = None
            raise HarmonyCaptureError("建立 fport 端口转发失败")

    def _connect_sock(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(SOCKET_TIMEOUT)
        self.sock.connect(("127.0.0.1", self.local_port))

    def _send_captures_msg(self, api: str) -> None:
        """发送 Captures 请求（一行一条 JSON + 换行）。"""
        msg = {
            "module": "com.ohos.devicetest.hypiumApiHelper",
            "method": "Captures",
            "params": {"api": api, "args": []},
            "request_id": datetime.now().strftime("%Y%m%d%H%M%S%f"),
        }
        payload = json.dumps(msg, ensure_ascii=False, separators=(",", ":"))
        self.sock.sendall(payload.encode("utf-8") + b"\n")

    def _start_capture_screen(self) -> None:
        """发送 startCaptureScreen 并校验回复。"""
        self._send_captures_msg("startCaptureScreen")
        try:
            reply = self.sock.recv(1024).decode("utf-8", errors="ignore")
        except socket.timeout as exc:
            raise HarmonyCaptureError("startCaptureScreen 回复超时") from exc
        if "true" not in reply:
            raise HarmonyCaptureError(f"startCaptureScreen 被拒绝: {reply.strip()}")

    def _recv_worker(self) -> None:
        """持续收取 JPEG 字节流并按魔数切帧，只保留最新帧。"""
        buffer = bytearray()
        while not self._stop_event.is_set():
            try:
                chunk = self.sock.recv(RECV_BUFF_SIZE)
            except socket.timeout:
                continue
            except Exception as exc:
                winerror = getattr(exc, "winerror", None)
                expected_disconnect = isinstance(
                    exc,
                    (ConnectionAbortedError, ConnectionResetError, BrokenPipeError),
                ) or winerror in (10053, 10054)
                if expected_disconnect:
                    logger.debug("帧流连接已断开: %s", exc)
                elif not self._stop_event.is_set():
                    logger.warning(f"帧流接收异常: {exc}")
                break
            if not chunk:
                logger.warning("帧流连接被设备侧关闭")
                break
            buffer += chunk
            frames, buffer = split_jpeg_frames(buffer)
            if frames:
                with self._frame_cond:
                    self._latest_frame = frames[-1]
                    self._frame_seq += 1
                    self._frame_cond.notify_all()
        # 唤醒等待中的 get_frame，避免消费者卡住
        self._stop_event.set()
        with self._frame_cond:
            self._frame_cond.notify_all()

    def _cleanup(self) -> None:
        """关闭 socket 并移除 fport 规则。"""
        if self.sock is not None:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None
        if self.local_port is not None:
            try:
                self.hdc.fport_rm(self.local_port, UITEST_SERVICE_PORT)
            except Exception as exc:
                logger.warning(f"移除 fport 规则失败: {exc}")
            self.local_port = None

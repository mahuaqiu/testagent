"""
鸿蒙 uitest daemon 投屏帧流客户端。

agent.so 来自华为官方 Hypium（DevEco Testing）投屏插件，协议参考
hmdriver2/hmnextauto 的实现（不集成其代码）：
1. 推送 agent.so 到设备（MD5 比对，按需更新；新版不兼容时自动回退旧版）；
2. 按需重启 uitest start-daemon singleness 守护进程（监听设备 8012 端口；
   端口已在监听时跳过重启，避免打断同设备其他 uitest 自动化）；
3. hdc fport 转发本地空闲端口到设备 8012；
4. socket 发送 Captures/startCaptureScreen 请求（按行聚合 JSON 回复），
   成功后同一连接持续收取 JPEG 字节流，按 JPEG 段结构切帧（约 10fps）。
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
# 最新帧的可信时长：断流后超龄帧视为陈旧，get_frame 返回 None 让调用方
# 感知断流，而不是拿着旧画面继续 OCR/断言。
FRAME_STALE_SECONDS = 3.0

# JPEG 帧起止魔数
JPEG_START = b"\xff\xd8"
JPEG_END = b"\xff\xd9"


class HarmonyCaptureError(Exception):
    """uitest 帧流启动或运行异常。"""

    pass


def _jpeg_frame_end(data: bytearray, soi: int) -> int:
    """返回以 ``soi`` 开头 JPEG 的结束位置（EOI 之后，切片排他上界）。

    按标记段结构解析：普通段读段长跳过，SOS 段后的熵编码数据跳过
    FF00 填充与 RST 标记直至 EOI。数据不足返回 -1。
    """
    size = len(data)
    i = soi + 2  # 跳过 SOI
    while i < size:
        if data[i] != 0xFF:
            i += 1
            continue
        j = i
        while j < size and data[j] == 0xFF:
            j += 1
        if j >= size:
            return -1
        marker = data[j]
        pos = j + 1  # marker 后第一个字节
        if marker == 0xD9:  # EOI，无负载
            return pos
        if marker == 0x01 or 0xD0 <= marker <= 0xD7 or marker == 0xD8:
            i = pos  # 无长度标记（TEM/RST/异常嵌套 SOI）
            continue
        if pos + 2 > size:
            return -1
        seg_len = (data[pos] << 8) | data[pos + 1]
        if seg_len < 2:
            return -1  # 非法流
        if marker == 0xDA:  # SOS：段头之后为熵编码数据
            k = pos + seg_len
            while k + 1 < size:
                if data[k] == 0xFF:
                    nxt = data[k + 1]
                    if nxt == 0x00 or 0xD0 <= nxt <= 0xD7:
                        k += 2
                        continue
                    if nxt == 0xD9:
                        return k + 2
                    i = k  # 熵编码后出现其他标记，回到段解析
                    break
                k += 1
            else:
                return -1
            continue
        i = pos + seg_len
    return -1


def split_jpeg_frames(buffer: bytearray) -> Tuple[List[bytes], bytearray]:
    """
    从字节流缓冲区按 JPEG 段结构切出完整帧。

    不按 FFD9 魔数简单查找：JPEG 内嵌的 EXIF 缩略图自身也是完整 JPEG
    （含 FFD8/FFD9），魔数匹配会把带 EXIF 的帧提前截断。

    Args:
        buffer: 累积的原始字节流缓冲区

    Returns:
        Tuple[List[bytes], bytearray]: (完整 JPEG 帧列表, 剩余未完整的缓冲)
    """
    frames: List[bytes] = []
    size = len(buffer)
    pos = 0
    while True:
        soi = buffer.find(JPEG_START, pos)
        if soi < 0:
            pos = size  # 丢弃 SOI 之前的脏数据，避免缓冲无限膨胀
            break
        end = _jpeg_frame_end(buffer, soi)
        if end < 0:
            pos = soi  # 帧未完整到达，保留 SOI 起的数据等待后续字节
            break
        frames.append(bytes(buffer[soi:end]))
        pos = end
    return frames, buffer[pos:]


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
        self._latest_frame_at: float = 0.0
        self._stale_warned = False
        self._frame_cond = threading.Condition()
        self._frame_seq = 0
        # startCaptureScreen 回复行之后可能已带入 JPEG 帧流首段
        self._pending_stream_prefix = bytearray()
        # daemon 探测通过但后续启动失败时，下一次尝试强制重启 daemon
        self._force_daemon_restart = False

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

        with self._frame_cond:
            # 复用实例重开流时丢弃上一轮残留的流前缀
            self._pending_stream_prefix = bytearray()

        last_error: Optional[Exception] = None
        for agent_path in agent_paths:
            try:
                self._setup_device_agent(agent_path)
                self._restart_uitest_daemon()
                self._setup_fport()
                self._connect_sock()
                self._start_capture_screen()
                self._force_daemon_restart = False
                break
            except Exception as exc:
                self._cleanup()
                last_error = exc
                # daemon 端口在监听但握手失败，说明实例已僵死，下一轮强制重启
                self._force_daemon_restart = True
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
        sock = self.sock
        if sock is not None:
            try:
                self._send_captures_msg("stopCaptureScreen")
            except Exception:
                pass
            try:
                # 先关闭 socket，打断接收线程可能长达 20 秒的 recv 超时。
                sock.shutdown(socket.SHUT_RDWR)
            except (OSError, socket.error):
                pass
            try:
                sock.close()
            except OSError:
                pass
        self._cleanup()
        recv_thread = self._recv_thread
        if recv_thread is not None and recv_thread is not threading.current_thread():
            recv_thread.join(timeout=3)
            if recv_thread.is_alive():
                logger.warning("鸿蒙帧流接收线程未能及时退出: %s", self.hdc.serial)
        self._recv_thread = None
        with self._frame_cond:
            self._latest_frame = None
            self._frame_cond.notify_all()
        logger.info(f"鸿蒙 uitest 帧流已停止: {self.hdc.serial}")

    def get_frame(self, timeout: float = 2.0) -> Optional[bytes]:
        """
        获取最新 JPEG 帧。

        断流保护：接收线程已退出，或最新帧龄超过 FRAME_STALE_SECONDS
        时返回 None，让调用方感知断流，而不是拿旧画面继续 OCR/断言。

        Args:
            timeout: 等待新帧的超时时间（秒）

        Returns:
            Optional[bytes]: JPEG 帧数据，超时或已陈旧返回 None
        """
        with self._frame_cond:
            seq = self._frame_seq
            if self._latest_frame is not None:
                return self._fresh_frame_or_none()
            self._frame_cond.wait_for(
                lambda: self._frame_seq != seq or self._stop_event.is_set(),
                timeout=timeout,
            )
            return self._fresh_frame_or_none()

    def _fresh_frame_or_none(self) -> Optional[bytes]:
        """仅当流仍活跃且帧未超龄时返回最新帧，否则返回 None（需持有 _frame_cond）。"""
        frame = self._latest_frame
        if frame is None or not self.is_running:
            return None
        if time.monotonic() - self._latest_frame_at > FRAME_STALE_SECONDS:
            if not self._stale_warned:
                self._stale_warned = True
                logger.warning(
                    "鸿蒙帧流超过 %.1fs 无新帧，视为断流: %s",
                    FRAME_STALE_SECONDS,
                    self.hdc.serial,
                )
            return None
        return frame

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

    def _daemon_port_listening(self) -> bool:
        """探测设备侧 uitest 端口是否处于监听状态（探测失败按未监听处理）。"""
        try:
            output = self.hdc.shell(
                f"netstat -an 2>/dev/null | grep {UITEST_SERVICE_PORT} | grep -i listen"
            ).output
        except Exception as exc:
            logger.debug("netstat 探测 uitest 端口失败: %s", exc)
            return False
        return bool(output.strip())

    def _restart_uitest_daemon(self) -> None:
        """按需重启 uitest daemon。

        端口已在监听时跳过 kill/启动，避免打断同设备上其他 uitest 自动化
        （单例进程，kill -9 会连带伤及）；仅在上轮握手失败标记了
        _force_daemon_restart 时才强制重启。
        """
        if not self._force_daemon_restart and self._daemon_port_listening():
            logger.debug(
                "uitest daemon 端口 %d 已监听，跳过重启: %s",
                UITEST_SERVICE_PORT,
                self.hdc.serial,
            )
            return

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
        """发送 startCaptureScreen 并按行读取 JSON 回复。

        回复与后续 JPEG 帧流共用同一条连接：一次 recv 可能只带回半行回复，
        也可能带回完整回复 + 帧流首段。因此按行聚合，整行可解析为 JSON 才
        算回复完整；回复行之后的多余字节留存给接收线程作流前缀。
        """
        self._send_captures_msg("startCaptureScreen")
        chunks = bytearray()
        deadline = time.monotonic() + SOCKET_TIMEOUT
        while b"\n" not in chunks and time.monotonic() < deadline:
            try:
                data = self.sock.recv(4096)
            except socket.timeout as exc:
                raise HarmonyCaptureError("startCaptureScreen 回复超时") from exc
            if not data:
                raise HarmonyCaptureError("startCaptureScreen 连接被关闭，回复不完整")
            chunks += data
        if b"\n" not in chunks:
            raise HarmonyCaptureError("startCaptureScreen 回复超时（未收到完整一行）")
        line, _, remainder = bytes(chunks).partition(b"\n")
        reply = line.decode("utf-8", errors="ignore")
        try:
            json.loads(reply)
        except ValueError as exc:
            raise HarmonyCaptureError(
                f"startCaptureScreen 回复不完整或非法: {reply.strip()!r}"
            ) from exc
        if "true" not in reply:
            raise HarmonyCaptureError(f"startCaptureScreen 被拒绝: {reply.strip()}")
        if remainder:
            self._pending_stream_prefix += remainder

    def _recv_worker(self) -> None:
        """持续收取 JPEG 字节流并按段结构切帧，只保留最新帧。"""
        with self._frame_cond:
            # startCaptureScreen 回复行之后可能已带入帧流首段
            buffer = bytearray(self._pending_stream_prefix)
            self._pending_stream_prefix = bytearray()
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
                    self._latest_frame_at = time.monotonic()
                    self._stale_warned = False
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

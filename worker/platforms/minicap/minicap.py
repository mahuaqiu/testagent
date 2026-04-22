"""
Minicap 流式截图工具实现。

基于 Airtest minicap 流式模式适配，解决 SDK 30+ 模拟器单帧截图失败问题。
"""

import logging
import socket
import struct
import subprocess
import time
from io import BytesIO
from pathlib import Path
from typing import Optional

from PIL import Image

from common.utils import run_cmd, popen_cmd
from worker.discovery.android import get_adb_cmd

logger = logging.getLogger(__name__)

# stf_libs 资源目录路径
STFLIB_PATH = Path(__file__).parent / "static" / "stf_libs"


class MinicapError(Exception):
    """Minicap 截图异常"""
    pass


class Minicap:
    """Android minicap 流式截图工具（支持 SDK 30+）"""

    VERSION = 5
    DEVICE_DIR = "/data/local/tmp"
    CMD = "LD_LIBRARY_PATH=/data/local/tmp /data/local/tmp/minicap"
    RECV_TIMEOUT = 3.0  # socket 接收超时（秒）

    def __init__(self, udid: str):
        self.udid = udid
        self._installed = False
        self._abi: Optional[str] = None
        self._sdk: Optional[int] = None
        self._display_info: Optional[dict] = None

        # 流式截图相关
        self._proc: Optional[subprocess.Popen] = None
        self._socket: Optional[socket.socket] = None
        self._local_port: int = 0
        self._quirk_flag: int = 0
        self._stream_rotation: int = 0

    def _adb_shell(self, cmd: str, timeout: int = 30) -> str:
        """执行 adb shell 命令"""
        full_cmd = get_adb_cmd("-s", self.udid, "shell", cmd)
        result = run_cmd(full_cmd, timeout=timeout)
        if result.returncode != 0:
            raise MinicapError(f"ADB shell failed: {result.stderr}")
        return result.stdout.strip()

    def _adb_push(self, local_path: str, remote_path: str) -> None:
        """执行 adb push 命令"""
        full_cmd = get_adb_cmd("-s", self.udid, "push", local_path, remote_path)
        result = run_cmd(full_cmd, timeout=60)
        if result.returncode != 0:
            raise MinicapError(f"ADB push failed: {result.stderr}")

    def _get_device_info(self) -> tuple[str, int]:
        """获取设备 CPU ABI 和 SDK 版本"""
        if self._abi and self._sdk:
            return self._abi, self._sdk

        abi = self._adb_shell("getprop ro.product.cpu.abi")
        self._abi = abi
        sdk_str = self._adb_shell("getprop ro.build.version.sdk")
        self._sdk = int(sdk_str)
        logger.info(f"Device info: abi={abi}, sdk={self._sdk}")
        return self._abi, self._sdk

    def get_display_info(self) -> dict:
        """获取屏幕显示信息"""
        if self._display_info:
            return self._display_info

        import re

        # 使用 wm size 获取物理分辨率
        size_output = self._adb_shell("wm size")
        width, height = 1080, 1920
        if "Physical size:" in size_output:
            match = re.search(r"Physical size: (\d+)x(\d+)", size_output)
            if match:
                width, height = int(match.group(1)), int(match.group(2))

        # 解析旋转角度
        rotation = 0
        try:
            display_output = self._adb_shell("dumpsys display | grep 'mOrientation'")
            match = re.search(r"mOrientation=(\d+)", display_output)
            if match:
                rotation = int(match.group(1)) * 90
        except Exception:
            pass

        self._display_info = {"width": width, "height": height, "rotation": rotation}
        logger.info(f"Display info: {self._display_info}")
        return self._display_info

    def install(self) -> None:
        """安装 minicap 到设备"""
        if self._installed:
            logger.info("Minicap already installed, skipping")
            return

        abi, sdk = self._get_device_info()

        # 选择 minicap 二进制文件
        if sdk >= 16:
            binfile = "minicap"
        else:
            binfile = "minicap-nopie"

        # 推送 minicap 二进制
        minicap_bin_path = STFLIB_PATH / abi / binfile
        if not minicap_bin_path.exists():
            raise MinicapError(f"Minicap binary not found: {minicap_bin_path}")

        logger.info(f"Pushing minicap: {minicap_bin_path}")
        self._adb_push(str(minicap_bin_path), f"{self.DEVICE_DIR}/minicap")

        # 推送 minicap.so
        minicap_so_pattern = STFLIB_PATH / "minicap-shared" / "aosp" / "libs" / f"android-{sdk}" / abi / "minicap.so"
        if not minicap_so_pattern.exists():
            rel = self._adb_shell("getprop ro.build.version.release")
            minicap_so_pattern = STFLIB_PATH / "minicap-shared" / "aosp" / "libs" / f"android-{rel}" / abi / "minicap.so"

        if not minicap_so_pattern.exists():
            raise MinicapError(f"Minicap.so not found for sdk={sdk}, abi={abi}")

        logger.info(f"Pushing minicap.so: {minicap_so_pattern}")
        self._adb_push(str(minicap_so_pattern), f"{self.DEVICE_DIR}/minicap.so")

        # 设置执行权限
        self._adb_shell(f"chmod 755 {self.DEVICE_DIR}/minicap")
        self._adb_shell(f"chmod 755 {self.DEVICE_DIR}/minicap.so")

        self._installed = True
        logger.info("Minicap installation completed")

    def _setup_stream(self) -> None:
        """设置 minicap 流式截图服务器"""
        if self._proc and self._proc.poll() is None:
            return  # 已启动

        display_info = self.get_display_info()
        width = display_info["width"]
        height = display_info["height"]
        rotation = display_info["rotation"]

        # 设置端口转发
        self._local_port = self._find_free_port()
        device_port_name = f"minicap_{self.udid[-8:]}"

        forward_cmd = get_adb_cmd("-s", self.udid, "forward",
                                  f"tcp:{self._local_port}",
                                  f"localabstract:{device_port_name}")
        result = run_cmd(forward_cmd, timeout=10)
        if result.returncode != 0:
            raise MinicapError(f"ADB forward failed: {result.stderr}")

        # 构建 minicap 流式命令（-l 模式）
        params = f"{width}x{height}@{width}x{height}/{rotation}"
        cmd = f"{self.CMD} -n '{device_port_name}' -P {params} -l 2>&1"

        # 启动 minicap 服务器进程
        full_cmd = get_adb_cmd("-s", self.udid, "shell", cmd)
        self._proc = popen_cmd(
            full_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # 等待服务器启动
        self._wait_server_start()
        self._stream_rotation = rotation

        # 连接 socket
        self._connect_socket()

        logger.info(f"Minicap stream setup: port={self._local_port}")

    def _find_free_port(self) -> int:
        """查找可用端口"""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('127.0.0.1', 0))
            return s.getsockname()[1]

    def _wait_server_start(self, timeout: float = 5.0) -> None:
        """等待 minicap 服务器启动"""
        start_time = time.time()
        while time.time() - start_time < timeout:
            if self._proc.poll() is not None:
                raise MinicapError("Minicap server quit immediately")

            # 读取 stdout 检查 "Server start"
            try:
                line = self._proc.stdout.readline()
                if b"Server start" in line:
                    return
            except Exception:
                pass
            time.sleep(0.1)

        raise MinicapError("Minicap server setup timeout")

    def _connect_socket(self) -> None:
        """连接 minicap socket 并读取全局 header"""
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.settimeout(self.RECV_TIMEOUT)
        self._socket.connect(('127.0.0.1', self._local_port))

        # 读取全局 header (24 bytes)
        header = self._recv_all(24)
        if len(header) < 24:
            raise MinicapError(f"Invalid minicap header: {len(header)} bytes")

        global_headers = struct.unpack("<2B5I2B", header)
        logger.debug(f"Minicap global headers: {global_headers}")

        # 解析 quirk_flag
        self._quirk_flag = global_headers[-1]

    def _recv_all(self, size: int) -> bytes:
        """接收指定大小的数据"""
        data = b''
        while len(data) < size:
            chunk = self._socket.recv(size - len(data))
            if not chunk:
                break
            data += chunk
        return data

    def get_frame(self) -> bytes:
        """获取单帧（JPEG 格式）- 流式模式"""
        if not self._installed:
            raise MinicapError("Minicap not installed, call install() first")

        # 确保流已启动
        self._setup_stream()

        # 发送请求帧信号
        self._socket.send(b"1")

        # 读取帧大小 (4 bytes)
        header = self._recv_all(4)
        if len(header) < 4:
            raise MinicapError("Failed to read frame size")

        frame_size = struct.unpack("<I", header)[0]
        if frame_size <= 0:
            raise MinicapError(f"Invalid frame size: {frame_size}")

        # 读取帧数据
        frame_data = self._recv_all(frame_size)
        if len(frame_data) < frame_size:
            raise MinicapError(f"Incomplete frame: {len(frame_data)} < {frame_size}")

        # 验证 JPG 格式
        if not frame_data.startswith(b'\xff\xd8'):
            raise MinicapError("Invalid JPG format")

        return frame_data

    def get_screenshot_png(self) -> bytes:
        """获取 PNG 格式截图"""
        jpg_data = self.get_frame()
        img = Image.open(BytesIO(jpg_data))
        buffer = BytesIO()
        img.save(buffer, format="PNG")
        return buffer.getvalue()

    def stop_stream(self) -> None:
        """停止流式截图"""
        if self._socket:
            try:
                self._socket.close()
            except Exception:
                pass
            self._socket = None

        if self._proc:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=2)
            except Exception:
                pass
            self._proc = None

        # 移除端口转发
        if self._local_port:
            try:
                remove_cmd = get_adb_cmd("-s", self.udid, "forward",
                                         "--remove", f"tcp:{self._local_port}")
                run_cmd(remove_cmd, timeout=5)
            except Exception:
                pass
            self._local_port = 0

        logger.info("Minicap stream stopped")
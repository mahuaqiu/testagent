"""
Minicap 截图工具实现。

基于 airtest.core.android.cap_methods.minicap 适配，
使用纯 ADB 命令操作设备。
"""

import logging
import os
import re
import subprocess
from io import BytesIO
from pathlib import Path
from typing import Optional

from PIL import Image

logger = logging.getLogger(__name__)

# stf_libs 资源目录路径
STFLIB_PATH = Path(__file__).parent / "static" / "stf_libs"


class MinicapError(Exception):
    """Minicap 截图异常"""
    pass


class Minicap:
    """Android minicap 截图工具"""

    VERSION = 5
    DEVICE_DIR = "/data/local/tmp"
    CMD = "LD_LIBRARY_PATH=/data/local/tmp /data/local/tmp/minicap"

    def __init__(self, udid: str):
        self.udid = udid
        self._installed = False
        self._abi: Optional[str] = None
        self._sdk: Optional[int] = None
        self._display_info: Optional[dict] = None

    def _adb_shell(self, cmd: str, timeout: int = 30) -> str:
        """执行 adb shell 命令"""
        full_cmd = ["adb", "-s", self.udid, "shell", cmd]
        result = subprocess.run(
            full_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            raise MinicapError(f"ADB shell failed: {result.stderr}")
        return result.stdout.strip()

    def _adb_push(self, local_path: str, remote_path: str) -> None:
        """执行 adb push 命令"""
        full_cmd = ["adb", "-s", self.udid, "push", local_path, remote_path]
        result = subprocess.run(
            full_cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            raise MinicapError(f"ADB push failed: {result.stderr}")

    def _get_device_info(self) -> tuple[str, int]:
        """获取设备 CPU ABI 和 SDK 版本"""
        if self._abi and self._sdk:
            return self._abi, self._sdk

        # 获取 CPU ABI
        abi = self._adb_shell("getprop ro.product.cpu.abi")
        self._abi = abi

        # 获取 SDK 版本
        sdk_str = self._adb_shell("getprop ro.build.version.sdk")
        self._sdk = int(sdk_str)

        logger.info(f"Device info: abi={abi}, sdk={self._sdk}")
        return self._abi, self._sdk

    def get_display_info(self) -> dict:
        """获取屏幕显示信息"""
        if self._display_info:
            return self._display_info

        # 使用 wm size 和 wm density 获取信息
        size_output = self._adb_shell("wm size")

        # 解析物理分辨率
        width, height = 1080, 1920  # 默认值
        if "Physical size:" in size_output:
            match = re.search(r"Physical size: (\d+)x(\d+)", size_output)
            if match:
                width, height = int(match.group(1)), int(match.group(2))

        # 解析旋转角度（从 dumpsys display）
        rotation = 0
        try:
            display_output = self._adb_shell("dumpsys display | grep 'mOrientation'")
            match = re.search(r"mOrientation=(\d+)", display_output)
            if match:
                rotation = int(match.group(1)) * 90
        except Exception:
            pass

        self._display_info = {
            "width": width,
            "height": height,
            "rotation": rotation,
        }
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
        # 尝试按 SDK 版本匹配，若不存在则按 Release 版本
        minicap_so_pattern = STFLIB_PATH / "minicap-shared" / "aosp" / "libs" / f"android-{sdk}" / abi / "minicap.so"
        if not minicap_so_pattern.exists():
            # 尝试使用 Release 版本匹配
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

    def get_frame(self) -> bytes:
        """获取单帧截图（JPG格式）"""
        if not self._installed:
            raise MinicapError("Minicap not installed, call install() first")

        display_info = self.get_display_info()
        width = display_info["width"]
        height = display_info["height"]
        rotation = display_info["rotation"]

        # 构建 minicap 参数
        # -P {width}x{height}@{width}x{height}/{rotation} -s
        params = f"{width}x{height}@{width}x{height}/{rotation}"
        cmd = f"{self.CMD} -n 'worker_minicap' -P {params} -s 2>&1"

        # 执行命令获取截图
        full_cmd = ["adb", "-s", self.udid, "shell", cmd]
        result = subprocess.run(
            full_cmd,
            capture_output=True,
            timeout=30,
        )

        raw_data = result.stdout

        # 提取 JPG 数据（去除日志输出）
        # minicap 输出格式：日志信息 + JPG 数据
        jpg_marker = b"for JPG encoder"
        if jpg_marker in raw_data:
            jpg_data = raw_data.split(jpg_marker)[-1]
            # 去除换行符
            jpg_data = jpg_data.replace(b"\r\r\n", b"\n").replace(b"\r\n", b"\n")
        else:
            jpg_data = raw_data

        # 验证 JPG 格式
        if not jpg_data.startswith(b"\xff\xd8") or not jpg_data.endswith(b"\xff\xd9"):
            raise MinicapError(f"Invalid JPG format, got {len(jpg_data)} bytes")

        return jpg_data

    def get_screenshot_png(self) -> bytes:
        """获取 PNG 格式截图"""
        jpg_data = self.get_frame()
        img = Image.open(BytesIO(jpg_data))
        buffer = BytesIO()
        img.save(buffer, format="PNG")
        return buffer.getvalue()
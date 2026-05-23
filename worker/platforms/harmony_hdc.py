"""
鸿蒙 HDC 命令封装模块。

提供鸿蒙设备 HDC 命令的封装，参考 hmnextauto 项目实现。
"""

import logging
import os
import subprocess
import tempfile
import uuid
import re
import json
import shutil
from dataclasses import dataclass
from typing import Optional, Tuple, List, Dict, Union

from common.packaging import get_base_dir

logger = logging.getLogger(__name__)


# ============================================================================
# 数据类和异常类
# ============================================================================


@dataclass
class CommandResult:
    """命令执行结果。"""

    output: str
    error: str
    exit_code: int


class HarmonyError(Exception):
    """鸿蒙设备相关异常基类。"""

    pass


class DeviceNotFoundError(HarmonyError):
    """设备未找到异常。"""

    pass


class HdcCommandError(HarmonyError):
    """HDC 命令执行失败异常。"""

    pass


# ============================================================================
# 命令执行基础方法
# ============================================================================


def _execute_hdc_command(
    hdc_path: str, args: List[str], timeout: int = 30
) -> CommandResult:
    """
    执行 HDC 命令。

    Args:
        hdc_path: HDC 工具路径
        args: 命令参数列表
        timeout: 执行超时时间（秒）

    Returns:
        CommandResult: 命令执行结果
    """
    cmdline = [hdc_path] + args
    logger.debug(f"执行 HDC 命令: {' '.join(cmdline)}")

    try:
        process = subprocess.Popen(
            cmdline,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )
        output, error = process.communicate(timeout=timeout)
        output = output.decode("utf-8", errors="ignore")
        error = error.decode("utf-8", errors="ignore")
        exit_code = process.returncode

        # HDC 命令失败标识
        if "error:" in output.lower() or "[fail]" in output.lower():
            return CommandResult("", output, -1)

        return CommandResult(output, error, exit_code)

    except subprocess.TimeoutExpired:
        process.kill()
        output, error = process.communicate()
        return CommandResult("", "命令执行超时", -1)

    except Exception as e:
        return CommandResult("", str(e), -1)


def _find_hdc_path() -> Optional[str]:
    """
    查找 HDC 工具路径。

    查找顺序：
    1. tools/hdc/hdc.exe（优先）
    2. 系统 PATH 中的 hdc

    Returns:
        Optional[str]: HDC 工具路径，未找到则返回 None
    """
    # 优先查找 tools/hdc 目录
    base_dir = get_base_dir()
    tools_hdc_path = os.path.join(base_dir, "tools", "hdc", "hdc.exe")

    if os.path.isfile(tools_hdc_path):
        logger.info(f"使用 tools 目录中的 HDC: {tools_hdc_path}")
        return tools_hdc_path

    # 查找系统 PATH 中的 hdc
    # Windows 使用 where 命令，Linux/Mac 使用 which 命令
    try:
        if os.name == "nt":
            result = subprocess.run(
                ["where", "hdc"], capture_output=True, text=True, timeout=5
            )
        else:
            result = subprocess.run(
                ["which", "hdc"], capture_output=True, text=True, timeout=5
            )

        if result.returncode == 0 and result.stdout.strip():
            hdc_path = result.stdout.strip().splitlines()[0]
            logger.info(f"使用系统 PATH 中的 HDC: {hdc_path}")
            return hdc_path

    except Exception as e:
        logger.warning(f"查找系统 HDC 失败: {e}")

    logger.warning("未找到 HDC 工具")
    return None


def list_devices(hdc_path: Optional[str] = None) -> List[str]:
    """
    列出所有在线的鸿蒙设备。

    Args:
        hdc_path: HDC 工具路径（可选，默认自动查找）

    Returns:
        List[str]: 设备序列号列表

    Raises:
        HdcCommandError: HDC 命令执行失败
    """
    if hdc_path is None:
        hdc_path = _find_hdc_path()

    if hdc_path is None:
        raise HdcCommandError("未找到 HDC 工具")

    result = _execute_hdc_command(hdc_path, ["list", "targets"])

    if result.exit_code != 0:
        raise HdcCommandError(f"HDC 列出设备失败: {result.error}")

    devices = []
    if result.output:
        lines = result.output.strip().split("\n")
        for line in lines:
            line = line.strip()
            if line and not line.__contains__("Empty"):
                devices.append(line)

    return devices


# ============================================================================
# HarmonyHdcWrapper 类
# ============================================================================


class HarmonyHdcWrapper:
    """
    鸿蒙设备 HDC 命令封装类。

    提供鸿蒙设备的各种操作封装，包括：
    - 设备连接和状态检查
    - Shell 命令执行
    - 按键操作
    - 应用管理
    - 截图和布局获取
    - 性能监控
    """

    # 按键映射表（参考 hmnextauto KeyCode）
    KEY_MAP = {
        "HOME": 1,
        "BACK": 2,
        "POWER": 18,
        "VOLUME_UP": 16,
        "VOLUME_DOWN": 17,
        "VOLUME_MUTE": 22,
        "ENTER": 2054,
        "MENU": 2067,
        "DPAD_UP": 19,
        "DPAD_DOWN": 20,
        "DPAD_LEFT": 21,
        "DPAD_RIGHT": 22,
        "DPAD_CENTER": 23,
    }

    def __init__(self, serial: str, hdc_path: Optional[str] = None):
        """
        初始化 HDC 包装器。

        Args:
            serial: 设备序列号
            hdc_path: HDC 工具路径（可选，默认自动查找）

        Raises:
            HdcCommandError: 未找到 HDC 工具
            DeviceNotFoundError: 设备未在线
        """
        self.serial = serial

        # 查找 HDC 工具
        if hdc_path is None:
            self.hdc_path = _find_hdc_path()
        else:
            self.hdc_path = hdc_path

        if self.hdc_path is None:
            raise HdcCommandError("未找到 HDC 工具")

        # 检查设备在线状态
        if not self.is_online():
            raise DeviceNotFoundError(f"设备 [{self.serial}] 未在线")

        logger.info(f"已连接鸿蒙设备: {self.serial}")

    def _execute(self, args: List[str], timeout: int = 30) -> CommandResult:
        """
        执行带设备 ID 的 HDC 命令。

        Args:
            args: 命令参数列表
            timeout: 执行超时时间（秒）

        Returns:
            CommandResult: 命令执行结果
        """
        # 添加设备 ID 参数
        full_args = ["-t", self.serial] + args
        return _execute_hdc_command(self.hdc_path, full_args, timeout)

    def is_online(self) -> bool:
        """
        检查设备是否在线。

        Returns:
            bool: True 表示设备在线，False 表示离线
        """
        try:
            devices = list_devices(self.hdc_path)
            return self.serial in devices
        except Exception as e:
            logger.warning(f"检查设备在线状态失败: {e}")
            return False

    def shell(self, cmd: str, timeout: int = 30) -> CommandResult:
        """
        执行 Shell 命令。

        Args:
            cmd: Shell 命令字符串
            timeout: 执行超时时间（秒）

        Returns:
            CommandResult: 命令执行结果

        Note:
            命令会自动用双引号包裹，确保正确执行。
        """
        # 确保命令用双引号包裹
        if cmd[0] != '"':
            cmd = '"' + cmd
        if cmd[-1] != '"':
            cmd += '"'

        result = self._execute(["shell", cmd], timeout)

        if result.exit_code != 0:
            logger.warning(f"Shell 命令执行失败: {cmd}\n{result.output}\n{result.error}")

        return result
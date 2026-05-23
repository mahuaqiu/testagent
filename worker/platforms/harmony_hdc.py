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

        # HDC 命令失败标识 - 只记录警告，不改变返回值
        if "error:" in output.lower() or "[fail]" in output.lower():
            logger.warning(f"HDC 命令可能失败: {output.strip()}")

        return CommandResult(output, error, exit_code)

    except subprocess.TimeoutExpired:
        process.kill()
        try:
            output, error = process.communicate()
        except Exception:
            output, error = b"", b""
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
            if line and "Empty" not in line:
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
        if not cmd:
            return CommandResult("", "Empty command", -1)
        if not (cmd.startswith('"') and cmd.endswith('"')):
            cmd = f'"{cmd}"'

        result = self._execute(["shell", cmd], timeout)

        if result.exit_code != 0:
            logger.warning(f"Shell 命令执行失败: {cmd}\n{result.output}\n{result.error}")

        return result

    # ========================================================================
    # 截图和文件操作
    # ========================================================================

    def screenshot(self, local_path: str, method: str = "snapshot_display") -> bool:
        """
        截取屏幕并保存到本地。

        Args:
            local_path: 本地保存路径
            method: 截图方法
                - "snapshot_display": 使用 snapshot_display -f 命令（默认）
                - "uitest": 使用 uitest screenCap -p 命令

        Returns:
            bool: True 表示成功，False 表示失败
        """
        try:
            if method == "uitest":
                # 使用 uitest 截图
                remote_path = f"/data/local/tmp/screenshot_{uuid.uuid4().hex}.png"
                result = self.shell(f"uitest screenCap -p {remote_path}")

                if result.exit_code != 0 or "fail" in result.output.lower():
                    logger.error(f"uitest 截图失败: {result.output}")
                    return False

                # 拉取到本地
                pull_result = self.pull_file(remote_path, local_path)

                # 清理远程文件
                self.shell(f"rm {remote_path}")

                return pull_result
            else:
                # 使用 snapshot_display 截图
                result = self.shell(f"snapshot_display -f {local_path}")

                if result.exit_code != 0 or "fail" in result.output.lower():
                    logger.error(f"snapshot_display 截图失败: {result.output}")
                    return False

                return os.path.exists(local_path)

        except Exception as e:
            logger.error(f"截图失败: {e}")
            return False

    def pull_file(self, remote_path: str, local_path: str) -> bool:
        """
        从设备拉取文件到本地。

        Args:
            remote_path: 设备上的文件路径
            local_path: 本地保存路径

        Returns:
            bool: True 表示成功，False 表示失败
        """
        try:
            # 确保本地目录存在
            local_dir = os.path.dirname(local_path)
            if local_dir and not os.path.exists(local_dir):
                os.makedirs(local_dir, exist_ok=True)

            result = self._execute(["file", "recv", remote_path, local_path])

            if result.exit_code != 0:
                logger.error(f"拉取文件失败: {result.error}")
                return False

            return os.path.exists(local_path)

        except Exception as e:
            logger.error(f"拉取文件失败: {e}")
            return False

    def push_file(self, local_path: str, remote_path: str) -> bool:
        """
        推送本地文件到设备。

        Args:
            local_path: 本地文件路径
            remote_path: 设备上的目标路径

        Returns:
            bool: True 表示成功，False 表示失败
        """
        try:
            if not os.path.exists(local_path):
                logger.error(f"本地文件不存在: {local_path}")
                return False

            result = self._execute(["file", "send", local_path, remote_path])

            if result.exit_code != 0:
                logger.error(f"推送文件失败: {result.error}")
                return False

            return True

        except Exception as e:
            logger.error(f"推送文件失败: {e}")
            return False

    # ========================================================================
    # 点击和滑动
    # ========================================================================

    def tap(self, x: int, y: int) -> bool:
        """
        点击屏幕指定位置。

        Args:
            x: X 坐标
            y: Y 坐标

        Returns:
            bool: True 表示成功，False 表示失败
        """
        result = self.shell(f"uitest uiInput click {x} {y}")

        if result.exit_code != 0 or "fail" in result.output.lower():
            logger.error(f"点击失败: {result.output}")
            return False

        return True

    def double_tap(self, x: int, y: int) -> bool:
        """
        双击屏幕指定位置。

        Args:
            x: X 坐标
            y: Y 坐标

        Returns:
            bool: True 表示成功，False 表示失败
        """
        # 执行两次快速点击
        result1 = self.tap(x, y)
        if not result1:
            return False

        import time
        time.sleep(0.1)  # 短暂延迟

        result2 = self.tap(x, y)
        return result2

    def long_tap(self, x: int, y: int, duration: int = 1000) -> bool:
        """
        长按屏幕指定位置。

        Args:
            x: X 坐标
            y: Y 坐标
            duration: 长按时长（毫秒），默认 1000ms

        Returns:
            bool: True 表示成功，False 表示失败
        """
        result = self.shell(f"uitest uiInput click {x} {y} {duration}")

        if result.exit_code != 0 or "fail" in result.output.lower():
            logger.error(f"长按失败: {result.output}")
            return False

        return True

    def swipe(
        self, x1: int, y1: int, x2: int, y2: int, speed: int = 1000
    ) -> bool:
        """
        滑动屏幕。

        Args:
            x1: 起点 X 坐标
            y1: 起点 Y 坐标
            x2: 终点 X 坐标
            y2: 终点 Y 坐标
            speed: 滑动速度（范围 200-40000），默认 1000

        Returns:
            bool: True 表示成功，False 表示失败
        """
        # 验证 speed 范围
        if speed < 200 or speed > 40000:
            logger.error(f"滑动速度超出范围 [200, 40000]: {speed}")
            return False

        result = self.shell(f"uitest uiInput swipe {x1} {y1} {x2} {y2} {speed}")

        if result.exit_code != 0 or "fail" in result.output.lower():
            logger.error(f"滑动失败: {result.output}")
            return False

        return True

    def input_text_at(self, x: int, y: int, text: str) -> bool:
        """
        在指定坐标位置输入文本。

        Args:
            x: X 坐标
            y: Y 坐标
            text: 要输入的文本

        Returns:
            bool: True 表示成功，False 表示失败
        """
        # 先点击目标位置
        if not self.tap(x, y):
            logger.error("点击目标位置失败")
            return False

        # 使用 input text 命令输入文本
        result = self.shell(f"uitest uiInput inputText {x} {y} '{text}'")

        if result.exit_code != 0 or "fail" in result.output.lower():
            logger.error(f"输入文本失败: {result.output}")
            return False

        return True

    # ========================================================================
    # 按键操作
    # ========================================================================

    def send_key(self, key_code: int) -> bool:
        """
        发送按键事件。

        Args:
            key_code: 按键代码

        Returns:
            bool: True 表示成功，False 表示失败
        """
        result = self.shell(f"uitest uiInput keyEvent {key_code}")

        if result.exit_code != 0 or "fail" in result.output.lower():
            logger.error(f"发送按键失败: {result.output}")
            return False

        return True

    def press_key(self, key_name: str) -> bool:
        """
        按键（使用按键名）。

        Args:
            key_name: 按键名称（如 HOME, BACK, POWER 等）

        Returns:
            bool: True 表示成功，False 表示失败

        Raises:
            ValueError: 按键名称不存在
        """
        key_name_upper = key_name.upper()

        if key_name_upper not in self.KEY_MAP:
            raise ValueError(
                f"未知按键名称: {key_name}. 可用按键: {list(self.KEY_MAP.keys())}"
            )

        key_code = self.KEY_MAP[key_name_upper]
        return self.send_key(key_code)

    # ========================================================================
    # 屏幕控制
    # ========================================================================

    def wakeup(self) -> bool:
        """
        唤醒屏幕。

        Returns:
            bool: True 表示成功，False 表示失败
        """
        # 发送 POWER 键唤醒屏幕
        return self.press_key("POWER")

    def screen_state(self) -> str:
        """
        获取屏幕状态。

        Returns:
            str: 屏幕状态
                - "AWAKE": 屏幕亮起
                - "INACTIVE": 屏幕变暗但未关闭
                - "SLEEP": 屏幕关闭
        """
        result = self.shell("hidumper -s 10", timeout=10)

        if result.exit_code != 0:
            logger.warning(f"获取屏幕状态失败: {result.error}")
            return "UNKNOWN"

        # 解析输出，查找屏幕状态
        output = result.output.upper()
        if "AWAKE" in output:
            return "AWAKE"
        elif "INACTIVE" in output:
            return "INACTIVE"
        elif "SLEEP" in output:
            return "SLEEP"
        else:
            return "UNKNOWN"

    def is_screen_on(self) -> bool:
        """
        检查屏幕是否点亮。

        Returns:
            bool: True 表示屏幕点亮，False 表示屏幕关闭
        """
        state = self.screen_state()
        return state in ("AWAKE", "INACTIVE")

    # ========================================================================
    # 设备信息
    # ========================================================================

    def display_size(self) -> Tuple[int, int]:
        """
        获取屏幕分辨率。

        Returns:
            Tuple[int, int]: (宽度, 高度)
        """
        result = self.shell("hidumper -s 10", timeout=10)

        if result.exit_code != 0:
            logger.warning(f"获取屏幕分辨率失败: {result.error}")
            return (0, 0)

        # 解析输出，查找分辨率信息
        # 格式示例: "width: 1080, height: 1920" 或 "Display 0: 1080x1920"
        output = result.output

        # 尝试匹配 "width: X, height: Y" 格式
        match = re.search(r"width:\s*(\d+).*height:\s*(\d+)", output, re.IGNORECASE)
        if match:
            return (int(match.group(1)), int(match.group(2)))

        # 尝试匹配 "XxY" 格式
        match = re.search(r"(\d+)\s*x\s*(\d+)", output)
        if match:
            return (int(match.group(1)), int(match.group(2)))

        logger.warning("未能解析屏幕分辨率")
        return (0, 0)

    def model(self) -> str:
        """
        获取设备型号。

        Returns:
            str: 设备型号
        """
        result = self.shell("param get const.product.model")

        if result.exit_code != 0:
            logger.warning(f"获取设备型号失败: {result.error}")
            return ""

        return result.output.strip()

    def product_name(self) -> str:
        """
        获取产品名称。

        Returns:
            str: 产品名称
        """
        result = self.shell("param get const.product.name")

        if result.exit_code != 0:
            logger.warning(f"获取产品名称失败: {result.error}")
            return ""

        return result.output.strip()

    def sdk_version(self) -> str:
        """
        获取 SDK 版本。

        Returns:
            str: SDK 版本
        """
        result = self.shell("param get const.ohos.apiversion")

        if result.exit_code != 0:
            logger.warning(f"获取 SDK 版本失败: {result.error}")
            return ""

        return result.output.strip()

    def sys_version(self) -> str:
        """
        获取系统版本。

        Returns:
            str: 系统版本
        """
        result = self.shell("param get const.product.devicetype")

        if result.exit_code != 0:
            logger.warning(f"获取系统版本失败: {result.error}")
            return ""

        return result.output.strip()

    def device_info(self) -> Dict:
        """
        获取设备信息字典。

        Returns:
            Dict: 设备信息字典，包含型号、产品名称、SDK版本、系统版本等
        """
        return {
            "serial": self.serial,
            "model": self.model(),
            "product_name": self.product_name(),
            "sdk_version": self.sdk_version(),
            "sys_version": self.sys_version(),
            "display_size": self.display_size(),
            "screen_on": self.is_screen_on(),
            "screen_state": self.screen_state(),
        }
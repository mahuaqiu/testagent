"""
鸿蒙 HDC 命令封装模块。

提供鸿蒙设备 HDC 命令的封装，参考 hmnextauto 项目实现。
"""

import logging
import os
import subprocess
import tempfile
import time
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

    def _check_result(self, result: CommandResult, operation: str) -> bool:
        """
        统一检查命令执行结果。

        Args:
            result: 命令执行结果
            operation: 操作名称（用于日志）

        Returns:
            bool: True 表示成功，False 表示失败
        """
        if result.exit_code != 0 or "fail" in result.output.lower():
            logger.error(f"{operation}失败: {result.output}")
            return False
        return True

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
                - "snapshot_display": 使用 snapshot_display -f 命令（默认，快速）
                - "uitest": 使用 uitest screenCap -p 命令（高质量）

        Returns:
            bool: True 表示成功，False 表示失败
        """
        try:
            # 生成设备临时路径
            remote_path = f"/data/local/tmp/screenshot_{uuid.uuid4().hex}.jpeg"

            if method == "uitest":
                # 使用 uitest 截图（PNG 格式）
                remote_path = f"/data/local/tmp/screenshot_{uuid.uuid4().hex}.png"
                result = self.shell(f"uitest screenCap -p {remote_path}")

                if not self._check_result(result, "uitest 截图"):
                    return False
            else:
                # 使用 snapshot_display 截图（JPEG 格式，速度更快）
                result = self.shell(f"snapshot_display -f {remote_path}")

                if not self._check_result(result, "snapshot_display 截图"):
                    return False

            # 拉取到本地
            pull_result = self.pull_file(remote_path, local_path)

            # 清理远程文件
            rm_result = self.shell(f"rm -rf {remote_path}")
            if rm_result.exit_code != 0:
                logger.warning(f"清理远程截图文件失败: {rm_result.output}")

            return pull_result

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
        return self._check_result(result, "点击")

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
        # 验证 duration 参数
        if duration <= 0:
            logger.error(f"长按时长必须大于 0: {duration}")
            return False

        result = self.shell(f"uitest uiInput click {x} {y} {duration}")
        return self._check_result(result, "长按")

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
        return self._check_result(result, "滑动")

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
        return self._check_result(result, "输入文本")

    def input_text(self, text: str) -> bool:
        """
        输入文本（使用剪贴板粘贴方式）。

        注意：调用此方法前应确保输入框已获取焦点。

        Args:
            text: 要输入的文本

        Returns:
            bool: True 表示成功，False 表示失败
        """
        # 使用 clipboard 命令设置剪贴板内容
        # 然后模拟粘贴操作（Ctrl+V 或长按粘贴）
        # 鸿蒙通过 aa paste 命令粘贴剪贴板内容
        try:
            # 设置剪贴板内容（通过 param 或直接 shell 命令）
            # 鸿蒙暂时使用 uitest uiInput inputText 在坐标 (0, 0) 输入
            # 这需要在输入框已聚焦的情况下使用
            result = self.shell(f"uitest uiInput inputText 0 0 '{text}'")
            return self._check_result(result, "输入文本")
        except Exception as e:
            logger.error(f"输入文本失败: {e}")
            return False

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
        return self._check_result(result, "发送按键")

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
        result = self.shell("param get const.product.software.version")

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

    # ========================================================================
    # 应用管理
    # ========================================================================

    def install(self, hap_path: str) -> bool:
        """
        安装 HAP 应用。

        Args:
            hap_path: HAP 文件路径

        Returns:
            bool: True 表示成功，False 表示失败

        Raises:
            FileNotFoundError: HAP 文件不存在
        """
        if not os.path.exists(hap_path):
            raise FileNotFoundError(f"HAP 文件不存在: {hap_path}")

        logger.info(f"安装应用: {hap_path}")

        # 使用 hdc install 命令
        result = self._execute(["install", hap_path], timeout=120)

        if not self._check_result(result, "安装应用"):
            return False

        # 检查输出中是否包含成功标识
        if "success" in result.output.lower() or result.exit_code == 0:
            logger.info(f"应用安装成功: {hap_path}")
            return True

        return False

    def uninstall(self, package: str) -> bool:
        """
        卸载应用。

        Args:
            package: 应用包名

        Returns:
            bool: True 表示成功，False 表示失败
        """
        logger.info(f"卸载应用: {package}")

        # 使用 hdc uninstall 命令
        result = self._execute(["uninstall", package], timeout=60)

        if not self._check_result(result, "卸载应用"):
            return False

        # 检查输出中是否包含成功标识
        if "success" in result.output.lower() or result.exit_code == 0:
            logger.info(f"应用卸载成功: {package}")
            return True

        return False

    def start_app(self, package: str, ability: str) -> bool:
        """
        启动应用。

        Args:
            package: 应用包名
            ability: Ability 名称

        Returns:
            bool: True 表示成功，False 表示失败
        """
        logger.info(f"启动应用: {package}/{ability}")

        # 使用 aa start -a ability -b package 命令
        result = self.shell(f"aa start -a {ability} -b {package}")

        if not self._check_result(result, "启动应用"):
            return False

        # 检查输出中是否包含成功标识
        # 成功的输出通常包含 "start ability successfully" 或类似标识
        if (
            "success" in result.output.lower()
            or "successfully" in result.output.lower()
            or result.exit_code == 0
        ):
            logger.info(f"应用启动成功: {package}/{ability}")
            return True

        return False

    def stop_app(self, package: str) -> bool:
        """
        强制停止应用。

        Args:
            package: 应用包名

        Returns:
            bool: True 表示成功，False 表示失败
        """
        logger.info(f"强制停止应用: {package}")

        # 使用 aa force-stop 命令
        result = self.shell(f"aa force-stop {package}")

        if not self._check_result(result, "强制停止应用"):
            return False

        logger.info(f"应用已停止: {package}")
        return True

    def clear_app(self, package: str) -> bool:
        """
        清除应用数据。

        Args:
            package: 应用包名

        Returns:
            bool: True 表示成功，False 表示失败
        """
        logger.info(f"清除应用数据: {package}")

        # 使用 bm clean 命令
        result = self.shell(f"bm clean -n {package}")

        if not self._check_result(result, "清除应用数据"):
            return False

        logger.info(f"应用数据已清除: {package}")
        return True

    def list_apps(self, include_system: bool = False) -> List[str]:
        """
        获取已安装应用列表。

        Args:
            include_system: 是否包含系统应用，默认 False

        Returns:
            List[str]: 应用包名列表
        """
        logger.debug("获取已安装应用列表")

        # 使用 bm dump -a 列出所有应用
        result = self.shell("bm dump -a", timeout=30)

        if result.exit_code != 0:
            logger.error(f"获取应用列表失败: {result.error}")
            return []

        # 解析输出，提取包名
        packages = []
        output = result.output

        # 包名格式示例：
        # "com.example.app"
        # 或者更复杂的输出格式
        for line in output.split("\n"):
            line = line.strip()
            # 匹配包名格式（通常为 com.xxx.xxx 格式）
            if re.match(r"^[a-zA-Z][\w\.]*$", line):
                # 如果不包含系统应用，需要过滤
                # 系统应用通常在特定路径下，这里简化处理
                if include_system or not self._is_system_package(line, output):
                    packages.append(line)

        logger.info(f"找到 {len(packages)} 个应用")
        return packages

    def _is_system_package(self, package: str, dump_output: str) -> bool:
        """
        检查是否为系统应用（内部辅助方法）。

        Args:
            package: 包名
            dump_output: bm dump 命令的完整输出

        Returns:
            bool: True 表示系统应用，False 表示第三方应用
        """
        # 简化的系统应用判断逻辑
        # 通常系统应用的包名包含特定前缀
        system_prefixes = [
            "com.huawei.",
            "com.android.",
            "com.ohos.",
            "ohos.",
            "system_",
        ]

        for prefix in system_prefixes:
            if package.startswith(prefix):
                return True

        return False

    def has_app(self, package: str) -> bool:
        """
        检查应用是否安装。

        Args:
            package: 应用包名

        Returns:
            bool: True 表示已安装，False 表示未安装
        """
        logger.debug(f"检查应用是否安装: {package}")

        # 使用 bm dump -n 查询指定包名
        result = self.shell(f"bm dump -n {package}")

        # 如果命令成功执行且输出不为空，则应用已安装
        if result.exit_code == 0 and result.output.strip():
            return True

        return False

    def current_app(self) -> Tuple[Optional[str], Optional[str]]:
        """
        获取当前前台应用。

        通过解析 aa dump -l 输出，查找 FOREGROUND 状态的 mission。

        Returns:
            Tuple[Optional[str], Optional[str]]: (包名, Ability名称)
                如果未找到前台应用，返回 (None, None)
        """
        logger.debug("获取当前前台应用")

        # 使用 aa dump -l 查看任务列表
        result = self.shell("aa dump -l", timeout=10)

        if result.exit_code != 0:
            logger.warning(f"获取前台应用失败: {result.error}")
            return (None, None)

        # 解析输出，查找 FOREGROUND 状态的 mission
        # 输出格式示例：
        # Mission ID: #1
        #   BundleName: com.example.app
        #   AbilityName: MainAbility
        #   State: FOREGROUND
        output = result.output
        lines = output.split("\n")

        # 先收集所有 mission 块的信息，再查找 FOREGROUND 状态
        missions = []
        current_mission = {}

        for line in lines:
            line = line.strip()

            # 检测新的 mission 开始
            if "Mission ID" in line or "mission ID" in line:
                # 保存之前的 mission（如果有）
                if current_mission:
                    missions.append(current_mission)
                # 开始新的 mission 块
                current_mission = {}

            # 收集字段信息
            if "BundleName:" in line or "bundleName:" in line:
                current_mission["bundle"] = line.split(":")[-1].strip()
            elif "AbilityName:" in line or "abilityName:" in line:
                current_mission["ability"] = line.split(":")[-1].strip()
            elif "State:" in line or "state:" in line:
                current_mission["state"] = line.split(":")[-1].strip()

        # 保存最后一个 mission
        if current_mission:
            missions.append(current_mission)

        # 查找 FOREGROUND 状态的 mission
        for mission in missions:
            if mission.get("state") == "FOREGROUND":
                current_package = mission.get("bundle")
                current_ability = mission.get("ability")
                if current_package and current_ability:
                    logger.info(f"当前前台应用: {current_package}/{current_ability}")
                    return (current_package, current_ability)

        logger.warning("未找到前台应用")
        return (None, None)
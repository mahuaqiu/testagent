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
import shlex
from dataclasses import dataclass
from typing import Optional, Tuple, List, Dict, Union

from common.packaging import get_base_dir
from common.utils import popen_cmd, run_cmd
from worker.platforms.harmony_keycodes import HARMONY_KEY_MAP

logger = logging.getLogger(__name__)


def classify_harmony_device(properties: Dict[str, str]) -> str:
    """按属性优先级和规范值判断鸿蒙设备形态。"""
    mobile_values = {"phone", "tablet", "watch", "wearable", "mobile"}
    pc_values = {"pc", "desktop", "laptop", "notebook", "computer", "2in1", "2-in-1"}
    for key in (
        "const.product.devicetype",
        "const.product.type",
        "const.product.device_type",
        "const.product.form",
        "const.product.family",
    ):
        value = properties.get(key, "").strip().lower().replace("_", "-")
        if value in mobile_values:
            return "mobile"
        if value in pc_values:
            return "pc"
    return "unknown"


def parse_harmony_display_size(output: str) -> Tuple[int, int]:
    """从不同版本的 hidumper 窗口信息中解析屏幕宽高。"""
    patterns = (
        # 手机 RenderService 输出：activeMode: 1260x2720, refreshrate=120
        r"activeMode\s*[:=]\s*(\d+)\s*[xX*×]\s*(\d+)",
        # PC 真机输出：render resolution=3120x2080
        r"render\s+resolution\s*[:=]\s*(\d+)\s*[xX*×]\s*(\d+)",
        r"(?:screen)?width\s*[:=]\s*(\d+).*?(?:screen)?height\s*[:=]\s*(\d+)",
        r"(?:resolution|display(?:\s+\d+)?)?\s*[:=]?\s*(\d+)\s*[xX*×]\s*(\d+)",
        r"(?:bounds|rect)\s*[:=]\s*[\[(]\s*0\s*[, ]+\s*0\s*[, ]+\s*(\d+)\s*[, ]+\s*(\d+)\s*[\])]",
    )
    for pattern in patterns:
        match = re.search(pattern, output, re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        width, height = int(match.group(1)), int(match.group(2))
        if width > 0 and height > 0:
            return (width, height)
    return (0, 0)


def parse_harmony_screen_state(output: str) -> str:
    """解析 RenderService screen 输出中的屏幕电源状态。"""
    match = re.search(r"powerStatus\s*=\s*POWER_STATUS_([A-Z_]+)", output, re.IGNORECASE)
    if match:
        state = match.group(1).upper()
        if state == "ON":
            return "AWAKE"
        if state == "OFF":
            return "SLEEP"
    upper = output.upper()
    for state in ("AWAKE", "INACTIVE", "SLEEP"):
        if state in upper:
            return state
    return "UNKNOWN"


def parse_harmony_lock_state(output: str) -> Optional[bool]:
    """解析 hidumper ScreenlockService 输出中的锁屏状态，无法判断返回 None。"""
    match = re.search(
        r"(?:is)?screen[_\s]?locked\b\s*[:=]?\s*(true|false|yes|no|1|0)",
        output,
        re.IGNORECASE,
    )
    if not match:
        return None
    return match.group(1).lower() in ("true", "yes", "1")


# ============================================================================
# 数据类和异常类
# ============================================================================


@dataclass
class CommandResult:
    """命令执行结果。"""

    output: str
    error: str
    exit_code: int


@dataclass
class HdcTarget:
    """HDC target 连接信息。"""

    udid: str
    connection_type: str = "unknown"
    status: str = "unknown"
    detail: str = ""

    @property
    def is_ready(self) -> bool:
        """判断 target 是否处于可执行状态。

        真机 HDC 输出的状态列是 Connected（如华为 MateBook 2in1），
        部分版本/模拟器是 Ready，两者都视为可用。
        """
        return self.status.lower() in {"ready", "connected"}


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
    hdc_path: str,
    args: List[str],
    timeout: int = 30,
    retries: int = 1,
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

    last_result = CommandResult("", "命令未执行", -1)
    for attempt in range(max(1, retries + 1)):
        process = None
        try:
            process = popen_cmd(
                cmdline,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
            )
            output, error = process.communicate(timeout=timeout)
            last_result = CommandResult(
                output.decode("utf-8", errors="ignore"),
                error.decode("utf-8", errors="ignore"),
                process.returncode,
            )
            combined = f"{last_result.output}\n{last_result.error}".lower()
            transient = any(
                marker in combined
                for marker in (
                    "timeout",
                    "temporarily",
                    "connection reset",
                    "service unavailable",
                )
            )
            if last_result.exit_code == 0 or not transient or attempt >= retries:
                return last_result
        except subprocess.TimeoutExpired:
            if process is not None:
                process.kill()
                try:
                    process.communicate()
                except Exception:
                    pass
            last_result = CommandResult("", "命令执行超时", -1)
            if attempt >= retries:
                return last_result
        except Exception as exc:
            last_result = CommandResult("", str(exc), -1)
            if attempt >= retries:
                return last_result
        time.sleep(0.2 * (attempt + 1))
    return last_result


def _find_hdc_path(configured_path: Optional[str] = None) -> Optional[str]:
    """
    查找 HDC 工具路径。

    查找顺序：
    1. tools/hdc/hdc.exe（优先）
    2. 系统 PATH 中的 hdc

    Returns:
        Optional[str]: HDC 工具路径，未找到则返回 None
    """
    def resolve_candidate(candidate: str) -> Optional[str]:
        """解析 hdc.exe、SDK 根目录或 command-line-tools 根目录。"""
        if not os.path.isabs(candidate):
            candidate = os.path.join(get_base_dir(), candidate)
        if os.path.isfile(candidate):
            return candidate
        if not os.path.isdir(candidate):
            return None

        candidates = (
            os.path.join(candidate, "hdc.exe"),
            os.path.join(candidate, "toolchains", "hdc.exe"),
            os.path.join(candidate, "sdk", "default", "openharmony", "toolchains", "hdc.exe"),
        )
        return next((path for path in candidates if os.path.isfile(path)), None)

    if configured_path:
        resolved = resolve_candidate(configured_path)
        if resolved:
            logger.info(f"使用配置中的 HDC: {resolved}")
            return resolved
        logger.warning(f"配置的 HDC 不存在或无法识别: {configured_path}")

    # 优先查找仓库内置 HDC。
    base_dir = get_base_dir()
    tools_hdc_path = os.path.join(base_dir, "tools", "hdc", "hdc.exe")

    if os.path.isfile(tools_hdc_path):
        logger.info(f"使用 tools 目录中的 HDC: {tools_hdc_path}")
        return tools_hdc_path

    # 支持通过 SDK 根目录环境变量使用 DevEco/OpenHarmony SDK 自带 HDC。
    # 环境变量既可以直接指向 hdc.exe，也可以指向 SDK 根目录。
    sdk_env_names = ("HDC_PATH", "OHOS_SDK_HOME", "HARMONY_SDK_HOME", "DEVECO_SDK_HOME")
    sdk_suffix = os.path.join("sdk", "default", "openharmony", "toolchains", "hdc.exe")
    for env_name in sdk_env_names:
        env_value = os.environ.get(env_name)
        if not env_value:
            continue
        env_candidate = resolve_candidate(env_value)
        if env_candidate is None:
            env_candidate = env_value if env_value.lower().endswith("hdc.exe") else os.path.join(env_value, sdk_suffix)
        if os.path.isfile(env_candidate):
            logger.info(f"使用 SDK 环境变量 {env_name} 中的 HDC: {env_candidate}")
            return env_candidate

    # 查找系统 PATH 中的 hdc
    # Windows 使用 where 命令，Linux/Mac 使用 which 命令
    try:
        if os.name == "nt":
            result = run_cmd(
                ["where", "hdc"], capture_output=True, text=True, timeout=5
            )
        else:
            result = run_cmd(
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


def _has_error_text(result: CommandResult, include_device_states: bool = True) -> bool:
    """识别退出码为 0 但实际失败的 HDC 输出。"""
    combined = f"{result.output}\n{result.error}".lower()
    markers = ["error:", "[fail]", "failed"]
    if include_device_states:
        markers.extend(("unauthorized", "offline"))
    return any(marker in combined for marker in markers)


def _quote_remote_shell_argument(value: str) -> str:
    """使用 POSIX shell 单引号安全引用远端参数。"""
    return "'" + value.replace("'", "'\"'\"'") + "'"


def parse_target_lines(output: str) -> List[HdcTarget]:
    """解析 hdc list targets -v 输出。"""
    targets: list[HdcTarget] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("[") or "empty" in line.lower():
            continue
        columns = re.split(r"\s+", line)
        if len(columns) < 2 or columns[0].lower() in {"serial", "target"}:
            continue
        targets.append(
            HdcTarget(
                udid=columns[0],
                connection_type=columns[1],
                status=columns[2] if len(columns) > 2 else "Ready",
                detail=line,
            )
        )
    return targets


def list_target_info(hdc_path: Optional[str] = None) -> List[HdcTarget]:
    """列出可用状态的 HDC target（排除 UART 串口，避免把 COM 口当设备）。"""
    hdc_path = _find_hdc_path(hdc_path)
    if hdc_path is None:
        raise HdcCommandError("未找到 HDC 工具")
    result = _execute_hdc_command(
        hdc_path,
        ["list", "targets", "-v"],
        retries=2,
    )
    if result.exit_code != 0 or _has_error_text(result, include_device_states=False):
        raise HdcCommandError(f"HDC 列出设备失败: {result.error or result.output}")
    return [
        target
        for target in parse_target_lines(result.output)
        if target.is_ready and target.connection_type.upper() != "UART"
    ]


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
    return [target.udid for target in list_target_info(hdc_path)]


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

    KEY_MAP = HARMONY_KEY_MAP

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
        return _execute_hdc_command(self.hdc_path, full_args, timeout, retries=1)

    def _check_result(self, result: CommandResult, operation: str) -> bool:
        """
        统一检查命令执行结果。

        Args:
            result: 命令执行结果
            operation: 操作名称（用于日志）

        Returns:
            bool: True 表示成功，False 表示失败
        """
        if result.exit_code != 0 or _has_error_text(result):
            logger.error(f"{operation}失败: {result.output or result.error}")
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
            命令作为单个 list 参数交给 subprocess，不能手工包裹双引号。
            Windows 下 list2cmdline 会自动加引号且被 hdc.exe 的 C runtime
            消费；若再手工包一层，literal 引号会透传到设备端，导致
            /bin/sh 把整段字符串当成单个命令名（inaccessible or not found）。
        """
        if not cmd:
            return CommandResult("", "Empty command", -1)
        # 兼容旧调用方：如果调用方已经把整条命令包了双引号，剥掉它。
        if len(cmd) >= 2 and cmd.startswith('"') and cmd.endswith('"'):
            cmd = cmd[1:-1].replace(chr(92) + chr(34), chr(34))

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

            if _has_error_text(result, include_device_states=False):
                logger.error(f"拉取文件失败: {result.error or result.output}")
                return False

            return os.path.isfile(local_path) and os.path.getsize(local_path) > 0

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

            if result.exit_code != 0 or _has_error_text(result, include_device_states=False):
                logger.error(f"推送文件失败: {result.error}")
                return False

            return True

        except Exception as e:
            logger.error(f"推送文件失败: {e}")
            return False

    # ========================================================================
    # 端口转发
    # ========================================================================

    def fport(self, local_port: int, remote_port: int) -> bool:
        """
        建立本地 TCP 端口到设备 TCP 端口的转发。

        Args:
            local_port: 本地端口
            remote_port: 设备端口

        Returns:
            bool: True 表示成功，False 表示失败
        """
        result = self._execute(
            ["fport", f"tcp:{local_port}", f"tcp:{remote_port}"]
        )
        # 成功输出形如 "Forwardport result:OK"。
        if result.exit_code != 0 or "ok" not in result.output.lower():
            logger.error(
                f"端口转发失败 tcp:{local_port} -> tcp:{remote_port}: "
                f"{result.output or result.error}"
            )
            return False
        return True

    def fport_rm(self, local_port: int, remote_port: int) -> bool:
        """
        移除端口转发规则。

        Args:
            local_port: 本地端口
            remote_port: 设备端口

        Returns:
            bool: True 表示成功，False 表示失败
        """
        result = self._execute(
            ["fport", "rm", f"tcp:{local_port}", f"tcp:{remote_port}"]
        )
        # 成功输出形如 "Remove forward ruler success, ruler:tcp:X tcp:Y"。
        if result.exit_code != 0 or "success" not in result.output.lower():
            logger.warning(
                f"移除端口转发失败 tcp:{local_port} -> tcp:{remote_port}: "
                f"{result.output or result.error}"
            )
            return False
        return True

    def fport_ls(self) -> List[str]:
        """
        列出当前设备的端口转发规则。

        Returns:
            List[str]: 转发规则行列表（已去掉空行和 [Empty] 提示）
        """
        result = self._execute(["fport", "ls"])
        if result.exit_code != 0:
            logger.warning(f"列出端口转发失败: {result.error or result.output}")
            return []
        rules = []
        for raw_line in result.output.splitlines():
            line = raw_line.strip()
            if not line or "empty" in line.lower():
                continue
            rules.append(line)
        return rules

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
        quoted_text = _quote_remote_shell_argument(text)
        result = self.shell(f"uitest uiInput inputText {x} {y} {quoted_text}")
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
            quoted_text = _quote_remote_shell_argument(text)
            result = self.shell(f"uitest uiInput inputText 0 0 {quoted_text}")
            return self._check_result(result, "输入文本")
        except Exception as e:
            logger.error(f"输入文本失败: {e}")
            return False

    def device_category(self) -> str:
        """根据系统属性判断设备形态，无法确认时返回 unknown。"""
        properties: Dict[str, str] = {}
        for key in (
            "const.product.devicetype",
            "const.product.type",
            "const.product.device_type",
            "const.product.form",
            "const.product.family",
        ):
            result = self.shell(f"param get {key}")
            value = result.output.strip()
            # param get 对不存在的键返回 exit_code=0 + 失败文案（errNum 106），需要跳过
            if result.exit_code == 0 and value and "fail" not in value.lower():
                properties[key] = value
        return classify_harmony_device(properties)

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
        # power-shell wakeup 幂等亮屏（POWER 键在亮屏状态会反向熄屏），失败再回退 POWER 键
        result = self.shell("power-shell wakeup")
        if result.exit_code == 0 and "fail" not in (result.output or "").lower():
            return True
        logger.warning("power-shell wakeup 失败，回退 POWER 键唤醒")
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
        result = self.shell("hidumper -s 10 -a screen", timeout=10)

        if result.exit_code != 0:
            logger.warning(f"获取屏幕状态失败: {result.error}")
            return "UNKNOWN"

        return parse_harmony_screen_state(result.output)

    def is_screen_on(self) -> bool:
        """
        检查屏幕是否点亮。

        Returns:
            bool: True 表示屏幕点亮，False 表示屏幕关闭
        """
        state = self.screen_state()
        return state in ("AWAKE", "INACTIVE")

    def lock_state(self) -> Optional[bool]:
        """
        查询锁屏状态。

        通过 hidumper dump ScreenlockService（服务 ID 3704）解析
        screenLocked 字段；服务不可 dump 或解析失败时返回 None。

        Returns:
            Optional[bool]: True 已锁屏，False 未锁屏，None 无法判断
        """
        for dump_cmd in (
            "hidumper -s 3704 -a -all",
            "hidumper -s ScreenlockService -a -all",
        ):
            result = self.shell(dump_cmd, timeout=10)
            if result.exit_code != 0:
                continue
            state = parse_harmony_lock_state(result.output)
            if state is not None:
                return state
        logger.warning("未能从 ScreenlockService 解析锁屏状态")
        return None

    def is_locked(self) -> bool:
        """
        检查设备是否锁屏（锁屏服务查不到时退化为熄屏代理）。

        Returns:
            bool: True 表示锁屏，False 表示未锁屏
        """
        state = self.lock_state()
        if state is not None:
            return state
        return not self.is_screen_on()

    # ========================================================================
    # 设备信息
    # ========================================================================

    def display_size(self) -> Tuple[int, int]:
        """
        获取屏幕分辨率。

        优先用 RenderService 数字服务 ID（PC 真机已验证），失败时兼容
        按服务名 dump 的版本（手机输出 activeMode: WxH）。

        Returns:
            Tuple[int, int]: (宽度, 高度)
        """
        for dump_cmd in (
            "hidumper -s 10 -a screen",
            "hidumper -s RenderService -a screen",
        ):
            result = self.shell(dump_cmd, timeout=10)
            if result.exit_code != 0:
                logger.warning(f"获取屏幕分辨率失败({dump_cmd}): {result.error}")
                continue
            size = parse_harmony_display_size(result.output)
            if size != (0, 0):
                return size

        logger.warning("未能解析屏幕分辨率，保留未知值但不影响设备入池")
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

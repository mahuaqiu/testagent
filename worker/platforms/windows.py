"""
Windows 桌面平台执行引擎。

基于 pyautogui 实现，支持 OCR/图像识别定位。
"""

import io
import logging
import subprocess  # 用于 CalledProcessError 异常类型
import time
from typing import Any, Dict, Optional, Set

import pyautogui
import pyperclip

from common.utils import run_cmd, popen_cmd
from worker.platforms.base import PlatformManager
from worker.task import Action, ActionResult, ActionStatus
from worker.config import PlatformConfig
from worker.actions import ActionRegistry
from worker.tools import get_tools_dir

logger = logging.getLogger(__name__)

# 设置 pyautogui 参数
# 禁用 FAILSAFE：自动化测试场景中，鼠标可能因上次操作停留在角落，
# 该机制会阻止后续操作，干扰正常执行
pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.1


class WindowsPlatformManager(PlatformManager):
    """
    Windows 桌面平台管理器。

    使用 pyautogui 控制 Windows 桌面，支持 OCR/图像识别定位。
    """

    # Windows 平台特有动作
    SUPPORTED_ACTIONS: Set[str] = {"start_app", "stop_app"}

    def __init__(self, config: PlatformConfig, ocr_client=None):
        super().__init__(config, ocr_client)
        self.timeout = config.timeout
        self._current_monitor: int = 1  # 当前操作的显示器：1=主屏幕，2=副屏幕

    @property
    def platform(self) -> str:
        return "windows"

    # ========== 生命周期管理 ==========

    def start(self) -> None:
        """启动 Windows 平台。"""
        if self._started:
            return
        self._started = True
        logger.info("Windows platform started")

    def stop(self) -> None:
        """停止 Windows 平台。"""
        self._contexts.clear()
        self._started = False
        logger.info("Windows platform stopped")

    def is_available(self) -> bool:
        """检查平台是否可用。"""
        return self._started

    # ========== 上下文管理 ==========

    def create_context(self, device_id: Optional[str] = None, options: Optional[Dict] = None) -> Any:
        """创建桌面上下文（Windows 不需要特殊上下文）。"""
        logger.info("Windows context created (no-op)")
        return None

    def close_context(self, context: Any, close_session: bool = False) -> None:
        """关闭桌面上下文（Windows 不需要）。"""
        logger.info("Windows context closed (no-op)")

    # ========== 基础能力实现 ==========

    def click(self, x: int, y: int, duration: int = 0, context: Any = None) -> None:
        """点击指定坐标，支持长按。

        Args:
            x: X 坐标
            y: Y 坐标
            duration: 点击持续时间（毫秒），0=普通点击，>0=长按
            context: 执行上下文

        Note:
            Windows 平台使用 pyautogui，需要将截图相对坐标转换为全局坐标。
        """
        from worker.screen.monitor_utils import convert_to_global_coords
        monitor = self._current_monitor
        global_x, global_y = convert_to_global_coords(x, y, monitor)

        if duration > 0:
            duration_sec = duration / 1000.0
            pyautogui.moveTo(global_x, global_y)
            pyautogui.mouseDown()
            pyautogui.mouseUp(duration=duration_sec)
            logger.info(f"Long click at ({x}, {y}) -> global ({global_x}, {global_y}) for {duration}ms, monitor={monitor}")
        else:
            pyautogui.click(global_x, global_y)
            logger.info(f"Click at ({x}, {y}) -> global ({global_x}, {global_y}), monitor={monitor}")

    def double_click(self, x: int, y: int, context: Any = None) -> None:
        """双击指定坐标。

        Note:
            Windows 平台使用 pyautogui，需要将截图相对坐标转换为全局坐标。
        """
        from worker.screen.monitor_utils import convert_to_global_coords
        monitor = self._current_monitor
        global_x, global_y = convert_to_global_coords(x, y, monitor)
        pyautogui.doubleClick(global_x, global_y)
        logger.debug(f"Double click at ({x}, {y}) -> global ({global_x}, {global_y})")

    def move(self, x: int, y: int, context: Any = None) -> None:
        """移动鼠标到指定坐标。

        Note:
            Windows 平台使用 pyautogui，需要将截图相对坐标转换为全局坐标。
        """
        from worker.screen.monitor_utils import convert_to_global_coords
        monitor = self._current_monitor
        global_x, global_y = convert_to_global_coords(x, y, monitor)
        pyautogui.moveTo(global_x, global_y)
        logger.debug(f"Move to ({x}, {y}) -> global ({global_x}, {global_y})")

    def input_text(self, text: str, context: Any = None) -> None:
        """输入文本（使用剪贴板粘贴，支持特殊字符）。"""
        pyperclip.copy(text)
        pyautogui.hotkey('ctrl', 'v')

    def swipe(self, start_x: int, start_y: int, end_x: int, end_y: int,
              duration: int = 500, steps: Optional[int] = None, context: Any = None) -> None:
        """滑动/拖拽。

        Args:
            start_x: 走始 X 坐标
            start_y: 起始 Y 坐标
            end_x: 结束 X 坐标
            end_y: 结束 Y 坐标
            duration: 滑动持续时间（毫秒），默认 500ms
            steps: 滑动步数（pyautogui 不支持，参数忽略）
            context: 执行上下文

        Note:
            pyautogui 不支持 steps 参数，始终使用 duration 控制滑动时间。
            Windows 平台使用 pyautogui，需要将截图相对坐标转换为全局坐标。
        """
        from worker.screen.monitor_utils import convert_to_global_coords
        monitor = self._current_monitor
        global_start_x, global_start_y = convert_to_global_coords(start_x, start_y, monitor)
        global_end_x, global_end_y = convert_to_global_coords(end_x, end_y, monitor)

        duration_sec = duration / 1000.0
        pyautogui.moveTo(global_start_x, global_start_y)
        pyautogui.mouseDown()
        pyautogui.moveTo(global_end_x, global_end_y, duration=duration_sec)
        pyautogui.mouseUp()
        logger.debug(f"Swipe from ({start_x}, {start_y}) to ({end_x}, {end_y}) -> global ({global_start_x}, {global_start_y}) to ({global_end_x}, {global_end_y})")

    def press(self, key: str, context: Any = None) -> None:
        """按键。支持组合键，如 "ctrl+c"。"""
        keys = key.split("+")
        if len(keys) > 1:
            pyautogui.hotkey(*keys)
        else:
            pyautogui.press(key)

    def take_screenshot(self, context: Any = None) -> bytes:
        """获取截图。"""
        screenshot = pyautogui.screenshot()
        buffer = io.BytesIO()
        screenshot.save(buffer, format="PNG")
        return buffer.getvalue()

    def get_screenshot(self, context: Any) -> bytes:
        """获取当前屏幕截图（兼容旧接口）。"""
        return self.take_screenshot(context)

    # ========== 动作执行 ==========

    def execute_action(self, context: Any, action: Action) -> ActionResult:
        """执行动作。"""
        start_time = time.time()

        try:
            # 平台特有动作
            if action.action_type == "start_app":
                result = self._action_start_app(action)
            elif action.action_type == "stop_app":
                result = self._action_stop_app(action)
            else:
                # 使用 ActionRegistry 执行通用动作
                executor = ActionRegistry.get(action.action_type)
                if executor:
                    result = executor.execute(self, action, context)
                else:
                    result = ActionResult(
                        number=0,
                        action_type=action.action_type,
                        status=ActionStatus.FAILED,
                        error=f"Unknown action type: {action.action_type}",
                    )

            duration_ms = int((time.time() - start_time) * 1000)
            result.duration_ms = duration_ms
            return result

        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            return ActionResult(
                number=0,
                action_type=action.action_type,
                status=ActionStatus.FAILED,
                duration_ms=duration_ms,
                error=str(e),
            )

    # ========== 平台特有动作实现 ==========

    def _action_start_app(self, action: Action) -> ActionResult:
        """启动应用。"""
        app_path = action.app_path or action.value
        if not app_path:
            return ActionResult(
                number=0,
                action_type="start_app",
                status=ActionStatus.FAILED,
                error="app_path is required",
            )

        # 构建 PowerShell 脚本路径
        tools_dir = get_tools_dir()
        script_path = f"{tools_dir}/start_app.ps1"

        # 构建 PowerShell 命令
        restart_param = "$true" if action.restart else "$false"
        # 转义路径中的特殊字符（如空格）
        escaped_path = app_path.replace("'", "''")
        cmd = f"powershell -ExecutionPolicy Bypass -File \"{script_path}\" -AppPath \"{escaped_path}\" -Restart {restart_param}"

        try:
            result = run_cmd(cmd, shell=True, timeout=10)

            if result.returncode == 0:
                output_msg = result.stdout.strip() if result.stdout else f"Started: {app_path}"
                return ActionResult(
                    number=0,
                    action_type="start_app",
                    status=ActionStatus.SUCCESS,
                    output=output_msg,
                )
            else:
                error_msg = result.stderr.strip() if result.stderr else f"Script failed with exit code {result.returncode}"
                return ActionResult(
                    number=0,
                    action_type="start_app",
                    status=ActionStatus.FAILED,
                    error=error_msg,
                )

        except Exception as e:
            return ActionResult(
                number=0,
                action_type="start_app",
                status=ActionStatus.FAILED,
                error=str(e),
            )

    def _action_stop_app(self, action: Action) -> ActionResult:
        """关闭应用。"""
        app_name = action.value
        if not app_name:
            return ActionResult(
                number=0,
                action_type="stop_app",
                status=ActionStatus.FAILED,
                error="app_name is required",
            )

        try:
            run_cmd(["taskkill", "/IM", app_name, "/F"], check=True)
            return ActionResult(
                number=0,
                action_type="stop_app",
                status=ActionStatus.SUCCESS,
                output=f"Stopped: {app_name}",
            )
        except subprocess.CalledProcessError as e:
            return ActionResult(
                number=0,
                action_type="stop_app",
                status=ActionStatus.FAILED,
                error=f"Failed to stop app: {e}",
            )
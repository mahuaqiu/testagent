"""
Mac 桌面平台执行引擎。

基于 pyautogui 实现，支持 OCR/图像识别定位。
"""

import io
import logging
import subprocess  # 用于 CalledProcessError 异常类型
import time
from typing import Any, Dict, Optional, Set

import pyautogui

from common.utils import run_cmd
from worker.platforms.base import PlatformManager
from worker.task import Action, ActionResult, ActionStatus
from worker.config import PlatformConfig
from worker.actions import ActionRegistry

logger = logging.getLogger(__name__)

# 设置 pyautogui 安全措施
pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.1


class MacPlatformManager(PlatformManager):
    """
    Mac 桌面平台管理器。

    使用 pyautogui 控制 macOS 桌面，支持 OCR/图像识别定位。
    """

    # Mac 平台特有动作
    SUPPORTED_ACTIONS: Set[str] = {"start_app", "stop_app"}

    def __init__(self, config: PlatformConfig, ocr_client=None):
        super().__init__(config, ocr_client)
        self.timeout = config.timeout

    @property
    def platform(self) -> str:
        return "mac"

    # ========== 生命周期管理 ==========

    def start(self) -> None:
        """启动 Mac 平台。"""
        if self._started:
            return
        self._started = True
        logger.info("Mac platform started")

    def stop(self) -> None:
        """停止 Mac 平台。"""
        self._contexts.clear()
        self._started = False
        logger.info("Mac platform stopped")

    def is_available(self) -> bool:
        """检查平台是否可用。"""
        return self._started

    # ========== 上下文管理 ==========

    def create_context(self, device_id: Optional[str] = None, options: Optional[Dict] = None) -> Any:
        """创建桌面上下文（Mac 不需要特殊上下文）。"""
        logger.info("Mac context created (no-op)")
        return None

    def close_context(self, context: Any, close_session: bool = False) -> None:
        """关闭桌面上下文（Mac 不需要）。"""
        logger.info("Mac context closed (no-op)")

    # ========== 基础能力实现 ==========

    def click(self, x: int, y: int, context: Any = None) -> None:
        """点击指定坐标。"""
        pyautogui.click(x, y)

    def double_click(self, x: int, y: int, context: Any = None) -> None:
        """双击指定坐标。"""
        pyautogui.doubleClick(x, y)

    def move(self, x: int, y: int, context: Any = None) -> None:
        """移动鼠标到指定坐标。"""
        pyautogui.moveTo(x, y)

    def input_text(self, text: str, context: Any = None) -> None:
        """输入文本。"""
        pyautogui.write(text)

    def swipe(self, start_x: int, start_y: int, end_x: int, end_y: int, context: Any = None) -> None:
        """滑动/拖拽。"""
        pyautogui.moveTo(start_x, start_y)
        pyautogui.mouseDown()
        pyautogui.moveTo(end_x, end_y, duration=0.5)
        pyautogui.mouseUp()

    def press(self, key: str, context: Any = None) -> None:
        """按键。支持组合键，如 "command+c"。"""
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
        app_name = action.app_path or action.value
        if not app_name:
            return ActionResult(
                number=0,
                action_type="start_app",
                status=ActionStatus.FAILED,
                error="app_name is required",
            )

        # macOS 使用 open 命令启动应用
        run_cmd(["open", "-a", app_name])
        time.sleep(2)  # 等待应用启动

        return ActionResult(
            number=0,
            action_type="start_app",
            status=ActionStatus.SUCCESS,
            output=f"Started: {app_name}",
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
            run_cmd(
                ["osascript", "-e", f'tell app "{app_name}" to quit'],
                check=True,
            )
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
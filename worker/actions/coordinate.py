"""
坐标类 Action 执行器。

包含所有基于坐标的动作：click, input, swipe, press, screenshot, wait。
"""

import base64
import time
import logging
from typing import Optional, TYPE_CHECKING

from common.utils import compress_image_to_jpeg
from worker.task import Action, ActionResult, ActionStatus
from worker.actions.base import BaseActionExecutor

if TYPE_CHECKING:
    from worker.platforms.base import PlatformManager

logger = logging.getLogger(__name__)


class ClickAction(BaseActionExecutor):
    """坐标点击。"""

    name = "click"

    def execute(self, platform: "PlatformManager", action: Action, context: Optional[object] = None) -> ActionResult:
        # 设置执行层级（Web 平台专用）
        self._set_level(platform, action)

        if action.x is None or action.y is None:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error="x and y coordinates are required",
            )

        # 获取点击持续时间（毫秒）
        click_duration = action.click_duration or 0

        platform.click(action.x, action.y, duration=click_duration, context=context)

        if click_duration > 0:
            output = f"Long clicked at ({action.x}, {action.y}) for {click_duration}ms"
        else:
            output = f"Clicked at ({action.x}, {action.y})"

        return ActionResult(
            number=0,
            action_type=self.name,
            status=ActionStatus.SUCCESS,
            output=output,
        )


class DoubleClickAction(BaseActionExecutor):
    """坐标双击。"""

    name = "double_click"

    def execute(self, platform: "PlatformManager", action: Action, context: Optional[object] = None) -> ActionResult:
        # 设置执行层级（Web 平台专用）
        self._set_level(platform, action)

        if action.x is None or action.y is None:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error="x and y coordinates are required",
            )

        # 应用偏移
        x, y = self._apply_offset(action.x, action.y, action.offset)

        platform.double_click(x, y, context)

        return ActionResult(
            number=0,
            action_type=self.name,
            status=ActionStatus.SUCCESS,
            output=f"Double clicked at ({x}, {y})",
        )


class MoveAction(BaseActionExecutor):
    """坐标移动。"""

    name = "move"

    def execute(self, platform: "PlatformManager", action: Action, context: Optional[object] = None) -> ActionResult:
        # 设置执行层级（Web 平台专用）
        self._set_level(platform, action)

        if action.x is None or action.y is None:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error="x and y coordinates are required",
            )

        # 应用偏移
        x, y = self._apply_offset(action.x, action.y, action.offset)

        try:
            platform.move(x, y, context)
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.SUCCESS,
                output=f"Moved to ({x}, {y})",
            )
        except NotImplementedError as e:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error=str(e),
            )


class InputAction(BaseActionExecutor):
    """坐标输入。"""

    name = "input"

    def execute(self, platform: "PlatformManager", action: Action, context: Optional[object] = None) -> ActionResult:
        # 设置执行层级（Web 平台专用）
        self._set_level(platform, action)

        if action.x is None or action.y is None:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error="x and y coordinates are required",
            )

        # 点击（普通点击，duration=0）
        platform.click(action.x, action.y, duration=0, context=context)

        # 输入
        if action.text:
            platform.input_text(action.text, context)

        return ActionResult(
            number=0,
            action_type=self.name,
            status=ActionStatus.SUCCESS,
            output=f"Input at ({action.x}, {action.y})",
        )


class DragAction(BaseActionExecutor):
    """拖拽（与 swipe 功能相同，语义化命名）。"""

    name = "drag"

    def execute(self, platform: "PlatformManager", action: Action, context: Optional[object] = None) -> ActionResult:
        # 设置执行层级（Web 平台专用）
        self._set_level(platform, action)

        if action.x is None or action.y is None:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error="Start coordinates are required",
            )

        end_x = action.end_x if action.end_x is not None else action.x
        end_y = action.end_y if action.end_y is not None else action.y
        duration = action.duration or 500  # 默认 500ms
        steps = action.steps  # 默认由平台决定

        platform.swipe(action.x, action.y, end_x, end_y, duration=duration, steps=steps, context=context)

        if steps is not None:
            output = f"Dragged from ({action.x}, {action.y}) to ({end_x}, {end_y}) with steps={steps}"
        else:
            output = f"Dragged from ({action.x}, {action.y}) to ({end_x}, {end_y}) in {duration}ms"

        return ActionResult(
            number=0,
            action_type=self.name,
            status=ActionStatus.SUCCESS,
            output=output,
        )


class SwipeAction(BaseActionExecutor):
    """滑动/拖拽。"""

    name = "swipe"

    def execute(self, platform: "PlatformManager", action: Action, context: Optional[object] = None) -> ActionResult:
        # 设置执行层级（Web 平台专用）
        self._set_level(platform, action)

        if action.x is None or action.y is None:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error="Start coordinates are required",
            )

        end_x = action.end_x if action.end_x is not None else action.x
        end_y = action.end_y if action.end_y is not None else action.y
        duration = action.duration or 500  # 默认 500ms
        steps = action.steps  # 默认由平台决定

        platform.swipe(action.x, action.y, end_x, end_y, duration=duration, steps=steps, context=context)

        if steps is not None:
            output = f"Swiped from ({action.x}, {action.y}) to ({end_x}, {end_y}) with steps={steps}"
        else:
            output = f"Swiped from ({action.x}, {action.y}) to ({end_x}, {end_y}) in {duration}ms"

        return ActionResult(
            number=0,
            action_type=self.name,
            status=ActionStatus.SUCCESS,
            output=output,
        )


class PressAction(BaseActionExecutor):
    """按键。"""

    name = "press"

    def execute(self, platform: "PlatformManager", action: Action, context: Optional[object] = None) -> ActionResult:
        # 设置执行层级（Web 平台专用）
        self._set_level(platform, action)

        if not action.value:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error="Key is required",
            )

        platform.press(action.value, context)

        return ActionResult(
            number=0,
            action_type=self.name,
            status=ActionStatus.SUCCESS,
            output=f"Pressed: {action.value}",
        )


class ScreenshotAction(BaseActionExecutor):
    """截图。"""

    name = "screenshot"

    def execute(self, platform: "PlatformManager", action: Action, context: Optional[object] = None) -> ActionResult:
        # 设置执行层级（Web 平台专用）
        self._set_level(platform, action)

        screenshot = platform.take_screenshot(context)
        # 压缩为 JPEG q=80，减少传输体积（返回给调用方查看）
        compressed = compress_image_to_jpeg(screenshot, quality=80)
        screenshot_base64 = base64.b64encode(compressed).decode("utf-8")

        name = action.value or "screenshot"

        return ActionResult(
            number=0,
            action_type=self.name,
            status=ActionStatus.SUCCESS,
            output=name,
            screenshot=screenshot_base64,
        )


class WaitAction(BaseActionExecutor):
    """固定等待。"""

    name = "wait"
    requires_context = False

    def execute(self, platform: "PlatformManager", action: Action, context: Optional[object] = None) -> ActionResult:
        # time 参数（秒）优先，其次是 wait（毫秒），最后是 value
        if action.time is not None:
            wait_time_sec = action.time
            time.sleep(wait_time_sec)
            wait_time_ms = wait_time_sec * 1000
        else:
            wait_time_ms = action.wait or int(action.value or 1000)
            time.sleep(wait_time_ms / 1000.0)
            wait_time_sec = wait_time_ms / 1000

        return ActionResult(
            number=0,
            action_type=self.name,
            status=ActionStatus.SUCCESS,
            output=f"Waited {wait_time_sec}s",
        )
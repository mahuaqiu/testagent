"""
图像类 Action 执行器。

包含所有基于图像匹配的动作：image_click, image_wait, image_assert。
"""

import time
import logging
from typing import Optional, TYPE_CHECKING

from worker.task import Action, ActionResult, ActionStatus
from worker.actions.base import BaseActionExecutor

if TYPE_CHECKING:
    from worker.platforms.base import PlatformManager

logger = logging.getLogger(__name__)


class ImageClickAction(BaseActionExecutor):
    """图像匹配点击。"""

    name = "image_click"

    def execute(self, platform: "PlatformManager", action: Action, context: Optional[object] = None) -> ActionResult:
        if not action.image_path:
            return ActionResult(
                index=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error="image_path is required",
            )

        # 获取截图
        screenshot = platform.take_screenshot(context)

        # 查找图像位置
        threshold = action.threshold if action.threshold is not None else 0.8
        position = self._find_image_position(
            platform, screenshot, action.image_path, threshold
        )

        if not position:
            return ActionResult(
                index=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error=f"Image not found: {action.image_path}",
            )

        # 应用偏移
        x, y = self._apply_offset(position[0], position[1], action.offset)

        # 记录图像匹配结果
        logger.debug(f"Image matched: position=({x}, {y}), threshold={threshold}")

        # 点击
        platform.click(x, y, context)

        return ActionResult(
            index=0,
            action_type=self.name,
            status=ActionStatus.SUCCESS,
            output=f"Clicked at ({x}, {y})",
        )


class ImageWaitAction(BaseActionExecutor):
    """等待图像出现。"""

    name = "image_wait"

    def execute(self, platform: "PlatformManager", action: Action, context: Optional[object] = None) -> ActionResult:
        if not action.image_path:
            return ActionResult(
                index=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error="image_path is required",
            )

        start_time = time.time()
        timeout = action.timeout / 1000
        threshold = action.threshold if action.threshold is not None else 0.8

        while time.time() - start_time < timeout:
            screenshot = platform.take_screenshot(context)
            position = self._find_image_position(
                platform, screenshot, action.image_path, threshold
            )

            if position:
                return ActionResult(
                    index=0,
                    action_type=self.name,
                    status=ActionStatus.SUCCESS,
                    output=f"Image appeared: {action.image_path}",
                )

            time.sleep(0.5)

        return ActionResult(
            index=0,
            action_type=self.name,
            status=ActionStatus.FAILED,
            error=f"Image not appeared within timeout: {action.image_path}",
        )


class ImageAssertAction(BaseActionExecutor):
    """图像断言。"""

    name = "image_assert"

    def execute(self, platform: "PlatformManager", action: Action, context: Optional[object] = None) -> ActionResult:
        if not action.image_path:
            return ActionResult(
                index=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error="image_path is required",
            )

        screenshot = platform.take_screenshot(context)
        threshold = action.threshold if action.threshold is not None else 0.8
        position = self._find_image_position(
            platform, screenshot, action.image_path, threshold
        )

        if position:
            return ActionResult(
                index=0,
                action_type=self.name,
                status=ActionStatus.SUCCESS,
                output=f"Image found: {action.image_path}",
            )
        else:
            return ActionResult(
                index=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error=f"Image not found: {action.image_path}",
            )
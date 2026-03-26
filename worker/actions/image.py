"""
图像类 Action 执行器。

包含所有基于图像匹配的动作：image_click, image_wait, image_assert, image_click_near_text。
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
    requires_ocr = True

    def execute(self, platform: "PlatformManager", action: Action, context: Optional[object] = None) -> ActionResult:
        # 检查 OCR 客户端
        error = self._check_ocr_client(platform)
        if error:
            return error

        if not action.image_path:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error="image_path is required",
            )

        # 获取截图
        screenshot = platform.take_screenshot(context)

        # 查找图像位置
        threshold = action.threshold if action.threshold is not None else 0.8
        index = action.index if action.index is not None else 0
        position = self._find_image_position(
            platform, screenshot, action.image_path, threshold, index
        )

        if not position:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error=f"Image not found: {action.image_path}" + (f" at index {index}" if index > 0 else ""),
            )

        # 应用偏移
        x, y = self._apply_offset(position[0], position[1], action.offset)

        # 记录图像匹配结果
        logger.debug(f"Image matched: position=({x}, {y}), threshold={threshold}, index={index}")

        # 点击
        platform.click(x, y, context)

        return ActionResult(
            number=0,
            action_type=self.name,
            status=ActionStatus.SUCCESS,
            output=f"Clicked at ({x}, {y})",
        )


class ImageWaitAction(BaseActionExecutor):
    """等待图像出现。"""

    name = "image_wait"
    requires_ocr = True

    def execute(self, platform: "PlatformManager", action: Action, context: Optional[object] = None) -> ActionResult:
        # 检查 OCR 客户端
        error = self._check_ocr_client(platform)
        if error:
            return error

        if not action.image_path:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error="image_path is required",
            )

        start_time = time.time()
        timeout = action.timeout / 1000
        threshold = action.threshold if action.threshold is not None else 0.8
        index = action.index if action.index is not None else 0

        while time.time() - start_time < timeout:
            screenshot = platform.take_screenshot(context)
            position = self._find_image_position(
                platform, screenshot, action.image_path, threshold, index
            )

            if position:
                return ActionResult(
                    number=0,
                    action_type=self.name,
                    status=ActionStatus.SUCCESS,
                    output=f"Image appeared: {action.image_path}",
                )

            time.sleep(0.5)

        return ActionResult(
            number=0,
            action_type=self.name,
            status=ActionStatus.FAILED,
            error=f"Image not appeared within timeout: {action.image_path}",
        )


class ImageAssertAction(BaseActionExecutor):
    """图像断言。"""

    name = "image_assert"
    requires_ocr = True

    def execute(self, platform: "PlatformManager", action: Action, context: Optional[object] = None) -> ActionResult:
        # 检查 OCR 客户端
        error = self._check_ocr_client(platform)
        if error:
            return error

        if not action.image_path:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error="image_path is required",
            )

        screenshot = platform.take_screenshot(context)
        threshold = action.threshold if action.threshold is not None else 0.8
        index = action.index if action.index is not None else 0
        position = self._find_image_position(
            platform, screenshot, action.image_path, threshold, index
        )

        if position:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.SUCCESS,
                output=f"Image found: {action.image_path}",
            )
        else:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error=f"Image not found: {action.image_path}" + (f" at index {index}" if index > 0 else ""),
            )


class ImageClickNearTextAction(BaseActionExecutor):
    """点击文本附近最近的图片。"""

    name = "image_click_near_text"
    requires_ocr = True

    def execute(self, platform: "PlatformManager", action: Action, context: Optional[object] = None) -> ActionResult:
        # 检查 OCR 客户端
        error = self._check_ocr_client(platform)
        if error:
            return error

        if not action.image_path:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error="image_path is required",
            )

        if not action.value:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error="value (filter_text) is required",
            )

        # 获取截图
        screenshot = platform.take_screenshot(context)

        # 读取模板图片
        import os
        if not os.path.exists(action.image_path):
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error=f"Template image not found: {action.image_path}",
            )

        with open(action.image_path, "rb") as f:
            template_bytes = f.read()

        # 调用 match_near_text
        threshold = action.threshold if action.threshold is not None else 0.8
        max_distance = action.end_x if action.end_x is not None else 500  # 复用 end_x 作为 max_distance

        match = platform.ocr_client.match_near_text(
            screenshot,
            template_bytes,
            action.value,  # filter_text
            max_distance=max_distance,
            threshold=threshold,
        )

        if not match:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error=f"Image not found near text: {action.value}",
            )

        # 应用偏移
        x, y = self._apply_offset(match.center_x, match.center_y, action.offset)

        # 记录匹配结果
        logger.debug(f"Image near text matched: text=\"{action.value}\", position=({x}, {y}), distance<={max_distance}")

        # 点击
        platform.click(x, y, context)

        return ActionResult(
            number=0,
            action_type=self.name,
            status=ActionStatus.SUCCESS,
            output=f"Clicked at ({x}, {y}) near text \"{action.value}\"",
        )
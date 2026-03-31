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

        if not action.image_base64:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error="image_base64 is required",
            )

        # 获取截图
        screenshot = platform.take_screenshot(context)

        # 查找图像位置
        threshold = action.threshold if action.threshold is not None else 0.8
        index = action.index if action.index is not None else 0
        position = self._find_image_position(
            platform, screenshot, action.image_base64, threshold, index
        )

        if not position:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error=f"Image not found" + (f" at index {index}" if index > 0 else ""),
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

        if not action.image_base64:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error="image_base64 is required",
            )

        start_time = time.time()
        timeout = action.timeout / 1000
        threshold = action.threshold if action.threshold is not None else 0.8
        index = action.index if action.index is not None else 0

        while time.time() - start_time < timeout:
            screenshot = platform.take_screenshot(context)
            position = self._find_image_position(
                platform, screenshot, action.image_base64, threshold, index
            )

            if position:
                return ActionResult(
                    number=0,
                    action_type=self.name,
                    status=ActionStatus.SUCCESS,
                    output="Image appeared",
                )

            time.sleep(0.5)

        return ActionResult(
            number=0,
            action_type=self.name,
            status=ActionStatus.FAILED,
            error="Image not appeared within timeout",
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

        if not action.image_base64:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error="image_base64 is required",
            )

        screenshot = platform.take_screenshot(context)
        threshold = action.threshold if action.threshold is not None else 0.8
        index = action.index if action.index is not None else 0
        position = self._find_image_position(
            platform, screenshot, action.image_base64, threshold, index
        )

        if position:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.SUCCESS,
                output="Image found",
            )
        else:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error="Image not found" + (f" at index {index}" if index > 0 else ""),
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

        if not action.image_base64:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error="image_base64 is required",
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

        # 解码 base64 模板图片
        template_bytes = platform._base64_to_bytes(action.image_base64)

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


class ImageMoveAction(BaseActionExecutor):
    """图像匹配后移动鼠标。"""

    name = "image_move"
    requires_ocr = True

    def execute(self, platform: "PlatformManager", action: Action, context: Optional[object] = None) -> ActionResult:
        # 检查 OCR 客户端
        error = self._check_ocr_client(platform)
        if error:
            return error

        if not action.image_base64:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error="image_base64 is required",
            )

        # 获取截图
        screenshot = platform.take_screenshot(context)

        # 查找图像位置
        threshold = action.threshold if action.threshold is not None else 0.8
        index = action.index if action.index is not None else 0
        position = self._find_image_position(
            platform, screenshot, action.image_base64, threshold, index
        )

        if not position:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error=f"Image not found" + (f" at index {index}" if index > 0 else ""),
            )

        # 应用偏移
        x, y = self._apply_offset(position[0], position[1], action.offset)

        # 移动鼠标（捕获移动端不支持异常）
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
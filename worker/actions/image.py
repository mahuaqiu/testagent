"""
图像类 Action 执行器。

包含所有基于图像匹配的动作：image_click, image_wait, image_assert, image_click_near_text,
image_move, image_double_click, image_exist,
ocr_click_same_row_image, ocr_check_same_row_image。
"""

import time
import logging
import io
from typing import Optional, TYPE_CHECKING
from PIL import Image

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


class ImageDoubleClickAction(BaseActionExecutor):
    """图像匹配双击。"""

    name = "image_double_click"
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

        # 双击
        platform.double_click(x, y, context)

        return ActionResult(
            number=0,
            action_type=self.name,
            status=ActionStatus.SUCCESS,
            output=f"Double clicked at ({x}, {y})",
        )


class OcrClickSameRowImageAction(BaseActionExecutor):
    """点击同行图片。"""

    name = "ocr_click_same_row_image"
    requires_ocr = True

    def execute(self, platform: "PlatformManager", action: Action, context: Optional[object] = None) -> ActionResult:
        # 检查 OCR 客户端
        error = self._check_ocr_client(platform)
        if error:
            return error

        if not action.anchor_text:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error="anchor_text is required",
            )

        if not action.image_base64:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error="image_base64 is required",
            )

        # 获取完整截图
        screenshot = platform.take_screenshot(context)

        # 定位锚点文本（使用降级匹配策略）
        anchor_index = action.anchor_index if action.anchor_index is not None else 0
        anchor_position = self._find_text_with_fallback(platform, screenshot, action.anchor_text, anchor_index)

        if not anchor_position:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error=f"Anchor text not found: {action.anchor_text}" + (f" at index {anchor_index}" if anchor_index > 0 else ""),
            )

        anchor_x, anchor_y = anchor_position
        logger.debug(f"Anchor found: text=\"{action.anchor_text}\", position=({anchor_x}, {anchor_y})")

        # 获取截图尺寸
        img = Image.open(io.BytesIO(screenshot))
        img_width, img_height = img.size

        # 裁剪水平带状区域
        row_tolerance = action.row_tolerance if action.row_tolerance is not None else 20
        top = max(0, anchor_y - row_tolerance)
        bottom = min(img_height, anchor_y + row_tolerance + 1)

        cropped = img.crop((0, top, img_width, bottom))

        # 将裁剪后的图片转为bytes
        cropped_bytes_io = io.BytesIO()
        cropped.save(cropped_bytes_io, format="PNG")
        cropped_bytes = cropped_bytes_io.getvalue()

        # 在裁剪区域内查找目标图片
        threshold = action.threshold if action.threshold is not None else 0.8
        target_index = action.target_index if action.target_index is not None else 0
        target_position = self._find_image_position(
            platform, cropped_bytes, action.image_base64, threshold, target_index
        )

        if not target_position:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error=f"Target image not found in row of \"{action.anchor_text}\"" + (f" at target_index {target_index}" if target_index > 0 else ""),
            )

        # 计算目标在原图中的坐标（加上裁剪偏移）
        target_x = target_position[0]
        target_y = target_position[1] + top

        logger.debug(f"Target image found: position=({target_x}, {target_y}) in row")

        # 应用偏移
        x, y = self._apply_offset(target_x, target_y, action.offset)

        # 点击
        platform.click(x, y, context)

        return ActionResult(
            number=0,
            action_type=self.name,
            status=ActionStatus.SUCCESS,
            output=f"Clicked at ({x}, {y}) in row of \"{action.anchor_text}\"",
        )

    def _find_text_with_fallback(self, platform: "PlatformManager", image_bytes: bytes, text: str, index: int = 0) -> Optional[tuple[int, int]]:
        """使用降级策略查找文字位置：精确匹配 → 模糊匹配。"""
        # 1. 先精确匹配
        position = platform._find_text_position(image_bytes, text, "exact", index)
        if position:
            logger.debug(f"Text found with exact match: \"{text}\"")
            return position

        # 2. 再模糊匹配
        position = platform._find_text_position(image_bytes, text, "fuzzy", index)
        if position:
            logger.debug(f"Text found with fuzzy match: \"{text}\"")
            return position

        return None


class OcrCheckSameRowImageAction(BaseActionExecutor):
    """检查同行图片是否存在。"""

    name = "ocr_check_same_row_image"
    requires_ocr = True

    def execute(self, platform: "PlatformManager", action: Action, context: Optional[object] = None) -> ActionResult:
        # 检查 OCR 客户端
        error = self._check_ocr_client(platform)
        if error:
            return error

        if not action.anchor_text:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error="anchor_text is required",
            )

        if not action.image_base64:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error="image_base64 is required",
            )

        # 获取完整截图
        screenshot = platform.take_screenshot(context)

        # 定位锚点文本（使用降级匹配策略）
        anchor_index = action.anchor_index if action.anchor_index is not None else 0
        anchor_position = self._find_text_with_fallback(platform, screenshot, action.anchor_text, anchor_index)

        if not anchor_position:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error=f"Anchor text not found: {action.anchor_text}" + (f" at index {anchor_index}" if anchor_index > 0 else ""),
            )

        anchor_x, anchor_y = anchor_position

        # 获取截图尺寸并裁剪水平带状区域
        img = Image.open(io.BytesIO(screenshot))
        img_width, img_height = img.size

        row_tolerance = action.row_tolerance if action.row_tolerance is not None else 20
        top = max(0, anchor_y - row_tolerance)
        bottom = min(img_height, anchor_y + row_tolerance + 1)

        cropped = img.crop((0, top, img_width, bottom))
        cropped_bytes_io = io.BytesIO()
        cropped.save(cropped_bytes_io, format="PNG")
        cropped_bytes = cropped_bytes_io.getvalue()

        # 在裁剪区域内查找目标图片
        threshold = action.threshold if action.threshold is not None else 0.8
        target_index = action.target_index if action.target_index is not None else 0
        target_position = self._find_image_position(
            platform, cropped_bytes, action.image_base64, threshold, target_index
        )

        if not target_position:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error=f"Target image not found in row of \"{action.anchor_text}\"",
            )

        # 计算目标在原图中的坐标
        target_x = target_position[0]
        target_y = target_position[1] + top

        return ActionResult(
            number=0,
            action_type=self.name,
            status=ActionStatus.SUCCESS,
            output=f"Found at ({target_x}, {target_y})",
        )

    def _find_text_with_fallback(self, platform: "PlatformManager", image_bytes: bytes, text: str, index: int = 0) -> Optional[tuple[int, int]]:
        """使用降级策略查找文字位置：精确匹配 → 模糊匹配。"""
        # 1. 先精确匹配
        position = platform._find_text_position(image_bytes, text, "exact", index)
        if position:
            return position

        # 2. 再模糊匹配
        position = platform._find_text_position(image_bytes, text, "fuzzy", index)
        if position:
            return position

        return None


class ImageExistAction(BaseActionExecutor):
    """检查图像是否存在。"""

    name = "image_exist"
    requires_ocr = True

    def execute(self, platform: "PlatformManager", action: Action, context: Optional[object] = None) -> ActionResult:
        # 检查 OCR 客户端
        error = self._check_ocr_client(platform)
        if error:
            return error

        # 检查必填参数
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

        # 返回结果（始终 SUCCESS，通过 output 返回存在性）
        import json
        exists = position is not None
        return ActionResult(
            number=0,
            action_type=self.name,
            status=ActionStatus.SUCCESS,
            output=json.dumps({"exists": exists}),
        )
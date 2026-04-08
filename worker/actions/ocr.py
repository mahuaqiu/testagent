"""
OCR 类 Action 执行器。

包含所有基于 OCR 文字识别的动作：ocr_click, ocr_input, ocr_wait, ocr_assert, ocr_get_text, ocr_paste,
ocr_double_click, ocr_click_same_row_text, ocr_check_same_row_text。
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


class OcrClickAction(BaseActionExecutor):
    """OCR 文字点击。"""

    name = "ocr_click"
    requires_ocr = True

    def execute(self, platform: "PlatformManager", action: Action, context: Optional[object] = None) -> ActionResult:
        # 检查 OCR 客户端
        error = self._check_ocr_client(platform)
        if error:
            return error

        # 获取截图
        screenshot = platform.take_screenshot(context)

        # 查找文字位置（使用统一匹配策略）
        index = action.index if action.index is not None else 0
        position = self._find_text_with_fallback(
            platform, screenshot, action.value, index
        )

        if not position:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error=f"Text not found: {action.value}" + (f" at index {index}" if index > 0 else ""),
            )

        # 应用偏移
        x, y = self._apply_offset(position[0], position[1], action.offset)

        # 记录 OCR 定位结果
        logger.debug(f"OCR located: text=\"{action.value}\", position=({x}, {y})")

        # 点击
        platform.click(x, y, context)

        return ActionResult(
            number=0,
            action_type=self.name,
            status=ActionStatus.SUCCESS,
            output=f"Clicked at ({x}, {y})",
        )


class OcrInputAction(BaseActionExecutor):
    """OCR 文字附近输入。"""

    name = "ocr_input"
    requires_ocr = True

    def execute(self, platform: "PlatformManager", action: Action, context: Optional[object] = None) -> ActionResult:
        # 检查 OCR 客户端
        error = self._check_ocr_client(platform)
        if error:
            return error

        # 获取截图
        screenshot = platform.take_screenshot(context)

        # 查找文字位置
        index = action.index if action.index is not None else 0
        position = self._find_text_position(
            platform, screenshot, action.value, action.match_mode, index
        )

        if not position:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error=f"Text not found: {action.value}" + (f" at index {index}" if index > 0 else ""),
            )

        # 应用偏移
        x, y = self._apply_offset(position[0], position[1], action.offset)

        # 记录 OCR 定位结果
        logger.debug(f"OCR located: text=\"{action.value}\", position=({x}, {y})")

        # 点击输入框
        platform.click(x, y, context)

        # 输入文本
        if action.text:
            platform.input_text(action.text, context)

        return ActionResult(
            number=0,
            action_type=self.name,
            status=ActionStatus.SUCCESS,
            output=f"Input at ({x}, {y})",
        )


class OcrWaitAction(BaseActionExecutor):
    """等待文字出现。"""

    name = "ocr_wait"
    requires_ocr = True

    def execute(self, platform: "PlatformManager", action: Action, context: Optional[object] = None) -> ActionResult:
        # 检查 OCR 客户端
        error = self._check_ocr_client(platform)
        if error:
            return error

        # 如果有 time 参数，先等待指定秒数
        if action.time:
            time.sleep(action.time)

        start_time = time.time()
        timeout = action.timeout / 1000

        while time.time() - start_time < timeout:
            screenshot = platform.take_screenshot(context)
            position = self._find_text_position(
                platform, screenshot, action.value, action.match_mode
            )

            if position:
                return ActionResult(
                    number=0,
                    action_type=self.name,
                    status=ActionStatus.SUCCESS,
                    output=f"Text appeared: {action.value}",
                )

            time.sleep(0.5)

        return ActionResult(
            number=0,
            action_type=self.name,
            status=ActionStatus.FAILED,
            error=f"Text not appeared within timeout: {action.value}",
        )


class OcrAssertAction(BaseActionExecutor):
    """OCR 文字断言。"""

    name = "ocr_assert"
    requires_ocr = True

    def execute(self, platform: "PlatformManager", action: Action, context: Optional[object] = None) -> ActionResult:
        # 检查 OCR 客户端
        error = self._check_ocr_client(platform)
        if error:
            return error

        screenshot = platform.take_screenshot(context)

        # 处理正则匹配：以 "reg_" 开头时使用正则模式
        match_mode = action.match_mode
        target_value = action.value
        if action.value and action.value.startswith("reg_"):
            match_mode = "regex"
            target_value = action.value[4:]  # 去掉 "reg_" 前缀

        position = self._find_text_position(platform, screenshot, target_value, match_mode)

        if position:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.SUCCESS,
                output=f"Text found: {action.value}",
            )
        else:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error=f"Text not found: {action.value}",
            )


class OcrGetTextAction(BaseActionExecutor):
    """获取 OCR 文字区域内容。"""

    name = "ocr_get_text"
    requires_ocr = True

    def execute(self, platform: "PlatformManager", action: Action, context: Optional[object] = None) -> ActionResult:
        # 检查 OCR 客户端
        error = self._check_ocr_client(platform)
        if error:
            return error

        screenshot = platform.take_screenshot(context)
        texts = platform.ocr_client.recognize(screenshot)

        all_text = " ".join([t.text for t in texts])

        return ActionResult(
            number=0,
            action_type=self.name,
            status=ActionStatus.SUCCESS,
            output=all_text,
        )


class OcrPasteAction(BaseActionExecutor):
    """OCR 定位后粘贴文本。"""

    name = "ocr_paste"
    requires_ocr = True

    def execute(self, platform: "PlatformManager", action: Action, context: Optional[object] = None) -> ActionResult:
        # 检查 OCR 客户端
        error = self._check_ocr_client(platform)
        if error:
            return error

        if not action.text:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error="text is required for ocr_paste",
            )

        # 获取截图
        screenshot = platform.take_screenshot(context)

        # 查找文字位置
        index = action.index if action.index is not None else 0
        position = self._find_text_position(
            platform, screenshot, action.value, action.match_mode, index
        )

        if not position:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error=f"Text not found: {action.value}" + (f" at index {index}" if index > 0 else ""),
            )

        # 应用偏移
        x, y = self._apply_offset(position[0], position[1], action.offset)

        # 记录 OCR 定位结果
        logger.debug(f"OCR located: text=\"{action.value}\", position=({x}, {y})")

        # 点击坐标
        platform.click(x, y, context)

        # 使用剪贴板粘贴
        import pyperclip
        original_clipboard = pyperclip.paste()
        try:
            pyperclip.copy(action.text)
            platform.press("Control+v", context)
        finally:
            # 恢复原始剪贴板内容
            pyperclip.copy(original_clipboard)

        return ActionResult(
            number=0,
            action_type=self.name,
            status=ActionStatus.SUCCESS,
            output=f"Pasted at ({x}, {y})",
        )


class OcrMoveAction(BaseActionExecutor):
    """OCR 定位后移动鼠标。"""

    name = "ocr_move"
    requires_ocr = True

    def execute(self, platform: "PlatformManager", action: Action, context: Optional[object] = None) -> ActionResult:
        # 检查 OCR 客户端
        error = self._check_ocr_client(platform)
        if error:
            return error

        # 获取截图
        screenshot = platform.take_screenshot(context)

        # 查找文字位置
        index = action.index if action.index is not None else 0
        position = self._find_text_position(
            platform, screenshot, action.value, action.match_mode, index
        )

        if not position:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error=f"Text not found: {action.value}" + (f" at index {index}" if index > 0 else ""),
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


class OcrDoubleClickAction(BaseActionExecutor):
    """OCR 文字双击。"""

    name = "ocr_double_click"
    requires_ocr = True

    def execute(self, platform: "PlatformManager", action: Action, context: Optional[object] = None) -> ActionResult:
        # 检查 OCR 客户端
        error = self._check_ocr_client(platform)
        if error:
            return error

        # 获取截图
        screenshot = platform.take_screenshot(context)

        # 查找文字位置（使用降级匹配策略）
        index = action.index if action.index is not None else 0
        position = self._find_text_with_fallback(platform, screenshot, action.value, index)

        if not position:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error=f"Text not found: {action.value}" + (f" at index {index}" if index > 0 else ""),
            )

        # 应用偏移
        x, y = self._apply_offset(position[0], position[1], action.offset)

        # 记录 OCR 定位结果
        logger.debug(f"OCR located: text=\"{action.value}\", position=({x}, {y})")

        # 双击
        platform.double_click(x, y, context)

        return ActionResult(
            number=0,
            action_type=self.name,
            status=ActionStatus.SUCCESS,
            output=f"Double clicked at ({x}, {y})",
        )

    def _find_text_with_fallback(self, platform: "PlatformManager", image_bytes: bytes, text: str, index: int = 0) -> Optional[tuple[int, int]]:
        """
        使用降级策略查找文字位置：精确匹配 → 模糊匹配。

        Args:
            platform: 平台管理器
            image_bytes: 图像数据
            text: 目标文字
            index: 选择第几个匹配结果

        Returns:
            文字中心坐标 (x, y)，未找到返回 None
        """
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


class OcrClickSameRowTextAction(BaseActionExecutor):
    """点击同行文本。"""

    name = "ocr_click_same_row_text"
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

        if not action.value:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error="value (target_text) is required",
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

        # 在裁剪区域内查找目标文本（使用降级匹配策略）
        target_index = action.target_index if action.target_index is not None else 0
        target_position = self._find_text_with_fallback(platform, cropped_bytes, action.value, target_index)

        if not target_position:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error=f"Target text not found in row of \"{action.anchor_text}\": {action.value}" + (f" at target_index {target_index}" if target_index > 0 else ""),
            )

        # 计算目标在原图中的坐标（加上裁剪偏移）
        target_x = target_position[0]
        target_y = target_position[1] + top

        logger.debug(f"Target found: text=\"{action.value}\", position=({target_x}, {target_y}) in row")

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


class OcrCheckSameRowTextAction(BaseActionExecutor):
    """检查同行文本是否存在。"""

    name = "ocr_check_same_row_text"
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

        if not action.value:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error="value (target_text) is required",
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

        # 在裁剪区域内查找目标文本
        target_index = action.target_index if action.target_index is not None else 0
        target_position = self._find_text_with_fallback(platform, cropped_bytes, action.value, target_index)

        if not target_position:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error=f"Target text not found in row of \"{action.anchor_text}\"",
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
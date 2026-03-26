"""
OCR 类 Action 执行器。

包含所有基于 OCR 文字识别的动作：ocr_click, ocr_input, ocr_wait, ocr_assert, ocr_get_text, ocr_paste。
"""

import time
import logging
from typing import Optional, TYPE_CHECKING

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
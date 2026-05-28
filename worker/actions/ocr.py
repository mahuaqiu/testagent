"""
OCR 类 Action 执行器。

包含所有基于 OCR 文字识别的动作：ocr_click, ocr_input, ocr_wait, ocr_assert, ocr_get_text, ocr_paste,
ocr_move, ocr_double_click, ocr_exist,
ocr_click_same_row_text, ocr_check_same_row_text。

统一匹配策略：精确匹配 → 模糊匹配，reg_ 开头使用正则匹配。
"""

import io
import json
import logging
import time
from typing import TYPE_CHECKING

from PIL import Image

from worker.actions.base import BaseActionExecutor
from worker.task import Action, ActionResult, ActionStatus

if TYPE_CHECKING:
    from worker.platforms.base import PlatformManager

logger = logging.getLogger(__name__)


class OcrClickAction(BaseActionExecutor):
    """OCR 文字点击。"""

    name = "ocr_click"
    requires_ocr = True

    def execute(self, platform: "PlatformManager", action: Action, context: object | None = None) -> ActionResult:
        # 设置执行层级（Web 平台专用）
        self._set_level(platform, action)

        # 检查 OCR 客户端
        error = self._check_ocr_client(platform)
        if error:
            return error

        # 获取截图
        screenshot = platform.take_screenshot(context)

        # 应用 region 裁剪
        if action.region:
            screenshot = self._crop_region(screenshot, action.region)

        # 查找文字位置（使用统一匹配策略）
        index = action.index if action.index is not None else 0
        position = self._find_text_with_fallback(
            platform, screenshot, action.value, index, action.match_mode
        )

        if not position:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error=f"Text not found: {action.value}" + (f" at index {index}" if index > 0 else ""),
                ocr_info=self._get_last_ocr_info(platform),
            )

        # 将相对坐标转换为全局坐标
        if action.region:
            position = self._offset_position(position, action.region)

        # 应用偏移
        x, y = self._apply_offset(position[0], position[1], action.offset)

        # 记录 OCR 定位结果
        logger.debug(f"OCR located: text=\"{action.value}\", position=({x}, {y})")

        # 点击（支持长按）
        click_duration = action.click_duration or 0
        platform.click(x, y, duration=click_duration, context=context)

        if click_duration > 0:
            output = f"Long clicked at ({x}, {y}) for {click_duration}ms"
        else:
            output = f"Clicked at ({x}, {y})"

        return ActionResult(
            number=0,
            action_type=self.name,
            status=ActionStatus.SUCCESS,
            output=output,
            ocr_info=self._get_last_ocr_info(platform),
        )


class OcrInputAction(BaseActionExecutor):
    """OCR 文字附近输入。"""

    name = "ocr_input"
    requires_ocr = True

    def execute(self, platform: "PlatformManager", action: Action, context: object | None = None) -> ActionResult:
        # 设置执行层级（Web 平台专用）
        self._set_level(platform, action)

        # 检查 OCR 客户端
        error = self._check_ocr_client(platform)
        if error:
            return error

        # 获取截图
        screenshot = platform.take_screenshot(context)

        # 应用 region 裁剪
        if action.region:
            screenshot = self._crop_region(screenshot, action.region)

        # 查找文字位置（使用统一匹配策略）
        index = action.index if action.index is not None else 0
        position = self._find_text_with_fallback(
            platform, screenshot, action.value, index, action.match_mode
        )

        if not position:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error=f"Text not found: {action.value}" + (f" at index {index}" if index > 0 else ""),
                ocr_info=self._get_last_ocr_info(platform),
            )

        # 将相对坐标转换为全局坐标
        if action.region:
            position = self._offset_position(position, action.region)

        # 应用偏移
        x, y = self._apply_offset(position[0], position[1], action.offset)

        # 记录 OCR 定位结果
        logger.debug(f"OCR located: text=\"{action.value}\", position=({x}, {y})")

        # 点击输入框（支持长按）
        click_duration = action.click_duration or 0
        platform.click(x, y, duration=click_duration, context=context)

        # 输入文本
        if action.text:
            platform.input_text(action.text, context)

        return ActionResult(
            number=0,
            action_type=self.name,
            status=ActionStatus.SUCCESS,
            output=f"Input at ({x}, {y})",
            ocr_info=self._get_last_ocr_info(platform),
        )


class OcrWaitAction(BaseActionExecutor):
    """等待文字出现。"""

    name = "ocr_wait"
    requires_ocr = True

    def execute(self, platform: "PlatformManager", action: Action, context: object | None = None) -> ActionResult:
        # 设置执行层级（Web 平台专用）
        self._set_level(platform, action)

        # 检查 OCR 客户端
        error = self._check_ocr_client(platform)
        if error:
            return error

        # 如果有 time 参数，先等待指定秒数
        if action.time:
            time.sleep(action.time)

        timeout = action.timeout / 1000

        # 定义检查函数：截图 + OCR 查找文字
        def check_text_appeared():
            screenshot = platform.take_screenshot(context)
            if action.region:
                screenshot = self._crop_region(screenshot, action.region)
            position = self._find_text_with_fallback(
                platform, screenshot, action.value, match_mode=action.match_mode
            )
            return position is not None

        # 智能等待（带中间检查）：固定等待超过6秒时每3秒检查一次
        found, elapsed = self._smart_wait_with_check(action.timeout, check_text_appeared)
        if found:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.SUCCESS,
                output=f"Text appeared: {action.value}",
                ocr_info=self._get_last_ocr_info(platform),
            )

        # 继续剩余时间的循环检查
        # 如果剩余时间超过5秒，改为每2秒循环（减少识别调用）
        remaining_timeout = timeout - elapsed
        loop_interval = 2 if remaining_timeout > 5 else 1
        start_time = time.time()

        while time.time() - start_time < remaining_timeout:
            screenshot = platform.take_screenshot(context)
            if action.region:
                screenshot = self._crop_region(screenshot, action.region)
            position = self._find_text_with_fallback(
                platform, screenshot, action.value, match_mode=action.match_mode
            )

            if position:
                # 将相对坐标转换为全局坐标
                if action.region:
                    position = self._offset_position(position, action.region)
                return ActionResult(
                    number=0,
                    action_type=self.name,
                    status=ActionStatus.SUCCESS,
                    output=f"Text appeared: {action.value}",
                    ocr_info=self._get_last_ocr_info(platform),
                )

            time.sleep(loop_interval)

        return ActionResult(
            number=0,
            action_type=self.name,
            status=ActionStatus.FAILED,
            error=f"Text not appeared within timeout: {action.value}",
            ocr_info=self._get_last_ocr_info(platform),
        )


class OcrAssertAction(BaseActionExecutor):
    """OCR 文字断言。"""

    name = "ocr_assert"
    requires_ocr = True

    def execute(self, platform: "PlatformManager", action: Action, context: object | None = None) -> ActionResult:
        # 设置执行层级（Web 平台专用）
        self._set_level(platform, action)

        # 检查 OCR 客户端
        error = self._check_ocr_client(platform)
        if error:
            return error

        # 解析 texts 列表（支持单字符串和 list）
        texts = action.value if isinstance(action.value, list) else [action.value]
        if not texts or texts == [None]:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error="value is required",
            )

        # 获取截图并 OCR 识别（只识别一次）
        screenshot = platform.take_screenshot(context)
        if action.region:
            screenshot = self._crop_region(screenshot, action.region)

        # 调用一次 OCR，结果缓存在 ocr_client 中
        platform.ocr_client.recognize(screenshot)

        # 在缓存结果中批量检查
        found, not_found = self._check_texts_in_ocr_result(platform, texts, action.match_mode)

        # 根据 negate 参数返回结果
        if action.negate:
            # negate=true: 要求所有文字都不存在
            if found:
                return ActionResult(
                    number=0,
                    action_type=self.name,
                    status=ActionStatus.FAILED,
                    error=f"Texts found but expected not exist: {found}",
                    ocr_info=self._get_last_ocr_info(platform),
                )
            else:
                return ActionResult(
                    number=0,
                    action_type=self.name,
                    status=ActionStatus.SUCCESS,
                    output=f"All texts not found as expected: {texts}",
                    ocr_info=self._get_last_ocr_info(platform),
                )
        else:
            # negate=false: 要求所有文字都存在
            if not_found:
                return ActionResult(
                    number=0,
                    action_type=self.name,
                    status=ActionStatus.FAILED,
                    error=f"Texts not found: {not_found}",
                    ocr_info=self._get_last_ocr_info(platform),
                )
            else:
                return ActionResult(
                    number=0,
                    action_type=self.name,
                    status=ActionStatus.SUCCESS,
                    output=f"All texts found: {texts}",
                    ocr_info=self._get_last_ocr_info(platform),
                )


class OcrGetTextAction(BaseActionExecutor):
    """获取 OCR 文字区域内容。"""

    name = "ocr_get_text"
    requires_ocr = True

    def execute(self, platform: "PlatformManager", action: Action, context: object | None = None) -> ActionResult:
        # 设置执行层级（Web 平台专用）
        self._set_level(platform, action)

        # 检查 OCR 客户端
        error = self._check_ocr_client(platform)
        if error:
            return error

        screenshot = platform.take_screenshot(context)

        # 应用 region 裁剪
        if action.region:
            screenshot = self._crop_region(screenshot, action.region)

        texts = platform.ocr_client.recognize(screenshot)

        all_text = " ".join([t.text for t in texts])

        return ActionResult(
            number=0,
            action_type=self.name,
            status=ActionStatus.SUCCESS,
            output=all_text,
            ocr_info=self._get_last_ocr_info(platform),
        )


class OcrMoveAction(BaseActionExecutor):
    """OCR 定位后移动鼠标。"""

    name = "ocr_move"
    requires_ocr = True

    def execute(self, platform: "PlatformManager", action: Action, context: object | None = None) -> ActionResult:
        # 设置执行层级（Web 平台专用）
        self._set_level(platform, action)

        # 检查 OCR 客户端
        error = self._check_ocr_client(platform)
        if error:
            return error

        # 获取截图
        screenshot = platform.take_screenshot(context)

        # 应用 region 裁剪
        if action.region:
            screenshot = self._crop_region(screenshot, action.region)

        # 查找文字位置（使用统一匹配策略）
        index = action.index if action.index is not None else 0
        position = self._find_text_with_fallback(
            platform, screenshot, action.value, index, action.match_mode
        )

        if not position:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error=f"Text not found: {action.value}" + (f" at index {index}" if index > 0 else ""),
                ocr_info=self._get_last_ocr_info(platform),
            )

        # 将相对坐标转换为全局坐标
        if action.region:
            position = self._offset_position(position, action.region)

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
                ocr_info=self._get_last_ocr_info(platform),
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

    def execute(self, platform: "PlatformManager", action: Action, context: object | None = None) -> ActionResult:
        # 设置执行层级（Web 平台专用）
        self._set_level(platform, action)

        # 检查 OCR 客户端
        error = self._check_ocr_client(platform)
        if error:
            return error

        # 获取截图
        screenshot = platform.take_screenshot(context)

        # 应用 region 裁剪
        if action.region:
            screenshot = self._crop_region(screenshot, action.region)

        # 查找文字位置（使用降级匹配策略）
        index = action.index if action.index is not None else 0
        position = self._find_text_with_fallback(platform, screenshot, action.value, index)

        if not position:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error=f"Text not found: {action.value}" + (f" at index {index}" if index > 0 else ""),
                ocr_info=self._get_last_ocr_info(platform),
            )

        # 将相对坐标转换为全局坐标
        if action.region:
            position = self._offset_position(position, action.region)

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
            ocr_info=self._get_last_ocr_info(platform),
        )


class OcrClickSameRowTextAction(BaseActionExecutor):
    """点击同行文本。"""

    name = "ocr_click_same_row_text"
    requires_ocr = True

    def execute(self, platform: "PlatformManager", action: Action, context: object | None = None) -> ActionResult:
        # 设置执行层级（Web 平台专用）
        self._set_level(platform, action)

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

        # 应用 region 裁剪
        if action.region:
            screenshot = self._crop_region(screenshot, action.region)

        # 定位锚点文本（使用降级匹配策略）
        anchor_index = action.anchor_index if action.anchor_index is not None else 0
        anchor_position = self._find_text_with_fallback(platform, screenshot, action.anchor_text, anchor_index, action.match_mode)

        if not anchor_position:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error=f"Anchor text not found: {action.anchor_text}" + (f" at index {anchor_index}" if anchor_index > 0 else ""),
                ocr_info=self._get_last_ocr_info(platform),
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
        target_position = self._find_text_with_fallback(platform, cropped_bytes, action.value, target_index, action.match_mode)

        if not target_position:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error=f"Target text not found in row of \"{action.anchor_text}\": {action.value}" + (f" at target_index {target_index}" if target_index > 0 else ""),
                ocr_info=self._get_last_ocr_info(platform),
            )

        # 计算目标在 region 裁剪图中的坐标（加上 row 裁剪偏移）
        target_x = target_position[0]
        target_y = target_position[1] + top

        # 将相对坐标转换为全局坐标
        if action.region:
            target_x, target_y = self._offset_position((target_x, target_y), action.region)

        logger.debug(f"Target found: text=\"{action.value}\", position=({target_x}, {target_y}) in row")

        # 应用偏移
        x, y = self._apply_offset(target_x, target_y, action.offset)

        # 点击（支持长按）
        click_duration = action.click_duration or 0
        platform.click(x, y, duration=click_duration, context=context)

        return ActionResult(
            number=0,
            action_type=self.name,
            status=ActionStatus.SUCCESS,
            output=f"Clicked at ({x}, {y}) in row of \"{action.anchor_text}\"",
            ocr_info=self._get_last_ocr_info(platform),
        )


class OcrCheckSameRowTextAction(BaseActionExecutor):
    """检查同行文本是否存在。"""

    name = "ocr_check_same_row_text"
    requires_ocr = True

    def execute(self, platform: "PlatformManager", action: Action, context: object | None = None) -> ActionResult:
        # 设置执行层级（Web 平台专用）
        self._set_level(platform, action)

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

        # 应用 region 裁剪
        if action.region:
            screenshot = self._crop_region(screenshot, action.region)

        # 定位锚点文本（使用降级匹配策略）
        anchor_index = action.anchor_index if action.anchor_index is not None else 0
        anchor_position = self._find_text_with_fallback(platform, screenshot, action.anchor_text, anchor_index, action.match_mode)

        if not anchor_position:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error=f"Anchor text not found: {action.anchor_text}" + (f" at index {anchor_index}" if anchor_index > 0 else ""),
                ocr_info=self._get_last_ocr_info(platform),
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
        target_position = self._find_text_with_fallback(platform, cropped_bytes, action.value, target_index, action.match_mode)

        # 返回结果（始终 SUCCESS，通过 output 返回存在性）
        import json
        exists = target_position is not None
        output_data = {"exists": exists}

        # 如果找到目标，计算坐标并返回
        if exists:
            # 计算目标在 region 裁剪图中的坐标（加上 row 裁剪偏移）
            target_x = target_position[0]
            target_y = target_position[1] + top

            # 将相对坐标转换为全局坐标
            if action.region:
                target_x, target_y = self._offset_position((target_x, target_y), action.region)

            output_data["position"] = [target_x, target_y]

        return ActionResult(
            number=0,
            action_type=self.name,
            status=ActionStatus.SUCCESS,
            output=json.dumps(output_data),
            ocr_info=self._get_last_ocr_info(platform),
        )


class OcrExistAction(BaseActionExecutor):
    """检查文字是否存在。"""

    name = "ocr_exist"
    requires_ocr = True

    def execute(self, platform: "PlatformManager", action: Action, context: object | None = None) -> ActionResult:
        # 设置执行层级（Web 平台专用）
        self._set_level(platform, action)

        # 检查 OCR 客户端
        error = self._check_ocr_client(platform)
        if error:
            return error

        # 检查必填参数
        texts = action.value if isinstance(action.value, list) else [action.value]
        if not texts or texts == [None]:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error="value is required",
            )

        # 获取截图并 OCR 识别（只识别一次）
        screenshot = platform.take_screenshot(context)
        if action.region:
            screenshot = self._crop_region(screenshot, action.region)

        # 调用一次 OCR，结果缓存在 ocr_client 中
        platform.ocr_client.recognize(screenshot)

        # 在缓存结果中批量检查
        found, not_found = self._check_texts_in_ocr_result(platform, texts, action.match_mode)

        # 根据 negate 参数返回结果（保持兼容格式）
        if action.negate:
            # negate=true: 要求所有文字都不存在
            exists = len(found) == 0
        else:
            # negate=false: 要求所有文字都存在
            exists = len(not_found) == 0

        return ActionResult(
            number=0,
            action_type=self.name,
            status=ActionStatus.SUCCESS,
            output=json.dumps({"exists": exists}),
            ocr_info=self._get_last_ocr_info(platform),
        )

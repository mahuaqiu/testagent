"""
坐标获取动作执行器。

提供 OCR 和图像识别获取坐标列表的能力。
"""

import logging
from typing import TYPE_CHECKING, Optional

from worker.actions.base import BaseActionExecutor
from worker.task import Action, ActionResult, ActionStatus

if TYPE_CHECKING:
    from worker.platforms.base import PlatformManager

logger = logging.getLogger(__name__)


class OcrGetPositionExecutor(BaseActionExecutor):
    """获取文字坐标列表。"""

    name = "ocr_get_position"
    requires_ocr = True

    def execute(self, platform: "PlatformManager", action: Action, context: Optional[object] = None) -> ActionResult:
        """执行 OCR 文字坐标获取。

        Args:
            platform: 平台管理器
            action: 动作参数
            context: 执行上下文

        Returns:
            ActionResult: 包含坐标列表的结果
        """
        # 检查 OCR 客户端
        error = self._check_ocr_client(platform)
        if error:
            return error

        # 检查文本参数
        if not action.value:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error="Text value is required",
            )

        # 获取截图
        screenshot = platform.take_screenshot(context)
        if not screenshot:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error="Failed to take screenshot",
            )

        # 获取所有匹配文字的坐标
        positions = platform._find_all_text_positions(screenshot, action.value)

        # 转换为列表格式 [[x1, y1], [x2, y2], ...]
        positions_list = [[p[0], p[1]] for p in positions]

        logger.info(f"Found {len(positions_list)} positions for text: \"{action.value}\"")

        return ActionResult(
            number=0,
            action_type=self.name,
            status=ActionStatus.SUCCESS,
            output={"positions": positions_list},
        )


class ImageGetPositionExecutor(BaseActionExecutor):
    """获取图片坐标列表。"""

    name = "image_get_position"

    def execute(self, platform: "PlatformManager", action: Action, context: Optional[object] = None) -> ActionResult:
        """执行图像坐标获取。

        Args:
            platform: 平台管理器
            action: 动作参数
            context: 执行上下文

        Returns:
            ActionResult: 包含坐标列表的结果
        """
        # 检查图片参数
        if not action.image_base64:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error="image_base64 is required",
            )

        # 获取截图
        screenshot = platform.take_screenshot(context)
        if not screenshot:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error="Failed to take screenshot",
            )

        # 获取阈值
        threshold = action.threshold if action.threshold else 0.8

        # 获取所有匹配图片的坐标
        positions = platform._find_all_image_positions(screenshot, action.image_base64, threshold)

        # 转换为列表格式 [[x1, y1], [x2, y2], ...]
        positions_list = [[p[0], p[1]] for p in positions]

        logger.info(f"Found {len(positions_list)} image positions with threshold={threshold}")

        return ActionResult(
            number=0,
            action_type=self.name,
            status=ActionStatus.SUCCESS,
            output={"positions": positions_list},
        )
"""
Action 执行器基类。

定义所有 Action 需要实现的接口。
"""

import io
import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from PIL import Image

logger = logging.getLogger(__name__)

from worker.task import Action, ActionResult, ActionStatus

if TYPE_CHECKING:
    from worker.platforms.base import PlatformManager


class ActionExecutor(ABC):
    """
    Action 执行器抽象基类。

    所有动作执行器都需要继承此类并实现 execute 方法。
    动作执行器负责协调平台能力完成特定动作，不关心具体平台的实现细节。
    """

    # Action 名称（子类必须覆盖）
    name: str = ""

    # 是否需要有效的 context（默认需要，start_app 等不需要）
    requires_context: bool = True

    # 是否需要 OCR 客户端
    requires_ocr: bool = False

    @abstractmethod
    def execute(self, platform: "PlatformManager", action: Action, context: object | None = None) -> ActionResult:
        """
        执行动作。

        Args:
            platform: 平台管理器（提供基础能力）
            action: 动作参数
            context: 执行上下文（可选，某些平台可能需要）

        Returns:
            ActionResult: 动作执行结果
        """
        pass

    def _set_level(self, platform: "PlatformManager", action: Action) -> str:
        """
        设置执行层级（Web 平台专用）。

        对于 Web 平台，根据 action.level 决定使用 Playwright 还是系统级操作。
        其他平台忽略此参数。

        Args:
            platform: 平台管理器
            action: 动作参数

        Returns:
            执行层级字符串
        """
        level = action.level if hasattr(action, 'level') else "browser"
        # 只有 Web 平台支持 level 参数
        if hasattr(platform, '_current_level'):
            platform._current_level = level
        return level

    def _apply_offset(self, x: int, y: int, offset: dict | None) -> tuple[int, int]:
        """
        应用偏移量。

        Args:
            x: 原始 X 坐标
            y: 原始 Y 坐标
            offset: 偏移量 {"x": 10, "y": 5}

        Returns:
            tuple[int, int]: 偏移后的坐标
        """
        if offset:
            x += offset.get("x", 0)
            y += offset.get("y", 0)
        return (x, y)

    def _crop_region(self, image_bytes: bytes, region: list[int]) -> bytes:
        """
        按 region [x1, y1, x2, y2] 裁剪图像。

        Args:
            image_bytes: 原始图像数据
            region: 操作区域 [x1, y1, x2, y2]

        Returns:
            bytes: 裁剪后的图像数据

        Raises:
            ValueError: region 无效
        """
        # 验证 region 格式
        if len(region) != 4:
            raise ValueError(f"Invalid region: {region}, must have exactly 4 elements [x1, y1, x2, y2]")
        if not all(isinstance(v, int) for v in region):
            raise ValueError(f"Invalid region: {region}, all values must be integers")
        if any(v < 0 for v in region):
            raise ValueError(f"Invalid region: {region}, coordinates must be non-negative")

        x1, y1, x2, y2 = region
        if x1 >= x2 or y1 >= y2:
            raise ValueError(f"Invalid region: {region}, x1 must be < x2 and y1 must be < y2")

        img = Image.open(io.BytesIO(image_bytes))
        img_width, img_height = img.size

        # 将 region 限制在图像边界内
        x1 = min(x1, img_width)
        y1 = min(y1, img_height)
        x2 = min(x2, img_width)
        y2 = min(y2, img_height)

        # PIL.crop 使用 (left, upper, right, lower) 即 (x1, y1, x2, y2)
        cropped = img.crop((x1, y1, x2, y2))
        buf = io.BytesIO()
        cropped.save(buf, format=img.format or "PNG")
        return buf.getvalue()

    def _offset_position(self, position: tuple[int, int], region: list[int]) -> tuple[int, int]:
        """
        将相对于裁剪区域的坐标转换为全局坐标。

        Args:
            position: 相对坐标 (x, y)
            region: 操作区域 [x1, y1, x2, y2]

        Returns:
            tuple[int, int]: 全局坐标 (x+x1, y+y1)
        """
        x1, y1, _, _ = region
        return (position[0] + x1, position[1] + y1)


class BaseActionExecutor(ActionExecutor):
    """
    基础 Action 执行器。

    提供一些通用的辅助方法，子类可以继承以减少重复代码。
    """

    def _check_ocr_client(self, platform: "PlatformManager") -> ActionResult | None:
        """
        检查 OCR 客户端是否可用。

        Returns:
            如果不可用返回错误 ActionResult，否则返回 None
        """
        if not platform.ocr_client:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error="OCR client not available",
            )
        return None

    def _find_text_position(
        self,
        platform: "PlatformManager",
        image_bytes: bytes,
        text: str,
        match_mode: str = "exact",
        index: int = 0
    ) -> tuple[int, int] | None:
        """
        在图像中查找文字位置。

        Args:
            platform: 平台管理器
            image_bytes: 图像数据
            text: 目标文字
            match_mode: 匹配模式
            index: 选择第几个匹配结果

        Returns:
            文字中心坐标 (x, y)，未找到返回 None
        """
        return platform._find_text_position(image_bytes, text, match_mode, index)

    def _find_image_position(
        self,
        platform: "PlatformManager",
        source_bytes: bytes,
        template_base64: str,
        threshold: float = 0.8,
        index: int = 0
    ) -> tuple[int, int] | None:
        """
        在源图像中查找模板图像位置。

        Args:
            platform: 平台管理器
            source_bytes: 源图像数据
            template_base64: 模板图像 base64 编码
            threshold: 匹配阈值
            index: 选择第几个匹配结果

        Returns:
            匹配中心坐标 (x, y)，未找到返回 None
        """
        return platform._find_image_position(source_bytes, template_base64, threshold, index)

    def _find_text_with_fallback(
        self,
        platform: "PlatformManager",
        image_bytes: bytes,
        text: str,
        index: int = 0
    ) -> tuple[int, int] | None:
        """
        使用统一匹配策略查找文字位置：精确匹配 → 模糊匹配。
        reg_ 开头的文字使用正则匹配（不受降级约束）。

        Args:
            platform: 平台管理器
            image_bytes: 图像数据
            text: 目标文字（reg_ 开头表示正则匹配）
            index: 选择第几个匹配结果

        Returns:
            文字中心坐标 (x, y)，未找到返回 None
        """
        # 处理正则快捷模式：reg_ 开头直接使用正则匹配
        if text.startswith("reg_"):
            actual_text = text[4:]  # 去掉前缀
            position = platform._find_text_position(image_bytes, actual_text, "regex", index)
            if position:
                logger.debug(f"Text found with regex match: \"{actual_text}\" (from \"{text}\")")
                return position
            return None

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

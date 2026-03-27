"""
Action 执行器基类。

定义所有 Action 需要实现的接口。
"""

import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Optional

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
    def execute(self, platform: "PlatformManager", action: Action, context: Optional[object] = None) -> ActionResult:
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

    def _apply_offset(self, x: int, y: int, offset: Optional[dict]) -> tuple[int, int]:
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


class BaseActionExecutor(ActionExecutor):
    """
    基础 Action 执行器。

    提供一些通用的辅助方法，子类可以继承以减少重复代码。
    """

    def _check_ocr_client(self, platform: "PlatformManager") -> Optional[ActionResult]:
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
    ) -> Optional[tuple[int, int]]:
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
    ) -> Optional[tuple[int, int]]:
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
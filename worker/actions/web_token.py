"""
Web Token 捕获 Action 执行器。

获取 Web 平台捕获的响应头 token。
"""

import json
from typing import Optional, TYPE_CHECKING

from worker.task import Action, ActionResult, ActionStatus
from worker.actions.base import BaseActionExecutor

if TYPE_CHECKING:
    from worker.platforms.base import PlatformManager


class GetTokenAction(BaseActionExecutor):
    """获取 Web 平台捕获的 token。"""

    name = "get_token"
    requires_context = False  # 不需要活跃的 page
    requires_ocr = False

    def execute(
        self,
        platform: "PlatformManager",
        action: Action,
        context: Optional[object] = None
    ) -> ActionResult:
        """
        执行 get_token action。

        Args:
            platform: 平台管理器
            action: 动作参数（无需参数）
            context: 执行上下文

        Returns:
            ActionResult: 包含捕获的 tokens dict
        """
        # 检查是否是 Web 平台
        if platform.platform != "web":
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error="get_token only supported on web platform",
            )

        # 获取捕获的 tokens
        tokens = platform.get_captured_tokens()

        return ActionResult(
            number=0,
            action_type=self.name,
            status=ActionStatus.SUCCESS,
            output=json.dumps(tokens),
        )
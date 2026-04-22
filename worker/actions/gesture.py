"""pinch 手势动作处理器。"""

import logging

from worker.actions.base import ActionExecutor
from worker.task import Action, ActionResult, ActionStatus

logger = logging.getLogger(__name__)


class PinchAction(ActionExecutor):
    """pinch 双指缩放动作。"""

    name = "pinch"

    def execute(self, platform, action: Action, context=None) -> ActionResult:
        """
        执行 pinch 手势。

        Args:
            platform: 平台管理器
            action: 动作参数
                - value: "in" 缩小 / "out" 放大
                - params.scale: 缩放比例（默认 0.5）
                - params.duration: 持续时间（毫秒，默认 500）
            context: 执行上下文
        """
        direction = action.value  # "in" 或 "out"
        scale = action.params.get("scale", 0.5) if action.params else 0.5
        duration = action.params.get("duration", 500) if action.params else 500

        try:
            platform.pinch(direction, scale, duration, context)
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.SUCCESS,
            )
        except Exception as e:
            logger.error(f"pinch failed: {e}")
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error=str(e),
            )
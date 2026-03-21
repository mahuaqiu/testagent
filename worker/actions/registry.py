"""
Action 注册表。

管理所有 Action 执行器的注册和查找。
"""

import logging
from typing import Dict, Optional, Set, TYPE_CHECKING

if TYPE_CHECKING:
    from worker.actions.base import ActionExecutor

logger = logging.getLogger(__name__)


class ActionRegistry:
    """
    Action 执行器注册表。

    使用单例模式管理所有注册的 Action 执行器。
    支持动态注册和查找。
    """

    _actions: Dict[str, "ActionExecutor"] = {}

    @classmethod
    def register(cls, action: "ActionExecutor") -> None:
        """
        注册 Action 执行器。

        Args:
            action: Action 执行器实例
        """
        if not action.name:
            raise ValueError("Action must have a name")

        cls._actions[action.name] = action
        logger.debug(f"Registered action: {action.name}")

    @classmethod
    def get(cls, action_type: str) -> Optional["ActionExecutor"]:
        """
        获取 Action 执行器。

        Args:
            action_type: Action 类型名称

        Returns:
            Action 执行器实例，未找到返回 None
        """
        return cls._actions.get(action_type)

    @classmethod
    def has(cls, action_type: str) -> bool:
        """
        检查 Action 是否已注册。

        Args:
            action_type: Action 类型名称

        Returns:
            是否已注册
        """
        return action_type in cls._actions

    @classmethod
    def list_all(cls) -> Set[str]:
        """
        列出所有已注册的 Action。

        Returns:
            所有已注册的 Action 名称集合
        """
        return set(cls._actions.keys())

    @classmethod
    def clear(cls) -> None:
        """清空所有注册的 Action（主要用于测试）。"""
        cls._actions.clear()
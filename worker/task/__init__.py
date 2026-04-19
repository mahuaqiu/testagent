"""
任务模型模块。
"""

from worker.task.action import Action, ActionType, MatchMode, SwipeDirection
from worker.task.result import (
    TaskResult,
    TaskStatus,
    ActionResult,
    ActionStatus,
)
from worker.task.task import Task, TaskConfig

__all__ = [
    "Action",
    "ActionType",
    "MatchMode",
    "SwipeDirection",
    "TaskResult",
    "TaskStatus",
    "ActionResult",
    "ActionStatus",
    "Task",
    "TaskConfig",
]
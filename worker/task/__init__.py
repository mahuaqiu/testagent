"""
任务模型模块。
"""

from worker.task.action import Action, ActionType, MatchMode, SwipeDirection
from worker.task.result import (
    ActionResult,
    ActionStatus,
    TaskResult,
    TaskStatus,
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

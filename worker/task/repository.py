"""任务仓储接口。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from worker.task.result import TaskResult, TaskStatus
from worker.task.task import Task


class TaskRepository(Protocol):
    """TaskService 所需的持久化接口。"""

    def create(
        self,
        task: Task,
        *,
        request_id: str | None,
        status: TaskStatus,
        idempotency_key: str | None,
        expires_at: datetime,
    ) -> None:
        ...

    def get(self, task_id: str) -> dict[str, Any] | None:
        ...

    def get_by_idempotency(self, idempotency_key: str) -> dict[str, Any] | None:
        ...

    def update_status(
        self,
        task_id: str,
        status: TaskStatus,
        *,
        result: TaskResult | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        retryable: bool = False,
        cancel_requested: bool | None = None,
    ) -> None:
        ...

    def cleanup_expired(self, now: datetime | None = None) -> int:
        ...

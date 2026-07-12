"""Worker 统一错误模型。"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class WorkerError(Exception):
    """可序列化的 Worker 业务错误。"""

    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)
    http_status: int = 500

    def __post_init__(self) -> None:
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        """转换为接口响应结构。"""
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "details": self.details,
        }


class TaskConflictError(WorkerError):
    """任务占用资源冲突。"""

    def __init__(self, message: str = "Device/Platform is busy", task_id: str | None = None):
        super().__init__(
            code="DEVICE_BUSY",
            message=message,
            retryable=True,
            details={"busy_task_id": task_id} if task_id else {},
            http_status=409,
        )
        self.task_id = task_id


class TaskNotFoundError(WorkerError):
    """任务不存在或已过期。"""

    def __init__(self, task_id: str):
        super().__init__(
            code="TASK_NOT_FOUND",
            message="Task not found",
            details={"task_id": task_id},
            http_status=404,
        )


class IdempotencyConflictError(WorkerError):
    """同一个幂等键对应了不同请求。"""

    def __init__(self, idempotency_key: str):
        super().__init__(
            code="IDEMPOTENCY_CONFLICT",
            message="Idempotency key is already used by a different task",
            details={"idempotency_key": idempotency_key},
            http_status=409,
        )

"""具备稳定请求比较的任务服务。"""

from worker.errors import IdempotencyConflictError
from worker.task.service import TaskService
from worker.task.task import Task, request_fingerprint


__all__ = ["IdempotentTaskService", "request_fingerprint"]


class IdempotentTaskService(TaskService):
    """处理带幂等键的异步提交。"""

    def submit_async(
        self,
        task: Task,
        *,
        request_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> tuple[str, str]:
        if idempotency_key:
            existing = self.repository.get_by_idempotency(idempotency_key)
            if existing:
                if request_fingerprint(existing["task"]) != request_fingerprint(task):
                    raise IdempotencyConflictError(idempotency_key)
                return existing["task"].task_id, self._status_value(existing["status"])
        return super().submit_async(
            task,
            request_id=request_id,
            idempotency_key=idempotency_key,
        )


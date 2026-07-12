"""具备稳定请求比较的任务服务。"""

from __future__ import annotations

import json

from worker.errors import IdempotencyConflictError
from worker.task.service import TaskService
from worker.task.task import Task


def request_fingerprint(task: Task) -> str:
    """生成不受本地 task_id 和创建时间影响的请求指纹。"""
    payload = task.to_dict()
    payload.pop("task_id", None)
    payload.pop("created_at", None)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


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


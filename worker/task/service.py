"""Worker 任务生命周期服务。"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from common.request_context import reset_request_id, set_request_id
from worker.errors import IdempotencyConflictError, TaskConflictError, TaskNotFoundError, WorkerError
from worker.scheduling.models import ResourceLease
from worker.scheduling.scheduler import ResourceScheduler
from worker.task.repository import TaskRepository
from worker.task.result import TaskResult, TaskStatus
from worker.task.task import Task

logger = logging.getLogger(__name__)

HOST_COMMAND_RESOURCE_KEY = "host:command"
REMOTE_EXECUTION_DOMAIN = "remote"

TERMINAL_STATUSES = {
    TaskStatus.SUCCESS.value,
    TaskStatus.FAILED.value,
    TaskStatus.CANCELLED.value,
    TaskStatus.TIMEOUT.value,
    "interrupted",
}


@dataclass
class TaskHandle:
    """进程内运行任务句柄。"""

    task: Task
    request_id: str | None
    lease: ResourceLease
    cancel_event: threading.Event = field(default_factory=threading.Event)
    future: Future | None = None


class TaskService:
    """统一处理同步和异步任务。"""

    def __init__(
        self,
        repository: TaskRepository,
        scheduler: ResourceScheduler,
        execute_callback: Callable[[Task, threading.Event], TaskResult],
        *,
        result_retention_hours: int = 24,
        max_workers: int = 16,
    ):
        self.repository = repository
        self.scheduler = scheduler
        self.execute_callback = execute_callback
        self.retention = timedelta(hours=max(1, result_retention_hours))
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, max_workers), thread_name_prefix="worker-task"
        )
        self._lock = threading.RLock()
        self._handles: dict[str, TaskHandle] = {}
        self._stopping = False

    def submit_async(
        self,
        task: Task,
        *,
        request_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> tuple[str, str]:
        """接受异步任务并立即返回。"""
        with self._lock:
            if self._stopping:
                raise RuntimeError("Worker is stopping")

            request_json = json.dumps(task.to_dict(), ensure_ascii=False)
            existing = (
                self.repository.get_by_idempotency(idempotency_key)
                if idempotency_key
                else None
            )
            if existing:
                if existing.get("request_json") != request_json:
                    raise IdempotencyConflictError(idempotency_key or "")
                return existing["task"].task_id, self._status_value(existing["status"])

            lease_key = self._resource_key_for_task(task)
            lease = self.scheduler.try_acquire_key(lease_key, task.task_id)
            if lease is None:
                raise TaskConflictError(
                    task_id=self.scheduler.get_busy_task_id_by_key(lease_key)
                )
            try:
                self.repository.create(
                    task,
                    request_id=request_id,
                    status=TaskStatus.RUNNING,
                    idempotency_key=idempotency_key,
                    expires_at=datetime.now() + self.retention,
                )
            except Exception:
                self.scheduler.release_lease(lease, "create_failed")
                raise

            handle = TaskHandle(task=task, request_id=request_id, lease=lease)
            self._handles[task.task_id] = handle
            handle.future = self._executor.submit(self._run, handle)
            return task.task_id, TaskStatus.RUNNING.value

    def execute_sync(self, task: Task, *, request_id: str | None = None) -> TaskResult:
        """同步任务复用异步执行入口并等待同一个 Future。"""
        if not task.task_id:
            task.task_id = f"sync_{uuid.uuid4().hex}"
        task_id, _ = self.submit_async(task, request_id=request_id)
        with self._lock:
            handle = self._handles.get(task_id)
        if handle is None or handle.future is None:
            row = self.repository.get(task_id)
            if row and row.get("result"):
                return row["result"]
            raise RuntimeError(f"Task handle unavailable: {task_id}")
        return handle.future.result()

    def get(self, task_id: str) -> dict[str, Any] | None:
        """幂等查询任务，不删除结果。"""
        row = self.repository.get(task_id)
        if row is None:
            return None
        result: TaskResult | None = row.get("result")
        if result is not None:
            return result.to_dict(include_task_id=True)
        return {
            "task_id": task_id,
            "status": self._status_value(row["status"]),
            "request_id": row.get("request_id"),
        }

    def get_request_id(self, task_id: str) -> str | None:
        """返回任务首次提交时绑定的 request-id。"""
        row = self.repository.get(task_id)
        return row.get("request_id") if row else None

    def cancel(self, task_id: str) -> dict[str, Any]:
        """请求取消任务，资源释放后才进入 cancelled。"""
        with self._lock:
            row = self.repository.get(task_id)
            if row is None:
                raise TaskNotFoundError(task_id)
            status = self._status_value(row["status"])
            if status in TERMINAL_STATUSES:
                return {"task_id": task_id, "status": status}
            handle = self._handles.get(task_id)
            if handle:
                handle.cancel_event.set()
            self.repository.update_status(task_id, TaskStatus.RUNNING, cancel_requested=True)
            return {"task_id": task_id, "status": "cancelling"}

    def shutdown(self) -> None:
        """停止接受任务并等待现有任务结束。"""
        with self._lock:
            self._stopping = True
            for handle in self._handles.values():
                handle.cancel_event.set()
        self._executor.shutdown(wait=True, cancel_futures=False)

    def cleanup_expired(self) -> int:
        """清理过期任务。"""
        return self.repository.cleanup_expired()

    def active_count(self) -> int:
        """返回活动任务数。"""
        with self._lock:
            return len(self._handles)

    def _run(self, handle: TaskHandle) -> TaskResult:
        task = handle.task
        request_id_token = (
            set_request_id(handle.request_id) if handle.request_id else None
        )
        try:
            if handle.cancel_event.is_set():
                result = TaskResult(
                    task_id=task.task_id,
                    request_id=handle.request_id,
                    status=TaskStatus.CANCELLED,
                    platform=task.platform,
                    error="Task cancelled by user",
                )
            else:
                result = self.execute_callback(task, handle.cancel_event)
                result.task_id = task.task_id
                result.request_id = handle.request_id or result.request_id
                if handle.cancel_event.is_set() and result.status == TaskStatus.SUCCESS:
                    result.status = TaskStatus.CANCELLED
                    result.error = "Task cancelled by user"
        except Exception as exc:
            logger.exception("Task execution failed: task_id=%s", task.task_id)
            result = TaskResult(
                task_id=task.task_id,
                request_id=handle.request_id,
                status=TaskStatus.FAILED,
                platform=task.platform,
                error=str(exc),
            )
        finally:
            try:
                self.scheduler.release_lease(handle.lease, "task_finished")
                with self._lock:
                    self._handles.pop(task.task_id, None)
                self.repository.update_status(task.task_id, result.status, result=result)
            finally:
                if request_id_token is not None:
                    reset_request_id(request_id_token)
        return result

    @staticmethod
    def _status_value(status: Any) -> str:
        return status.value if isinstance(status, TaskStatus) else str(status)

    @staticmethod
    def _resource_key_for_task(task: Task) -> str:
        """为任务选择资源域。

        普通用例、远程操作和宿主机命令属于不同执行域。带设备 SN 的
        ``cmd_exec`` 使用设备级命令锁，避免同一 Worker 上不同设备互相影响。
        """
        from worker.scheduling.models import resource_key

        action_types = {action.action_type for action in task.actions}
        if action_types == {"cmd_exec"}:
            if task.platform in ("android", "ios", "harmony_mobile", "harmony_pc") and task.device_id:
                return f"command:{task.platform}:{task.device_id}"
            return HOST_COMMAND_RESOURCE_KEY
        if "cmd_exec" in action_types:
            raise WorkerError(
                code="INVALID_TASK_ACTIONS",
                message="cmd_exec cannot be mixed with platform actions",
                http_status=400,
            )
        if task.execution_domain == REMOTE_EXECUTION_DOMAIN:
            if task.device_id:
                return f"remote:{task.platform}:{task.device_id}"
            return f"remote:{task.platform}"
        return resource_key(task.platform, task.device_id)

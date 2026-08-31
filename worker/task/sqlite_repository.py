"""SQLite 任务仓储实现。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from worker.errors import IdempotencyConflictError
from worker.storage.database import Database
from worker.task.repository import TaskRepository
from worker.task.result import TaskResult, TaskStatus
from worker.task.task import Task


class SQLiteTaskRepository(TaskRepository):
    """持久化 Worker 最近任务和动作结果。"""

    def __init__(self, database: Database, result_retention_hours: int = 1):
        self.database = database
        self.result_retention = timedelta(hours=max(1, result_retention_hours))

    def create(
        self,
        task: Task,
        *,
        request_id: str | None,
        status: TaskStatus,
        idempotency_key: str | None,
        expires_at: datetime,
    ) -> None:
        now_dt = datetime.now()
        now = now_dt.isoformat()
        request_json = json.dumps(task.to_dict(), ensure_ascii=False)
        try:
            with self.database.transaction() as conn:
                conn.execute(
                    """
                    INSERT INTO worker_tasks(
                        task_id, idempotency_key, request_id, platform, device_id,
                        status, request_json, created_at, expires_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task.task_id,
                        idempotency_key,
                        request_id,
                        task.platform,
                        task.device_id,
                        status.value,
                        request_json,
                        now,
                        expires_at.isoformat(),
                    ),
                )
        except Exception as exc:
            if idempotency_key and "UNIQUE constraint failed: worker_tasks.idempotency_key" in str(exc):
                existing = self.get_by_idempotency(idempotency_key)
                if existing and existing.get("request_json") != request_json:
                    raise IdempotencyConflictError(idempotency_key) from exc
            raise

    def get(self, task_id: str) -> dict[str, Any] | None:
        row = self.database.connection().execute(
            "SELECT * FROM worker_tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def get_by_idempotency(self, idempotency_key: str) -> dict[str, Any] | None:
        row = self.database.connection().execute(
            "SELECT * FROM worker_tasks WHERE idempotency_key = ?", (idempotency_key,)
        ).fetchone()
        return self._row_to_dict(row) if row else None

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
        result_json = json.dumps(result.to_dict(include_task_id=True), ensure_ascii=False) if result else None
        now_dt = datetime.now()
        now = now_dt.isoformat()
        started_at = now if status == TaskStatus.RUNNING else None
        terminal = {
            TaskStatus.SUCCESS,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.TIMEOUT,
        }
        # interrupted 在旧枚举还未升级前也能由恢复逻辑直接写入字符串。
        terminal_value = getattr(TaskStatus, "INTERRUPTED", None)
        if terminal_value is not None:
            terminal.add(terminal_value)
        finished_at = now if status in terminal else None
        expires_at = (now_dt + self.result_retention).isoformat() if finished_at is not None else None
        with self.database.transaction() as conn:
            conn.execute(
                """
                UPDATE worker_tasks
                SET status = ?, result_json = COALESCE(?, result_json),
                    error_code = ?, error_message = ?, retryable = ?,
                    cancel_requested = COALESCE(?, cancel_requested),
                    started_at = COALESCE(started_at, ?),
                    finished_at = COALESCE(?, finished_at),
                    expires_at = COALESCE(?, expires_at)
                WHERE task_id = ?
                """,
                (
                    status.value,
                    result_json,
                    error_code,
                    error_message,
                    int(retryable),
                    int(cancel_requested) if cancel_requested is not None else None,
                    started_at,
                    finished_at,
                    expires_at,
                    task_id,
                ),
            )

    def mark_interrupted(self) -> int:
        """将 Worker 重启前遗留任务标记为 interrupted。"""
        active = (TaskStatus.PENDING.value, TaskStatus.RUNNING.value)
        placeholders = ",".join("?" for _ in active)
        now = datetime.now()
        with self.database.transaction() as conn:
            cursor = conn.execute(
                f"""
                UPDATE worker_tasks
                SET status = ?, error_code = ?, error_message = ?, finished_at = ?, expires_at = ?
                WHERE status IN ({placeholders})
                """,
                (
                    "interrupted",
                    "TASK_INTERRUPTED",
                    "Worker restarted before task completed",
                    now.isoformat(),
                    (now + self.result_retention).isoformat(),
                    *active,
                ),
            )
            return cursor.rowcount

    def cleanup_expired(self, now: datetime | None = None, batch_size: int = 10) -> int:
        """分批删除过期终态任务，避免一次大事务长时间占用数据库。"""
        current = (now or datetime.now()).isoformat()
        batch_size = max(1, int(batch_size))
        removed = 0
        while True:
            connection = self.database.connection(timeout=0.1)
            with self.database.transaction(timeout=0.1) as conn:
                rows = connection.execute(
                    """
                    SELECT task_id FROM worker_tasks
                    WHERE expires_at <= ?
                      AND status NOT IN ('pending', 'running')
                    LIMIT ?
                    """,
                    (current, batch_size),
                ).fetchall()
                if not rows:
                    break
                conn.executemany(
                    "DELETE FROM worker_tasks WHERE task_id = ?",
                    [(row["task_id"],) for row in rows],
                )
                removed += len(rows)
                if len(rows) < batch_size:
                    break
        return removed

    @staticmethod
    def _row_to_dict(row) -> dict[str, Any]:
        result = dict(row)
        status = result["status"]
        try:
            result["status"] = TaskStatus(status)
        except ValueError:
            # 允许新状态在旧 Worker 进程内以字符串形式读取，便于升级恢复。
            result["status"] = status
        result["task"] = Task.from_dict(json.loads(result["request_json"]))
        result["result"] = (
            TaskResult.from_dict(json.loads(result["result_json"]))
            if result.get("result_json")
            else None
        )
        return result

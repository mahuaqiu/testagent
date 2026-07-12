"""Worker 执行内核的组件组装和生命周期。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from common.packaging import get_base_dir
from worker.artifacts.service import ArtifactService
from worker.devices.registry import DeviceRegistry
from worker.scheduling.scheduler import ResourceScheduler
from worker.storage.database import Database
from worker.task.recovery import recover_interrupted_tasks
from worker.task.repository import TaskRepository
from worker.task.idempotent_service import IdempotentTaskService
from worker.task.sqlite_repository import SQLiteTaskRepository
from worker.task.result import TaskResult
from worker.task.task import Task

logger = logging.getLogger(__name__)


class WorkerRuntime:
    """集中持有 Worker 本地执行内核组件。

    该类不创建平台管理器，也不改变 Action 的具体实现；平台层通过
    ``execute_callback`` 接入现有执行逻辑即可。这样任务生命周期、资源
    占用、设备事实和附件存储有清晰的所有者。
    """

    def __init__(
        self,
        execute_callback: Callable[[Task, object], TaskResult],
        *,
        base_dir: str | Path | None = None,
        repository: TaskRepository | None = None,
        result_retention_hours: int = 24,
        max_workers: int = 16,
    ) -> None:
        root = Path(base_dir or get_base_dir()).resolve()
        self.root_dir = root
        self.database = Database(root / "data" / "worker.db")
        self.repository = repository or SQLiteTaskRepository(self.database)
        self.scheduler = ResourceScheduler()
        self.device_registry = DeviceRegistry()
        self.artifact_service = ArtifactService(
            self.database,
            root / "data" / "artifacts",
            retention_hours=result_retention_hours,
        )
        self.task_service = IdempotentTaskService(
            self.repository,
            self.scheduler,
            execute_callback,
            result_retention_hours=result_retention_hours,
            max_workers=max_workers,
        )
        self._started = False

    def start(self) -> int:
        """恢复任务并开始接受本地执行请求。"""
        if self._started:
            return 0
        recovered = recover_interrupted_tasks(self.repository)
        self.task_service.cleanup_expired()
        self.artifact_service.cleanup_expired()
        self._started = True
        return recovered

    def stop(self) -> None:
        """停止任务服务，等待活动任务完成后关闭当前数据库连接。"""
        if not self._started:
            return
        self.task_service.shutdown()
        self._started = False
        self.database.close()

    @property
    def active_count(self) -> int:
        """当前活动任务数量。"""
        return self.task_service.active_count()


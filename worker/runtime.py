"""Worker 执行内核的组件组装和生命周期。"""

from __future__ import annotations

import logging
import threading
import time
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
        result_retention_hours: int = 1,
        artifact_retention_hours: int = 72,
        max_workers: int = 16,
        cleanup_interval_hours: int = 6,
    ) -> None:
        root = Path(base_dir or get_base_dir()).resolve()
        self.root_dir = root
        self.database = Database(root / "data" / "worker.db")
        self.repository = repository or SQLiteTaskRepository(
            self.database,
            result_retention_hours=result_retention_hours,
        )
        self.scheduler = ResourceScheduler()
        self.device_registry = DeviceRegistry()
        self.artifact_service = ArtifactService(
            self.database,
            root / "data" / "artifacts",
            retention_hours=artifact_retention_hours,
        )
        self.task_service = IdempotentTaskService(
            self.repository,
            self.scheduler,
            execute_callback,
            result_retention_hours=result_retention_hours,
            max_workers=max_workers,
        )
        self._started = False
        self._cleanup_interval = max(1, cleanup_interval_hours) * 3600.0
        self._stop_event = threading.Event()
        self._cleanup_thread: threading.Thread | None = None

    def start(self) -> int:
        """恢复任务并开始接受本地执行请求。"""
        if self._started:
            return 0
        recovered = recover_interrupted_tasks(self.repository)
        self._stop_event = threading.Event()
        self._cleanup_thread = threading.Thread(
            target=self._cleanup_loop, name="worker-cleanup", daemon=True
        )
        self._started = True
        try:
            self._cleanup_thread.start()
        except Exception:
            self._started = False
            raise
        return recovered

    def _run_cleanup(self) -> None:
        """在后台清理过期任务、附件文件和孤儿文件。"""
        started = time.monotonic()
        artifact_count = 0
        task_count = 0
        orphan_count = 0
        vacuumed = False
        try:
            artifact_count = self.artifact_service.cleanup_expired()
        except Exception:
            logger.exception("过期附件清理失败")
        try:
            task_count = self.task_service.cleanup_expired()
        except Exception:
            logger.exception("过期任务清理失败")
        try:
            orphan_count = self.artifact_service.cleanup_orphans()
        except Exception:
            logger.exception("孤儿附件清理失败")
        if self.task_service.active_count() == 0:
            try:
                vacuumed = self.database.compact_if_needed()
            except Exception:
                logger.exception("SQLite 空间回收失败")
        else:
            logger.debug("存在活动任务，跳过本轮 SQLite 空间回收")
        logger.info(
            "后台数据清理完成: artifacts=%d, tasks=%d, orphans=%d, vacuumed=%s, elapsed_ms=%d",
            artifact_count,
            task_count,
            orphan_count,
            vacuumed,
            int((time.monotonic() - started) * 1000),
        )

    def _cleanup_loop(self) -> None:
        """后台立即执行一次清理，之后按固定间隔重复执行。"""
        try:
            # 给 Worker 主流程留出启动窗口，清理完全在后台进行。
            if self._stop_event.wait(0.5):
                return
            self._run_cleanup()
            while not self._stop_event.wait(self._cleanup_interval):
                self._run_cleanup()
        finally:
            # 释放清理线程自己的线程局部 SQLite 连接。
            self.database.close()

    def stop(self) -> None:
        """停止任务服务，等待活动任务完成后关闭当前数据库连接。"""
        if not self._started:
            return
        self._stop_event.set()
        self.task_service.shutdown()
        if self._cleanup_thread is not None:
            self._cleanup_thread.join(timeout=5)
            if self._cleanup_thread.is_alive():
                logger.warning("后台数据清理仍在执行，停止流程不再等待")
            self._cleanup_thread = None
        self._started = False
        self.database.close()

    @property
    def active_count(self) -> int:
        """当前活动任务数量。"""
        return self.task_service.active_count()

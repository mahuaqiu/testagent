"""任务幂等服务测试。"""

import time

import pytest

from worker.scheduling.scheduler import ResourceScheduler
from worker.storage.database import Database
from worker.task.idempotent_service import IdempotentTaskService
from worker.task.result import TaskResult, TaskStatus
from worker.task.sqlite_repository import SQLiteTaskRepository
from worker.task.task import Task


def make_task(value="same"):
    task = Task.create(
        platform="web",
        actions=[{"action_type": "wait", "value": value}],
    )
    return task


def test_same_request_reuses_persisted_task(tmp_path):
    repository = SQLiteTaskRepository(Database(tmp_path / "worker.db"))
    calls = []

    def callback(task, cancel_event):
        calls.append(task.task_id)
        return TaskResult(status=TaskStatus.SUCCESS, platform=task.platform)

    service = IdempotentTaskService(repository, ResourceScheduler(), callback, max_workers=1)
    first_id, _ = service.submit_async(
        make_task(), request_id="original-request", idempotency_key="request-1"
    )
    second_id, _ = service.submit_async(
        make_task(), request_id="retry-request", idempotency_key="request-1"
    )
    time.sleep(0.05)
    assert second_id == first_id
    assert service.get_request_id(second_id) == "original-request"
    assert calls == [first_id]
    service.shutdown()


def test_same_key_with_different_request_is_rejected(tmp_path):
    repository = SQLiteTaskRepository(Database(tmp_path / "worker.db"))

    def callback(task, cancel_event):
        return TaskResult(status=TaskStatus.SUCCESS, platform=task.platform)

    service = IdempotentTaskService(repository, ResourceScheduler(), callback, max_workers=1)
    service.submit_async(make_task("one"), idempotency_key="request-1")
    with pytest.raises(Exception) as exc_info:
        service.submit_async(make_task("two"), idempotency_key="request-1")
    assert getattr(exc_info.value, "code", None) == "IDEMPOTENCY_CONFLICT"
    service.shutdown()

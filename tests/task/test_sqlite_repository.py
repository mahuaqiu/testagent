"""SQLite 任务仓储测试。"""

from datetime import datetime, timedelta

from worker.storage.database import Database
from worker.task.result import TaskResult, TaskStatus
from worker.task.sqlite_repository import SQLiteTaskRepository
from worker.task.task import Task


def make_task(task_id: str = "task-1") -> Task:
    return Task.create(
        platform="web",
        actions=[{"action_type": "wait", "value": 1}],
        generate_id=False,
    )


def test_repository_round_trip_and_repeated_read(tmp_path):
    repository = SQLiteTaskRepository(Database(tmp_path / "worker.db"))
    task = make_task()
    repository.create(
        task,
        request_id="req-1",
        status=TaskStatus.RUNNING,
        idempotency_key="idem-1",
        expires_at=datetime.now() + timedelta(hours=1),
    )

    result = TaskResult(task_id=task.task_id, status=TaskStatus.SUCCESS, platform="web")
    repository.update_status(task.task_id, TaskStatus.SUCCESS, result=result)

    first = repository.get(task.task_id)
    second = repository.get(task.task_id)
    assert first is not None
    assert second is not None
    assert first["result"].status == TaskStatus.SUCCESS
    assert second["result"].task_id == task.task_id


def test_repository_recovers_running_tasks(tmp_path):
    repository = SQLiteTaskRepository(Database(tmp_path / "worker.db"))
    task = make_task()
    repository.create(
        task,
        request_id=None,
        status=TaskStatus.RUNNING,
        idempotency_key=None,
        expires_at=datetime.now() + timedelta(hours=1),
    )

    assert repository.mark_interrupted() == 1
    row = repository.get(task.task_id)
    assert row is not None
    assert row["status"] == "interrupted"


def test_repository_cleanup_expired(tmp_path):
    repository = SQLiteTaskRepository(Database(tmp_path / "worker.db"))
    task = make_task()
    repository.create(
        task,
        request_id=None,
        status=TaskStatus.RUNNING,
        idempotency_key=None,
        expires_at=datetime.now() - timedelta(seconds=1),
    )
    assert repository.cleanup_expired() == 1
    assert repository.get(task.task_id) is None

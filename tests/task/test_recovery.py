"""任务启动恢复测试。"""

from datetime import datetime, timedelta

from worker.storage.database import Database
from worker.task.recovery import recover_interrupted_tasks
from worker.task.result import TaskStatus
from worker.task.sqlite_repository import SQLiteTaskRepository
from worker.task.task import Task


def test_recover_marks_running_tasks_interrupted(tmp_path):
    database = Database(tmp_path / "worker.db")
    repository = SQLiteTaskRepository(database)
    task = Task.create(platform="web", actions=[], generate_id=False)
    task.task_id = "task-running"
    repository.create(
        task,
        request_id="req-1",
        status=TaskStatus.RUNNING,
        idempotency_key=None,
        expires_at=datetime.now() + timedelta(hours=1),
    )

    assert recover_interrupted_tasks(repository) == 1
    row = repository.get(task.task_id)
    assert row["status"] == "interrupted"
    assert row["error_code"] == "TASK_INTERRUPTED"


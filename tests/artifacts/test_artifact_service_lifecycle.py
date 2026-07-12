"""附件服务与任务外键生命周期测试。"""

from datetime import datetime, timedelta

from worker.artifacts.service import ArtifactService
from worker.storage.database import Database
from worker.task.result import TaskStatus
from worker.task.sqlite_repository import SQLiteTaskRepository
from worker.task.task import Task


def test_artifact_requires_existing_task(tmp_path):
    database = Database(tmp_path / "worker.db")
    task = Task.create(platform="web", actions=[], generate_id=False)
    SQLiteTaskRepository(database).create(
        task,
        request_id=None,
        status=TaskStatus.RUNNING,
        idempotency_key=None,
        expires_at=datetime.now() + timedelta(hours=1),
    )
    service = ArtifactService(database, tmp_path / "artifacts")
    reference = service.save_bytes(
        task.task_id,
        b"image-data",
        artifact_type="screenshot",
        mime_type="image/jpeg",
        extension="jpg",
    )
    assert service.get_path(reference.artifact_id)[1].read_bytes() == b"image-data"

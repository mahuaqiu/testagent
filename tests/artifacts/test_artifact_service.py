"""附件服务测试。"""

from worker.artifacts.service import ArtifactService
from worker.storage.database import Database


def test_artifact_service_saves_and_reads_safe_path(tmp_path):
    service = ArtifactService(Database(tmp_path / "worker.db"), tmp_path / "artifacts")
    from datetime import datetime, timedelta
    from worker.task.result import TaskStatus
    from worker.task.task import Task
    from worker.task.sqlite_repository import SQLiteTaskRepository
    repository = SQLiteTaskRepository(service.database)
    task = Task.create(platform="web", actions=[], generate_id=False)
    task.task_id = "task-1"
    repository.create(task, request_id=None, status=TaskStatus.RUNNING, idempotency_key=None, expires_at=datetime.now() + timedelta(hours=1))
    reference = service.save_bytes(
        "task-1",
        b"image-data",
        artifact_type="screenshot",
        mime_type="image/jpeg",
        extension="jpg",
    )
    found = service.get_path(reference.artifact_id)
    assert found is not None
    assert found[0].sha256 == reference.sha256
    assert found[1].read_bytes() == b"image-data"


def test_artifact_service_rejects_bad_extension(tmp_path):
    service = ArtifactService(Database(tmp_path / "worker.db"), tmp_path / "artifacts")
    try:
        service.save_bytes(
            "task-1",
            b"data",
            artifact_type="file",
            mime_type="application/octet-stream",
            extension="../txt",
        )
    except ValueError:
        pass
    else:
        raise AssertionError("bad extension must be rejected")

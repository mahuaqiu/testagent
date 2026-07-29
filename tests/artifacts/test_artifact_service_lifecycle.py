"""附件服务与任务外键生命周期测试。"""

import os
import time
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


def _create_task(database: Database, expires_at: datetime) -> Task:
    task = Task.create(platform="web", actions=[], generate_id=False)
    SQLiteTaskRepository(database).create(
        task,
        request_id=None,
        status=TaskStatus.RUNNING,
        idempotency_key=None,
        expires_at=expires_at,
    )
    return task


def test_cleanup_expired_removes_files_of_expired_task(tmp_path):
    """任务过期但附件本身未过期时，附件文件也必须被删除。"""
    database = Database(tmp_path / "worker.db")
    task = _create_task(database, datetime.now() - timedelta(hours=1))
    service = ArtifactService(database, tmp_path / "artifacts")
    reference = service.save_bytes(
        task.task_id,
        b"image-data",
        artifact_type="screenshot",
        mime_type="image/jpeg",
        extension="jpg",
    )
    file_path = tmp_path / "artifacts" / reference.relative_path
    assert file_path.is_file()

    assert service.cleanup_expired() == 1
    assert not file_path.exists()
    assert service.get_path(reference.artifact_id) is None


def test_cleanup_orphans_removes_stale_files_only(tmp_path):
    """孤儿清理只删超过保留期的无元数据文件，保留新文件与在库文件。"""
    database = Database(tmp_path / "worker.db")
    task = _create_task(database, datetime.now() + timedelta(hours=1))
    service = ArtifactService(database, tmp_path / "artifacts")
    reference = service.save_bytes(
        task.task_id,
        b"image-data",
        artifact_type="screenshot",
        mime_type="image/jpeg",
        extension="jpg",
    )
    tracked = tmp_path / "artifacts" / reference.relative_path

    # 旧孤儿文件：mtime 超过保留期，应被删除（目录一并移除）
    stale_dir = tmp_path / "artifacts" / "task_gone"
    stale_dir.mkdir()
    stale = stale_dir / "orphan.jpg"
    stale.write_bytes(b"old")
    old = time.time() - 48 * 3600
    os.utime(stale, (old, old))

    # 新孤儿文件：尚在宽限期内，不应被删除
    fresh = stale_dir.parent / "task_fresh"
    fresh.mkdir()
    (fresh / "pending.jpg").write_bytes(b"new")

    assert service.cleanup_orphans() == 1
    assert not stale_dir.exists()
    assert (fresh / "pending.jpg").is_file()
    assert tracked.is_file()

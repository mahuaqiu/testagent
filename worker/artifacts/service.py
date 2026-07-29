"""本地任务附件服务。"""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from worker.artifacts.models import ArtifactRef
from worker.storage.database import Database


class ArtifactService:
    """保存截图、录屏等附件并维护 SQLite 元数据。"""

    def __init__(self, database: Database, root_dir: str | Path, retention_hours: int = 24):
        self.database = database
        self.root_dir = Path(root_dir).resolve()
        self.retention = timedelta(hours=max(1, retention_hours))
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def save_bytes(
        self,
        task_id: str,
        data: bytes,
        *,
        artifact_type: str,
        mime_type: str,
        extension: str,
        action_number: int | None = None,
    ) -> ArtifactRef:
        """安全保存二进制附件并写入元数据。"""
        if not data:
            raise ValueError("artifact data must not be empty")
        extension = extension.lstrip(".").lower()
        if not extension or not extension.isalnum() or len(extension) > 8:
            raise ValueError("invalid artifact extension")

        artifact_id = f"artifact_{uuid.uuid4().hex}"
        relative_path = Path(task_id) / f"{artifact_id}.{extension}"
        target = (self.root_dir / relative_path).resolve()
        if self.root_dir not in target.parents:
            raise ValueError("artifact path escapes root directory")
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix(target.suffix + ".tmp")
        temp.write_bytes(data)
        os.replace(temp, target)

        now = datetime.now()
        ref = ArtifactRef(
            artifact_id=artifact_id,
            task_id=task_id,
            artifact_type=artifact_type,
            mime_type=mime_type,
            relative_path=str(relative_path).replace("\\", "/"),
            size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            created_at=now,
            expires_at=now + self.retention,
            action_number=action_number,
        )
        with self.database.transaction() as conn:
            conn.execute(
                """
                INSERT INTO artifacts(
                    artifact_id, task_id, action_number, artifact_type, mime_type,
                    relative_path, size, sha256, created_at, expires_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ref.artifact_id,
                    ref.task_id,
                    ref.action_number,
                    ref.artifact_type,
                    ref.mime_type,
                    ref.relative_path,
                    ref.size,
                    ref.sha256,
                    ref.created_at.isoformat(),
                    ref.expires_at.isoformat(),
                ),
            )
        return ref

    def get_path(self, artifact_id: str) -> tuple[ArtifactRef, Path] | None:
        """查找附件元数据和安全路径。"""
        row = self.database.connection().execute(
            "SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)
        ).fetchone()
        if row is None:
            return None
        ref = self._from_row(row)
        path = (self.root_dir / ref.relative_path).resolve()
        if self.root_dir not in path.parents or not path.is_file():
            return None
        return ref, path

    def save_file(
        self,
        task_id: str,
        source_path: str | Path,
        *,
        artifact_type: str,
        mime_type: str,
        extension: str | None = None,
        action_number: int | None = None,
    ) -> ArtifactRef:
        """复制已有文件到受控附件目录并登记元数据。"""
        source = Path(source_path).resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        suffix = extension or source.suffix.lstrip(".")
        return self.save_bytes(
            task_id,
            source.read_bytes(),
            artifact_type=artifact_type,
            mime_type=mime_type,
            extension=suffix,
            action_number=action_number,
        )

    def cleanup_expired(self, now: datetime | None = None) -> int:
        """删除过期附件（含所属任务已过期的附件），返回删除数量。

        必须先于任务清理执行：任务行删除时外键会级联清除 artifacts
        元数据但不会触碰磁盘文件，因此这里把所属任务已过期的附件也
        一并按元数据删文件，避免留下孤儿文件。
        """
        current = now or datetime.now()
        rows = self.database.connection().execute(
            """
            SELECT artifact_id, relative_path FROM artifacts
            WHERE expires_at <= ?
               OR task_id IN (SELECT task_id FROM worker_tasks WHERE expires_at <= ?)
            """,
            (current.isoformat(), current.isoformat()),
        ).fetchall()
        removed = 0
        with self.database.transaction() as conn:
            for row in rows:
                path = (self.root_dir / row["relative_path"]).resolve()
                if self.root_dir in path.parents and path.is_file():
                    path.unlink()
                conn.execute("DELETE FROM artifacts WHERE artifact_id = ?", (row["artifact_id"],))
                removed += 1
        return removed

    def cleanup_orphans(self, now: datetime | None = None) -> int:
        """删除没有元数据行的孤儿附件文件，返回删除数量。

        兜底清理历史遗留（级联删除元数据后残留的文件、写入中断的
        .tmp 文件等）。只删除超过保留期未修改的文件，避免误删刚落盘
        但元数据尚未提交的附件。
        """
        current = now or datetime.now()
        known = {
            row["relative_path"]
            for row in self.database.connection()
            .execute("SELECT relative_path FROM artifacts")
            .fetchall()
        }
        removed = 0
        for path in self.root_dir.rglob("*"):
            if not path.is_file():
                continue
            relative = str(path.relative_to(self.root_dir)).replace("\\", "/")
            if relative in known:
                continue
            modified = datetime.fromtimestamp(path.stat().st_mtime)
            if modified + self.retention > current:
                continue
            path.unlink()
            removed += 1
        # 顺带移除清空后的任务目录
        for directory in self.root_dir.glob("*"):
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()
        return removed

    @staticmethod
    def _from_row(row) -> ArtifactRef:
        return ArtifactRef(
            artifact_id=row["artifact_id"],
            task_id=row["task_id"],
            action_number=row["action_number"],
            artifact_type=row["artifact_type"],
            mime_type=row["mime_type"],
            relative_path=row["relative_path"],
            size=row["size"],
            sha256=row["sha256"],
            created_at=datetime.fromisoformat(row["created_at"]),
            expires_at=datetime.fromisoformat(row["expires_at"]),
        )

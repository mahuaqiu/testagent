"""本地任务附件服务。"""

from __future__ import annotations

import hashlib
import logging
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from worker.artifacts.models import ArtifactRef
from worker.storage.database import Database

logger = logging.getLogger(__name__)


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

    def cleanup_expired(self, now: datetime | None = None, batch_size: int = 10) -> int:
        """分批删除超过附件保留期的附件文件和元数据，返回删除数量。"""
        current = now or datetime.now()
        cutoff = current - self.retention
        batch_size = max(1, int(batch_size))
        removed = 0
        while True:
            rows = self.database.connection(timeout=0.1).execute(
                """
                SELECT artifact_id, relative_path FROM artifacts
                WHERE created_at <= ?
                LIMIT ?
                """,
                (cutoff.isoformat(), batch_size),
            ).fetchall()
            if not rows:
                break

            # 先删物理文件，再删元数据；文件删除失败时保留记录，下一轮继续重试。
            artifact_ids = []
            for row in rows:
                candidate = self.root_dir / Path(row["relative_path"])
                path = candidate.resolve()
                if self.root_dir not in candidate.absolute().parents or self.root_dir not in path.parents:
                    logger.warning("跳过目录外的附件路径: %s", row["relative_path"])
                    continue
                try:
                    if candidate.is_file() or candidate.is_symlink():
                        candidate.unlink()
                    # 文件已经不存在也视为清理成功，只删除无效的元数据。
                    artifact_ids.append((row["artifact_id"],))
                except OSError:
                    logger.warning("删除过期附件文件失败: %s", candidate, exc_info=True)

            if not artifact_ids:
                break
            with self.database.transaction(timeout=0.1) as conn:
                conn.executemany(
                    "DELETE FROM artifacts WHERE artifact_id = ?",
                    artifact_ids,
                )
            removed += len(artifact_ids)
            if len(rows) < batch_size:
                break
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
            for row in self.database.connection(timeout=0.1)
            .execute("SELECT relative_path FROM artifacts")
            .fetchall()
        }
        removed = 0
        # 自底向上扫描，既清理孤儿文件，也清理对应的空目录。
        for directory_path, directory_names, file_names in os.walk(
            self.root_dir, topdown=False
        ):
            directory = Path(directory_path)
            for file_name in file_names:
                path = directory / file_name
                relative = str(path.relative_to(self.root_dir)).replace("\\", "/")
                if relative in known:
                    continue
                try:
                    modified = datetime.fromtimestamp(path.stat().st_mtime)
                    if modified + self.retention > current:
                        continue
                    path.unlink()
                    removed += 1
                except OSError:
                    logger.warning("删除孤儿附件文件失败: %s", path, exc_info=True)
            for directory_name in directory_names:
                child = directory / directory_name
                try:
                    child.rmdir()
                except OSError:
                    pass
        try:
            self.root_dir.rmdir()
        except OSError:
            pass
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

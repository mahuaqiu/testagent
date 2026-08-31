"""SQLite 数据库连接和初始化。"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Iterator


class Database:
    """为 Worker 提供线程安全的 SQLite 连接工厂。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._local = threading.local()
        self._init_lock = threading.Lock()
        self._maintenance_lock = threading.Lock()
        self._initialized = False

    def connection(self, timeout: float = 10.0) -> sqlite3.Connection:
        """获取当前线程连接。"""
        connection = getattr(self._local, "connection", None)
        if connection is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fresh_database = not self.path.exists() or self.path.stat().st_size == 0
            connection = sqlite3.connect(
                self.path,
                timeout=max(0.0, timeout),
                isolation_level=None,
                check_same_thread=True,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute(f"PRAGMA busy_timeout={max(0, int(timeout * 1000))}")
            if fresh_database:
                # 新库直接启用增量回收，不做旧库迁移。
                connection.execute("PRAGMA auto_vacuum=INCREMENTAL")
            connection.execute("PRAGMA journal_mode=WAL")
            self._local.connection = connection
        self.initialize(connection)
        return connection

    def initialize(self, connection: sqlite3.Connection | None = None) -> None:
        """创建当前版本所需表。"""
        if self._initialized:
            return
        with self._init_lock:
            if self._initialized:
                return
            conn = connection or self.connection()
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS worker_tasks (
                    task_id TEXT PRIMARY KEY,
                    platform_task_id TEXT,
                    idempotency_key TEXT UNIQUE,
                    request_id TEXT,
                    platform TEXT NOT NULL,
                    device_id TEXT,
                    status TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    result_json TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    retryable INTEGER NOT NULL DEFAULT 0,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    expires_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_worker_tasks_status ON worker_tasks(status);
                CREATE INDEX IF NOT EXISTS idx_worker_tasks_expires ON worker_tasks(expires_at);
                CREATE INDEX IF NOT EXISTS idx_worker_tasks_platform_task ON worker_tasks(platform_task_id);

                CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    action_number INTEGER,
                    artifact_type TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_artifacts_task ON artifacts(task_id);
                CREATE INDEX IF NOT EXISTS idx_artifacts_expires ON artifacts(expires_at);
                """
            )
            conn.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('schema_version', '1')"
            )
            self._initialized = True

    def compact_if_needed(
        self,
        *,
        minimum_free_pages: int = 256,
        minimum_free_ratio: float = 0.25,
    ) -> bool:
        """在后台回收大量 SQLite 空闲页，返回是否执行了压缩。"""
        if not self._maintenance_lock.acquire(blocking=False):
            return False
        connection = None
        try:
            # 使用短连接和短超时，主任务繁忙时直接跳过本轮维护。
            connection = sqlite3.connect(
                self.path,
                timeout=0.1,
                isolation_level=None,
                check_same_thread=True,
            )
            connection.execute("PRAGMA busy_timeout=100")
            connection.execute("PRAGMA wal_checkpoint(PASSIVE)")
            page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
            free_pages = int(connection.execute("PRAGMA freelist_count").fetchone()[0])
            if free_pages < minimum_free_pages:
                return False
            if page_count <= 0 or free_pages / page_count < minimum_free_ratio:
                return False

            # 只对新库使用增量回收，不对旧库做兼容迁移或全量 VACUUM。
            # 旧库由覆盖安装删除整个 data 目录后重新创建。
            if int(connection.execute("PRAGMA auto_vacuum").fetchone()[0]) != 2:
                return False
            connection.execute(
                f"PRAGMA incremental_vacuum({min(free_pages, 4096)})"
            )
            return True
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                return False
            raise
        finally:
            if connection is not None:
                connection.close()
            self._maintenance_lock.release()

    def transaction(self, timeout: float = 10.0) -> Iterator[sqlite3.Connection]:
        """提供事务上下文。"""
        class _Transaction:
            def __init__(self, database: Database, timeout: float):
                self.database = database
                self.timeout = timeout
                self.connection: sqlite3.Connection | None = None

            def __enter__(self) -> sqlite3.Connection:
                self.connection = self.database.connection(self.timeout)
                self.connection.execute("BEGIN IMMEDIATE")
                return self.connection

            def __exit__(self, exc_type, exc_value, traceback) -> None:
                assert self.connection is not None
                if exc_type is None:
                    self.connection.commit()
                else:
                    self.connection.rollback()

        return _Transaction(self, timeout)  # type: ignore[return-value]

    def close(self) -> None:
        """关闭当前线程连接。"""
        connection = getattr(self._local, "connection", None)
        if connection is not None:
            connection.close()
            self._local.connection = None

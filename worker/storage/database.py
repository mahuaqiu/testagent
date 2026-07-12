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
        self._initialized = False

    def connection(self) -> sqlite3.Connection:
        """获取当前线程连接。"""
        connection = getattr(self._local, "connection", None)
        if connection is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(
                self.path,
                timeout=10,
                isolation_level=None,
                check_same_thread=True,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=10000")
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

                CREATE TABLE IF NOT EXISTS task_actions (
                    task_id TEXT NOT NULL,
                    action_number INTEGER NOT NULL,
                    action_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    duration_ms INTEGER,
                    output_json TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    PRIMARY KEY(task_id, action_number),
                    FOREIGN KEY(task_id) REFERENCES worker_tasks(task_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS resource_leases (
                    resource_key TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    acquired_at TEXT NOT NULL,
                    released_at TEXT,
                    release_reason TEXT
                );

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
                    expires_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES worker_tasks(task_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_artifacts_task ON artifacts(task_id);
                CREATE INDEX IF NOT EXISTS idx_artifacts_expires ON artifacts(expires_at);
                """
            )
            conn.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('schema_version', '1')"
            )
            self._initialized = True

    def transaction(self) -> Iterator[sqlite3.Connection]:
        """提供事务上下文。"""
        class _Transaction:
            def __init__(self, database: Database):
                self.database = database
                self.connection: sqlite3.Connection | None = None

            def __enter__(self) -> sqlite3.Connection:
                self.connection = self.database.connection()
                self.connection.execute("BEGIN IMMEDIATE")
                return self.connection

            def __exit__(self, exc_type, exc_value, traceback) -> None:
                assert self.connection is not None
                if exc_type is None:
                    self.connection.commit()
                else:
                    self.connection.rollback()

        return _Transaction(self)  # type: ignore[return-value]

    def close(self) -> None:
        """关闭当前线程连接。"""
        connection = getattr(self._local, "connection", None)
        if connection is not None:
            connection.close()
            self._local.connection = None

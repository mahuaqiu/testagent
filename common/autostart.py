"""开机自启模块。

db 的 schema_meta.auto_start 是自启状态的唯一真相源，
HKLM\\...\\Run 注册表项是它的投影。所有操作失败只记日志，不抛异常。
"""

import logging
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 注册表 Run 键路径（系统级，所有用户登录生效）
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "test-worker"
AUTO_START_KEY = "auto_start"

# 非 Windows 平台 winreg 不可用，模块仍可安全 import
if sys.platform == "win32":
    import winreg
else:
    winreg = None


def _db_path() -> Path:
    """返回 worker.db 路径（可被测试 monkeypatch）。"""
    from common.packaging import get_base_dir
    return Path(get_base_dir()) / "data" / "worker.db"


def _get_db():
    """获取临时 db 连接工厂（短连接，低频读写）。"""
    from worker.storage.database import Database
    return Database(_db_path())


def _read_db_flag() -> Optional[str]:
    """读 schema_meta.auto_start，无 key 返回 None。失败返回 None。"""
    try:
        db = _get_db()
        conn = db.connection()
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key = ?", (AUTO_START_KEY,)
        ).fetchone()
        return row["value"] if row else None
    except Exception as e:
        logger.warning(f"读取自启标志失败: {e}")
        return None


def _write_db_flag(value: bool) -> None:
    """写 schema_meta.auto_start。失败只记日志。"""
    try:
        db = _get_db()
        conn = db.connection()
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES(?, ?)",
            (AUTO_START_KEY, "true" if value else "false"),
        )
        conn.commit()
    except Exception as e:
        logger.warning(f"写入自启标志失败: {e}")


def is_enabled() -> bool:
    """读 db 的 schema_meta.auto_start，返回当前自启意愿。

    db 无该 key 时返回 False（未初始化视为关闭）。
    非 Windows 平台返回 False。
    """
    if winreg is None:
        return False
    flag = _read_db_flag()
    return flag == "true"

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


def _exe_path() -> str:
    """返回自启要执行的 exe 全路径（打包后即 test-worker.exe）。"""
    return sys.executable


def _write_registry() -> None:
    """写 HKLM Run 值，数据为带引号的 exe 路径。失败只记日志。"""
    if winreg is None:
        return
    try:
        key = winreg.CreateKeyEx(
            winreg.HKEY_LOCAL_MACHINE, RUN_KEY, 0, winreg.KEY_SET_VALUE
        )
        try:
            winreg.SetValueEx(
                key, VALUE_NAME, 0, winreg.REG_SZ, f'"{_exe_path()}"'
            )
        finally:
            key.Close()
    except Exception as e:
        logger.warning(f"写入自启注册表失败: {e}")


def _delete_registry() -> None:
    """删 HKLM Run 值。键不存在视为成功。失败只记日志。"""
    if winreg is None:
        return
    try:
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, RUN_KEY, 0, winreg.KEY_SET_VALUE
        )
        try:
            winreg.DeleteValue(key, VALUE_NAME)
        finally:
            key.Close()
    except FileNotFoundError:
        # 值不存在视为成功
        pass
    except Exception as e:
        logger.warning(f"删除自启注册表失败: {e}")


def _registry_has_value() -> bool:
    """查询 HKLM Run 是否存在 test-worker 值。失败返回 False。"""
    if winreg is None:
        return False
    try:
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, RUN_KEY, 0, winreg.KEY_READ
        )
        try:
            winreg.QueryValueEx(key, VALUE_NAME)
            return True
        finally:
            key.Close()
    except FileNotFoundError:
        return False
    except Exception as e:
        logger.warning(f"查询自启注册表失败: {e}")
        return False


def set_enabled(enabled: bool) -> None:
    """设置自启状态：先写 db，再同步注册表。

    enabled=True→写 HKLM Run 键；enabled=False→删 Run 键。
    注册表操作失败不抛异常（只记日志），db 已更新成功即视为设置成功。
    """
    _write_db_flag(enabled)
    if enabled:
        _write_registry()
    else:
        _delete_registry()


def seed_from_registry() -> None:
    """首次启动播种：db 无 auto_start key 时，读注册表有无 test-worker
    值来初始化 db。已有则跳过。幂等、不抛异常（失败只记日志）。
    """
    if winreg is None:
        return
    try:
        if _read_db_flag() is not None:
            # db 已有标志，不覆盖
            return
        has_value = _registry_has_value()
        _write_db_flag(has_value)
        logger.info(f"自启状态已从注册表播种到 db: {has_value}")
    except Exception as e:
        logger.warning(f"播种自启状态失败: {e}")

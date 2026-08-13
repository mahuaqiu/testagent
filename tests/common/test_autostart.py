"""开机自启模块测试。"""

import sys
from pathlib import Path

import pytest

from worker.storage.database import Database


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """让 autostart 模块使用临时目录的 db。"""
    db_path = tmp_path / "worker.db"
    # 初始化 schema_meta 表
    Database(db_path).connection().close()
    import common.autostart as autostart
    monkeypatch.setattr(autostart, "_db_path", lambda: db_path)
    return db_path


def test_is_enabled_returns_false_when_no_flag(isolated_db):
    """db 无 auto_start key 时返回 False。"""
    import common.autostart as autostart

    assert autostart.is_enabled() is False


def test_set_enabled_true_writes_db_flag_true(isolated_db):
    """set_enabled(True) 应把 db 标志写为 true。"""
    import common.autostart as autostart

    autostart.set_enabled(True)

    assert autostart._read_db_flag() == "true"
    assert autostart.is_enabled() is True


def test_set_enabled_false_writes_db_flag_false(isolated_db):
    """set_enabled(False) 应把 db 标志写为 false。"""
    import common.autostart as autostart

    autostart.set_enabled(False)

    assert autostart._read_db_flag() == "false"
    assert autostart.is_enabled() is False

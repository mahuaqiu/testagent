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


def test_seed_writes_true_when_registry_has_value_and_db_empty(isolated_db, monkeypatch):
    """db 无 key 且注册表有值时，播种应写 db 为 true。"""
    import common.autostart as autostart

    monkeypatch.setattr(autostart, "_registry_has_value", lambda: True)

    autostart.seed_from_registry()

    assert autostart._read_db_flag() == "true"
    assert autostart.is_enabled() is True


def test_seed_writes_false_when_registry_empty_and_db_empty(isolated_db, monkeypatch):
    """db 无 key 且注册表无值时，播种应写 db 为 false。"""
    import common.autostart as autostart

    monkeypatch.setattr(autostart, "_registry_has_value", lambda: False)

    autostart.seed_from_registry()

    assert autostart._read_db_flag() == "false"
    assert autostart.is_enabled() is False


def test_seed_idempotent_when_db_already_set(isolated_db, monkeypatch):
    """db 已有 key 时，播种不应改写（即使注册表状态不同）。"""
    import common.autostart as autostart

    autostart._write_db_flag(False)
    # 注册表显示有值，但 db 已设为 false，播种不应覆盖
    monkeypatch.setattr(autostart, "_registry_has_value", lambda: True)

    autostart.seed_from_registry()

    assert autostart._read_db_flag() == "false"

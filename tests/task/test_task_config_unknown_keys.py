# tests/task/test_task_config_unknown_keys.py
"""TaskConfig.from_dict 对未知键的告警行为。"""
import logging

from worker.task.task import TaskConfig


def test_known_keys_no_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="worker.task.task"):
        cfg = TaskConfig.from_dict({"timeout": 1000, "retry_count": 2})
    assert cfg.timeout == 1000 and cfg.retry_count == 2
    assert not [r for r in caplog.records if "未知配置键" in r.message]


def test_unknown_keys_warn_but_defaults_apply(caplog):
    with caplog.at_level(logging.WARNING, logger="worker.task.task"):
        cfg = TaskConfig.from_dict({"time_out": 1000, "timeout": 2000})
    assert cfg.timeout == 2000  # 已知键不受影响
    warnings = [r for r in caplog.records if "未知配置键" in r.message]
    assert warnings and "time_out" in warnings[0].getMessage()

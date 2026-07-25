"""Worker 性能接口的设备身份契约测试。"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

import worker.server as server_module


def _worker_with_record(record=None):
    """构造只包含性能接口所需字段的 Worker 替身。"""
    registry = MagicMock()
    registry.get.return_value = record
    config = SimpleNamespace(
        platform_api="",
        get_platform_config=lambda platform: {"hdc_path": "tools/hdc/hdc.exe"}
        if platform in {"harmony_pc", "harmony_mobile"}
        else {},
    )
    return SimpleNamespace(device_registry=registry, config=config)


def _harmony_record(**kwargs):
    """构造在线鸿蒙设备记录。"""
    values = {
        "connection_status": "connected",
        "health_status": "healthy",
    }
    values.update(kwargs)
    return SimpleNamespace(**values)


def test_harmony_uses_hdc_udid_independent_of_database_id():
    """URL 中的数据库 ID 不应替代 HDC UDID。"""
    record = _harmony_record()
    worker = _worker_with_record(record)
    collector = MagicMock()
    with (
        patch.object(server_module, "worker", worker),
        patch.object(server_module, "get_collector", return_value=collector),
        patch.object(
            server_module,
            "_find_hdc_path",
            return_value="tools/hdc/hdc.exe",
        ),
    ):
        result, device_type, device_sn = server_module._prepare_performance_collector(
            "env-machine-id", "harmony_mobile", "HDC-UDID-001"
        )

    assert result is collector
    assert device_type == "harmony_mobile"
    assert device_sn == "HDC-UDID-001"
    worker.device_registry.get.assert_called_once_with("harmony_mobile", "HDC-UDID-001")
    collector.configure_device.assert_called_once_with(
        "harmony_mobile", "HDC-UDID-001", "tools/hdc/hdc.exe"
    )


def test_harmony_requires_device_sn():
    """鸿蒙请求缺少 HDC UDID 时在创建 Collector 前拒绝。"""
    worker = _worker_with_record()
    with patch.object(server_module, "worker", worker):
        with pytest.raises(HTTPException) as error:
            server_module._prepare_performance_collector("env-machine-id", "harmony_pc", None)
    assert error.value.status_code == 400
    worker.device_registry.get.assert_not_called()


def test_unknown_harmony_udid_is_rejected():
    """未注册的 HDC UDID 不能启动采集。"""
    worker = _worker_with_record(None)
    with patch.object(server_module, "worker", worker):
        with pytest.raises(HTTPException) as error:
            server_module._prepare_performance_collector("env-machine-id", "harmony_pc", "UNKNOWN")
    assert error.value.status_code == 404


def test_disconnected_harmony_device_is_rejected():
    """注册表中的离线设备不能启动采集。"""
    worker = _worker_with_record(_harmony_record(connection_status="disconnected"))
    with patch.object(server_module, "worker", worker):
        with pytest.raises(HTTPException) as error:
            server_module._prepare_performance_collector("env-machine-id", "harmony_pc", "HDC-001")
    assert error.value.status_code == 409


def test_unhealthy_harmony_device_is_rejected():
    """注册表中的不健康设备不能启动采集。"""
    worker = _worker_with_record(_harmony_record(health_status="unhealthy"))
    with patch.object(server_module, "worker", worker):
        with pytest.raises(HTTPException) as error:
            server_module._prepare_performance_collector("env-machine-id", "harmony_pc", "HDC-001")
    assert error.value.status_code == 409


def test_unsupported_type_is_rejected_without_registry_fallback():
    """未知类型不能静默回退为 Windows。"""
    worker = _worker_with_record()
    with patch.object(server_module, "worker", worker):
        with pytest.raises(HTTPException) as error:
            server_module._prepare_performance_collector("env-machine-id", "mac", None)
    assert error.value.status_code == 400
    worker.device_registry.get.assert_not_called()


def test_legacy_windows_request_defaults_to_windows():
    """旧 Windows 调用缺少类型时仍可走兼容路径。"""
    worker = _worker_with_record()
    collector = MagicMock()
    with (
        patch.object(server_module, "worker", worker),
        patch.object(server_module, "get_collector", return_value=collector),
    ):
        _, device_type, device_sn = server_module._prepare_performance_collector(
            "env-machine-id", None, None
        )
    assert device_type == "windows"
    assert device_sn is None
    collector.configure_device.assert_called_once_with("windows", None, None)


def test_stop_without_identity_keeps_existing_collector():
    """stop/status 缺身份时不应把正在运行的鸿蒙 Collector 重新配成 Windows。"""
    worker = _worker_with_record()
    collector = MagicMock()
    collector._device_type = "harmony_pc"
    collector._device_sn = "HDC-UDID-001"
    with (
        patch.object(server_module, "worker", worker),
        patch.object(server_module, "get_collector", return_value=collector),
    ):
        result, device_type, device_sn = server_module._prepare_performance_collector(
            "env-machine-id",
            None,
            None,
            require_identity=False,
        )
    assert result is collector
    assert device_type == "harmony_pc"
    assert device_sn == "HDC-UDID-001"
    collector.configure_device.assert_not_called()


def test_harmony_hdc_path_is_resolved_before_configure():
    """性能路径应先把 SDK 根/相对路径解析成 hdc 可执行文件。"""
    record = _harmony_record()
    worker = _worker_with_record(record)
    collector = MagicMock()
    with (
        patch.object(server_module, "worker", worker),
        patch.object(server_module, "get_collector", return_value=collector),
        patch.object(
            server_module,
            "_find_hdc_path",
            return_value="D:/resolved/tools/hdc/hdc.exe",
        ) as find_hdc,
    ):
        server_module._prepare_performance_collector(
            "env-machine-id", "harmony_mobile", "HDC-UDID-001"
        )
    find_hdc.assert_called_once_with("tools/hdc/hdc.exe")
    collector.configure_device.assert_called_once_with(
        "harmony_mobile",
        "HDC-UDID-001",
        "D:/resolved/tools/hdc/hdc.exe",
    )

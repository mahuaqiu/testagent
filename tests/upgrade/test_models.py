"""
升级模块数据模型测试。
"""

import pytest
from worker.upgrade.models import (
    UpgradeStatus,
    UpgradeRequest,
    UpgradeResponse,
    UpgradeState,
)


def test_upgrade_status_enum():
    """测试升级状态枚举值。"""
    assert UpgradeStatus.SKIPPED.value == "skipped"
    assert UpgradeStatus.DOWNLOADING.value == "downloading"
    assert UpgradeStatus.INSTALLING.value == "installing"
    assert UpgradeStatus.COMPLETED.value == "completed"
    assert UpgradeStatus.FAILED.value == "failed"


def test_upgrade_request_defaults():
    """测试升级请求默认值。"""
    request = UpgradeRequest(download_url="http://example.com/installer.exe")
    assert request.version is None
    assert request.download_url == "http://example.com/installer.exe"
    assert request.force is True


def test_upgrade_request_with_version():
    """测试升级请求带版本号。"""
    request = UpgradeRequest(
        version="20260408150000",
        download_url="http://example.com/installer.exe",
        force=False,
    )
    assert request.version == "20260408150000"
    assert request.force is False


def test_upgrade_response_to_dict():
    """测试升级响应序列化。"""
    response = UpgradeResponse(
        status="skipped",
        message="当前版本已是最新",
        current_version="20260408150000",
        target_version="20260408150000",
    )
    result = response.to_dict()
    assert result["status"] == "skipped"
    assert result["message"] == "当前版本已是最新"
    assert result["current_version"] == "20260408150000"
    assert result["target_version"] == "20260408150000"


def test_upgrade_state_to_dict():
    """测试升级状态序列化。"""
    state = UpgradeState(
        status="downloading",
        target_version="20260408150000",
        current_version="20260405120000",
        download_url="http://example.com/installer.exe",
        started_at="2026-04-08T15:00:00",
    )
    result = state.to_dict()
    assert result["status"] == "downloading"
    assert result["target_version"] == "20260408150000"
    assert result["current_version"] == "20260405120000"
    assert result["download_url"] == "http://example.com/installer.exe"
    assert result["started_at"] == "2026-04-08T15:00:00"
    assert result["completed_at"] is None
    assert result["error"] is None
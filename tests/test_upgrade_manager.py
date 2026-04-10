import pytest
from worker.upgrade_manager import UpgradeManager, UpgradeInfo


def test_upgrade_info_from_response():
    """测试从响应创建 UpgradeInfo。"""
    response = {
        "version": "202604101500",
        "download_url": "http://example.com/download.exe"
    }
    info = UpgradeInfo.from_response(response)
    assert info.version == "202604101500"
    assert info.download_url == "http://example.com/download.exe"


def test_is_newer_version():
    """测试版本比较。"""
    current = "202604101400"

    # 新版本
    assert UpgradeManager.is_newer_version(current, "202604101500") is True

    # 相同版本
    assert UpgradeManager.is_newer_version(current, "202604101400") is False

    # 旧版本
    assert UpgradeManager.is_newer_version(current, "202604101300") is False
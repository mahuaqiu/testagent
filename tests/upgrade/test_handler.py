"""
升级 HTTP 接口处理测试。
"""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime

from worker.upgrade.handler import (
    get_current_version,
    handle_upgrade,
    UpgradeError,
)
from worker.upgrade.models import UpgradeRequest, UpgradeResponse


class TestGetCurrentVersion:
    """测试获取当前版本。"""

    def test_get_version_exists(self):
        """测试版本模块存在。"""
        mock_version = MagicMock()
        mock_version.VERSION = "20260408150000"

        with patch.dict('sys.modules', {'worker._version': mock_version}):
            result = get_current_version()
            assert result == "20260408150000"

    def test_get_version_not_exists(self):
        """测试版本模块不存在。"""
        with patch.dict('sys.modules', {}, clear=True):
            # ImportError 会被捕获
            result = get_current_version()
            assert result is None


class TestHandleUpgrade:
    """测试升级请求处理。"""

    @pytest.mark.asyncio
    async def test_version_skipped(self):
        """测试版本一致，跳过升级。"""
        request = UpgradeRequest(
            version="20260408150000",
            download_url="http://example.com/installer.exe",
        )

        with patch('worker.upgrade.handler.get_current_version', return_value="20260408150000"):
            result = await handle_upgrade(request)
            assert result.status == "skipped"
            assert "无需升级" in result.message
            assert result.current_version == "20260408150000"
            assert result.target_version == "20260408150000"

    @pytest.mark.asyncio
    async def test_upgrade_success(self, tmp_path):
        """测试升级成功流程。"""
        request = UpgradeRequest(
            version="20260408150000",
            download_url="http://example.com/installer.exe",
        )

        state_file = tmp_path / "upgrade.json"

        with patch('worker.upgrade.handler.get_current_version', return_value="20260405120000"):
            with patch('worker.upgrade.state.get_state_file_path', return_value=str(state_file)):
                with patch('worker.upgrade.handler.download_installer', return_value=str(tmp_path / "installer.exe")):
                    with patch('worker.upgrade.handler.run_silent_install'):
                        result = await handle_upgrade(request)
                        assert result.status == "upgrading"
                        assert "正在升级" in result.message

    @pytest.mark.asyncio
    async def test_upgrade_download_error(self, tmp_path):
        """测试下载失败。"""
        request = UpgradeRequest(
            version="20260408150000",
            download_url="http://example.com/installer.exe",
        )

        state_file = tmp_path / "upgrade.json"

        from worker.upgrade.downloader import DownloadError

        with patch('worker.upgrade.handler.get_current_version', return_value="20260405120000"):
            with patch('worker.upgrade.state.get_state_file_path', return_value=str(state_file)):
                with patch('worker.upgrade.handler.download_installer', side_effect=DownloadError("网络错误")):
                    with pytest.raises(UpgradeError, match="下载失败"):
                        await handle_upgrade(request)

    @pytest.mark.asyncio
    async def test_upgrade_install_error(self, tmp_path):
        """测试安装失败。"""
        request = UpgradeRequest(
            version="20260408150000",
            download_url="http://example.com/installer.exe",
        )

        state_file = tmp_path / "upgrade.json"

        from worker.upgrade.installer import InstallError

        with patch('worker.upgrade.handler.get_current_version', return_value="20260405120000"):
            with patch('worker.upgrade.state.get_state_file_path', return_value=str(state_file)):
                with patch('worker.upgrade.handler.download_installer', return_value=str(tmp_path / "installer.exe")):
                    with patch('worker.upgrade.handler.run_silent_install', side_effect=InstallError("启动失败")):
                        with pytest.raises(UpgradeError, match="安装失败"):
                            await handle_upgrade(request)
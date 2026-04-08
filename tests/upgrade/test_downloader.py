"""
安装包下载器测试。
"""

import os
import pytest
from unittest.mock import patch, MagicMock
import httpx

from worker.upgrade.downloader import (
    get_temp_dir,
    download_installer,
    DownloadError,
)


class TestGetTempDir:
    """测试临时目录获取。"""

    def test_get_temp_dir_frozen(self, tmp_path):
        """测试打包后临时目录。"""
        with patch('sys.frozen', True, create=True):
            with patch('sys.executable', str(tmp_path / "test-worker.exe")):
                temp_dir = get_temp_dir()
                assert temp_dir.endswith("temp")
                assert os.path.exists(temp_dir)

    def test_get_temp_dir_development(self):
        """测试开发模式临时目录。"""
        temp_dir = get_temp_dir()
        assert temp_dir.endswith("temp")
        assert os.path.exists(temp_dir)


class TestDownloadInstaller:
    """测试安装包下载。"""

    def test_download_success(self, tmp_path):
        """测试成功下载。"""
        # 模拟 httpx 客户端
        mock_response = MagicMock()
        mock_response.content = b"fake installer content"
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        temp_dir = tmp_path / "temp"
        temp_dir.mkdir()

        with patch('worker.upgrade.downloader.get_temp_dir', return_value=str(temp_dir)):
            with patch('httpx.Client', return_value=mock_client):
                result = download_installer("http://example.com/installer.exe")
                assert result == str(temp_dir / "installer.exe")
                assert os.path.exists(result)

    def test_download_with_size_validation(self, tmp_path):
        """测试带大小校验的下载。"""
        content = b"fake installer content"
        expected_size = len(content)

        mock_response = MagicMock()
        mock_response.content = content
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        temp_dir = tmp_path / "temp"
        temp_dir.mkdir()

        with patch('worker.upgrade.downloader.get_temp_dir', return_value=str(temp_dir)):
            with patch('httpx.Client', return_value=mock_client):
                result = download_installer(
                    "http://example.com/installer.exe",
                    expected_size=expected_size
                )
                assert os.path.exists(result)

    def test_download_size_mismatch(self, tmp_path):
        """测试文件大小不匹配。"""
        content = b"fake installer content"

        mock_response = MagicMock()
        mock_response.content = content
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        temp_dir = tmp_path / "temp"
        temp_dir.mkdir()

        with patch('worker.upgrade.downloader.get_temp_dir', return_value=str(temp_dir)):
            with patch('httpx.Client', return_value=mock_client):
                with pytest.raises(DownloadError, match="文件大小不匹配"):
                    download_installer(
                        "http://example.com/installer.exe",
                        expected_size=100  # 错误的预期大小
                    )

    def test_download_http_error(self, tmp_path):
        """测试 HTTP 错误。"""
        mock_response = MagicMock()
        mock_response.status_code = 404
        http_error = httpx.HTTPStatusError(
            "Not Found",
            request=MagicMock(),
            response=mock_response
        )
        mock_response.raise_for_status.side_effect = http_error

        mock_client = MagicMock()
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        temp_dir = tmp_path / "temp"
        temp_dir.mkdir()

        with patch('worker.upgrade.downloader.get_temp_dir', return_value=str(temp_dir)):
            with patch('httpx.Client', return_value=mock_client):
                with pytest.raises(DownloadError, match="HTTP 404"):
                    download_installer("http://example.com/installer.exe")

    def test_download_request_error(self, tmp_path):
        """测试请求错误（网络问题）。"""
        mock_client = MagicMock()
        mock_client.get.side_effect = httpx.RequestError("Connection failed")
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        temp_dir = tmp_path / "temp"
        temp_dir.mkdir()

        with patch('worker.upgrade.downloader.get_temp_dir', return_value=str(temp_dir)):
            with patch('httpx.Client', return_value=mock_client):
                with pytest.raises(DownloadError, match="下载请求失败"):
                    download_installer("http://example.com/installer.exe")
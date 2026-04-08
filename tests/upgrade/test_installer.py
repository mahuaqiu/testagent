"""
静默安装执行器测试。
"""

import os
import pytest
from unittest.mock import patch, MagicMock

from worker.upgrade.installer import (
    get_current_install_dir,
    run_silent_install,
    InstallError,
)


class TestGetCurrentInstallDir:
    """测试获取当前安装目录。"""

    def test_get_install_dir_frozen(self, tmp_path):
        """测试打包后安装目录。"""
        exe_path = tmp_path / "test-worker.exe"
        exe_path.touch()

        with patch('sys.frozen', True, create=True):
            with patch('sys.executable', str(exe_path)):
                result = get_current_install_dir()
                assert result == str(tmp_path)

    def test_get_install_dir_development(self):
        """测试开发模式安装目录。"""
        result = get_current_install_dir()
        # 应返回项目根目录或其父目录
        assert os.path.exists(result)


class TestRunSilentInstall:
    """测试静默安装执行。"""

    def test_run_silent_install_success(self, tmp_path):
        """测试成功启动静默安装。"""
        installer_path = tmp_path / "installer.exe"
        installer_path.touch()

        mock_popen = MagicMock()

        with patch('worker.upgrade.installer.get_current_install_dir', return_value=str(tmp_path)):
            with patch('subprocess.Popen', mock_popen):
                run_silent_install(str(installer_path))
                # 验证 Popen 被调用
                mock_popen.assert_called_once()
                # 验证命令参数
                call_args = mock_popen.call_args[0][0]
                assert str(installer_path) in call_args
                assert "/VERYSILENT" in call_args
                assert "/SUPPRESSMSGBOXES" in call_args

    def test_run_silent_install_with_custom_dir(self, tmp_path):
        """测试指定安装目录的静默安装。"""
        installer_path = tmp_path / "installer.exe"
        installer_path.touch()
        custom_dir = tmp_path / "custom"

        mock_popen = MagicMock()

        with patch('subprocess.Popen', mock_popen):
            run_silent_install(str(installer_path), str(custom_dir))
            call_args = mock_popen.call_args[0][0]
            # 检查命令参数列表中是否有包含 custom_dir 的参数
            assert any(str(custom_dir) in arg for arg in call_args)

    def test_run_silent_install_installer_not_exists(self):
        """测试安装包不存在。"""
        with pytest.raises(InstallError, match="安装包不存在"):
            run_silent_install("/nonexistent/installer.exe")

    def test_run_silent_install_popen_error(self, tmp_path):
        """测试启动进程失败。"""
        installer_path = tmp_path / "installer.exe"
        installer_path.touch()

        with patch('subprocess.Popen', side_effect=OSError("Process failed")):
            with pytest.raises(InstallError, match="启动安装失败"):
                run_silent_install(str(installer_path))
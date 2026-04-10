import pytest
from unittest.mock import patch, MagicMock
from worker.single_instance import check_single_instance, release_instance_lock


def test_check_single_instance():
    """测试单实例检查。"""
    # 模拟首次启动，GetLastError 返回 0（无错误）
    mock_kernel32 = MagicMock()
    mock_kernel32.CreateMutexW.return_value = 12345  # 返回句柄
    mock_kernel32.GetLastError.return_value = 0

    with patch('ctypes.windll.kernel32', mock_kernel32):
        result = check_single_instance()
        assert result is True


def test_check_single_instance_already_running():
    """测试已有实例运行时的检查。"""
    # 模拟已有实例，GetLastError 返回 183（ERROR_ALREADY_EXISTS）
    mock_kernel32 = MagicMock()
    mock_kernel32.CreateMutexW.return_value = None
    mock_kernel32.GetLastError.return_value = 183

    with patch('ctypes.windll.kernel32', mock_kernel32):
        result = check_single_instance()
        assert result is False


def test_release_instance_lock():
    """测试释放实例锁。"""
    mock_kernel32 = MagicMock()
    mock_kernel32.CreateMutexW.return_value = 12345
    mock_kernel32.GetLastError.return_value = 0
    mock_kernel32.CloseHandle.return_value = True

    with patch('ctypes.windll.kernel32', mock_kernel32):
        check_single_instance()
        release_instance_lock()
        mock_kernel32.CloseHandle.assert_called_once()
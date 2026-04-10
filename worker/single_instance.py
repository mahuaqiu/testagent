"""
Windows 单实例锁模块。

使用 Windows Mutex 确保只有一个实例运行。
"""

import ctypes
import logging

logger = logging.getLogger(__name__)

# 全局 Mutex 句柄
_mutex_handle = None
MUTEX_NAME = "Global\\TestWorkerSingleInstance"


def check_single_instance() -> bool:
    """
    检查是否已有实例运行。

    Returns:
        bool: True 表示可以启动（无其他实例），False 表示已有实例运行
    """
    global _mutex_handle

    # 创建 Mutex
    _mutex_handle = ctypes.windll.kernel32.CreateMutexW(
        None, False, MUTEX_NAME
    )

    # 检查是否已存在
    last_error = ctypes.windll.kernel32.GetLastError()
    if last_error == 183:  # ERROR_ALREADY_EXISTS
        logger.warning("Another instance is already running")
        return False

    logger.debug("Single instance lock acquired")
    return True


def release_instance_lock() -> None:
    """释放单实例锁。"""
    global _mutex_handle

    if _mutex_handle:
        ctypes.windll.kernel32.CloseHandle(_mutex_handle)
        _mutex_handle = None
        logger.debug("Single instance lock released")
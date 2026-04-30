# worker/upgrade/state.py
"""
升级状态管理。

负责升级状态的持久化和读取。
"""

import json
import logging
import os
import sys
import threading
from typing import Optional

from common.packaging import is_packaged, get_base_dir
from worker.upgrade.models import UpgradeState

logger = logging.getLogger(__name__)

# 状态文件名
STATE_FILE = "upgrade.json"


def get_state_file_path() -> str:
    """
    获取状态文件路径。

    状态文件存储在 Worker 安装目录。

    Returns:
        str: 状态文件完整路径
    """
    return os.path.join(get_base_dir(), STATE_FILE)


def save_state(state: UpgradeState) -> None:
    """
    保存升级状态到文件。

    Args:
        state: 升级状态对象
    """
    path = get_state_file_path()
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(state.to_dict(), f, ensure_ascii=False, indent=2)
        logger.info(f"升级状态已保存: {path}")
    except Exception as e:
        logger.warning(f"保存升级状态失败: {e}")


def load_state() -> UpgradeState | None:
    """
    从文件加载升级状态。

    Returns:
        UpgradeState | None: 升级状态对象，不存在或读取失败返回 None
    """
    path = get_state_file_path()
    try:
        if os.path.exists(path):
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
            return UpgradeState(**data)
    except Exception as e:
        logger.warning(f"加载升级状态失败: {e}")
    return None


def clear_state() -> None:
    """
    清除升级状态文件。
    """
    path = get_state_file_path()
    try:
        if os.path.exists(path):
            os.remove(path)
            logger.info(f"升级状态文件已清除: {path}")
    except Exception as e:
        logger.warning(f"清除升级状态文件失败: {e}")


class UpgradeStatusManager:
    """
    线程安全的升级状态管理器（单例模式）。

    管理：
    - 内存中的升级状态
    - 状态持久化到文件
    - 防止并发升级请求
    """

    _instance: Optional['UpgradeStatusManager'] = None
    _lock: threading.Lock = threading.Lock()

    def __new__(cls) -> 'UpgradeStatusManager':
        """单例模式。"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._state: UpgradeState | None = None
                    cls._instance._thread: threading.Thread | None = None
                    cls._instance._state_lock = threading.Lock()
        return cls._instance

    def is_upgrading(self) -> bool:
        """
        检查是否有升级正在进行。

        Returns:
            bool: 状态为 accepted/downloading/installing 时返回 True
        """
        with self._state_lock:
            if self._state is None:
                return False
            return self._state.status in ('accepted', 'downloading', 'installing')

    def get_state(self) -> UpgradeState | None:
        """
        获取当前状态（线程安全）。

        Returns:
            UpgradeState | None: 当前状态，无升级时返回 None
        """
        with self._state_lock:
            return self._state

    def set_state(self, state: UpgradeState) -> None:
        """
        设置状态（线程安全，同时持久化）。

        Args:
            state: 升级状态对象
        """
        with self._state_lock:
            self._state = state
        save_state(state)

    def update_status(self, status: str, **kwargs) -> None:
        """
        更新状态字段（线程安全）。

        Args:
            status: 新状态值
            **kwargs: 其他要更新的字段
        """
        with self._state_lock:
            if self._state:
                self._state.status = status
                for key, value in kwargs.items():
                    if hasattr(self._state, key):
                        setattr(self._state, key, value)
                save_state(self._state)

    def update_download_progress(self, downloaded: int, total: int) -> None:
        """
        更新下载进度。

        Args:
            downloaded: 已下载字节
            total: 总字节
        """
        with self._state_lock:
            if self._state:
                self._state.downloaded_bytes = downloaded
                self._state.total_bytes = total
                if total > 0:
                    self._state.download_progress = int(downloaded / total * 100)
                save_state(self._state)

    def set_thread(self, thread: threading.Thread | None) -> None:
        """
        设置执行线程引用。

        Args:
            thread: 执行升级的线程
        """
        with self._state_lock:
            self._thread = thread

    def clear(self) -> None:
        """
        清除状态（升级失败后调用）。
        """
        with self._state_lock:
            self._state = None
            self._thread = None
        clear_state()

# worker/upgrade/state.py
"""
升级状态管理。

负责升级状态的持久化和读取。
"""

import json
import os
import sys
import logging
from typing import Optional
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
    if getattr(sys, 'frozen', False):
        # PyInstaller 打包后，使用 exe 所在目录
        base_dir = os.path.dirname(sys.executable)
    else:
        # 开发模式，使用项目根目录
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    return os.path.join(base_dir, STATE_FILE)


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


def load_state() -> Optional[UpgradeState]:
    """
    从文件加载升级状态。

    Returns:
        UpgradeState | None: 升级状态对象，不存在或读取失败返回 None
    """
    path = get_state_file_path()
    try:
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
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
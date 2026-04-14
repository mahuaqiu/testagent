"""
升级模块。

提供 Worker 远程升级功能（异步模式）。
"""

from worker.upgrade.downloader import DownloadError, download_installer, download_installer_async
from worker.upgrade.handler import UpgradeError, get_upgrade_status, start_async_upgrade
from worker.upgrade.installer import InstallError, run_silent_install
from worker.upgrade.models import (
    UpgradeRequest,
    UpgradeResponse,
    UpgradeState,
    UpgradeStatus,
)
from worker.upgrade.state import UpgradeStatusManager, clear_state, load_state, save_state

__all__ = [
    "UpgradeStatus",
    "UpgradeRequest",
    "UpgradeResponse",
    "UpgradeState",
    "start_async_upgrade",
    "get_upgrade_status",
    "UpgradeError",
    "save_state",
    "load_state",
    "clear_state",
    "UpgradeStatusManager",
    "download_installer",
    "download_installer_async",
    "DownloadError",
    "run_silent_install",
    "InstallError",
]

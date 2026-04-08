"""
升级模块。

提供 Worker 远程升级功能。
"""

from worker.upgrade.models import (
    UpgradeStatus,
    UpgradeRequest,
    UpgradeResponse,
    UpgradeState,
)
from worker.upgrade.handler import handle_upgrade, UpgradeError
from worker.upgrade.state import save_state, load_state, clear_state
from worker.upgrade.downloader import download_installer, DownloadError
from worker.upgrade.installer import run_silent_install, InstallError

__all__ = [
    "UpgradeStatus",
    "UpgradeRequest",
    "UpgradeResponse",
    "UpgradeState",
    "handle_upgrade",
    "UpgradeError",
    "save_state",
    "load_state",
    "clear_state",
    "download_installer",
    "DownloadError",
    "run_silent_install",
    "InstallError",
]
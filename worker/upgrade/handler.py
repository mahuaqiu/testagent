"""
升级 HTTP 接口处理。

负责处理 /worker/upgrade 接口请求（异步模式）。
"""

import logging
import os
import threading
import time
from datetime import datetime

from worker.upgrade.downloader import DownloadError, download_installer_async
from worker.upgrade.installer import InstallError, run_silent_install
from worker.upgrade.models import (
    UpgradeRequest,
    UpgradeResponse,
    UpgradeState,
)
from worker.upgrade.state import UpgradeStatusManager

logger = logging.getLogger(__name__)

# 状态管理器（单例）
_status_manager = UpgradeStatusManager()


def get_current_version() -> str | None:
    """
    获取当前版本号。

    Returns:
        str | None: 版本号，非 EXE 运行时返回 None
    """
    try:
        from worker._version import VERSION
        return VERSION
    except ImportError:
        return None


def start_async_upgrade(request: UpgradeRequest) -> UpgradeResponse:
    """
    启动异步升级（立即返回，后台执行）。

    Args:
        request: 升级请求

    Returns:
        UpgradeResponse: 立即返回 accepted 状态

    Raises:
        UpgradeError: 已有升级正在进行
    """
    # 检查是否已有升级
    if _status_manager.is_upgrading():
        raise UpgradeError("已有升级任务正在进行中")

    current_version = get_current_version()
    target_version = request.version

    # 版本校验
    if target_version and target_version == current_version:
        return UpgradeResponse(
            status="skipped",
            message="当前版本已是最新，无需升级",
            current_version=current_version,
            target_version=target_version,
        )

    # 初始化状态
    state = UpgradeState(
        status="accepted",
        target_version=target_version or "unknown",
        current_version=current_version or "unknown",
        download_url=request.download_url,
        started_at=datetime.now().isoformat(),
        download_progress=0,
        downloaded_bytes=0,
        total_bytes=0,
    )
    _status_manager.set_state(state)

    # 启动后台线程
    thread = threading.Thread(
        target=_execute_upgrade_background,
        args=(request, current_version, target_version),
        daemon=False,  # 非 daemon 确保升级完成
    )
    _status_manager.set_thread(thread)
    thread.start()

    logger.info(f"异步升级已启动: {current_version} -> {target_version}")

    return UpgradeResponse(
        status="upgrading",
        message="升级任务已接受，正在后台执行",
        current_version=current_version,
        target_version=target_version,
    )


def _execute_upgrade_background(
    request: UpgradeRequest,
    current_version: str | None,
    target_version: str | None
) -> None:
    """
    后台执行升级的线程函数。

    流程：下载 -> 安装 -> 退出

    注意：安装前清理（杀进程、删 playwright）由 Inno Setup 安装包处理
    """
    try:
        # 1. 开始下载
        _status_manager.update_status("downloading")

        installer_path = download_installer_async(
            request.download_url,
            progress_callback=_status_manager.update_download_progress,
        )

        logger.info(f"下载完成: {installer_path}")

        # 2. 开始安装（安装包会自动清理进程和 playwright 目录）
        _status_manager.update_status("installing")

        run_silent_install(installer_path)

        # 3. 标记完成（安装程序会重启 Worker）
        _status_manager.update_status(
            "completed",
            completed_at=datetime.now().isoformat()
        )

        logger.info("升级完成，Worker 即将退出...")

        # 4. 延迟退出（给调用方时间查询最终状态）
        time.sleep(1.0)
        os._exit(0)

    except DownloadError as e:
        logger.error(f"下载失败: {e}")
        _status_manager.update_status(
            "failed",
            error=f"下载失败: {e}",
            completed_at=datetime.now().isoformat()
        )

    except InstallError as e:
        logger.error(f"安装失败: {e}")
        _status_manager.update_status(
            "failed",
            error=f"安装失败: {e}",
            completed_at=datetime.now().isoformat()
        )

    except Exception as e:
        logger.error(f"升级失败: {e}", exc_info=True)
        _status_manager.update_status(
            "failed",
            error=f"升级失败: {e}",
            completed_at=datetime.now().isoformat()
        )


def get_upgrade_status() -> UpgradeState | None:
    """
    获取当前升级状态。

    Returns:
        UpgradeState | None: 当前状态，无升级时返回 None
    """
    return _status_manager.get_state()


class UpgradeError(Exception):
    """升级错误。"""
    pass
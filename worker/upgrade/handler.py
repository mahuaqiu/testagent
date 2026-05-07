"""
升级 HTTP 接口处理。

负责处理 /worker/upgrade 接口请求（异步模式）。
"""

import logging
import os
import platform
import shutil
import subprocess
import threading
import time
from datetime import datetime

from common.packaging import get_base_dir, is_packaged
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


def _cleanup_before_install() -> None:
    """
    安装前清理：杀掉本项目启动的进程，删除 playwright 目录。

    解决问题：
    1. adb.exe、ios.exe 等进程占用会导致升级失败
    2. playwright 目录覆盖升级可能导致浏览器启动失败
    """
    base_dir = get_base_dir()

    # 1. 杀掉 tools 目录下的进程
    if platform.system() == "Windows":
        _kill_tools_processes_windows(base_dir)
    else:
        _kill_tools_processes_unix(base_dir)

    # 2. 删除 playwright 目录
    _delete_playwright_dir(base_dir)


def _kill_tools_processes_windows(base_dir: str) -> None:
    """Windows: 杀掉 tools 目录下启动的进程。"""
    tools_dir = os.path.join(base_dir, "tools")
    if not os.path.exists(tools_dir):
        return

    # 收集 tools 目录下的所有 exe 文件名
    exe_names = []
    for root, dirs, files in os.walk(tools_dir):
        for file in files:
            if file.endswith(".exe"):
                exe_names.append(file)

    if not exe_names:
        return

    logger.info(f"准备杀掉 tools 进程: {exe_names}")

    for exe_name in exe_names:
        try:
            # 使用 taskkill 杀掉进程
            result = subprocess.run(
                ["taskkill", "/F", "/IM", exe_name],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                logger.info(f"已杀掉进程: {exe_name}")
            else:
                # 进程可能不存在，忽略
                logger.debug(f"进程不存在或已退出: {exe_name}")
        except Exception as e:
            logger.warning(f"杀进程失败 ({exe_name}): {e}")


def _kill_tools_processes_unix(base_dir: str) -> None:
    """Unix: 杀掉 tools 目录下启动的进程。"""
    tools_dir = os.path.join(base_dir, "tools")
    if not os.path.exists(tools_dir):
        return

    # 收集 tools 目录下的可执行文件
    exe_names = []
    for root, dirs, files in os.walk(tools_dir):
        for file in files:
            # Unix 下常见的可执行文件（无扩展名或 .sh）
            if (not file.startswith(".") and "." not in file) or file.endswith(".sh"):
                exe_names.append(file)

    if not exe_names:
        return

    logger.info(f"准备杀掉 tools 进程: {exe_names}")

    for exe_name in exe_names:
        try:
            # 使用 pkill 杀掉进程
            result = subprocess.run(
                ["pkill", "-f", exe_name],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                logger.info(f"已杀掉进程: {exe_name}")
        except Exception as e:
            logger.warning(f"杀进程失败 ({exe_name}): {e}")


def _delete_playwright_dir(base_dir: str) -> None:
    """删除 playwright 目录，避免覆盖升级导致浏览器启动失败。"""
    playwright_dir = os.path.join(base_dir, "playwright")
    if not os.path.exists(playwright_dir):
        logger.debug("playwright 目录不存在，无需删除")
        return

    logger.info(f"删除 playwright 目录: {playwright_dir}")
    try:
        shutil.rmtree(playwright_dir)
        logger.info("playwright 目录已删除")
    except Exception as e:
        logger.warning(f"删除 playwright 目录失败: {e}")


def _execute_upgrade_background(
    request: UpgradeRequest,
    current_version: str | None,
    target_version: str | None
) -> None:
    """
    后台执行升级的线程函数。

    流程：下载 -> 清理 -> 安装 -> 退出
    """
    try:
        # 1. 开始下载
        _status_manager.update_status("downloading")

        installer_path = download_installer_async(
            request.download_url,
            progress_callback=_status_manager.update_download_progress,
        )

        logger.info(f"下载完成: {installer_path}")

        # 2. 清理进程和目录（安装前）
        _cleanup_before_install()

        # 3. 开始安装
        _status_manager.update_status("installing")

        run_silent_install(installer_path)

        # 4. 标记完成（安装程序会重启 Worker）
        _status_manager.update_status(
            "completed",
            completed_at=datetime.now().isoformat()
        )

        logger.info("升级完成，Worker 即将退出...")

        # 5. 延迟退出（给调用方时间查询最终状态）
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

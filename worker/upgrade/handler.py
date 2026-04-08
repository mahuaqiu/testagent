"""
升级 HTTP 接口处理。

负责处理 /worker/upgrade 接口请求。
"""

import logging
from datetime import datetime
from typing import Optional

from worker.upgrade.models import (
    UpgradeRequest,
    UpgradeResponse,
    UpgradeState,
)
from worker.upgrade.downloader import download_installer, DownloadError
from worker.upgrade.installer import run_silent_install, InstallError
from worker.upgrade.state import save_state

logger = logging.getLogger(__name__)


def get_current_version() -> Optional[str]:
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


async def handle_upgrade(request: UpgradeRequest) -> UpgradeResponse:
    """
    处理升级请求。

    Args:
        request: 升级请求对象

    Returns:
        UpgradeResponse: 升级响应

    Raises:
        UpgradeError: 升级过程中发生错误
    """
    current_version = get_current_version()
    target_version = request.version

    # 1. 版本校验：版本一致则无需升级
    if target_version and target_version == current_version:
        logger.info(f"版本一致，无需升级: {current_version}")
        return UpgradeResponse(
            status="skipped",
            message="当前版本已是最新，无需升级",
            current_version=current_version,
            target_version=target_version,
        )

    # 2. 记录升级状态
    state = UpgradeState(
        status="downloading",
        target_version=target_version or "unknown",
        current_version=current_version or "unknown",
        download_url=request.download_url,
        started_at=datetime.now().isoformat(),
    )
    save_state(state)

    logger.info(f"开始升级: {current_version} -> {target_version}")

    try:
        # 3. 下载安装包
        state.status = "downloading"
        save_state(state)

        installer_path = download_installer(request.download_url)

        # 4. 启动静默安装
        state.status = "installing"
        save_state(state)

        run_silent_install(installer_path)

        # 5. 返回响应后 Worker 立即退出
        logger.info("升级安装已启动，Worker 即将退出")

        # 注意：实际退出逻辑在调用方处理（sys.exit(0)）
        return UpgradeResponse(
            status="upgrading",
            message="Worker 正在升级，预计 30 秒后恢复",
            current_version=current_version,
            target_version=target_version,
        )

    except DownloadError as e:
        state.status = "failed"
        state.error = str(e)
        save_state(state)
        raise UpgradeError(f"下载失败: {e}")

    except InstallError as e:
        state.status = "failed"
        state.error = str(e)
        save_state(state)
        raise UpgradeError(f"安装失败: {e}")

    except Exception as e:
        state.status = "failed"
        state.error = str(e)
        save_state(state)
        raise UpgradeError(f"升级失败: {e}")


class UpgradeError(Exception):
    """升级错误。"""
    pass
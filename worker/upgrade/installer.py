# worker/upgrade/installer.py
"""
静默安装执行模块。

负责启动 Inno Setup 静默安装进程。
"""

import os
import sys
import subprocess
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Windows 进程创建标志，使子进程独立于父进程
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200


def get_current_install_dir() -> str:
    """
    获取当前安装目录。

    Returns:
        str: 当前安装目录路径
    """
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        # 开发模式，返回模拟路径
        return os.path.dirname(os.path.dirname(os.path.dirname(__file__)))


def run_silent_install(installer_path: str, install_dir: Optional[str] = None) -> None:
    """
    执行静默安装。

    启动 Inno Setup 静默安装进程后立即返回，不等待安装完成。

    Args:
        installer_path: 安装包路径
        install_dir: 安装目录（可选，默认使用当前目录）

    Raises:
        InstallError: 安装包不存在或启动失败
    """
    if not os.path.exists(installer_path):
        raise InstallError(f"安装包不存在: {installer_path}")

    # 获取安装目录
    if install_dir is None:
        install_dir = get_current_install_dir()

    # 构建静默安装命令
    # /VERYSILENT - 完全静默，无任何界面
    # /SUPPRESSMSGBOXES - 抑制消息框
    # /NORESTART - 不自动重启系统
    cmd = [
        installer_path,
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        f'/DIR="{install_dir}"',
    ]

    logger.info(f"启动静默安装: {' '.join(cmd)}")

    try:
        # 启动安装进程（后台运行，不等待，独立进程）
        # 使用 CREATE_BREAKAWAY_FROM_JOB 标志确保进程独立
        # Windows 服务或进程组可能限制子进程，此标志允许子进程脱离父进程组
        CREATE_BREAKAWAY_FROM_JOB = 0x01000000
        subprocess.Popen(
            [installer_path, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", f'/DIR={install_dir}'],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB,
        )
        logger.info("静默安装进程已启动（独立进程）")

    except Exception as e:
        raise InstallError(f"启动安装失败: {e}")


class InstallError(Exception):
    """安装错误。"""
    pass
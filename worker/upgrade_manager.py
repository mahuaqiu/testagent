"""
升级管理器模块。

负责检查更新、下载安装包、执行静默安装。
"""

import os
import sys
import logging
import tempfile
import subprocess
import httpx
from dataclasses import dataclass
from typing import Optional
from datetime import datetime

from common.utils import SUBPROCESS_HIDE_WINDOW

logger = logging.getLogger(__name__)


@dataclass
class UpgradeInfo:
    """升级信息。"""
    version: str
    download_url: str

    @classmethod
    def from_response(cls, response: dict) -> "UpgradeInfo":
        """
        从 HTTP 响应创建 UpgradeInfo。

        Args:
            response: HTTP 响应字典，包含 version 和 download_url

        Returns:
            UpgradeInfo: 升级信息对象
        """
        return cls(
            version=response.get("version", ""),
            download_url=response.get("download_url", "")
        )


class UpgradeManager:
    """
    升级管理器。

    负责：
    - 检查更新：调用 HTTP API 获取最新版本信息
    - 下载安装包：下载到临时目录
    - 执行安装：静默安装并重启服务
    """

    def __init__(
        self,
        check_url: str,
        current_version: str,
        check_timeout: float = 30.0,
        download_timeout: float = 300.0
    ):
        """
        初始化升级管理器。

        Args:
            check_url: 检查更新的 API 地址
            current_version: 当前版本号
            check_timeout: 检查请求超时时间（秒）
            download_timeout: 下载超时时间（秒）
        """
        self.check_url = check_url
        self.current_version = current_version
        self.check_timeout = check_timeout
        self.download_timeout = download_timeout

    def check_upgrade(self) -> Optional[UpgradeInfo]:
        """
        检查是否有新版本。

        调用 HTTP API 检查更新，如果返回新版本信息则表示需要升级。

        Returns:
            UpgradeInfo | None: 新版本信息，无新版本时返回 None

        Raises:
            httpx.HTTPError: HTTP 请求失败
        """
        logger.info(f"检查更新: {self.check_url}")

        try:
            with httpx.Client(timeout=self.check_timeout, trust_env=False) as client:
                response = client.get(self.check_url)
                response.raise_for_status()

                data = response.json()

                # 如果响应中有版本和下载地址，创建 UpgradeInfo
                if "version" in data and "download_url" in data:
                    info = UpgradeInfo.from_response(data)

                    # 检查是否是新版本
                    if self.is_newer_version(self.current_version, info.version):
                        logger.info(f"发现新版本: {info.version}")
                        return info
                    else:
                        logger.info("当前版本已是最新")
                        return None

                return None

        except httpx.HTTPStatusError as e:
            logger.error(f"检查更新失败 (HTTP {e.response.status_code}): {e}")
            raise
        except httpx.RequestError as e:
            logger.error(f"检查更新请求失败: {e}")
            raise
        except Exception as e:
            logger.error(f"检查更新失败: {e}")
            raise

    @staticmethod
    def is_newer_version(current: str, target: str) -> bool:
        """
        比较版本号，判断目标版本是否比当前版本新。

        Args:
            current: 当前版本号（如 "202604101400"）
            target: 目标版本号（如 "202604101500"）

        Returns:
            bool: 目标版本是否比当前版本新
        """
        # 简单的字符串比较，适用于时间戳格式的版本号
        # 版本号格式：YYYYMMDDHHMM
        return target > current

    def download_installer(self, download_url: str) -> str:
        """
        下载安装包到临时目录。

        Args:
            download_url: 安装包下载地址

        Returns:
            str: 安装包本地路径

        Raises:
            DownloadError: 下载失败
        """
        # 获取临时目录
        temp_dir = tempfile.gettempdir()
        installer_path = os.path.join(temp_dir, "test-worker-installer.exe")

        logger.info(f"开始下载安装包: {download_url}")
        logger.info(f"目标路径: {installer_path}")

        try:
            with httpx.Client(
                timeout=self.download_timeout,
                trust_env=False,
                follow_redirects=True
            ) as client:
                response = client.get(download_url)
                response.raise_for_status()

                # 写入文件
                with open(installer_path, 'wb') as f:
                    f.write(response.content)

                actual_size = os.path.getsize(installer_path)
                logger.info(f"下载完成，文件大小: {actual_size} bytes")

                return installer_path

        except httpx.HTTPStatusError as e:
            raise DownloadError(f"下载失败 (HTTP {e.response.status_code}): {e}")
        except httpx.RequestError as e:
            raise DownloadError(f"下载请求失败: {e}")
        except Exception as e:
            raise DownloadError(f"下载失败: {e}")

    def run_silent_install(self, installer_path: str) -> None:
        """
        执行静默安装。

        启动 Inno Setup 静默安装进程后立即返回，不等待安装完成。

        Args:
            installer_path: 安装包路径

        Raises:
            InstallError: 安装包不存在或启动失败
        """
        if not os.path.exists(installer_path):
            raise InstallError(f"安装包不存在: {installer_path}")

        # 获取当前安装目录
        install_dir = self._get_current_install_dir()

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
            # 启动安装进程（后台运行，不等待）
            subprocess.Popen(
                cmd,
                shell=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=SUBPROCESS_HIDE_WINDOW,
            )
            logger.info("静默安装进程已启动")

        except Exception as e:
            raise InstallError(f"启动安装失败: {e}")

    def _get_current_install_dir(self) -> str:
        """
        获取当前安装目录。

        Returns:
            str: 当前安装目录路径
        """
        if getattr(sys, 'frozen', False):
            # PyInstaller 打包后，使用 exe 所在目录
            return os.path.dirname(sys.executable)
        else:
            # 开发模式，返回项目根目录
            return os.path.dirname(os.path.dirname(os.path.dirname(__file__)))


class DownloadError(Exception):
    """下载错误。"""
    pass


class InstallError(Exception):
    """安装错误。"""
    pass
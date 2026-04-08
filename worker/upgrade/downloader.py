"""
安装包下载模块。

负责从远程下载升级安装包。
"""

import os
import sys
import logging
import httpx
from typing import Optional

logger = logging.getLogger(__name__)

# 临时目录名
TEMP_DIR = "temp"
INSTALLER_FILENAME = "installer.exe"


def get_temp_dir() -> str:
    """
    获取临时目录路径。

    Returns:
        str: 临时目录完整路径
    """
    if getattr(sys, 'frozen', False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    temp_dir = os.path.join(base_dir, TEMP_DIR)
    os.makedirs(temp_dir, exist_ok=True)
    return temp_dir


def download_installer(url: str, expected_size: Optional[int] = None) -> str:
    """
    下载安装包。

    Args:
        url: 安装包下载地址
        expected_size: 预期文件大小（字节），用于校验，可选

    Returns:
        str: 安装包本地路径

    Raises:
        DownloadError: 下载失败
    """
    temp_dir = get_temp_dir()
    installer_path = os.path.join(temp_dir, INSTALLER_FILENAME)

    logger.info(f"开始下载安装包: {url}")
    logger.info(f"目标路径: {installer_path}")

    try:
        with httpx.Client(timeout=300.0, trust_env=False, follow_redirects=True) as client:
            response = client.get(url)
            response.raise_for_status()

            # 写入文件
            with open(installer_path, 'wb') as f:
                f.write(response.content)

            # 校验文件大小
            actual_size = os.path.getsize(installer_path)
            logger.info(f"下载完成，文件大小: {actual_size} bytes")

            if expected_size and actual_size != expected_size:
                os.remove(installer_path)
                raise DownloadError(
                    f"文件大小不匹配: 预期 {expected_size}, 实际 {actual_size}"
                )

            return installer_path

    except httpx.HTTPStatusError as e:
        raise DownloadError(f"下载失败 (HTTP {e.response.status_code}): {e}")
    except httpx.RequestError as e:
        raise DownloadError(f"下载请求失败: {e}")
    except Exception as e:
        raise DownloadError(f"下载失败: {e}")


class DownloadError(Exception):
    """下载错误。"""
    pass
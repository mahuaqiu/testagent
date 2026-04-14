"""
安装包下载模块。

负责从远程下载升级安装包。
"""

import logging
import os
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

import httpx

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


def download_installer(url: str, expected_size: int | None = None) -> str:
    """
    下载安装包。

    支持两种方式：
    - HTTP/HTTPS URL: 使用 httpx 下载
    - UNC/本地路径: 使用文件复制

    Args:
        url: 安装包下载地址（HTTP/HTTPS URL 或 UNC/本地路径）
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
        # 判断是 HTTP/HTTPS URL 还是本地/UNC 路径
        parsed = urlparse(url)
        is_http_url = parsed.scheme in ('http', 'https')
        is_unc_or_local = (
            url.startswith('\\\\') or  # UNC 路径: \\server\share
            os.path.isabs(url) or       # 绝对路径: C:\path
            parsed.scheme == ''         # 相对路径或无协议
        )

        if is_http_url:
            # HTTP/HTTPS 下载
            with httpx.Client(timeout=300.0, trust_env=False, follow_redirects=True) as client:
                response = client.get(url)
                response.raise_for_status()

                # 写入文件
                with open(installer_path, 'wb') as f:
                    f.write(response.content)

        elif is_unc_or_local:
            # UNC 路径或本地文件复制
            source_path = Path(url)
            if not source_path.exists():
                raise DownloadError(f"源文件不存在: {url}")
            shutil.copy2(url, installer_path)
            logger.info(f"文件复制完成: {url} -> {installer_path}")

        else:
            raise DownloadError(f"不支持的下载路径格式: {url}")

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
    except DownloadError:
        raise
    except Exception as e:
        raise DownloadError(f"下载失败: {e}")


class DownloadError(Exception):
    """下载错误。"""
    pass


def download_installer_async(
    url: str,
    progress_callback: Callable[[int, int], None],
    expected_size: int | None = None
) -> str:
    """
    异步下载安装包（支持进度回调）。

    使用 httpx.stream 流式下载，实时报告进度。

    Args:
        url: 安装包下载地址（HTTP/HTTPS URL 或 UNC/本地路径）
        progress_callback: 进度回调函数(downloaded_bytes, total_bytes)
        expected_size: 预期文件大小（字节），用于校验，可选

    Returns:
        str: 安装包本地路径

    Raises:
        DownloadError: 下载失败
    """
    temp_dir = get_temp_dir()
    installer_path = os.path.join(temp_dir, INSTALLER_FILENAME)

    logger.info(f"开始流式下载安装包: {url}")
    logger.info(f"目标路径: {installer_path}")

    try:
        parsed = urlparse(url)
        is_http_url = parsed.scheme in ('http', 'https')
        is_unc_or_local = (
            url.startswith('\\') or  # UNC 路径: \\server\share
            os.path.isabs(url) or    # 绝对路径: C:\path
            parsed.scheme == ''      # 相对路径或无协议
        )

        if is_http_url:
            # HTTP 流式下载
            with httpx.Client(timeout=300.0, trust_env=False, follow_redirects=True) as client:
                with client.stream("GET", url) as response:
                    response.raise_for_status()

                    total_size = int(response.headers.get("content-length", 0))
                    downloaded = 0

                    os.makedirs(temp_dir, exist_ok=True)

                    with open(installer_path, "wb") as f:
                        for chunk in response.iter_bytes(chunk_size=8192):
                            f.write(chunk)
                            downloaded += len(chunk)
                            # 调用进度回调
                            progress_callback(downloaded, total_size)

                    logger.info(f"下载完成: {downloaded} bytes")

        elif is_unc_or_local:
            # 本地文件复制（无进度追踪，直接报告完成）
            source_path = Path(url)
            if not source_path.exists():
                raise DownloadError(f"源文件不存在: {url}")
            shutil.copy2(url, installer_path)
            # 模拟进度完成
            actual_size = os.path.getsize(installer_path)
            progress_callback(actual_size, actual_size)
            logger.info(f"文件复制完成: {url} -> {installer_path}")

        else:
            raise DownloadError(f"不支持的下载路径格式: {url}")

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
    except DownloadError:
        raise
    except Exception as e:
        raise DownloadError(f"下载失败: {e}")

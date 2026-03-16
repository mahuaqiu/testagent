"""
平台执行引擎模块。
"""

from worker.platforms.base import PlatformManager
from worker.platforms.web import WebPlatformManager
from worker.platforms.android import AndroidPlatformManager
from worker.platforms.ios import iOSPlatformManager
from worker.platforms.windows import WindowsPlatformManager
from worker.platforms.mac import MacPlatformManager

__all__ = [
    "PlatformManager",
    "WebPlatformManager",
    "AndroidPlatformManager",
    "iOSPlatformManager",
    "WindowsPlatformManager",
    "MacPlatformManager",
]
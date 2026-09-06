"""
平台执行引擎模块。
"""

import sys

# Windows：必须在任何平台模块导入前声明 per-monitor v2 DPI 感知。
# web.py 等模块顶层 import pyautogui 时，mouseinfo 会抢先调用系统级
# SetProcessDPIAware()，之后 per-monitor v2 永远设置失败——非 100% 缩放/
# 多屏混合 DPI 的被控机上注入坐标会系统性偏移。
if sys.platform.startswith("win"):
    from worker.screen.monitor_utils import ensure_process_dpi_awareness

    ensure_process_dpi_awareness()

from worker.platforms.base import PlatformManager
from worker.platforms.web import WebPlatformManager
from worker.platforms.android import AndroidPlatformManager
from worker.platforms.ios import iOSPlatformManager
from worker.platforms.windows import WindowsPlatformManager
from worker.platforms.mac import MacPlatformManager
from worker.platforms.harmony import HarmonyPlatformManager

__all__ = [
    "PlatformManager",
    "WebPlatformManager",
    "AndroidPlatformManager",
    "iOSPlatformManager",
    "WindowsPlatformManager",
    "MacPlatformManager",
    "HarmonyPlatformManager",
]
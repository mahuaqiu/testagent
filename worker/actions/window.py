"""
窗口激活 Action 执行器。

支持 Windows/Mac/Web 平台将指定窗口带到前台并获取焦点。
match_by 支持两种模式：title（窗口标题）、class（窗口类名）。
"""

import logging
import subprocess
import time
from random import randint
from typing import TYPE_CHECKING

from worker.task import Action, ActionResult, ActionStatus
from worker.actions.base import BaseActionExecutor

if TYPE_CHECKING:
    from worker.platforms.base import PlatformManager

logger = logging.getLogger(__name__)


class ActivateWindowAction(BaseActionExecutor):
    """窗口激活。"""

    name = "activate_window"
    requires_context = False

    def execute(self, platform: "PlatformManager", action: Action, context=None) -> ActionResult:
        """执行窗口激活。"""
        value = action.value
        match_by = action.match_by or "title"
        exe_name = action.name  # 进程 exe 名称过滤

        if not value:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error="value is required",
            )

        # 验证 match_by 参数
        if match_by not in ("title", "class"):
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error=f"Invalid match_by: {match_by}, must be 'title' or 'class'",
            )

        # Windows 平台和 Web 平台（运行在 Windows 上）都使用 Windows 逻辑
        if platform.platform in ("windows", "web"):
            return self._activate_windows(value, match_by, exe_name)
        elif platform.platform == "mac":
            return self._activate_mac(value, match_by)
        else:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error=f"activate_window is not supported on {platform.platform}",
            )

    def _activate_windows(self, value: str, match_by: str, exe_name: str | None = None) -> ActionResult:
        """Windows 平台窗口激活。

        先通过标题或类名找到窗口句柄 HWND，然后用 Win32Window 激活并移动鼠标到窗口中心。
        找不到句柄或激活异常时，走移动 500 兜底逻辑。

        Args:
            value: 窗口标题或窗口类名
            match_by: 定位方式，"title" 或 "class"
            exe_name: 进程 exe 名称过滤（可选），如 "chrome.exe"
        """
        import pygetwindow as gw
        import pyautogui

        # 1. 查找窗口句柄
        hwnd = self._find_hwnd(value, match_by, exe_name)
        if not hwnd:
            logger.error(f"Window not found: {match_by}={value}" + (f", exe={exe_name}" if exe_name else ""))
            # 找不到句柄，走移动 500 兜底
            pyautogui.moveTo(500 + randint(0, 20), 500 + randint(0, 20), duration=0.1)
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.SUCCESS,
                output=f"Window not found: {match_by}={value} (activate skipped, moved to 500,500)",
            )

        # 2. 激活窗口并移动鼠标到窗口中心
        try:
            win = gw.Win32Window(hwnd)
            if not win.isActive:
                win.activate()
                time.sleep(0.2)
            pyautogui.moveTo(win.center[0] + randint(0, 20), win.center[1] + randint(0, 20), duration=0.1)
            logger.info(f"Activated window: {match_by}={value}")
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.SUCCESS,
                output=f"Activated window: {match_by}={value}",
            )
        except gw.PyGetWindowException as e:
            logger.error(f"Failed to activate window: {e}")
            # 激活异常，走移动 500 兜底
            pyautogui.moveTo(500 + randint(0, 20), 500 + randint(0, 20), duration=0.1)
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.SUCCESS,
                output=f"Failed to activate window: {e} (moved to 500,500)",
            )

    def _find_hwnd(self, value: str, match_by: str, exe_name: str | None = None) -> int:
        """查找窗口句柄。

        统一入口，根据 match_by 选择按标题或类名查找。

        Args:
            value: 窗口标题或窗口类名
            match_by: 定位方式，"title" 或 "class"
            exe_name: 进程 exe 名称过滤（可选）

        Returns:
            窗口句柄（HWND），未找到返回 0
        """
        if match_by == "title":
            return self._find_window_by_title(value, exe_name)
        else:
            return self._find_window_by_class(value, exe_name)

    def _find_window_by_title(self, title: str, exe_name: str | None = None) -> int:
        """通过窗口标题查找句柄（包含匹配）。

        Args:
            title: 窗口标题
            exe_name: 进程 exe 名称过滤（可选）

        Returns:
            窗口句柄（HWND），未找到返回 0
        """
        import pygetwindow as gw

        windows = gw.getWindowsWithTitle(title)
        if not windows:
            return 0
        if exe_name:
            window = self._filter_window_by_exe(windows, exe_name)
            if not window:
                return 0
            return getattr(window, '_hWnd', 0)
        return getattr(windows[0], '_hWnd', 0)

    def _find_window_by_class(self, class_name: str, exe_name: str | None = None) -> int:
        """通过窗口类名查找窗口句柄。

        支持精确匹配和包含匹配（传入部分类名也能找到）。
        只查找可见窗口。可选支持按进程 exe 名称过滤。

        Args:
            class_name: 窗口类名（如 Chrome_WidgetWin_1）
            exe_name: 进程 exe 名称（可选），如 "chrome.exe"

        Returns:
            窗口句柄（HWND），未找到返回 0
        """
        import win32gui
        import win32process
        import pywintypes

        exact_match_hwnd = 0
        partial_match_hwnd = 0

        def enum_windows_callback(hwnd, _):
            nonlocal exact_match_hwnd, partial_match_hwnd
            try:
                # 只查找可见窗口
                if not win32gui.IsWindowVisible(hwnd):
                    return True

                cls = win32gui.GetClassName(hwnd)

                # 如果指定了 exe_name，检查进程名
                if exe_name:
                    exe = self._get_exe_name(hwnd)
                    if exe != exe_name:
                        return True  # 跳过不匹配的窗口

                # 精确匹配优先
                if cls == class_name:
                    exact_match_hwnd = hwnd
                    return False  # 停止枚举
                # 包含匹配作为备选
                if class_name in cls and partial_match_hwnd == 0:
                    partial_match_hwnd = hwnd
            except pywintypes.error:
                # 某些系统窗口访问属性会抛异常，跳过即可
                pass
            except Exception:
                # 回调函数中任何异常都不应中断枚举
                pass
            return True

        try:
            win32gui.EnumWindows(enum_windows_callback, None)
        except Exception as e:
            logger.error(f"EnumWindows failed in _find_window_by_class: {e}")

        # 精确匹配优先
        if exact_match_hwnd:
            return exact_match_hwnd
        return partial_match_hwnd

    def _get_exe_name(self, hwnd: int) -> str | None:
        """获取窗口对应的进程 exe 名称。

        Args:
            hwnd: 窗口句柄

        Returns:
            进程 exe 名称（如 "chrome.exe"），获取失败返回 None
        """
        try:
            import win32process
            import psutil

            _, process_id = win32process.GetWindowThreadProcessId(hwnd)
            process = psutil.Process(process_id)
            return process.name()
        except Exception:
            # NoSuchProcess、AccessDenied 或其他异常
            return None

    def _filter_window_by_exe(self, windows: list, exe_name: str):
        """从窗口列表中过滤指定 exe 的窗口。

        Args:
            windows: pygetwindow 窗口列表
            exe_name: 进程 exe 名称，如 "chrome.exe"

        Returns:
            匹配的窗口对象，未找到返回 None
        """
        for window in windows:
            try:
                # pygetwindow 的 window 对象有 _hWnd 属性
                hwnd = getattr(window, '_hWnd', None)
                if hwnd:
                    exe = self._get_exe_name(hwnd)
                    if exe == exe_name:
                        return window
            except Exception:
                continue
        return None

    def _activate_mac(self, value: str, match_by: str) -> ActionResult:
        """Mac 平台窗口激活。

        Mac 平台只支持 class 模式（通过应用名激活）。
        title 模式不推荐使用，因为需要额外权限。
        """
        try:
            if match_by == "title":
                # Mac 上按标题激活需要特殊处理
                # AppleScript 无法直接通过窗口标题激活，需要额外权限
                return ActionResult(
                    number=0,
                    action_type=self.name,
                    status=ActionStatus.FAILED,
                    error="Mac platform does not support match_by='title', use match_by='class' (application name)",
                )
            else:  # class - Mac 上 class 模式实际是应用名
                # 通过应用名激活
                cmd = f'tell application "{value}" to activate'
                result = subprocess.run(
                    ["osascript", "-e", cmd],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode != 0:
                    return ActionResult(
                        number=0,
                        action_type=self.name,
                        status=ActionStatus.FAILED,
                        error=f"Application not found: {value}",
                    )
                logger.info(f"Activated application on Mac: {value}")
                return ActionResult(
                    number=0,
                    action_type=self.name,
                    status=ActionStatus.SUCCESS,
                    output=f"Activated application: {value}",
                )

        except subprocess.TimeoutExpired:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error="Timeout while activating window",
            )
        except Exception as e:
            logger.error(f"Failed to activate window on Mac: {e}")
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error=f"Failed to activate: {e}",
            )
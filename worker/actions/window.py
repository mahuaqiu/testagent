"""
窗口激活 Action 执行器。

支持 Windows/Mac/Web 平台将指定窗口带到前台并获取焦点。
match_by 支持两种模式：title（窗口标题）、class（窗口类名）。
"""

import logging
import subprocess
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

        Args:
            value: 窗口标题或窗口类名
            match_by: 定位方式，"title" 或 "class"
            exe_name: 进程 exe 名称过滤（可选），如 "chrome.exe"
        """
        try:
            if match_by == "title":
                # 按标题查找（包含匹配）
                import pygetwindow as gw

                windows = gw.getWindowsWithTitle(value)
                if not windows:
                    return ActionResult(
                        number=0,
                        action_type=self.name,
                        status=ActionStatus.FAILED,
                        error=f"Window not found by title: {value}",
                    )
                # 如果指定了 exe_name，需要过滤
                if exe_name:
                    window = self._filter_window_by_exe(windows, exe_name)
                    if not window:
                        return ActionResult(
                            number=0,
                            action_type=self.name,
                            status=ActionStatus.FAILED,
                            error=f"Window not found by title '{value}' with exe '{exe_name}'",
                        )
                else:
                    window = windows[0]
                window.activate()
                logger.info(f"Activated window by title: {window.title}")
                return ActionResult(
                    number=0,
                    action_type=self.name,
                    status=ActionStatus.SUCCESS,
                    output=f"Activated window by title: {value}",
                )
            else:  # class
                # 按窗口类名查找（精确匹配或包含匹配）
                import win32gui
                import win32con
                import win32api

                hwnd = self._find_window_by_class(value, exe_name)
                if not hwnd:
                    error_msg = f"Window not found by class: {value}"
                    if exe_name:
                        error_msg = f"Window not found by class '{value}' with exe '{exe_name}'"
                    return ActionResult(
                        number=0,
                        action_type=self.name,
                        status=ActionStatus.FAILED,
                        error=error_msg,
                    )

                # 使用 AttachThreadInput 技术确保后台进程能正确激活窗口
                # Windows 安全机制阻止后台进程直接调用 SetForegroundWindow
                self._force_set_foreground_window(hwnd)

                # 获取窗口标题用于日志
                window_title = win32gui.GetWindowText(hwnd)
                logger.info(f"Activated window by class: {value}, title: {window_title}")
                return ActionResult(
                    number=0,
                    action_type=self.name,
                    status=ActionStatus.SUCCESS,
                    output=f"Activated window by class: {value}",
                )

        except Exception as e:
            logger.error(f"Failed to activate window on Windows: {e}")
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error=f"Failed to activate window: {e}",
            )

    def _find_window_by_class(self, class_name: str, exe_name: str | None = None) -> int:
        """通过窗口类名查找窗口句柄。

        支持精确匹配和包含匹配（传入部分类名也能找到）。
        可选支持按进程 exe 名称过滤。

        Args:
            class_name: 窗口类名（如 Chrome_WidgetWin_1）
            exe_name: 进程 exe 名称（可选），如 "chrome.exe"

        Returns:
            窗口句柄（HWND），未找到返回 0
        """
        import win32gui
        import win32process
        import psutil

        exact_match_hwnd = 0
        partial_match_hwnd = 0

        def get_exe_name(hwnd: int) -> str | None:
            """获取窗口对应的进程 exe 名称。"""
            try:
                _, process_id = win32process.GetWindowThreadProcessId(hwnd)
                process = psutil.Process(process_id)
                return process.name()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                return None

        def enum_windows_callback(hwnd, _):
            nonlocal exact_match_hwnd, partial_match_hwnd
            cls = win32gui.GetClassName(hwnd)

            # 如果指定了 exe_name，检查进程名
            if exe_name:
                exe = get_exe_name(hwnd)
                if exe != exe_name:
                    return True  # 跳过不匹配的窗口

            # 精确匹配优先
            if cls == class_name:
                exact_match_hwnd = hwnd
                return False  # 停止枚举
            # 包含匹配作为备选
            if class_name in cls and partial_match_hwnd == 0:
                partial_match_hwnd = hwnd
            return True

        win32gui.EnumWindows(enum_windows_callback, None)

        # 精确匹配优先
        if exact_match_hwnd:
            return exact_match_hwnd
        return partial_match_hwnd

    def _filter_window_by_exe(self, windows: list, exe_name: str):
        """从窗口列表中过滤指定 exe 的窗口。

        Args:
            windows: pygetwindow 窗口列表
            exe_name: 进程 exe 名称，如 "chrome.exe"

        Returns:
            匹配的窗口对象，未找到返回 None
        """
        import win32process
        import psutil

        for window in windows:
            try:
                # pygetwindow 的 window 对象有 _hWnd 属性
                hwnd = getattr(window, '_hWnd', None)
                if hwnd:
                    _, process_id = win32process.GetWindowThreadProcessId(hwnd)
                    process = psutil.Process(process_id)
                    if process.name() == exe_name:
                        return window
            except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError):
                continue
        return None

    def _force_set_foreground_window(self, hwnd: int) -> None:
        """强制将窗口设为前台窗口。

        Windows 安全机制阻止后台进程直接调用 SetForegroundWindow。
        使用 AttachThreadInput 技术绕过限制。

        Args:
            hwnd: 目标窗口句柄
        """
        import win32gui
        import win32process
        import win32api
        import win32con

        # 如果窗口最小化，先恢复
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

        # 获取当前前台窗口的线程 ID
        remote_thread_id = win32process.GetWindowThreadProcessId(win32gui.GetForegroundWindow())[0]
        # 获取当前脚本运行的线程 ID
        current_thread_id = win32api.GetCurrentThreadId()

        if current_thread_id != remote_thread_id:
            # 附加线程输入
            win32process.AttachThreadInput(current_thread_id, remote_thread_id, True)
            # 执行置顶操作
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
            # 操作完成后解除附加，防止系统输入混乱
            win32process.AttachThreadInput(current_thread_id, remote_thread_id, False)
        else:
            win32gui.SetForegroundWindow(hwnd)

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
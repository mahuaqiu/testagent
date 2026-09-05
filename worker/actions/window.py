"""
窗口相关 Action 执行器。

- activate_window: 将指定窗口带到前台并获取焦点（Windows/Mac/Web）
- close_window: 通过 WM_CLOSE 关闭指定窗口（Windows/Web）

match_by 支持 title（窗口标题）、class（窗口类名）。
close_window 额外支持 window_class，可与 title 组合实现双条件精确定位。
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


def _resolve_window_filters(action: Action) -> tuple[str | None, str | None, str | None, str | None]:
    """从 Action 解析 title / class_name / exe_name。

    Returns:
        (title, class_name, exe_name, error)
        error 非空表示参数不合法。
    """
    match_by = action.match_by or "title"
    if match_by not in ("title", "class"):
        return None, None, None, f"Invalid match_by: {match_by}, must be 'title' or 'class'"

    value = action.value if isinstance(action.value, str) else None
    window_class = getattr(action, "window_class", None)
    exe_name = action.name

    title: str | None = None
    class_name: str | None = window_class or None

    if value:
        if match_by == "class":
            class_name = value
        else:
            title = value

    if not title and not class_name:
        return None, None, None, "value or window_class is required"

    return title, class_name, exe_name, None


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
        except pywintypes.error as e:
            # 回调返回 False 主动停止枚举时，pywin32 会把 EnumWindows 返回 0 视为失败
            # 并抛出 error(2, 'EnumWindows', ...)，此时窗口实际已找到，属预期行为
            if not exact_match_hwnd:
                logger.error(f"EnumWindows failed in _find_window_by_class: {e}")
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


class CloseWindowAction(BaseActionExecutor):
    """关闭指定窗口（Windows/Web）。

    通过 Win32 PostMessage(WM_CLOSE) 请求窗口关闭。
    支持 title / class / window_class + name(exe) 组合精确定位，
    避免 #32770 等通用对话框类名误关其他窗口。

    幂等语义：窗口不存在（含已关闭后重复调用）视为成功。
    发送 WM_CLOSE 后轮询等待窗口销毁或隐藏（最长 2 秒，每 0.3 秒检查一次）。
    """

    name = "close_window"
    requires_context = False

    # 发送 WM_CLOSE 后轮询等待窗口关闭的最长时间（秒）
    _CLOSE_WAIT_TIMEOUT = 2.0
    # 轮询间隔（秒）
    _CLOSE_POLL_INTERVAL = 0.3

    def execute(self, platform: "PlatformManager", action: Action, context=None) -> ActionResult:
        """执行关闭窗口。"""
        if platform.platform not in ("windows", "web"):
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error=f"close_window is not supported on {platform.platform}",
            )

        title, class_name, exe_name, error = _resolve_window_filters(action)
        if error:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error=error,
            )

        return self._close_windows(title, class_name, exe_name)

    def _close_windows(
        self,
        title: str | None,
        class_name: str | None,
        exe_name: str | None,
    ) -> ActionResult:
        """Windows 平台关闭窗口。"""
        import win32con
        import win32gui

        from worker.platforms.win_utils import find_window_handle

        filter_desc = self._format_filters(title, class_name, exe_name)

        hwnd = find_window_handle(
            title=title,
            class_name=class_name,
            exe_name=exe_name,
            retry=True,
        )
        if not hwnd:
            # 幂等：窗口已关闭（或不存在）即为目标状态，返回成功
            logger.info(f"Window not found for close_window (already closed): {filter_desc}")
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.SUCCESS,
                output=f"Window not found (already closed): {filter_desc}",
            )

        try:
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        except Exception as e:
            logger.error(f"PostMessage WM_CLOSE failed: hwnd={hwnd}, {e}")
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error=f"Failed to post WM_CLOSE: {e}",
            )

        # 部分应用关闭慢，轮询等待：每 _CLOSE_POLL_INTERVAL 检查一次，
        # 最长 _CLOSE_WAIT_TIMEOUT；窗口销毁或隐藏均视为已关闭
        deadline = time.monotonic() + self._CLOSE_WAIT_TIMEOUT
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(self._CLOSE_POLL_INTERVAL, remaining))
            if not self._window_alive(hwnd):
                logger.info(f"Closed window: {filter_desc}, hwnd={hwnd}")
                return ActionResult(
                    number=0,
                    action_type=self.name,
                    status=ActionStatus.SUCCESS,
                    output=f"Closed window: {filter_desc}",
                )

        logger.warning(
            f"WM_CLOSE sent but window still exists after "
            f"{self._CLOSE_WAIT_TIMEOUT:.0f}s: hwnd={hwnd}, {filter_desc}"
        )
        return ActionResult(
            number=0,
            action_type=self.name,
            status=ActionStatus.FAILED,
            error=(
                f"WM_CLOSE sent but window still exists after "
                f"{self._CLOSE_WAIT_TIMEOUT:.0f}s: {filter_desc} "
                f"(hwnd={hwnd}). App may show a confirm dialog."
            ),
        )

    @staticmethod
    def _window_alive(hwnd: int) -> bool:
        """窗口是否仍然存活且可见。

        已销毁（IsWindow=False）或已隐藏（IsWindowVisible=False）都视为已关闭；
        查询异常按已关闭处理。
        """
        try:
            import win32gui

            if not win32gui.IsWindow(hwnd):
                return False
            return bool(win32gui.IsWindowVisible(hwnd))
        except Exception:
            return False

    @staticmethod
    def _format_filters(
        title: str | None,
        class_name: str | None,
        exe_name: str | None,
    ) -> str:
        parts: list[str] = []
        if title:
            parts.append(f"title~={title}")
        if class_name:
            parts.append(f"class={class_name}")
        if exe_name:
            parts.append(f"exe={exe_name}")
        return ", ".join(parts) if parts else "(empty)"
"""
窗口激活 Action 执行器。

支持 Windows/Mac 平台将指定窗口带到前台并获取焦点。
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

        if not value:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error="value is required",
            )

        if platform.platform == "windows":
            return self._activate_windows(value, match_by)
        elif platform.platform == "mac":
            return self._activate_mac(value, match_by)
        else:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error=f"activate_window is not supported on {platform.platform}",
            )

    def _activate_windows(self, value: str, match_by: str) -> ActionResult:
        """Windows 平台窗口激活。"""
        import pygetwindow as gw

        try:
            if match_by == "title":
                # 按标题查找（包含匹配）
                windows = gw.getWindowsWithTitle(value)
                if not windows:
                    return ActionResult(
                        number=0,
                        action_type=self.name,
                        status=ActionStatus.FAILED,
                        error=f"Window not found: {value}",
                    )
                window = windows[0]
                window.activate()
                logger.info(f"Activated window by title: {window.title}")
                return ActionResult(
                    number=0,
                    action_type=self.name,
                    status=ActionStatus.SUCCESS,
                    output=f"Activated window: {value}",
                )
            else:  # process
                # 按进程名查找 - 使用 psutil 辅助
                import psutil

                for proc in psutil.process_iter(["pid", "name"]):
                    proc_name = proc.info.get("name", "")
                    if proc_name and value.lower() in proc_name.lower():
                        # 找到进程，尝试获取其窗口
                        # pygetwindow 不直接支持按进程名获取窗口
                        # 使用进程名去掉 .exe 后作为窗口标题搜索
                        app_name = proc_name.replace(".exe", "")
                        windows = gw.getWindowsWithTitle(app_name)
                        if windows:
                            windows[0].activate()
                            logger.info(f"Activated window by process: {proc_name}")
                            return ActionResult(
                                number=0,
                                action_type=self.name,
                                status=ActionStatus.SUCCESS,
                                output=f"Activated window by process: {value}",
                            )

                return ActionResult(
                    number=0,
                    action_type=self.name,
                    status=ActionStatus.FAILED,
                    error=f"Window not found by process: {value}",
                )

        except Exception as e:
            logger.error(f"Failed to activate window on Windows: {e}")
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error=f"Failed to activate window: {e}",
            )

    def _activate_mac(self, value: str, match_by: str) -> ActionResult:
        """Mac 平台窗口激活。"""
        try:
            if match_by == "title":
                # Mac 上按标题激活需要特殊处理
                # AppleScript 无法直接通过窗口标题激活，需要额外权限
                # 简化实现：提示用户建议使用 process 模式
                return ActionResult(
                    number=0,
                    action_type=self.name,
                    status=ActionStatus.FAILED,
                    error="Mac platform recommends using match_by='process' for window activation",
                )
            else:  # process
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
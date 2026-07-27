"""
命令执行 Action。

在宿主机执行 shell/cmd 命令，所有平台均支持。
"""

import subprocess  # 用于 TimeoutExpired 异常类型
import logging
import os
from typing import Optional, TYPE_CHECKING

from common.utils import SUBPROCESS_HIDE_WINDOW, run_cmd_with_process_tree_timeout
from worker.tools import get_tools_dir
from worker.task import Action, ActionResult, ActionStatus
from worker.actions.base import BaseActionExecutor

if TYPE_CHECKING:
    from worker.platforms.base import PlatformManager

logger = logging.getLogger(__name__)


class CmdExecAction(BaseActionExecutor):
    """命令执行动作。在宿主机上执行 shell/cmd 命令。"""

    name = "cmd_exec"
    requires_context = False  # 不需要浏览器/设备上下文
    requires_ocr = False

    def execute(self, platform: "PlatformManager", action: Action, context: Optional[object] = None) -> ActionResult:
        cmd = action.value
        if not cmd:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error="command is required (use 'value' field)",
            )

        # 替换 @tools/ 占位符为完整路径
        tools_dir = get_tools_dir()
        cmd = cmd.replace('@tools/', tools_dir + '/')

        # 后台异步执行模式：不等待结果直接返回
        if action.background:
            return self._execute_background(cmd, action)

        # 同步执行模式：等待命令完成
        return self._execute_sync(cmd, action)

    def _execute_background(self, cmd: str, action: Action) -> ActionResult:
        """启动独立后台进程，不纳入任务取消和 Worker 生命周期。"""
        popen_kwargs = {
            "shell": True,
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "close_fds": True,
        }
        if os.name == "nt":
            # 用 CREATE_NO_WINDOW 而非 DETACHED_PROCESS：两者互斥（同时传时后者生效），
            # DETACHED_PROCESS 下 cmd 派生的控制台子程序会自行分配可见控制台导致黑框闪现，
            # CREATE_NO_WINDOW 则提供一个隐形控制台供子进程继承。
            popen_kwargs["creationflags"] = (
                subprocess.CREATE_NEW_PROCESS_GROUP | SUBPROCESS_HIDE_WINDOW
            )
        else:
            popen_kwargs["start_new_session"] = True
        subprocess.Popen(cmd, **popen_kwargs)

        logger.info(f"Executing command in background: {cmd}")

        return ActionResult(
            number=0,
            action_type=self.name,
            status=ActionStatus.SUCCESS,
            output="command started in background",
        )

    def _execute_sync(self, cmd: str, action: Action) -> ActionResult:
        """同步执行命令，等待结果返回。"""

        # 超时时间，默认 30 秒
        timeout_ms = action.timeout if action.timeout_explicit else 30000
        if action.execution_control:
            remaining = action.execution_control.remaining_seconds()
            if remaining is not None:
                remaining_ms = max(1, int(remaining * 1000))
                timeout_ms = min(timeout_ms, remaining_ms) if action.timeout_explicit else remaining_ms
        timeout_sec = timeout_ms / 1000

        logger.info(f"Executing command: {cmd}")

        try:
            result = run_cmd_with_process_tree_timeout(
                cmd,
                shell=True,
                timeout=timeout_sec,
            )

            status = ActionStatus.SUCCESS if result.returncode == 0 else ActionStatus.FAILED

            logger.info(f"Command completed: exit_code={result.returncode}")

            # 日志增强：输出 stdout/stderr 最多 500 字符
            if result.stdout:
                stdout_preview = result.stdout[-500:] if len(result.stdout) > 500 else result.stdout
                logger.info(f"Script output: {stdout_preview}")

            if result.stderr:
                stderr_preview = result.stderr[-500:] if len(result.stderr) > 500 else result.stderr
                if result.returncode != 0:
                    logger.error(f"Script error: {stderr_preview}")
                else:
                    logger.info(f"Script stderr: {stderr_preview}")

            # 成功时 output 返回 stdout，失败时返回 stderr（最多 500 字符）
            if result.returncode == 0:
                output_text = result.stdout or ""
            else:
                output_text = result.stderr or ""
            output_preview = output_text[-500:] if len(output_text) > 500 else output_text

            return ActionResult(
                number=0,
                action_type=self.name,
                status=status,
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                output=output_preview,
                error=result.stderr if result.returncode != 0 else None,
            )

        except subprocess.TimeoutExpired:
            logger.warning(f"Command timeout after {timeout_ms}ms")
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error=f"Command timeout after {timeout_ms}ms",
            )
        except Exception as e:
            logger.error(f"Command execution failed: {e}")
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error=str(e),
            )

"""
命令执行 Action。

在宿主机执行 shell/cmd 命令，所有平台均支持。
"""

import subprocess  # 用于 TimeoutExpired 异常类型
import logging
from typing import Optional, TYPE_CHECKING

from common.utils import run_cmd
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

        # 超时时间，默认 30 秒
        timeout_ms = action.timeout or 30000
        timeout_sec = timeout_ms / 1000

        logger.info(f"Executing command: {cmd[:100]}...")

        try:
            result = run_cmd(
                cmd,
                shell=True,
                timeout=timeout_sec,
            )

            status = ActionStatus.SUCCESS if result.returncode == 0 else ActionStatus.FAILED

            logger.info(f"Command completed: exit_code={result.returncode}")

            # 日志增强：输出 stdout/stderr 后 500 字符
            if result.stdout:
                stdout_preview = result.stdout[-500:] if len(result.stdout) > 500 else result.stdout
                logger.info(f"Script output: {stdout_preview}")

            if result.stderr:
                stderr_preview = result.stderr[-500:] if len(result.stderr) > 500 else result.stderr
                if result.returncode != 0:
                    logger.error(f"Script error: {stderr_preview}")

            # 输出信息截断（避免过长）
            output_preview = cmd[:50] if len(cmd) > 50 else cmd

            return ActionResult(
                number=0,
                action_type=self.name,
                status=status,
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                output=f"Command executed: {output_preview}",
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
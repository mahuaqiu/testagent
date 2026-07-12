"""
命令执行 Action。

在宿主机执行 shell/cmd 命令，所有平台均支持。
"""

import subprocess  # 用于 TimeoutExpired 异常类型
import logging
import threading
from typing import Optional, TYPE_CHECKING

from common.utils import run_cmd_with_process_tree_timeout
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
    _background_processes: set[subprocess.Popen] = set()
    _background_lock = threading.RLock()

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
        """启动可由 Worker 生命周期统一回收的后台进程。"""
        with self._background_lock:
            self._background_processes = {
                process for process in self._background_processes if process.poll() is None
            }
            process = subprocess.Popen(
                cmd,
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._background_processes.add(process)

        logger.info(f"Executing command in background: {cmd}")

        return ActionResult(
            number=0,
            action_type=self.name,
            status=ActionStatus.SUCCESS,
            output="command started in background",
        )

    @classmethod
    def shutdown_background_processes(cls) -> None:
        """停止仍在运行的后台命令。"""
        with cls._background_lock:
            processes = list(cls._background_processes)
            cls._background_processes.clear()
        for process in processes:
            if process.poll() is None:
                try:
                    process.kill()
                except OSError:
                    pass

    def _execute_sync(self, cmd: str, action: Action) -> ActionResult:
        """同步执行命令，等待结果返回。"""

        # 超时时间，默认 30 秒
        timeout_ms = action.timeout or 30000
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
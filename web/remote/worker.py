"""
Worker —— 主服务类。

作为独立服务入口，管理浏览器、会话、任务队列。
支持从队列获取任务并执行，返回执行结果。

Usage:
    from web.remote.worker import Worker

    # 启动服务
    worker = Worker(cdp_port=9222)
    worker.start()

    # 提交任务
    task_id = worker.submit_task(task)

    # 执行任务
    result = worker.execute_task(task)

    # 获取结果
    result = worker.get_result(task_id)

    # 停止服务
    worker.stop()
"""

from dataclasses import dataclass
from typing import Optional
import threading
import time

from web.remote.browser import RemoteBrowser
from web.remote.session import SessionManager, Session
from web.remote.task import Task, TaskQueue
from web.remote.page import RemotePage
from web.remote.result import TaskResult, ResultBuilder, TaskStatus
from web.remote.actions import ActionExecutor
from web.remote.logger import ActionLogger
from web.remote.screenshot import ScreenshotManager


@dataclass
class WorkerConfig:
    """Worker 配置。"""

    cdp_port: int = 9222
    cdp_endpoint: Optional[str] = None  # 远程连接时使用
    headless: bool = True
    session_timeout: int = 300  # 秒
    browser_timeout: int = 30000  # 毫秒
    screenshot_dir: str = "data/screenshots"
    auto_cleanup: bool = True  # 自动清理超时会话
    cleanup_interval: int = 60  # 清理间隔（秒）


class Worker:
    """
    Worker 主服务类。

    管理浏览器、会话、任务队列，提供任务提交、执行、结果查询功能。

    Attributes:
        config: Worker 配置
        browser: RemoteBrowser 实例
        session_manager: SessionManager 实例
        task_queue: TaskQueue 实例
        _running: 是否运行中
        _worker_thread: 工作线程
    """

    def __init__(
        self,
        cdp_port: int = 9222,
        cdp_endpoint: Optional[str] = None,
        headless: bool = True,
        session_timeout: int = 300,
        browser_timeout: int = 30000,
        screenshot_dir: str = "data/screenshots",
        auto_cleanup: bool = True,
        cleanup_interval: int = 60,
    ):
        """
        初始化 Worker。

        Args:
            cdp_port: CDP 端口
            cdp_endpoint: 远程 CDP 端点（使用远程连接模式）
            headless: 是否无头模式
            session_timeout: 会话超时秒数
            browser_timeout: 浏览器默认超时（毫秒）
            screenshot_dir: 截图目录
            auto_cleanup: 是否自动清理超时会话
            cleanup_interval: 清理间隔（秒）
        """
        self.config = WorkerConfig(
            cdp_port=cdp_port,
            cdp_endpoint=cdp_endpoint,
            headless=headless,
            session_timeout=session_timeout,
            browser_timeout=browser_timeout,
            screenshot_dir=screenshot_dir,
            auto_cleanup=auto_cleanup,
            cleanup_interval=cleanup_interval,
        )

        self._browser: Optional[RemoteBrowser] = None
        self._session_manager: Optional[SessionManager] = None
        self._task_queue = TaskQueue()
        self._running = False
        self._worker_thread: Optional[threading.Thread] = None
        self._cleanup_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    @property
    def browser(self) -> Optional[RemoteBrowser]:
        return self._browser

    @property
    def session_manager(self) -> Optional[SessionManager]:
        return self._session_manager

    @property
    def task_queue(self) -> TaskQueue:
        return self._task_queue

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> str:
        """
        启动 Worker 服务。

        Returns:
            str: CDP WebSocket 端点

        Raises:
            RuntimeError: 服务已启动
        """
        if self._running:
            raise RuntimeError("Worker already running. Call stop() first.")

        # 创建并启动浏览器
        self._browser = RemoteBrowser(
            cdp_port=self.config.cdp_port,
            headless=self.config.headless,
            timeout=self.config.browser_timeout,
        )

        if self.config.cdp_endpoint:
            # 远程连接模式
            self._browser.connect(self.config.cdp_endpoint)
        else:
            # 本地启动模式
            self._browser.start()

        # 创建会话管理器
        self._session_manager = SessionManager(
            browser=self._browser,
            session_timeout=self.config.session_timeout,
        )

        self._running = True

        # 启动自动清理线程
        if self.config.auto_cleanup:
            self._cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
            self._cleanup_thread.start()

        return self._browser.get_ws_endpoint()

    def stop(self) -> None:
        """停止 Worker 服务。"""
        self._running = False

        # 关闭所有会话
        if self._session_manager:
            self._session_manager.close_all_sessions()

        # 关闭浏览器
        if self._browser:
            self._browser.close()

        self._browser = None
        self._session_manager = None

    def submit_task(self, task: Task) -> str:
        """
        提交任务到队列。

        Args:
            task: 任务定义

        Returns:
            str: 任务 ID
        """
        self._task_queue.push(task)
        return task.task_id

    def execute_task(self, task: Task) -> TaskResult:
        """
        执行单个任务。

        流程:
        1. 创建会话
        2. 获取页面
        3. 逐个执行动作
        4. 构建结果
        5. 关闭会话
        6. 返回结果

        Args:
            task: 任务定义

        Returns:
            TaskResult: 执行结果
        """
        result_builder = ResultBuilder(task.task_id)
        result_builder.start_task()
        result_builder.set_metadata("user_id", task.user_id)

        session = None
        try:
            # 1. 创建会话
            session = self._session_manager.create_session(
                user_id=task.user_id,
                metadata=task.metadata,
            )
            result_builder.set_metadata("session_id", session.session_id)

            # 2. 获取页面
            context = session.context
            playwright_page = context.new_page()

            # 创建 RemotePage
            logger = ActionLogger(session_id=session.session_id)
            screenshot_mgr = ScreenshotManager(output_dir=self.config.screenshot_dir)
            remote_page = RemotePage(
                page=playwright_page,
                logger=logger,
                screenshot_manager=screenshot_mgr,
                session_id=session.session_id,
            )

            # 3. 执行动作
            executor = ActionExecutor(
                page=remote_page,
                logger=logger,
                screenshot_manager=screenshot_mgr,
            )

            action_results = executor.execute_sequence(task.actions)

            for ar in action_results:
                result_builder.add_action_result(ar)
                # 添加日志
                logger.log_info(
                    ar.action.action_type,
                    f"Action {ar.index}: {ar.status}",
                    {"duration_ms": ar.duration_ms, "error": ar.error},
                )

            # 添加截图
            for screenshot in screenshot_mgr.get_screenshots():
                result_builder.add_screenshot(
                    screenshot.name, screenshot.data, screenshot.action_index
                )

            # 添加日志
            for log in logger.get_logs():
                result_builder.add_log(
                    action=log.action,
                    detail=log.detail,
                    level=log.level,
                    duration_ms=log.duration_ms,
                )

            # 判断最终状态
            failed_actions = [ar for ar in action_results if ar.status == "failed"]
            if failed_actions:
                result_builder.set_error(failed_actions[0].error)
                status = TaskStatus.FAILED
            else:
                status = TaskStatus.SUCCESS

            result = result_builder.finish_task(status)

        except Exception as e:
            result_builder.set_error(str(e))
            result = result_builder.finish_task(TaskStatus.FAILED)

        finally:
            # 5. 关闭会话
            if session:
                self._session_manager.close_session(session.session_id)

        # 6. 存储结果
        self._task_queue.store_result(result)

        return result

    def run_once(self) -> Optional[TaskResult]:
        """
        执行队列中的一个任务。

        Returns:
            TaskResult 或 None（队列为空时）
        """
        task = self._task_queue.pop()
        if task is None:
            return None

        return self.execute_task(task)

    def run_loop(self) -> None:
        """
        持续运行，处理队列中的任务。

        在独立线程中运行。
        """
        while self._running:
            try:
                self.run_once()
            except Exception as e:
                print(f"[Worker] Error executing task: {e}")

            # 短暂休眠避免 CPU 空转
            time.sleep(0.1)

    def _cleanup_loop(self) -> None:
        """自动清理超时会话的循环。"""
        while self._running:
            try:
                if self._session_manager:
                    expired = self._session_manager.cleanup_expired()
                    if expired:
                        print(f"[Worker] Cleaned up {len(expired)} expired sessions")
            except Exception as e:
                print(f"[Worker] Cleanup error: {e}")

            time.sleep(self.config.cleanup_interval)

    def get_result(self, task_id: str) -> Optional[TaskResult]:
        """
        获取任务结果。

        Args:
            task_id: 任务 ID

        Returns:
            TaskResult 或 None
        """
        return self._task_queue.get_result(task_id)

    def get_status(self) -> dict:
        """
        获取 Worker 状态。

        Returns:
            dict: 状态信息
        """
        return {
            "running": self._running,
            "active_sessions": self._session_manager.get_active_count() if self._session_manager else 0,
            "queue_size": self._task_queue.size(),
            "cdp_endpoint": self._browser.get_ws_endpoint() if self._browser else None,
            "browser_version": self._browser.version() if self._browser else None,
        }

    def get_cdp_endpoint(self) -> Optional[str]:
        """
        获取 CDP 端点。

        Returns:
            str 或 None
        """
        if self._browser:
            return self._browser.get_ws_endpoint()
        return None

    def create_session(
        self,
        user_id: str,
        context_options: Optional[dict] = None,
    ) -> Session:
        """
        创建新会话。

        Args:
            user_id: 用户 ID
            context_options: 上下文选项

        Returns:
            Session: 会话对象
        """
        if not self._session_manager:
            raise RuntimeError("Worker not started. Call start() first.")
        return self._session_manager.create_session(user_id, context_options)

    def close_session(self, session_id: str) -> bool:
        """
        关闭会话。

        Args:
            session_id: 会话 ID

        Returns:
            bool: 是否成功
        """
        if not self._session_manager:
            return False
        return self._session_manager.close_session(session_id)

    def __enter__(self) -> "Worker":
        """上下文管理器入口。"""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """上下文管理器退出。"""
        self.stop()

    def __repr__(self) -> str:
        return (
            f"Worker(running={self._running}, "
            f"cdp_port={self.config.cdp_port}, "
            f"sessions={self._session_manager.get_active_count() if self._session_manager else 0})"
        )
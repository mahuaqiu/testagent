"""
Web Remote 模块 —— 基于 Playwright CDP 的远程测试执行能力。

提供浏览器远程连接、会话管理、页面操作封装、任务队列和结果输出。
供外部 pytest agent 通过 CDP 协议调度执行测试任务。

Usage:
    from web.remote import Worker, RemoteBrowser, RemotePage

    # 启动服务
    worker = Worker()
    worker.start()

    # 或直接使用浏览器
    browser = RemoteBrowser()
    browser.start()
    cdp_endpoint = browser.get_ws_endpoint()
"""

from web.remote.browser import RemoteBrowser
from web.remote.page import RemotePage
from web.remote.session import SessionManager, Session
from web.remote.task import Task, TaskQueue, Action, TaskConfig
from web.remote.result import TaskResult, ActionResult, ResultBuilder, TaskStatus
from web.remote.logger import ActionLogger, LogEntry
from web.remote.screenshot import ScreenshotManager
from web.remote.actions import ActionExecutor
from web.remote.worker import Worker

__all__ = [
    "RemoteBrowser",
    "RemotePage",
    "SessionManager",
    "Session",
    "Task",
    "TaskQueue",
    "Action",
    "TaskConfig",
    "TaskResult",
    "ActionResult",
    "ResultBuilder",
    "TaskStatus",
    "ActionLogger",
    "LogEntry",
    "ScreenshotManager",
    "ActionExecutor",
    "Worker",
]
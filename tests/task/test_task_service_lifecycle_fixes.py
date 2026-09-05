"""TaskService 取消/关闭/资源域修复的回归测试。"""

import threading
import time
from datetime import datetime, timedelta

from worker.scheduling.scheduler import ResourceScheduler
from worker.storage.database import Database
from worker.task.result import TaskResult, TaskStatus
from worker.task.service import TaskService
from worker.task.sqlite_repository import SQLiteTaskRepository
from worker.task.task import Task


def make_service(tmp_path, callback):
    database = Database(tmp_path / "worker.db")
    repository = SQLiteTaskRepository(database)
    return TaskService(repository, ResourceScheduler(), callback, max_workers=2), repository


def make_task(platform="web", task_id="task-1"):
    task = Task.create(platform=platform, actions=[], generate_id=False)
    task.task_id = task_id
    return task


def test_cancel_without_handle_marks_cancelled(tmp_path):
    """重启后残留的 running 任务（无运行句柄）取消时直接置为 cancelled。"""
    service, repository = make_service(
        tmp_path, lambda task, cancel_event: TaskResult(status=TaskStatus.SUCCESS, platform=task.platform)
    )
    task = make_task()
    repository.create(
        task,
        request_id=None,
        status=TaskStatus.RUNNING,
        idempotency_key=None,
        expires_at=datetime.now() + timedelta(hours=1),
    )

    result = service.cancel("task-1")

    assert result["status"] == TaskStatus.CANCELLED.value
    assert service.get("task-1")["status"] == TaskStatus.CANCELLED.value
    service.shutdown()


def test_desktop_platform_remote_domain_is_exclusive_with_task_domain(tmp_path):
    """桌面平台共享物理鼠标：remote 域与 task 域必须互斥。"""
    started = threading.Event()
    release = threading.Event()

    def callback(task, cancel_event):
        started.set()
        release.wait(2)
        return TaskResult(status=TaskStatus.SUCCESS, platform=task.platform)

    service, _ = make_service(tmp_path, callback)
    service.submit_async(make_task(platform="windows", task_id="win-task-1"))
    assert started.wait(1)

    remote_task = make_task(platform="windows", task_id="win-remote-1")
    remote_task.execution_domain = "remote"
    try:
        service.submit_async(remote_task)
    except Exception as exc:
        assert getattr(exc, "code", None) == "DEVICE_BUSY"
    else:
        raise AssertionError("remote 域的 Windows 操作必须与 task 域互斥")

    release.set()
    service.shutdown()


def test_shutdown_with_timeout_returns_bounded(tmp_path):
    """任务卡死时 shutdown(timeout) 必须有界返回。"""
    started = threading.Event()
    release = threading.Event()

    def callback(task, cancel_event):
        started.set()
        release.wait(5)
        return TaskResult(status=TaskStatus.SUCCESS, platform=task.platform)

    service, _ = make_service(tmp_path, callback)
    service.submit_async(make_task())
    assert started.wait(1)

    begin = time.monotonic()
    service.shutdown(timeout=0.2)
    elapsed = time.monotonic() - begin
    assert elapsed < 2.0, "shutdown 必须在超时后返回而不是无限等待"

    release.set()

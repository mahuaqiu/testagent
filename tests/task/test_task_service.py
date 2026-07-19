"""TaskService 生命周期和并发测试。"""

import threading
import time

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


def make_action_task(action_types, platform="web", task_id="task-1"):
    task = Task.create(
        platform=platform,
        actions=[{"action_type": action_type, "value": "echo ok"} for action_type in action_types],
        generate_id=False,
    )
    task.task_id = task_id
    return task


def test_task_service_runs_sync_and_keeps_queryable_result(tmp_path):
    def callback(task, cancel_event):
        return TaskResult(status=TaskStatus.SUCCESS, platform=task.platform)

    service, repository = make_service(tmp_path, callback)
    result = service.execute_sync(make_task())
    assert result.status == TaskStatus.SUCCESS
    queried = service.get(result.task_id)
    assert queried["status"] == TaskStatus.SUCCESS.value
    assert repository.get(result.task_id) is not None
    service.shutdown()


def test_task_service_rejects_concurrent_same_resource(tmp_path):
    started = threading.Event()
    release = threading.Event()

    def callback(task, cancel_event):
        started.set()
        release.wait(2)
        return TaskResult(status=TaskStatus.SUCCESS, platform=task.platform)

    service, _ = make_service(tmp_path, callback)
    service.submit_async(make_task(task_id="task-1"))
    assert started.wait(1)
    try:
        service.submit_async(make_task(task_id="task-2"))
    except Exception as exc:
        assert getattr(exc, "code", None) == "DEVICE_BUSY"
    else:
        raise AssertionError("same resource must be rejected")
    release.set()
    time.sleep(0.05)
    service.shutdown()


def test_task_service_cancel_is_idempotent_and_releases_resource(tmp_path):
    started = threading.Event()

    def callback(task, cancel_event):
        started.set()
        while not cancel_event.is_set():
            time.sleep(0.01)
        return TaskResult(status=TaskStatus.CANCELLED, platform=task.platform)

    service, _ = make_service(tmp_path, callback)
    service.submit_async(make_task())
    assert started.wait(1)
    assert service.cancel("task-1")["status"] == "cancelling"
    deadline = time.time() + 1
    while time.time() < deadline:
        if service.get("task-1")["status"] == TaskStatus.CANCELLED.value:
            break
        time.sleep(0.01)
    assert service.get("task-1")["status"] == TaskStatus.CANCELLED.value
    assert not service.scheduler.is_busy("web")
    assert service.cancel("task-1")["status"] == TaskStatus.CANCELLED.value
    service.shutdown()


def test_cmd_exec_does_not_block_platform_resource(tmp_path):
    command_started = threading.Event()
    command_release = threading.Event()

    def callback(task, cancel_event):
        if task.actions and task.actions[0].action_type == "cmd_exec":
            command_started.set()
            command_release.wait(2)
        return TaskResult(status=TaskStatus.SUCCESS, platform=task.platform)

    service, _ = make_service(tmp_path, callback)
    service.submit_async(make_action_task(["cmd_exec"], task_id="command-1"))
    assert command_started.wait(1)

    result = service.execute_sync(make_action_task(["wait"], task_id="web-1"))
    assert result.status == TaskStatus.SUCCESS

    command_release.set()
    service.shutdown()


def test_cmd_exec_cannot_be_mixed_with_platform_actions(tmp_path):
    def callback(task, cancel_event):
        return TaskResult(status=TaskStatus.SUCCESS, platform=task.platform)

    service, _ = make_service(tmp_path, callback)
    try:
        service.submit_async(make_action_task(["cmd_exec", "click"]))
    except Exception as exc:
        assert getattr(exc, "code", None) == "INVALID_TASK_ACTIONS"
    else:
        raise AssertionError("cmd_exec 与平台动作混用时必须拒绝")
    service.shutdown()

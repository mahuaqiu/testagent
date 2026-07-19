"""Action 统一超时与取消控制测试。"""

import threading
import time

import pytest

from worker.actions.coordinate import WaitAction
from worker.actions.spec import ActionCancelled, ActionTimedOut, ExecutionControl
from worker.task import Action
from worker.task.task import Task


def test_wait_action_obeys_action_deadline() -> None:
    action = Action(action_type="wait", value=1000)
    action.execution_control = ExecutionControl(
        deadline_monotonic=time.monotonic() + 0.02,
    )

    with pytest.raises(ActionTimedOut):
        WaitAction().execute(None, action)


def test_wait_action_obeys_task_cancellation() -> None:
    cancel_event = threading.Event()
    cancel_event.set()
    action = Action(action_type="wait", value=1000)
    action.execution_control = ExecutionControl(cancel_event=cancel_event)

    with pytest.raises(ActionCancelled):
        WaitAction().execute(None, action)


def test_action_records_whether_timeout_was_explicit() -> None:
    implicit = Task.create("web", [{"action_type": "wait", "value": 1}]).actions[0]
    explicit = Task.create(
        "web", [{"action_type": "wait", "value": 1, "timeout": 5000}]
    ).actions[0]

    assert implicit.timeout == 30000
    assert implicit.timeout_explicit is False
    assert explicit.timeout == 5000
    assert explicit.timeout_explicit is True

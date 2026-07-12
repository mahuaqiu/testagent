"""Action 公共校验和执行控制测试。"""

import threading
import time

import pytest

from worker.actions.spec import ActionCancelled, ActionTimedOut, ExecutionControl
from worker.actions.validation import validate_action_dict, validate_actions
from worker.errors import WorkerError


def test_validation_rejects_invalid_common_fields():
    with pytest.raises(WorkerError) as timeout_error:
        validate_action_dict({"action_type": "click", "timeout": 0})
    assert timeout_error.value.code == "INVALID_REQUEST"

    with pytest.raises(WorkerError):
        validate_action_dict({"action_type": "image_click", "image_base64": "bad"})

    with pytest.raises(WorkerError):
        validate_action_dict({"action_type": "click", "region": [0, 0, 0, 10]})


def test_validation_rejects_empty_and_oversized_action_list():
    with pytest.raises(WorkerError):
        validate_actions([])
    with pytest.raises(WorkerError):
        validate_actions([{"action_type": "wait"}] * 2, max_actions=1)


def test_execution_control_wait_can_be_cancelled():
    event = threading.Event()
    control = ExecutionControl(cancel_event=event)
    event.set()
    with pytest.raises(ActionCancelled):
        control.wait(1)


def test_execution_control_deadline_is_enforced():
    control = ExecutionControl(deadline_monotonic=time.monotonic() - 1)
    with pytest.raises(ActionTimedOut):
        control.checkpoint()


def test_action_registry_rejects_duplicate_registration():
    from worker.actions.registry import ActionRegistry

    class Executor:
        name = "duplicate-test-action"

    ActionRegistry.clear()
    ActionRegistry.register(Executor())
    with pytest.raises(ValueError, match="already registered"):
        ActionRegistry.register(Executor())
    ActionRegistry.clear()

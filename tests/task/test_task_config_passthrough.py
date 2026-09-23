"""任务级 config 透传测试：平台可下发任务总超时，覆盖默认 5 分钟 deadline。"""

from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

import worker.server as server
from worker.task.task import Task
from worker.worker import Worker


def test_task_request_accepts_config() -> None:
    req = server.TaskRequest(
        platform="windows",
        actions=[{"action_type": "cmd_exec", "value": "dir", "timeout": 600000}],
        config={"timeout": 660000},
    )
    assert req.config == {"timeout": 660000}


def test_task_request_config_defaults_to_none() -> None:
    req = server.TaskRequest(platform="windows", actions=[])
    assert req.config is None


def test_execute_sync_passes_config_to_task() -> None:
    captured: dict[str, Any] = {}

    def fake_execute_sync(task: Task, request_id: Any = None) -> Any:
        captured["task"] = task
        return SimpleNamespace(to_dict=lambda **kw: {"status": "success"})

    w = Worker.__new__(Worker)
    w.runtime = SimpleNamespace(task_service=SimpleNamespace(execute_sync=fake_execute_sync))

    w.execute_sync(
        "windows",
        [{"action_type": "cmd_exec", "value": "dir", "timeout": 600000}],
        config={"timeout": 660000},
    )

    assert captured["task"].config.timeout == 660000


def test_execute_async_passes_config_to_task() -> None:
    captured: dict[str, Any] = {}

    def fake_submit_async(task: Task, request_id: Any = None, idempotency_key: Any = None) -> Any:
        captured["task"] = task
        return "task_1", "accepted"

    w = Worker.__new__(Worker)
    w.runtime = SimpleNamespace(
        task_service=SimpleNamespace(
            submit_async=fake_submit_async,
            get_request_id=lambda task_id: "req_1",
        )
    )

    w.execute_async(
        "windows",
        [{"action_type": "cmd_exec", "value": "dir", "timeout": 600000}],
        config={"timeout": 660000},
        idempotency_key="k1",
    )

    assert captured["task"].config.timeout == 660000


def test_sync_endpoint_forwards_config_to_worker() -> None:
    captured: dict[str, Any] = {}

    class StubWorker:
        def execute_sync(self, platform, actions, device_id=None, window=None, config=None):
            captured.update(platform=platform, actions=actions, config=config)
            return {"status": "success"}

    server.set_worker(StubWorker())  # type: ignore[arg-type]
    try:
        client = TestClient(server.app)
        resp = client.post(
            "/task/execute",
            json={
                "platform": "windows",
                "actions": [{"action_type": "cmd_exec", "value": "dir"}],
                "config": {"timeout": 660000},
            },
        )
        assert resp.status_code == 200
        assert captured["config"] == {"timeout": 660000}
    finally:
        server.set_worker(None)

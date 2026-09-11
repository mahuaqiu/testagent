"""任务级超时不得覆盖已自行完成的动作结果。

断言轮询的最后一轮检查可能超出动作窗口（截图+OCR 耗时超预算），此时断言
已按 FAILED 正常返回；调度层的 checkpoint 不得把它升级成整个任务 TIMEOUT。
"""

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

from worker.task import ActionStatus, Task, TaskStatus
from worker.worker import Worker


class _SlowFailManager:
    """execute_action 在动作截止后才返回 FAILED（模拟最后一轮检查超预算）。"""

    def __init__(self) -> None:
        self.context = SimpleNamespace()

    def execute_action(self, context, action):
        time.sleep(0.4)
        from worker.task import ActionResult

        return ActionResult(
            number=0,
            action_type="ocr_assert",
            status=ActionStatus.FAILED,
            error="Texts not found: ['操作成功']",
        )

    def get_screenshot(self, context) -> bytes:
        return b"not-an-image"  # 失败截图压缩失败走告警分支即可


def test_assert_failed_not_escalated_to_task_timeout() -> None:
    task = Task.create(
        platform="test",
        actions=[{"action_type": "ocr_assert", "value": "操作成功", "timeout": 200}],
        config={"timeout": 60000},
    )
    worker = Worker.__new__(Worker)
    worker.config = SimpleNamespace(action_step_delay=0)
    worker.artifact_service = MagicMock()

    result = worker._execute_actions(_SlowFailManager(), _SlowFailManager().context, task)

    assert result.status == TaskStatus.FAILED
    assert result.error == "Texts not found: ['操作成功']"
    assert result.actions[-1].status == ActionStatus.FAILED

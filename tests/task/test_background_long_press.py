"""任务循环 background 后台异步长按测试。

规则：background=true 仅对长按动作（click_duration>0；press 须为单个非组合键）
与 cmd_exec 有意义；其余动作显式判失败——异步只有在长按的时候才有意义。
"""

import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

from worker.task import ActionStatus, Task, TaskStatus
from worker.worker import Worker


class _LongPressManager:
    """execute_action 模拟鸿蒙长按注入：阻塞一段时间后返回成功。"""

    def __init__(self, hold_seconds: float = 0.5) -> None:
        self.hold_seconds = hold_seconds
        self.executed: list[str] = []
        self.started = threading.Event()
        self.context = SimpleNamespace()

    def execute_action(self, context, action):
        self.started.set()
        time.sleep(self.hold_seconds)
        self.executed.append(action.action_type)
        from worker.task import ActionResult

        return ActionResult(
            number=0,
            action_type=action.action_type,
            status=ActionStatus.SUCCESS,
            output="long pressed",
        )

    def get_screenshot(self, context) -> bytes:
        return b""


class _RecordingManager:
    """记录 execute_action 是否被调用（用于非长按拒绝路径）。"""

    def __init__(self) -> None:
        self.context = SimpleNamespace()
        self.executed: list[str] = []

    def execute_action(self, context, action):
        self.executed.append(action.action_type)
        from worker.task import ActionResult

        return ActionResult(
            number=0,
            action_type=action.action_type,
            status=ActionStatus.SUCCESS,
        )

    def get_screenshot(self, context) -> bytes:
        return b""


def _run_actions(manager, actions: list[dict], config: dict | None = None):
    task = Task.create(
        platform="harmony_pc",
        actions=actions,
        config=config or {"timeout": 60000},
    )
    worker = Worker.__new__(Worker)
    worker.config = SimpleNamespace(action_step_delay=0)
    worker.artifact_service = MagicMock()
    return worker._execute_actions(manager, manager.context, task)


def test_background_long_press_dispatches_and_returns_immediately() -> None:
    """长按 background=true：立即返回 SUCCESS，注入在后台线程完成。"""
    manager = _LongPressManager(hold_seconds=0.5)

    started = time.monotonic()
    result = _run_actions(
        manager,
        [{"action_type": "press", "value": "space", "click_duration": 2000, "background": True}],
    )
    dispatch_ms = (time.monotonic() - started) * 1000

    assert result.status == TaskStatus.SUCCESS
    assert result.actions[0].status == ActionStatus.SUCCESS
    assert "background" in result.actions[0].output
    # 任务不被长按阻塞：派发应在远小于长按时长内返回
    assert dispatch_ms < 2000

    # 等后台线程真实完成，验证注入确实被执行
    assert manager.started.wait(timeout=3)
    manager_thread_done = False
    for _ in range(100):
        if manager.executed == ["press"]:
            manager_thread_done = True
            break
        time.sleep(0.05)
    assert manager_thread_done


def test_background_long_press_continues_subsequent_actions() -> None:
    """后台长按期间，后续动作立刻继续执行（不被按住时长阻塞）。"""
    marker = _RecordingManager()
    executed: list[str] = []

    class _SeqManager:
        """第一个动作派发后台长按，第二个动作走记录桩。"""

        def __init__(self) -> None:
            self.context = SimpleNamespace()
            self.long = _LongPressManager(hold_seconds=0.8)
            self.recorder = marker

        def execute_action(self, context, action):
            if action.action_type == "press":
                return self.long.execute_action(context, action)
            executed.append(action.action_type)
            return self.recorder.execute_action(context, action)

        def get_screenshot(self, context) -> bytes:
            return b""

    seq = _SeqManager()
    task = Task.create(
        platform="harmony_pc",
        actions=[
            {"action_type": "press", "value": "w", "click_duration": 3000, "background": True},
            {"action_type": "wait", "value": "50"},
        ],
        config={"timeout": 60000},
    )
    worker = Worker.__new__(Worker)
    worker.config = SimpleNamespace(action_step_delay=0)
    worker.artifact_service = MagicMock()

    result = worker._execute_actions(seq, seq.context, task)

    assert result.status == TaskStatus.SUCCESS
    assert result.actions[0].status == ActionStatus.SUCCESS
    assert result.actions[1].status == ActionStatus.SUCCESS
    # 后台长按尚未结束时，后续动作已执行
    assert "wait" in executed


def test_background_on_plain_click_rejected() -> None:
    """普通点击 background=true 无意义 → 动作 FAILED，不执行。"""
    manager = _RecordingManager()

    result = _run_actions(
        manager,
        [{"action_type": "click", "x": 10, "y": 20, "background": True}],
    )

    assert result.status == TaskStatus.FAILED
    assert result.actions[0].status == ActionStatus.FAILED
    assert "长按" in result.actions[0].error
    assert manager.executed == []


def test_background_on_press_list_and_combo_rejected() -> None:
    """press 列表/组合键忽略 click_duration（非长按）→ background 拒绝。"""
    manager = _RecordingManager()

    # 按键列表：非长按，拒绝
    result = _run_actions(
        manager,
        [
            {
                "action_type": "press",
                "value": ["space", "space"],
                "click_duration": 2000,
                "background": True,
            }
        ],
    )
    assert result.status == TaskStatus.FAILED
    assert result.actions[0].status == ActionStatus.FAILED
    assert "长按" in result.actions[0].error

    # 组合键：忽略 click_duration，拒绝
    result = _run_actions(
        manager,
        [{"action_type": "press", "value": "ctrl+v", "click_duration": 2000, "background": True}],
    )
    assert result.status == TaskStatus.FAILED
    assert result.actions[0].status == ActionStatus.FAILED

    assert manager.executed == []


def test_background_on_long_press_click_types_dispatched() -> None:
    """click/ocr_click 等点击类动作 click_duration>0 + background 走异步。"""
    manager = _LongPressManager(hold_seconds=0.2)

    result = _run_actions(
        manager,
        [{"action_type": "click", "x": 10, "y": 20, "click_duration": 1500, "background": True}],
    )

    assert result.status == TaskStatus.SUCCESS
    assert result.actions[0].status == ActionStatus.SUCCESS
    assert manager.started.wait(timeout=3)


def test_sync_long_press_without_background_still_blocks() -> None:
    """不带 background 的长按保持同步阻塞语义（行为不变）。"""
    manager = _LongPressManager(hold_seconds=0.3)

    result = _run_actions(
        manager,
        [{"action_type": "press", "value": "space", "click_duration": 300}],
    )

    assert result.status == TaskStatus.SUCCESS
    assert result.actions[0].status == ActionStatus.SUCCESS
    assert manager.executed == ["press"]

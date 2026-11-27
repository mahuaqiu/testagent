"""press 动作执行器测试：序列批量、长按与组合键分流。

平台注入方式的详细断言在 tests/test_harmony_platform.py（uinput 命令格式），
本文件只验证 PressAction 对平台方法的分发行为。
"""

from types import SimpleNamespace

from worker.actions import ActionRegistry
from worker.task import Action, ActionStatus


class RecordingPlatform:
    """记录 press 系列调用的最小平台桩。"""

    def __init__(self):
        self.sequence_calls: list[list[str]] = []
        self.duration_calls: list[tuple[str, int]] = []
        self.press_calls: list[str] = []

    def press(self, key, context=None):
        self.press_calls.append(key)

    def press_for_duration(self, key, duration_ms, context=None):
        self.duration_calls.append((key, duration_ms))

    def press_sequence(self, keys, interval_ms=100, context=None):
        self.sequence_calls.append(list(keys))


def _execute(platform, payload: dict):
    action = Action.from_dict(payload)
    return ActionRegistry.get("press").execute(platform, action)


def test_press_action_is_registered() -> None:
    executor = ActionRegistry.get("press")

    assert executor is not None
    assert executor.name == "press"


def test_press_sequence_routes_to_press_sequence() -> None:
    """["space","space"] 列表走平台 press_sequence（鸿蒙批量注入）。"""
    platform = RecordingPlatform()

    result = _execute(platform, {"action_type": "press", "value": ["space", "space"]})

    assert result.status == ActionStatus.SUCCESS
    assert platform.sequence_calls == [["space", "space"]]
    assert platform.press_calls == []
    assert platform.duration_calls == []


def test_press_single_key_routes_to_press() -> None:
    platform = RecordingPlatform()

    result = _execute(platform, {"action_type": "press", "value": "enter"})

    assert result.status == ActionStatus.SUCCESS
    assert platform.press_calls == ["enter"]


def test_press_long_press_routes_to_press_for_duration() -> None:
    platform = RecordingPlatform()

    result = _execute(
        platform,
        {"action_type": "press", "value": "home", "click_duration": 1000},
    )

    assert result.status == ActionStatus.SUCCESS
    assert platform.duration_calls == [("home", 1000)]


def test_press_combo_ignores_click_duration() -> None:
    """组合键不执行长按（既有语义），按普通组合键操作。"""
    platform = RecordingPlatform()

    result = _execute(
        platform,
        {"action_type": "press", "value": "ctrl+v", "click_duration": 2000},
    )

    assert result.status == ActionStatus.SUCCESS
    assert platform.press_calls == ["ctrl+v"]
    assert platform.duration_calls == []


def test_press_sequence_ignores_click_duration() -> None:
    """按键列表忽略 click_duration（既有语义），列表整体走序列通道。"""
    platform = RecordingPlatform()

    result = _execute(
        platform,
        {"action_type": "press", "value": ["space", "space"], "click_duration": 2000},
    )

    assert result.status == ActionStatus.SUCCESS
    assert platform.sequence_calls == [["space", "space"]]
    assert platform.duration_calls == []


def test_press_rejects_empty_and_invalid_value() -> None:
    platform = RecordingPlatform()

    result = _execute(platform, {"action_type": "press"})
    assert result.status == ActionStatus.FAILED

    result = _execute(platform, {"action_type": "press", "value": ["", "  "]})
    assert result.status == ActionStatus.FAILED

    result = _execute(platform, {"action_type": "press", "value": 123})
    assert result.status == ActionStatus.FAILED


def test_base_press_sequence_defaults_to_per_key_press() -> None:
    """默认平台（未覆盖 press_sequence）逐键调用 press，行为不变。"""
    from worker.platforms.base import PlatformManager

    calls: list[str] = []
    fake_self = SimpleNamespace(press=lambda key, context=None: calls.append(key))
    # 直接调用基类默认实现，绕开抽象方法集合
    PlatformManager.press_sequence(fake_self, ["a", "b"], interval_ms=50)

    assert calls == ["a", "b"]


def test_is_long_press_action_matches_press_semantics() -> None:
    """background 异步仅在长按时有意义；press 组合键/列表不算长按。"""
    from worker.worker import _is_long_press_action

    # 长按类：单键 + click_duration
    assert _is_long_press_action(Action.from_dict({"action_type": "press", "value": "space", "click_duration": 2000}))
    # 点击类长按：click_duration>0 即视为长按
    assert _is_long_press_action(Action.from_dict({"action_type": "click", "x": 1, "y": 2, "click_duration": 2000}))
    # 普通点击/按键：无 click_duration，异步无意义
    assert not _is_long_press_action(Action.from_dict({"action_type": "click", "x": 1, "y": 2}))
    assert not _is_long_press_action(Action.from_dict({"action_type": "press", "value": "enter"}))
    # press 组合键与按键列表：忽略 click_duration，视为非长按
    assert not _is_long_press_action(
        Action.from_dict({"action_type": "press", "value": "ctrl+v", "click_duration": 2000})
    )
    assert not _is_long_press_action(
        Action.from_dict({"action_type": "press", "value": ["space", "space"], "click_duration": 2000})
    )

"""scroll 动作测试：参数解析、能力声明与 Windows/鸿蒙 PC/Web 注入路径。"""

from types import SimpleNamespace

import pytest

from worker.actions import ActionRegistry
from worker.config import PlatformConfig
from worker.platforms.harmony import HarmonyPlatformManager
from worker.platforms.harmony_hdc import HarmonyError
from worker.platforms.web import WebPlatformManager
from worker.platforms.windows import WindowsPlatformManager
from worker.task import Action, ActionStatus


class RecordingPlatform:
    """记录 scroll 调用参数的最小平台桩。"""

    def __init__(self):
        self.calls = []

    def scroll(self, x, y, direction="down", amount=3, context=None):
        self.calls.append((x, y, direction, amount))


def _execute(platform, payload: dict):
    action = Action.from_dict(payload)
    return ActionRegistry.get("scroll").execute(platform, action)


def test_scroll_action_is_registered() -> None:
    executor = ActionRegistry.get("scroll")

    assert executor is not None
    assert executor.name == "scroll"


def test_scroll_defaults_to_down_three_notches() -> None:
    platform = RecordingPlatform()

    result = _execute(platform, {"action_type": "scroll", "x": 100, "y": 200})

    assert result.status == ActionStatus.SUCCESS
    assert platform.calls == [(100, 200, "down", 3)]


def test_scroll_passes_positive_value_as_down() -> None:
    platform = RecordingPlatform()

    result = _execute(platform, {"action_type": "scroll", "x": 5, "y": 6, "value": 5})

    assert result.status == ActionStatus.SUCCESS
    assert platform.calls == [(5, 6, "down", 5)]


def test_scroll_negative_value_scrolls_up() -> None:
    platform = RecordingPlatform()

    result = _execute(platform, {"action_type": "scroll", "x": 5, "y": 6, "value": -5})

    assert result.status == ActionStatus.SUCCESS
    assert platform.calls == [(5, 6, "up", 5)]


def test_scroll_accepts_numeric_string_value() -> None:
    platform = RecordingPlatform()

    result = _execute(platform, {"action_type": "scroll", "x": 1, "y": 2, "value": "-4"})

    assert result.status == ActionStatus.SUCCESS
    assert platform.calls == [(1, 2, "up", 4)]


def test_scroll_clamps_value_below_one() -> None:
    platform = RecordingPlatform()

    result = _execute(platform, {"action_type": "scroll", "x": 1, "y": 2, "value": 0})

    assert result.status == ActionStatus.SUCCESS
    assert platform.calls == [(1, 2, "down", 1)]


def test_scroll_requires_coordinates() -> None:
    platform = RecordingPlatform()

    result = _execute(platform, {"action_type": "scroll", "y": 2})

    assert result.status == ActionStatus.FAILED
    assert platform.calls == []


def test_scroll_uses_value_not_amount_field() -> None:
    # 齿格数复用 value，不新增独立字段；amount 不是 Action 的合法参数
    assert not hasattr(Action.from_dict({"action_type": "scroll"}), "amount")


def test_harmony_declares_scroll_pc_only() -> None:
    mobile = HarmonyPlatformManager(PlatformConfig(), device_type="harmony_mobile")
    pc = HarmonyPlatformManager(PlatformConfig(), device_type="harmony_pc")

    assert pc.is_action_supported("scroll")
    assert not mobile.is_action_supported("scroll")


def test_harmony_pc_scroll_injects_uinput_per_notch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("worker.platforms.harmony.time.sleep", lambda _: None)
    manager = HarmonyPlatformManager(PlatformConfig(), device_type="harmony_pc")
    calls = []
    client = SimpleNamespace(
        wheel=lambda direction, x, y: calls.append((direction, x, y)) or True,
    )

    result = manager.execute_action(
        client,
        Action.from_dict({"action_type": "scroll", "x": 100, "y": 200, "value": -2}),
    )

    assert result.status == ActionStatus.SUCCESS
    assert calls == [("up", 100, 200), ("up", 100, 200)]


def test_harmony_pc_scroll_failure_raises_like_other_gestures() -> None:
    # 与 swipe/drag 契约一致：平台层注入失败抛 HarmonyError，由任务循环转任务失败
    manager = HarmonyPlatformManager(PlatformConfig(), device_type="harmony_pc")
    client = SimpleNamespace(wheel=lambda *args: False)

    with pytest.raises(HarmonyError):
        manager.execute_action(
            client,
            Action.from_dict({"action_type": "scroll", "x": 100, "y": 200}),
        )


def test_harmony_mobile_scroll_rejected() -> None:
    manager = HarmonyPlatformManager(PlatformConfig(), device_type="harmony_mobile")
    client = SimpleNamespace(wheel=lambda *args: True)

    with pytest.raises(HarmonyError, match="移动端不支持"):
        manager.execute_action(
            client,
            Action.from_dict({"action_type": "scroll", "x": 100, "y": 200}),
        )


def test_windows_platform_declares_scroll() -> None:
    platform = WindowsPlatformManager(PlatformConfig())

    assert platform.is_action_supported("scroll")


def test_web_platform_declares_scroll() -> None:
    platform = WebPlatformManager(PlatformConfig())

    assert platform.is_action_supported("scroll")


def test_web_scroll_browser_level_uses_playwright_wheel() -> None:
    platform = WebPlatformManager(PlatformConfig())
    moves, wheels = [], []

    async def _noop() -> None:
        return None

    def move(x, y):
        moves.append((x, y))
        return _noop()

    def wheel(delta_x, delta_y):
        wheels.append((delta_x, delta_y))
        return _noop()

    platform._current_page = SimpleNamespace(mouse=SimpleNamespace(move=move, wheel=wheel))

    result = _execute(platform, {"action_type": "scroll", "x": 10, "y": 20, "value": 2})

    assert result.status == ActionStatus.SUCCESS
    assert moves == [(10, 20)]
    # 每齿约 120px，2 齿向下
    assert wheels == [(0, 240)]


def test_web_scroll_browser_level_negative_value_is_up() -> None:
    platform = WebPlatformManager(PlatformConfig())
    wheels = []

    async def _noop() -> None:
        return None

    def move(x, y):
        return _noop()

    def wheel(delta_x, delta_y):
        wheels.append((delta_x, delta_y))
        return _noop()

    platform._current_page = SimpleNamespace(mouse=SimpleNamespace(move=move, wheel=wheel))

    result = _execute(platform, {"action_type": "scroll", "x": 10, "y": 20, "value": -1})

    assert result.status == ActionStatus.SUCCESS
    assert wheels == [(0, -120)]

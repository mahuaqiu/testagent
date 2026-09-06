"""实时指针注入器单元测试：move 合并、down/up 保序、安全抬起、重复 down 保护、鸿蒙滚轮成对发送。"""

from __future__ import annotations

import time

from worker.screen.pointer_injector import (
    HarmonyPointerDispatcher,
    PointerInjector,
    WindowsPointerDispatcher,
)


def test_move_coalesced_while_down_up_preserved_in_order() -> None:
    received: list[dict] = []
    injector = PointerInjector(
        lambda message: (time.sleep(0.005), received.append(dict(message))) and None,
        label="t1",
    )
    injector.start()
    injector.submit({"action": "down", "button": "left", "x": 0, "y": 0})
    for index in range(1, 21):
        injector.submit({"action": "move", "button": "left", "x": index, "y": index})
        time.sleep(0.0005)  # 提交快于注入：触发抵尾合并
    injector.submit({"action": "up", "button": "left", "x": 20, "y": 20})
    injector.close(timeout=5)

    moves = [m for m in received if m["action"] == "move"]
    # move 被合并为少数几批，但最后位置必须是最新坐标
    assert 1 <= len(moves) <= 5 and moves[-1]["x"] == 20
    assert [m["action"] for m in received] == ["down"] + ["move"] * len(moves) + ["up"]


def test_close_releases_pressed_button_at_last_coordinate() -> None:
    received: list[dict] = []
    injector = PointerInjector(received.append, label="t2")
    injector.start()
    injector.submit({"action": "down", "button": "left", "x": 5, "y": 6})
    injector.submit({"action": "move", "button": "left", "x": 7, "y": 8})
    time.sleep(0.1)
    injector.close(timeout=3)

    assert received[-1]["action"] == "up"
    assert received[-1]["x"] == 7 and received[-1]["y"] == 8


def test_duplicate_down_emits_protective_up_first() -> None:
    received: list[dict] = []
    injector = PointerInjector(received.append, label="t3")
    injector.start()
    injector.submit({"action": "down", "button": "left", "x": 1, "y": 1})
    injector.submit({"action": "down", "button": "right", "x": 2, "y": 2})
    injector.submit({"action": "up", "button": "right", "x": 2, "y": 2})
    injector.close(timeout=3)

    assert [(m["action"], m.get("button")) for m in received] == [
        ("down", "left"),
        ("up", "left"),
        ("down", "right"),
        ("up", "right"),
    ]


def test_invalid_action_dropped_and_overflow_never_drops_down_up() -> None:
    received: list[dict] = []

    def slow_collect(message: dict) -> None:
        time.sleep(0.01)  # 注入慢于提交：制造队列积压触发溢出保护
        received.append(dict(message))

    injector = PointerInjector(slow_collect, label="t4")
    injector.start()
    injector.submit({"action": "bogus"})
    injector.submit({"action": "down", "button": "left", "x": 0, "y": 0})
    for index in range(200):
        injector.submit({"action": "move", "x": index, "y": index})
    injector.submit({"action": "up", "x": 199, "y": 199})
    injector.close(timeout=10)

    actions = [m["action"] for m in received]
    assert actions[0] == "down" and actions[-1] == "up"
    assert "bogus" not in actions


class _FakeHdcManager:
    """鸿蒙管理器桩：记录 wheel 调用（uinput 路径）。"""

    def __init__(self, session=None) -> None:
        self._session = session
        self.wheel_calls: list[tuple[str, str, int, int]] = []

    def peek_official_session(self, device_id: str):
        return self._session

    def wheel(self, udid: str, direction: str, x: int, y: int) -> None:
        self.wheel_calls.append((udid, direction, x, y))


class _RecordingWindowsManager:
    """Windows 管理器桩：记录 scroll 调用。"""

    def __init__(self) -> None:
        self.scroll_calls: list[tuple[int, int, str, int]] = []

    def scroll(self, x: int, y: int, direction: str = "up", amount: int = 3, monitor: int | None = None) -> None:
        self.scroll_calls.append((x, y, direction, amount))


def test_harmony_wheel_uses_uinput_and_ignores_stop() -> None:
    """滚轮走 manager.wheel（uinput），独立 STOP 丢弃；无需官方会话就绪。"""
    manager = _FakeHdcManager()
    dispatcher = HarmonyPointerDispatcher(
        manager=manager,
        device_id="dev",
        device_type="harmony_pc",
    )

    dispatcher({"action": "wheel", "direction": "down", "x": 100, "y": 200})
    dispatcher({"action": "wheel", "direction": "up", "x": 100, "y": 200})
    dispatcher({"action": "wheel", "direction": "stop", "x": 100, "y": 200})

    assert manager.wheel_calls == [
        ("dev", "down", 100, 200),
        ("dev", "up", 100, 200),
    ]


def test_windows_wheel_ignores_stop_and_keeps_direction() -> None:
    """Windows 分发器忽略 stop（曾误注入 3 齿格下滚），up/down 透传。"""
    manager = _RecordingWindowsManager()
    dispatcher = WindowsPointerDispatcher(manager, monitor=1)

    dispatcher({"action": "wheel", "direction": "up", "amount": 2, "x": 10, "y": 20})
    dispatcher({"action": "wheel", "direction": "stop", "x": 10, "y": 20})
    dispatcher({"action": "wheel", "direction": "down", "x": 30, "y": 40})

    assert manager.scroll_calls == [(10, 20, "up", 2), (30, 40, "down", 3)]

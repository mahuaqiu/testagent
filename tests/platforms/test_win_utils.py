"""win_utils 窗口查找单元测试。"""

from contextlib import contextmanager
from unittest.mock import patch

from worker.platforms.win_utils import find_window_handle, find_window_handles

# 模拟按 Z 序枚举到的顶层窗口（字典顺序即 Z 序，Z 序最前在前）
FAKE_WINDOWS = {
    0x11: {"visible": True, "class": "#32770", "title": "声音"},
    0x22: {"visible": True, "class": "#32770", "title": "声音 - 副本"},
    0x33: {"visible": False, "class": "#32770", "title": "声音(隐藏)"},
    0x44: {"visible": True, "class": "Notepad", "title": "无题 - 记事本"},
}


def _fake_enum_windows(callback, _):
    for hwnd in FAKE_WINDOWS:
        if callback(hwnd, None) is False:
            break


@contextmanager
def fake_windows():
    with (
        patch("win32gui.EnumWindows", _fake_enum_windows),
        patch("win32gui.IsWindowVisible", side_effect=lambda h: FAKE_WINDOWS[h]["visible"]),
        patch("win32gui.GetClassName", side_effect=lambda h: FAKE_WINDOWS[h]["class"]),
        patch("win32gui.GetWindowText", side_effect=lambda h: FAKE_WINDOWS[h]["title"]),
    ):
        yield


def test_find_window_handles_returns_all_visible_matches_in_zorder() -> None:
    """应返回全部匹配的可见窗口并保持 Z 序，隐藏窗口被跳过。"""
    with fake_windows():
        assert find_window_handles(title="声音") == [0x11, 0x22]


def test_find_window_handles_class_exact_match() -> None:
    with fake_windows():
        assert find_window_handles(class_name="Notepad") == [0x44]


def test_find_window_handles_combined_filters() -> None:
    with fake_windows():
        assert find_window_handles(title="声音", class_name="Notepad") == []


def test_find_window_handle_returns_first_match() -> None:
    """单数版保持原行为：只返回 Z 序最前的第一个匹配窗口。"""
    with fake_windows():
        assert find_window_handle(title="声音", retry=False) == 0x11


def test_find_window_handles_requires_filters() -> None:
    """不传任何过滤条件时应返回空列表，而不是全量窗口。"""
    with fake_windows():
        assert find_window_handles() == []

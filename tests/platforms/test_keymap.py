"""统一按键名解析与映射测试。"""

import pytest

from worker.platforms import keymap


def test_parse_strips_and_lowercases_named_keys() -> None:
    assert keymap.parse_key_combo(" ctrl + a ") == ["ctrl", "a"]


def test_parse_single_char_keeps_case() -> None:
    assert keymap.parse_key_combo("A") == ["A"]
    assert keymap.parse_key_combo("ctrl+A") == ["ctrl", "A"]


def test_parse_empty_raises() -> None:
    with pytest.raises(ValueError, match="Key is required"):
        keymap.parse_key_combo("   ")
    with pytest.raises(ValueError, match="空段"):
        keymap.parse_key_combo("ctrl+")


def test_validate_combo_rejects_function_key_first() -> None:
    with pytest.raises(ValueError, match="不是修饰键"):
        keymap.validate_combo(["a", "ctrl"])


def test_validate_combo_rejects_modifier_last() -> None:
    with pytest.raises(ValueError, match="缺少功能键"):
        keymap.validate_combo(["ctrl", "shift"])


def test_playwright_combo_win() -> None:
    assert keymap.to_playwright_combo(["ctrl", "a"], on_mac=False) == "Control+a"
    assert keymap.to_playwright_combo(["esc"], on_mac=False) == "Escape"
    assert keymap.to_playwright_combo(["up"], on_mac=False) == "ArrowUp"
    assert keymap.to_playwright_combo(["win", "e"], on_mac=False) == "Meta+e"
    assert keymap.to_playwright_combo(["alt", "f4"], on_mac=False) == "Alt+F4"
    assert keymap.to_playwright_combo(["delete"], on_mac=False) == "Delete"


def test_playwright_combo_mac_ctrl_is_meta() -> None:
    assert keymap.to_playwright_combo(["ctrl", "c"], on_mac=True) == "Meta+c"


def test_playwright_combo_unsupported_key_hints_system_level() -> None:
    with pytest.raises(ValueError, match="level=system"):
        keymap.to_playwright_combo(["volumeup"], on_mac=False)


def test_win_mapping() -> None:
    assert keymap.to_pyautogui_win("win") == "winleft"
    assert keymap.to_pyautogui_win("meta") == "winleft"
    assert keymap.to_pyautogui_win("ctrl") == "ctrl"
    assert keymap.to_pyautogui_win("enter") == "enter"
    assert keymap.to_pyautogui_win("A") == "A"


def test_win_command_is_mac_semantic() -> None:
    with pytest.raises(ValueError, match="ctrl 或 win"):
        keymap.to_pyautogui_win("command")


def test_win_unknown_key_errors(monkeypatch) -> None:
    monkeypatch.setattr(keymap, "_pyautogui_keys", lambda: {"enter", "esc"})
    with pytest.raises(ValueError, match="未知按键名"):
        keymap.to_pyautogui_win("control")


def test_mac_ctrl_maps_to_command() -> None:
    assert keymap.to_pyautogui_mac("ctrl") == "command"
    assert keymap.to_pyautogui_mac("win") == "command"
    assert keymap.to_pyautogui_mac("command") == "command"


def test_mac_unknown_key_errors(monkeypatch) -> None:
    monkeypatch.setattr(keymap, "_pyautogui_keys", lambda: {"enter", "command"})
    with pytest.raises(ValueError, match="未知按键名"):
        keymap.to_pyautogui_mac("arrowup")

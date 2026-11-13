"""Web 平台按键（press）统一格式测试。"""

import sys
from unittest.mock import MagicMock, patch

import pytest

from worker.config import PlatformConfig
from worker.platforms.web import WebPlatformManager


def test_browser_level_maps_ctrl_a_to_playwright() -> None:
    manager = WebPlatformManager(PlatformConfig())
    page = MagicMock()
    with patch("worker.platforms.web._run_async"):
        manager.press("ctrl+a", context=page)
    page.keyboard.press.assert_called_once_with("Control+a")


def test_browser_level_maps_esc_and_arrows() -> None:
    manager = WebPlatformManager(PlatformConfig())
    for key, expected in [("esc", "Escape"), ("up", "ArrowUp"), ("enter", "Enter")]:
        page = MagicMock()
        with patch("worker.platforms.web._run_async"):
            manager.press(key, context=page)
        page.keyboard.press.assert_called_once_with(expected)


def test_browser_level_darwin_ctrl_maps_meta() -> None:
    manager = WebPlatformManager(PlatformConfig())
    page = MagicMock()
    with patch("worker.platforms.web._run_async"), patch.object(sys, "platform", "darwin"):
        manager.press("ctrl+a", context=page)
    page.keyboard.press.assert_called_once_with("Meta+a")


def test_system_level_combo_uses_hotkey() -> None:
    manager = WebPlatformManager(PlatformConfig())
    with (
        patch("worker.platforms.web.SYSTEM_LEVEL_AVAILABLE", True),
        patch("worker.platforms.web.pyautogui") as pag,
    ):
        manager._system_press("ctrl+alt+t", ["ctrl", "alt", "t"])
    pag.hotkey.assert_called_once_with("ctrl", "alt", "t")


def test_system_level_single_key_uses_press() -> None:
    manager = WebPlatformManager(PlatformConfig())
    with (
        patch("worker.platforms.web.SYSTEM_LEVEL_AVAILABLE", True),
        patch("worker.platforms.web.pyautogui") as pag,
    ):
        manager._system_press("esc", ["esc"])
    pag.press.assert_called_once_with("esc")


def test_system_level_darwin_maps_ctrl_to_command() -> None:
    manager = WebPlatformManager(PlatformConfig())
    with (
        patch("worker.platforms.web.SYSTEM_LEVEL_AVAILABLE", True),
        patch("worker.platforms.web.pyautogui") as pag,
        patch.object(sys, "platform", "darwin"),
    ):
        manager._system_press("ctrl+v", ["ctrl", "v"])
    pag.hotkey.assert_called_once_with("command", "v")


def test_system_level_darwin_win_maps_to_command(monkeypatch) -> None:
    manager = WebPlatformManager(PlatformConfig())
    monkeypatch.setattr("worker.platforms.keymap._pyautogui_keys", lambda: {"enter", "command"})
    with (
        patch("worker.platforms.web.SYSTEM_LEVEL_AVAILABLE", True),
        patch("worker.platforms.web.pyautogui") as pag,
        patch.object(sys, "platform", "darwin"),
    ):
        manager._system_press("win+e", ["win", "e"])
    pag.hotkey.assert_called_once_with("command", "e")


def test_legacy_playwright_style_rejected() -> None:
    manager = WebPlatformManager(PlatformConfig())
    with pytest.raises(ValueError, match="不是修饰键"):
        manager.press("Control+A", context=MagicMock())


def test_browser_level_system_only_key_rejected() -> None:
    manager = WebPlatformManager(PlatformConfig())
    with pytest.raises(ValueError, match="level=system"):
        manager.press("volumeup", context=MagicMock())


def test_system_level_unknown_key_rejected() -> None:
    manager = WebPlatformManager(PlatformConfig())
    with (
        patch("worker.platforms.web.SYSTEM_LEVEL_AVAILABLE", True),
        patch("worker.platforms.web.pyautogui"),
        patch.object(sys, "platform", "win32"),
    ):
        with pytest.raises(ValueError, match="未知按键名"):
            manager._system_press("Control+A", ["control", "a"])

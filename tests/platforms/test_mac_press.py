"""Mac 平台按键统一格式测试（ctrl 自动映射 command）。"""

from unittest.mock import patch

import pytest

from worker.config import PlatformConfig
from worker.platforms.mac import MacPlatformManager


def test_ctrl_c_maps_to_command_c() -> None:
    manager = MacPlatformManager(PlatformConfig())
    with patch("worker.platforms.mac.pyautogui") as pag:
        manager.press("ctrl+c")
    pag.hotkey.assert_called_once_with("command", "c")


def test_single_key_press(monkeypatch) -> None:
    manager = MacPlatformManager(PlatformConfig())
    monkeypatch.setattr("worker.platforms.keymap._pyautogui_keys", lambda: {"enter"})
    with patch("worker.platforms.mac.pyautogui") as pag:
        manager.press("enter")
    pag.press.assert_called_once_with("enter")


def test_legacy_style_rejected() -> None:
    manager = MacPlatformManager(PlatformConfig())
    with pytest.raises(ValueError, match="不是修饰键"):
        manager.press("Control+C")


def test_unknown_key_rejected(monkeypatch) -> None:
    manager = MacPlatformManager(PlatformConfig())
    monkeypatch.setattr("worker.platforms.keymap._pyautogui_keys", lambda: {"enter"})
    with pytest.raises(ValueError, match="未知按键名"):
        manager.press("arrowup")

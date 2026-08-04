"""Windows 桌面按键动作测试。"""

import sys
from unittest.mock import Mock, patch

from worker.config import PlatformConfig
from worker.platforms.windows import WindowsPlatformManager


def test_ctrl_alt_delete_uses_windows_send_sas() -> None:
    manager = WindowsPlatformManager(PlatformConfig())
    sas = Mock()

    with (
        patch.object(sys, "platform", "win32"),
        patch("worker.platforms.windows.ctypes.WinDLL", return_value=sas, create=True) as load,
    ):
        manager.press("ctrl+alt+delete")

    load.assert_called_once_with("sas.dll")
    sas.SendSAS.assert_called_once_with(True)


def test_windows_key_alias_uses_pyautogui_winleft() -> None:
    manager = WindowsPlatformManager(PlatformConfig())

    with patch("worker.platforms.windows.pyautogui.hotkey") as hotkey:
        manager.press("win+e")

    hotkey.assert_called_once_with("winleft", "e")

"""Android 按键统一格式测试。"""

from unittest.mock import MagicMock

import pytest

from worker.config import PlatformConfig
from worker.platforms.android import AndroidPlatformManager


def _device_with_manager():
    manager = AndroidPlatformManager(PlatformConfig())
    device = MagicMock()
    return manager, device


def test_esc_alias_maps_to_keycode_111() -> None:
    manager, device = _device_with_manager()
    manager.press("esc", device)
    device.press.assert_called_once_with(111)


def test_arrow_aliases() -> None:
    manager, device = _device_with_manager()
    for key, code in [("up", 19), ("down", 20), ("left", 21), ("right", 22)]:
        manager.press(key, device)
        device.press.assert_called_with(code)


def test_common_keys_still_work() -> None:
    manager, device = _device_with_manager()
    manager.press("enter", device)
    device.press.assert_called_once_with(66)


def test_combo_rejected() -> None:
    manager, device = _device_with_manager()
    with pytest.raises(ValueError, match="不支持组合键"):
        manager.press("ctrl+a", device)


def test_unknown_key_reports_supported_list() -> None:
    manager, device = _device_with_manager()
    with pytest.raises(ValueError, match="Supported keys"):
        manager.press("f5", device)

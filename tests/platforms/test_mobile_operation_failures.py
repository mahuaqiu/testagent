"""移动平台基础操作失败传播测试。"""

from types import SimpleNamespace

import pytest

from worker.config import PlatformConfig
from worker.platforms.android import AndroidPlatformManager
from worker.platforms.ios import iOSPlatformManager


@pytest.mark.parametrize(
    "operation",
    [
        lambda manager: manager.click(1, 2),
        lambda manager: manager.double_click(1, 2),
        lambda manager: manager.input_text("text"),
        lambda manager: manager.swipe(1, 2, 3, 4),
        lambda manager: manager.press("HOME"),
    ],
)
def test_android_operations_require_device_context(operation) -> None:
    manager = AndroidPlatformManager(PlatformConfig())

    with pytest.raises(RuntimeError, match="No device context"):
        operation(manager)


@pytest.mark.parametrize(
    "operation",
    [
        lambda manager: manager.click(1, 2),
        lambda manager: manager.double_click(1, 2),
        lambda manager: manager.input_text("text"),
        lambda manager: manager.swipe(1, 2, 3, 4),
        lambda manager: manager.press("HOME"),
    ],
)
def test_ios_operations_require_device_context(operation) -> None:
    manager = iOSPlatformManager(PlatformConfig())

    with pytest.raises(RuntimeError, match="No device context"):
        operation(manager)


def test_ios_click_propagates_wda_failure() -> None:
    manager = iOSPlatformManager(PlatformConfig())
    manager._convert_coords = lambda x, y: (x, y)
    client = SimpleNamespace(tap=lambda x, y: False)

    with pytest.raises(RuntimeError, match="Click failed"):
        manager.click(1, 2, context=client)


def test_ios_press_propagates_wda_failure() -> None:
    manager = iOSPlatformManager(PlatformConfig())
    client = SimpleNamespace(press_button=lambda key: False)

    with pytest.raises(RuntimeError, match="Press button failed"):
        manager.press("HOME", context=client)

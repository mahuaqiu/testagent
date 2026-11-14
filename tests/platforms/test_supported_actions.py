"""平台 Action 能力声明契约测试。"""

from worker.config import PlatformConfig
from worker.platforms.android import AndroidPlatformManager
from worker.platforms.base import PlatformManager
from worker.platforms.harmony import HarmonyPlatformManager
from worker.platforms.ios import iOSPlatformManager
from worker.platforms.mac import MacPlatformManager
from worker.platforms.web import WebPlatformManager
from worker.platforms.windows import WindowsPlatformManager


def test_base_actions_do_not_claim_optional_platform_capabilities() -> None:
    optional_actions = {
        "right_click", "move", "paste", "cmd_exec", "pinch",
        "start_recording", "stop_recording", "activate_window", "close_window",
    }

    assert PlatformManager.BASE_SUPPORTED_ACTIONS.isdisjoint(optional_actions)


def test_mobile_platforms_only_add_real_mobile_capabilities() -> None:
    android = AndroidPlatformManager(PlatformConfig())
    ios = iOSPlatformManager(PlatformConfig())

    for manager in (android, ios):
        assert manager.is_action_supported("pinch")
        assert not manager.is_action_supported("right_click")
        assert not manager.is_action_supported("move")
        assert not manager.is_action_supported("cmd_exec")


def test_harmony_keeps_shape_specific_capabilities() -> None:
    mobile = HarmonyPlatformManager(PlatformConfig(), device_type="harmony_mobile")
    pc = HarmonyPlatformManager(PlatformConfig(), device_type="harmony_pc")

    assert mobile.is_action_supported("unlock_screen")
    assert pc.is_action_supported("unlock_screen")
    assert pc.is_action_supported("move")
    assert pc.is_action_supported("activate_window")
    assert mobile.is_action_supported("move")
    assert not mobile.is_action_supported("activate_window")
    assert not mobile.is_action_supported("cmd_exec")


def test_desktop_platforms_support_activate_window() -> None:
    """activate_window 契约：Windows/Web/Mac 支持，移动端不支持（api.yaml 平台支持表）。

    Web 平台运行在 Windows 宿主上，执行器内部复用 Windows 激活逻辑
    （worker/actions/window.py 中 web 与 windows 同走 _activate_windows）；
    Mac 通过应用名（class 模式）激活。
    """
    assert WindowsPlatformManager(PlatformConfig()).is_action_supported("activate_window")
    assert WebPlatformManager(PlatformConfig()).is_action_supported("activate_window")
    assert MacPlatformManager(PlatformConfig()).is_action_supported("activate_window")
    assert not AndroidPlatformManager(PlatformConfig()).is_action_supported("activate_window")
    assert not iOSPlatformManager(PlatformConfig()).is_action_supported("activate_window")

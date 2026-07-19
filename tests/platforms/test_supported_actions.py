"""平台 Action 能力声明契约测试。"""

from worker.config import PlatformConfig
from worker.platforms.android import AndroidPlatformManager
from worker.platforms.base import PlatformManager
from worker.platforms.harmony import HarmonyPlatformManager
from worker.platforms.ios import iOSPlatformManager


def test_base_actions_do_not_claim_optional_platform_capabilities() -> None:
    optional_actions = {
        "right_click", "move", "paste", "cmd_exec", "pinch",
        "start_recording", "stop_recording", "activate_window",
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
    assert not pc.is_action_supported("unlock_screen")
    assert not mobile.is_action_supported("cmd_exec")

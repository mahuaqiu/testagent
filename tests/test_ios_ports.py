"""iOS WDA 端口分配测试。"""

from worker.platforms.ios import iOSPlatformManager


def test_get_wda_port_uses_device_assignment_and_configured_fallback():
    manager = iOSPlatformManager.__new__(iOSPlatformManager)
    manager._device_wda = {"device-1": {"port": 8201}}
    manager.wda_base_port = 8200

    assert manager.get_wda_port("device-1") == 8201
    assert manager.get_wda_port("unregistered-device") == 8200

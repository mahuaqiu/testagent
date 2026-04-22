"""iOS pinch 实现测试。"""

import pytest
from unittest.mock import MagicMock

from worker.platforms.ios import iOSPlatformManager


class TestiOSPinch:
    """测试 iOS pinch 实现。"""

    def test_pinch_calls_wda(self):
        """pinch 调用 WDA。"""
        from worker.config import PlatformConfig
        config = PlatformConfig()
        manager = iOSPlatformManager(config)

        mock_client = MagicMock()
        manager._device_clients["test_device"] = mock_client
        manager._current_device = "test_device"

        manager.pinch("in", scale=0.5, duration=500)

        mock_client.pinch.assert_called()

    def test_pinch_raises_when_no_device(self):
        """无设备时抛出异常。"""
        from worker.config import PlatformConfig
        config = PlatformConfig()
        manager = iOSPlatformManager(config)
        manager._current_device = None

        with pytest.raises(RuntimeError, match="No device context"):
            manager.pinch("in")

    def test_pinch_is_in_supported_actions(self):
        """pinch 在 SUPPORTED_ACTIONS 中。"""
        from worker.platforms.ios import iOSPlatformManager
        assert "pinch" in iOSPlatformManager.SUPPORTED_ACTIONS
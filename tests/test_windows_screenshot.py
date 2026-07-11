"""Windows 截图 fallback 回归测试。"""

from unittest.mock import Mock, patch

from PIL import Image

from worker.config import PlatformConfig
from worker.platforms.windows import WindowsPlatformManager


def _make_manager() -> WindowsPlatformManager:
    manager = WindowsPlatformManager(PlatformConfig())
    manager._current_device = "test-device"
    return manager


def test_empty_sidecar_jpeg_falls_back_to_selected_monitor():
    manager = _make_manager()
    manager._current_monitor = 2
    sidecar = Mock()
    sidecar.get_frame_jpeg.return_value = b""

    fallback_image = Image.new("RGB", (100, 100), "red")
    with (
        patch("worker.screen.windows_sidecar.get_windows_sidecar_manager", return_value=sidecar),
        patch(
            "worker.screen.monitor_utils.get_mapped_monitor_index",
            return_value=(2, {"left": 100, "top": 200, "width": 100, "height": 100}),
        ),
        patch("PIL.ImageGrab.grab", return_value=fallback_image) as grab,
    ):
        screenshot = manager.take_screenshot()

    assert screenshot
    grab.assert_called_once_with(bbox=(100, 200, 200, 300), all_screens=True)


def test_window_fallback_keeps_window_binding_and_uses_window_region():
    manager = _make_manager()
    manager._current_monitor = 2
    manager._window_handle = 123
    manager._window_rect = (120, 230, 320, 430)
    sidecar = Mock()
    sidecar.get_frame_raw_with_meta.side_effect = RuntimeError("sidecar unavailable")

    fallback_image = Image.new("RGB", (200, 200), "blue")
    with (
        patch("worker.screen.windows_sidecar.get_windows_sidecar_manager", return_value=sidecar),
        patch("PIL.ImageGrab.grab", return_value=fallback_image) as grab,
    ):
        screenshot = manager.take_screenshot()

    assert screenshot
    assert manager._window_handle == 123
    assert manager._window_rect == (120, 230, 320, 430)
    grab.assert_called_once_with(bbox=(120, 230, 320, 430), all_screens=True)


def test_valid_sidecar_screenshot_does_not_use_desktop_fallback():
    manager = _make_manager()
    sidecar = Mock()
    sidecar.get_frame_jpeg.return_value = b"valid-jpeg"

    with (
        patch("worker.screen.windows_sidecar.get_windows_sidecar_manager", return_value=sidecar),
        patch("PIL.ImageGrab.grab") as grab,
    ):
        screenshot = manager.take_screenshot()

    assert screenshot == b"valid-jpeg"
    grab.assert_not_called()

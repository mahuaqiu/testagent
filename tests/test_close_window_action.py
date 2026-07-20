"""close_window action 单元测试。"""

from unittest.mock import MagicMock, patch

from worker.actions.window import CloseWindowAction, _resolve_window_filters
from worker.task import Action, ActionStatus


def test_resolve_filters_title_only() -> None:
    action = Action(action_type="close_window", value="声音")
    title, class_name, exe_name, error = _resolve_window_filters(action)
    assert error is None
    assert title == "声音"
    assert class_name is None
    assert exe_name is None


def test_resolve_filters_class_only() -> None:
    action = Action(action_type="close_window", value="#32770", match_by="class")
    title, class_name, exe_name, error = _resolve_window_filters(action)
    assert error is None
    assert title is None
    assert class_name == "#32770"


def test_resolve_filters_title_and_window_class() -> None:
    action = Action(
        action_type="close_window",
        value="声音",
        window_class="#32770",
        name="rundll32.exe",
    )
    title, class_name, exe_name, error = _resolve_window_filters(action)
    assert error is None
    assert title == "声音"
    assert class_name == "#32770"
    assert exe_name == "rundll32.exe"


def test_resolve_filters_class_alias_from_dict() -> None:
    action = Action.from_dict(
        {"action_type": "close_window", "value": "声音", "class": "#32770"}
    )
    title, class_name, exe_name, error = _resolve_window_filters(action)
    assert error is None
    assert title == "声音"
    assert class_name == "#32770"
    assert action.window_class == "#32770"


def test_resolve_filters_requires_value_or_class() -> None:
    action = Action(action_type="close_window")
    _, _, _, error = _resolve_window_filters(action)
    assert error == "value or window_class is required"


def test_close_window_success() -> None:
    platform = MagicMock()
    platform.platform = "windows"
    action = Action(
        action_type="close_window",
        value="声音",
        window_class="#32770",
    )
    executor = CloseWindowAction()

    with (
        patch("worker.platforms.win_utils.find_window_handle", return_value=0x1234),
        patch("win32gui.PostMessage") as post_msg,
        patch("win32gui.IsWindow", return_value=False),
        patch("worker.actions.window.time.sleep"),
    ):
        result = executor.execute(platform, action)

    assert result.status == ActionStatus.SUCCESS
    post_msg.assert_called_once()
    assert "Closed window" in (result.output or "")


def test_close_window_not_found() -> None:
    platform = MagicMock()
    platform.platform = "windows"
    action = Action(action_type="close_window", value="不存在的窗口")
    executor = CloseWindowAction()

    with (
        patch("worker.platforms.win_utils.find_window_handle", return_value=None),
        patch("worker.actions.window.time.sleep"),
    ):
        result = executor.execute(platform, action)

    assert result.status == ActionStatus.FAILED
    assert "Window not found" in (result.error or "")


def test_close_window_still_exists_after_wm_close() -> None:
    platform = MagicMock()
    platform.platform = "windows"
    action = Action(action_type="close_window", value="声音", window_class="#32770")
    executor = CloseWindowAction()

    with (
        patch("worker.platforms.win_utils.find_window_handle", return_value=0x1234),
        patch("win32gui.PostMessage"),
        patch("win32gui.IsWindow", return_value=True),
        patch("worker.actions.window.time.sleep"),
    ):
        result = executor.execute(platform, action)

    assert result.status == ActionStatus.FAILED
    assert "still exists" in (result.error or "")


def test_close_window_unsupported_platform() -> None:
    platform = MagicMock()
    platform.platform = "android"
    action = Action(action_type="close_window", value="声音")
    result = CloseWindowAction().execute(platform, action)
    assert result.status == ActionStatus.FAILED
    assert "not supported" in (result.error or "")


def test_windows_platform_supports_close_window() -> None:
    from worker.config import PlatformConfig
    from worker.platforms.windows import WindowsPlatformManager

    manager = WindowsPlatformManager(PlatformConfig())
    assert manager.is_action_supported("close_window")

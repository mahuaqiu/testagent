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
    """单个匹配窗口：发送 WM_CLOSE 后下一次枚举已消失，应返回成功。"""
    platform = MagicMock()
    platform.platform = "windows"
    action = Action(
        action_type="close_window",
        value="声音",
        window_class="#32770",
    )
    executor = CloseWindowAction()

    with (
        patch("worker.platforms.win_utils.find_window_handles", side_effect=[[0x1234], []]),
        patch("win32gui.PostMessage") as post_msg,
        patch("worker.actions.window.time.sleep") as sleep_mock,
    ):
        result = executor.execute(platform, action)

    assert result.status == ActionStatus.SUCCESS
    post_msg.assert_called_once()
    assert post_msg.call_args[0][0] == 0x1234
    assert "Closed window" in (result.output or "")
    sleep_mock.assert_called_once()


def test_close_window_already_closed_returns_success() -> None:
    """一个匹配窗口都没有（含已全部关闭后重复调用）应返回成功，且不发送任何消息。"""
    platform = MagicMock()
    platform.platform = "windows"
    action = Action(action_type="close_window", value="不存在的窗口")
    executor = CloseWindowAction()

    with (
        patch("worker.platforms.win_utils.find_window_handles", return_value=[]),
        patch("win32gui.PostMessage") as post_msg,
        patch("worker.actions.window.time.sleep"),
    ):
        result = executor.execute(platform, action)

    assert result.status == ActionStatus.SUCCESS
    assert result.error is None
    post_msg.assert_not_called()
    assert "already closed" in (result.output or "")


def test_close_window_closes_all_matching_windows() -> None:
    """多个匹配窗口应全部发送 WM_CLOSE，并等待全部关闭。"""
    platform = MagicMock()
    platform.platform = "windows"
    action = Action(action_type="close_window", value="声音")
    executor = CloseWindowAction()

    with (
        patch("worker.platforms.win_utils.find_window_handles", side_effect=[[0x11, 0x22], []]),
        patch("win32gui.PostMessage") as post_msg,
        patch("worker.actions.window.time.sleep"),
    ):
        result = executor.execute(platform, action)

    assert result.status == ActionStatus.SUCCESS
    closed_hwnds = [call[0][0] for call in post_msg.call_args_list]
    assert closed_hwnds == [0x11, 0x22]
    assert "count=2" in (result.output or "")


def test_close_window_closes_new_window_appeared_during_poll() -> None:
    """轮询期间新出现的匹配窗口（连环弹窗）也应补发关闭请求，且已发过的不重复发送。"""
    platform = MagicMock()
    platform.platform = "windows"
    action = Action(action_type="close_window", value="声音")
    executor = CloseWindowAction()

    with (
        patch(
            "worker.platforms.win_utils.find_window_handles",
            side_effect=[[0x11], [0x11, 0x22], []],
        ),
        patch("win32gui.PostMessage") as post_msg,
        patch("worker.actions.window.time.sleep"),
    ):
        result = executor.execute(platform, action)

    assert result.status == ActionStatus.SUCCESS
    closed_hwnds = [call[0][0] for call in post_msg.call_args_list]
    assert closed_hwnds == [0x11, 0x22]


def test_close_window_still_exists_after_timeout() -> None:
    """超时后仍有匹配窗口可见（如弹出确认框）应返回失败，且不重复发送 WM_CLOSE。"""
    platform = MagicMock()
    platform.platform = "windows"
    action = Action(action_type="close_window", value="声音", window_class="#32770")
    executor = CloseWindowAction()

    with (
        patch("worker.platforms.win_utils.find_window_handles", return_value=[0x1234]),
        patch("win32gui.PostMessage") as post_msg,
        patch("worker.actions.window.time.sleep"),
    ):
        result = executor.execute(platform, action)

    assert result.status == ActionStatus.FAILED
    assert "still exist" in (result.error or "")
    post_msg.assert_called_once()


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

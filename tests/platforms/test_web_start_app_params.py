"""Web start_app 窗口尺寸/全屏与 token_headers 参数测试。"""

from worker.config import PlatformConfig
from worker.platforms.web import WebPlatformManager
from worker.task import Action


def _make_manager(token_headers: list[str] | None = None) -> WebPlatformManager:
    config = PlatformConfig()
    if token_headers is not None:
        config.token_headers = token_headers
    return WebPlatformManager(config)


# ========== 窗口尺寸/全屏参数解析 ==========


def test_no_size_params_uses_default_viewport() -> None:
    """不传 width/height 时保持默认视口（Playwright 默认 1280x720）。"""
    manager = _make_manager()

    manager._apply_window_size(Action(action_type="start_app"))

    assert manager._viewport is None
    assert manager._fullscreen is False


def test_positive_size_sets_viewport() -> None:
    manager = _make_manager()

    manager._apply_window_size(Action(action_type="start_app", width=1920, height=1080))

    assert manager._viewport == {"width": 1920, "height": 1080}
    assert manager._fullscreen is False


def test_minus_one_minus_one_means_fullscreen() -> None:
    manager = _make_manager()

    manager._apply_window_size(Action(action_type="start_app", width=-1, height=-1))

    assert manager._fullscreen is True
    assert manager._viewport is None


def test_partial_or_invalid_size_falls_back_to_default() -> None:
    """只传一个维度或非法组合时回退默认视口。"""
    manager = _make_manager()

    manager._apply_window_size(Action(action_type="start_app", width=1920))
    assert manager._viewport is None
    assert manager._fullscreen is False

    manager._apply_window_size(Action(action_type="start_app", width=-1, height=1080))
    assert manager._viewport is None
    assert manager._fullscreen is False


# ========== 启动选项组装 ==========


def test_launch_options_custom_viewport() -> None:
    manager = _make_manager()
    manager._apply_window_size(Action(action_type="start_app", width=1920, height=1080))

    context_options: dict = {}
    chromium_args: list[str] = []
    manager._apply_window_launch_options(context_options, chromium_args)

    assert context_options["viewport"] == {"width": 1920, "height": 1080}
    assert "no_viewport" not in context_options


def test_launch_options_fullscreen_headful() -> None:
    """headful 模式全屏：--start-fullscreen + no_viewport（视口跟随真实屏幕）。"""
    manager = _make_manager()
    manager.headless = False
    manager._apply_window_size(Action(action_type="start_app", width=-1, height=-1))

    context_options: dict = {}
    chromium_args: list[str] = []
    manager._apply_window_launch_options(context_options, chromium_args)

    assert "--start-fullscreen" in chromium_args
    assert context_options["no_viewport"] is True
    assert "viewport" not in context_options


def test_launch_options_fullscreen_headless_ignored() -> None:
    """headless 模式无窗口概念，全屏请求被忽略，保持默认视口。"""
    manager = _make_manager()
    manager.headless = True
    manager._apply_window_size(Action(action_type="start_app", width=-1, height=-1))

    context_options: dict = {}
    chromium_args: list[str] = []
    manager._apply_window_launch_options(context_options, chromium_args)

    assert "--start-fullscreen" not in chromium_args
    assert "no_viewport" not in context_options
    assert "viewport" not in context_options


def test_launch_options_no_params_keeps_playwright_default() -> None:
    manager = _make_manager()

    context_options: dict = {}
    chromium_args: list[str] = []
    manager._apply_window_launch_options(context_options, chromium_args)

    assert "viewport" not in context_options
    assert "no_viewport" not in context_options


# ========== token_headers 参数合并 ==========


def test_token_headers_from_action_appended() -> None:
    """配置为空时，start_app 传入的 token_headers 直接生效。"""
    manager = _make_manager()

    manager._merge_action_token_headers(
        Action(action_type="start_app", token_headers=["X-Auth-Token", "X-Request-Operator"])
    )

    assert manager._token_headers == ["X-Auth-Token", "X-Request-Operator"]


def test_token_headers_dedup_against_config() -> None:
    """与配置中的 header 去重，只追加新增项。"""
    manager = _make_manager(token_headers=["X-Auth-Token"])

    manager._merge_action_token_headers(
        Action(action_type="start_app", token_headers=["X-Auth-Token", "X-Space-Token"])
    )

    assert manager._token_headers == ["X-Auth-Token", "X-Space-Token"]


def test_token_headers_empty_and_blank_entries_ignored() -> None:
    manager = _make_manager(token_headers=["A"])

    manager._merge_action_token_headers(
        Action(action_type="start_app", token_headers=["", "A", None])  # type: ignore[list-item]
    )

    assert manager._token_headers == ["A"]


def test_token_headers_not_passed_keeps_config() -> None:
    manager = _make_manager(token_headers=["X-Auth-Token"])

    manager._merge_action_token_headers(Action(action_type="start_app"))

    assert manager._token_headers == ["X-Auth-Token"]

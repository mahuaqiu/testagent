"""activate_window action 单元测试。

覆盖 Windows 逻辑（web 非 headless 复用同一套），新流程分六步：
1. 查窗口句柄
2. 恢复最小化（仅 IsIconic）
3. Alt 键解除前台锁
4. SetForegroundWindow 激活 + 轮询验证（失败重试）
5. 验证通过：移动鼠标到窗口中心
6. 句柄找不到 / 激活验证失败：兜底移动到 (500,500)
"""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pywintypes
import win32con

from worker.actions.window import ActivateWindowAction
from worker.task import Action, ActionStatus

HWND = 0x1234
OTHER_HWND = 0x9999


def _make_platform(name="windows", headless=False):
    platform = MagicMock()
    platform.platform = name
    platform.headless = headless
    return platform


def _make_action(**kwargs):
    return Action(action_type="activate_window", **kwargs)


@contextmanager
def fake_win32(foreground_values, iconic=0, set_fg_raises=None):
    """模拟 win32 前台环境。

    foreground_values: GetForegroundWindow 的返回序列，耗尽后停在最后一个值。
    iconic: IsIconic 返回值。
    set_fg_raises: SetForegroundWindow 抛出的异常（None 表示正常返回）。
    """
    seq = iter(foreground_values)

    def _get_fg():
        try:
            return next(seq)
        except StopIteration:
            return foreground_values[-1]

    def _set_fg(_hwnd):
        if set_fg_raises is not None:
            raise set_fg_raises

    with (
        patch("win32gui.GetForegroundWindow", side_effect=_get_fg),
        patch("win32gui.SetForegroundWindow", side_effect=_set_fg) as set_fg,
        patch("win32gui.IsIconic", return_value=iconic),
        patch("win32gui.GetWindowRect", return_value=(100, 200, 500, 600)),
        patch("win32api.keybd_event") as alt,
        patch("worker.actions.window._show_window_async") as restore,
        patch("worker.actions.window.time.sleep"),
        patch.object(ActivateWindowAction, "_ACTIVATE_VERIFY_TIMEOUT", 0.05),
        patch.object(ActivateWindowAction, "_ACTIVATE_POLL_INTERVAL", 0.005),
    ):
        yield SimpleNamespace(set_fg=set_fg, alt=alt, restore=restore)


# ========== 参数校验（既有行为） ==========


def test_missing_value_fails() -> None:
    result = ActivateWindowAction().execute(_make_platform(), Action(action_type="activate_window"))
    assert result.status == ActionStatus.FAILED


def test_invalid_match_by_fails() -> None:
    action = _make_action(value="x", match_by="process")
    result = ActivateWindowAction().execute(_make_platform(), action)
    assert result.status == ActionStatus.FAILED


# ========== 成功流程 ==========


def test_activate_success_moves_mouse_to_center() -> None:
    """前台切换成功：Alt 解锁 + SetForegroundWindow，鼠标移到窗口中心（含 0~20 抖动）。"""
    executor = ActivateWindowAction()
    action = _make_action(value="Chrome_WidgetWin_1", match_by="class", name="chrome.exe")
    with (
        patch.object(executor, "_find_hwnd", return_value=HWND),
        patch("pyautogui.moveTo") as move,
        fake_win32([OTHER_HWND, HWND]) as ns,  # 快路径发现不在前台，激活后首轮验证即到前台
    ):
        result = executor.execute(_make_platform(), action)

    assert result.status == ActionStatus.SUCCESS
    assert "Activated window" in result.output
    assert ns.alt.call_count == 2  # Alt down + up
    ns.set_fg.assert_called_once_with(HWND)
    assert move.call_count == 1
    x, y = move.call_args[0][0], move.call_args[0][1]
    assert 300 <= x <= 320 and 400 <= y <= 420  # 窗口中心 (300,400) + 抖动


def test_already_foreground_skips_alt_and_setforeground() -> None:
    """窗口已在前台：不发 Alt、不调 SetForegroundWindow，直接移鼠标到中心。"""
    executor = ActivateWindowAction()
    action = _make_action(value="Chrome_WidgetWin_1", match_by="class", name="chrome.exe")
    with (
        patch.object(executor, "_find_hwnd", return_value=HWND),
        patch("pyautogui.moveTo") as move,
        fake_win32([HWND]) as ns,
    ):
        result = executor.execute(_make_platform(), action)

    assert result.status == ActionStatus.SUCCESS
    assert "Activated window" in result.output
    ns.set_fg.assert_not_called()
    ns.alt.assert_not_called()
    move.assert_called_once()


def test_web_headful_uses_windows_logic() -> None:
    """web 非 headless：与 windows 平台共用同一套 win32 激活逻辑。"""
    executor = ActivateWindowAction()
    action = _make_action(value="Chrome_WidgetWin_1", match_by="class", name="chrome.exe")
    with (
        patch.object(executor, "_find_hwnd", return_value=HWND),
        patch("pyautogui.moveTo") as move,
        fake_win32([HWND]) as ns,
    ):
        result = executor.execute(_make_platform(name="web", headless=False), action)

    assert result.status == ActionStatus.SUCCESS
    assert "Activated window" in result.output
    ns.set_fg.assert_not_called()  # 已在前台，快路径
    move.assert_called_once()


# ========== 恢复最小化 ==========


def test_minimized_window_restored_before_activate() -> None:
    """最小化窗口先 SW_RESTORE 再激活。"""
    executor = ActivateWindowAction()
    action = _make_action(value="Chrome_WidgetWin_1", match_by="class", name="chrome.exe")
    with (
        patch.object(executor, "_find_hwnd", return_value=HWND),
        patch("pyautogui.moveTo"),
        fake_win32([OTHER_HWND, HWND], iconic=1) as ns,
    ):
        result = executor.execute(_make_platform(), action)

    ns.restore.assert_called_once_with(HWND, win32con.SW_RESTORE)
    assert result.status == ActionStatus.SUCCESS
    assert "Activated window" in result.output


def test_maximized_window_not_restored() -> None:
    """仅最大化（未最小化）的窗口不应被 SW_RESTORE 拉回普通尺寸。"""
    executor = ActivateWindowAction()
    action = _make_action(value="Chrome_WidgetWin_1", match_by="class", name="chrome.exe")
    with (
        patch.object(executor, "_find_hwnd", return_value=HWND),
        patch("pyautogui.moveTo"),
        fake_win32([HWND], iconic=0) as ns,
    ):
        result = executor.execute(_make_platform(), action)

    ns.restore.assert_not_called()
    assert result.status == ActionStatus.SUCCESS


# ========== 激活失败与重试 ==========


def test_setforeground_never_succeeds_falls_back() -> None:
    """两次尝试都无法切到前台：兜底移动 500,500，output 明确说明失败与重试次数。"""
    executor = ActivateWindowAction()
    action = _make_action(value="Chrome_WidgetWin_1", match_by="class", name="chrome.exe")
    with (
        patch.object(executor, "_find_hwnd", return_value=HWND),
        patch("pyautogui.moveTo") as move,
        fake_win32([OTHER_HWND]) as ns,  # 永远不在前台
    ):
        result = executor.execute(_make_platform(), action)

    assert result.status == ActionStatus.SUCCESS
    assert "Failed to activate" in result.output
    assert "moved to 500,500" in result.output
    assert ns.set_fg.call_count == 2  # 重试了一次
    move.assert_called_once()
    x, y = move.call_args[0][0], move.call_args[0][1]
    assert 500 <= x <= 520 and 500 <= y <= 520


def test_setforeground_raise_falls_back() -> None:
    """SetForegroundWindow 抛异常（如 pywintypes.error）不冒泡，走兜底。"""
    executor = ActivateWindowAction()
    action = _make_action(value="Chrome_WidgetWin_1", match_by="class", name="chrome.exe")
    with (
        patch.object(executor, "_find_hwnd", return_value=HWND),
        patch("pyautogui.moveTo") as move,
        fake_win32([OTHER_HWND], set_fg_raises=pywintypes.error(0, "SetForegroundWindow", "nope")),
    ):
        result = executor.execute(_make_platform(), action)

    assert result.status == ActionStatus.SUCCESS
    assert "Failed to activate" in result.output
    move.assert_called_once()
    x, y = move.call_args[0][0], move.call_args[0][1]
    assert 500 <= x <= 520 and 500 <= y <= 520


def test_retry_then_success() -> None:
    """第一次验证未切到前台会重试一次 Alt+激活，第二次成功。

    用受控的 monotonic 序列让 attempt1 的轮询窗口内前台始终是 OTHER，
    attempt2 的首轮轮询返回 HWND，从而确定性地覆盖重试路径。
    """
    executor = ActivateWindowAction()
    action = _make_action(value="Chrome_WidgetWin_1", match_by="class", name="chrome.exe")
    fg_sequence = [OTHER_HWND, OTHER_HWND, HWND]  # 快路径 / attempt1 轮询 / attempt2 轮询
    monotonic_seq = [0.0, 0.01, 0.06, 0.06, 0.07]  # deadline start / poll<deadline / poll>=deadline / deadline start / poll<deadline
    with (
        patch.object(executor, "_find_hwnd", return_value=HWND),
        patch("pyautogui.moveTo"),
        fake_win32(fg_sequence) as ns,
        patch("worker.actions.window.time.monotonic", side_effect=monotonic_seq),
    ):
        result = executor.execute(_make_platform(), action)

    assert result.status == ActionStatus.SUCCESS
    assert "Activated window" in result.output
    assert ns.set_fg.call_count == 2


# ========== 找不到窗口 ==========


def test_window_not_found_falls_back() -> None:
    """找不到句柄：不发 Alt、不激活，兜底移动 500,500。"""
    executor = ActivateWindowAction()
    action = _make_action(value="不存在的窗口", match_by="title")
    with (
        patch("pygetwindow.getWindowsWithTitle", return_value=[]),
        patch("pyautogui.moveTo") as move,
        fake_win32([HWND]) as ns,  # 不应被触达
    ):
        result = executor.execute(_make_platform(), action)

    assert result.status == ActionStatus.SUCCESS
    assert "Window not found" in result.output
    ns.set_fg.assert_not_called()
    ns.alt.assert_not_called()
    move.assert_called_once()
    x, y = move.call_args[0][0], move.call_args[0][1]
    assert 500 <= x <= 520 and 500 <= y <= 520


# ========== headless 跳过 ==========


def test_web_headless_skips_activation() -> None:
    """web headless 没有 OS 窗口：直接跳过，不查句柄也不动真实鼠标。"""
    executor = ActivateWindowAction()
    action = _make_action(value="Chrome_WidgetWin_1", match_by="class")
    with (
        patch.object(executor, "_find_hwnd") as find_hwnd,
        patch("pyautogui.moveTo") as move,
        fake_win32([HWND]) as ns,
    ):
        result = executor.execute(_make_platform(name="web", headless=True), action)

    assert result.status == ActionStatus.SUCCESS
    find_hwnd.assert_not_called()
    move.assert_not_called()
    ns.set_fg.assert_not_called()

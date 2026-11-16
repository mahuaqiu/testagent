"""ocr_assert / image_assert 轮询（显式 timeout）行为测试。"""

import time
from types import SimpleNamespace

import pytest

from worker.actions.image import ImageAssertAction
from worker.actions.ocr import OcrAssertAction
from worker.actions.spec import ActionTimedOutError, ExecutionControl
from worker.config import PlatformConfig
from worker.platforms.base import PlatformManager
from worker.task import Action, ActionStatus

POLL_INTERVAL = 0.05


class _FakeOcrClient:
    def __init__(self) -> None:
        self.recognize_count = 0

    def recognize(self, image_bytes: bytes) -> list:
        self.recognize_count += 1
        return []

    def get_last_ocr_info(self) -> list:
        return []

    def get_last_response(self) -> dict | None:
        return None


class _FakePlatform(PlatformManager):
    """模拟屏幕状态：第 found_after 次截图起目标可见。"""

    def __init__(self, found_after: int = 99) -> None:
        super().__init__(PlatformConfig(), ocr_client=_FakeOcrClient())
        self.found_after = found_after
        self.check_count = 0

    @property
    def platform(self) -> str:
        return "test"

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def is_available(self) -> bool:
        return True

    def create_context(self, device_id=None, options=None):
        return SimpleNamespace()

    def close_context(self, context, close_session=False) -> None:
        pass

    def execute_action(self, context, action):
        raise NotImplementedError

    def get_screenshot(self, context) -> bytes:
        return b"screenshot"

    def click(self, x, y, duration=0, context=None) -> None:
        pass

    def double_click(self, x, y, context=None) -> None:
        pass

    def move(self, x, y, context=None) -> None:
        pass

    def input_text(self, text, context=None) -> None:
        pass

    def press(self, key, context=None) -> None:
        pass

    def swipe(self, start_x, start_y, end_x, end_y, duration=500, steps=None, context=None) -> None:
        pass

    def take_screenshot(self, context=None) -> bytes:
        self.check_count += 1
        return b"screenshot"

    def _find_text_position_cached(self, text, match_mode="exact", index=0, ocr_results=None):
        return (10, 20) if self.check_count >= self.found_after else None

    def _find_image_position(self, source_bytes, template_base64, threshold=0.9, index=0):
        return (30, 40) if self.check_count >= self.found_after else None


@pytest.fixture(autouse=True)
def fast_poll_interval(monkeypatch):
    """缩短轮询间隔，避免测试真实等待 1 秒。"""
    monkeypatch.setattr(OcrAssertAction, "ASSERT_POLL_INTERVAL", POLL_INTERVAL)
    monkeypatch.setattr(ImageAssertAction, "ASSERT_POLL_INTERVAL", POLL_INTERVAL)


def _control(window_sec: float) -> ExecutionControl:
    """模拟调度层注入的截止时间：动作开始时刻 + 显式 timeout。"""
    return ExecutionControl(deadline_monotonic=time.monotonic() + window_sec)


def test_ocr_assert_without_timeout_stays_single_check() -> None:
    platform = _FakePlatform(found_after=99)
    action = Action.from_dict({"action_type": "ocr_assert", "value": "操作成功"})
    action.execution_control = _control(5.0)

    result = OcrAssertAction().execute(platform, action)

    assert result.status == ActionStatus.FAILED
    assert platform.ocr_client.recognize_count == 1


def test_ocr_assert_polls_until_found() -> None:
    platform = _FakePlatform(found_after=3)
    action = Action.from_dict({"action_type": "ocr_assert", "value": "操作成功", "timeout": 2000})
    action.execution_control = _control(2.0)

    result = OcrAssertAction().execute(platform, action)

    assert result.status == ActionStatus.SUCCESS
    assert platform.ocr_client.recognize_count == 3


def test_ocr_assert_fails_after_poll_window() -> None:
    platform = _FakePlatform(found_after=99)
    action = Action.from_dict({"action_type": "ocr_assert", "value": "操作成功", "timeout": 300})
    action.execution_control = _control(0.3)

    started = time.monotonic()
    result = OcrAssertAction().execute(platform, action)

    assert result.status == ActionStatus.FAILED
    assert platform.ocr_client.recognize_count >= 2
    assert time.monotonic() - started >= 0.15


def test_ocr_assert_negate_fails_immediately_when_found() -> None:
    platform = _FakePlatform(found_after=2)
    action = Action.from_dict({"action_type": "ocr_assert", "value": "错误提示", "negate": True, "timeout": 2000})
    action.execution_control = _control(2.0)

    result = OcrAssertAction().execute(platform, action)

    assert result.status == ActionStatus.FAILED
    assert "expected not exist" in result.error
    assert platform.ocr_client.recognize_count == 2


def test_ocr_assert_negate_succeeds_after_window() -> None:
    platform = _FakePlatform(found_after=99)
    action = Action.from_dict({"action_type": "ocr_assert", "value": "错误提示", "negate": True, "timeout": 200})
    action.execution_control = _control(0.2)

    result = OcrAssertAction().execute(platform, action)

    assert result.status == ActionStatus.SUCCESS
    assert platform.ocr_client.recognize_count >= 2


def test_ocr_assert_deadline_expiry_returns_failed_not_raised() -> None:
    class _ExpiringControl:
        """模拟轮询等待中跨越看门狗截止时间。"""

        def remaining_seconds(self) -> float:
            return 0.5

        def wait(self, seconds: float) -> None:
            raise ActionTimedOutError("Task timeout")

    platform = _FakePlatform(found_after=99)
    action = Action.from_dict({"action_type": "ocr_assert", "value": "操作成功", "timeout": 500})
    action.execution_control = _ExpiringControl()

    result = OcrAssertAction().execute(platform, action)

    assert result.status == ActionStatus.FAILED


def test_image_assert_without_timeout_stays_single_check() -> None:
    platform = _FakePlatform(found_after=99)
    action = Action.from_dict({"action_type": "image_assert", "image_base64": "abc"})
    action.execution_control = _control(5.0)

    result = ImageAssertAction().execute(platform, action)

    assert result.status == ActionStatus.FAILED
    assert platform.check_count == 1


def test_image_assert_polls_until_found() -> None:
    platform = _FakePlatform(found_after=3)
    action = Action.from_dict({"action_type": "image_assert", "image_base64": "abc", "timeout": 2000})
    action.execution_control = _control(2.0)

    result = ImageAssertAction().execute(platform, action)

    assert result.status == ActionStatus.SUCCESS
    assert platform.check_count == 3


def test_image_assert_fails_after_poll_window() -> None:
    platform = _FakePlatform(found_after=99)
    action = Action.from_dict({"action_type": "image_assert", "image_base64": "abc", "timeout": 300})
    action.execution_control = _control(0.3)

    result = ImageAssertAction().execute(platform, action)

    assert result.status == ActionStatus.FAILED
    assert platform.check_count >= 2


def test_ocr_assert_short_window_still_polls() -> None:
    """窗口只剩约 2 个间隔时也不应退化为单次检查（契约：窗口内每秒复查一次）。"""
    platform = _FakePlatform(found_after=99)
    action = Action.from_dict({"action_type": "ocr_assert", "value": "操作成功", "timeout": 80})
    action.execution_control = _control(0.08)

    OcrAssertAction().execute(platform, action)

    assert platform.ocr_client.recognize_count >= 2


# ========== 轮询窗口边界：浮点残差应视为窗口耗尽 ==========


def test_next_assert_poll_wait_boundary_residual_ends_window(monkeypatch) -> None:
    """remaining 恰好比轮询间隔大一个浮点残差时，应视为窗口耗尽（返回 None）。

    真实场景：Windows time.monotonic 用 GetTickCount64，精度 15.6ms。轮询若干轮后
    remaining 可能落在间隔边界 +1e-13 级浮点残差上；若调度返回≈0 的等待值，
    ExecutionControl.wait 立即返回，循环无等待空转，且时钟冻结期内 remaining
    不再变化、退出条件永不触发，直到把后续检查次数烧完。
    """
    fake_now = 100.0
    monkeypatch.setattr(time, "monotonic", lambda: fake_now)
    for cls in (OcrAssertAction, ImageAssertAction):
        action = Action.from_dict({"action_type": "x", "value": "y", "timeout": 300})
        action.timeout_explicit = True
        action.execution_control = ExecutionControl(
            deadline_monotonic=fake_now + cls.ASSERT_POLL_INTERVAL + 1.8e-13
        )
        assert cls()._next_assert_poll_wait(action) is None, cls.__name__


def test_next_assert_poll_wait_normal_remaining_still_polls(monkeypatch) -> None:
    """边界修正是收窄：剩余时间充足时仍按原语义返回等待值。"""
    fake_now = 100.0
    monkeypatch.setattr(time, "monotonic", lambda: fake_now)
    for cls in (OcrAssertAction, ImageAssertAction):
        action = Action.from_dict({"action_type": "x", "value": "y", "timeout": 300})
        action.timeout_explicit = True
        action.execution_control = ExecutionControl(
            deadline_monotonic=fake_now + 3 * cls.ASSERT_POLL_INTERVAL
        )
        wait = cls()._next_assert_poll_wait(action)
        assert wait == cls.ASSERT_POLL_INTERVAL, cls.__name__

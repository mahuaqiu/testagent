"""Web create_context 重试与诊断日志测试。

覆盖场景：登录跳转慢导致 Playwright 报
"Target page, context or browser has been closed" 时的重试、最终失败诊断与状态自愈。
"""

import asyncio
import logging
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from worker.config import PlatformConfig
from worker.platforms.web import (
    CREATE_CONTEXT_MAX_RETRIES,
    CREATE_CONTEXT_RETRY_INTERVAL,
    WebPlatformManager,
)


def _make_manager() -> WebPlatformManager:
    manager = WebPlatformManager(PlatformConfig())
    manager._started = True
    manager._browser_context = MagicMock()
    manager._browser_context.pages = []
    return manager


@pytest.fixture
def no_sleep(monkeypatch) -> list[float]:
    """跳过真实等待，同时记录每次 sleep 的间隔。"""
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
    return sleeps


# ========== 重试成功路径 ==========


def test_retry_succeeds_on_third_attempt(monkeypatch, no_sleep) -> None:
    """前两次报 Target closed（跳转慢场景），第 3 次成功：共重试 2 次，间隔 3 秒。"""
    manager = _make_manager()
    fake_page = MagicMock()
    calls = {"n": 0}

    async def flaky_create_page():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("Target page, context or browser has been closed")
        return fake_page

    monkeypatch.setattr(manager, "_async_create_page", flaky_create_page)

    page = manager.create_context()

    assert page is fake_page
    assert calls["n"] == 3
    assert no_sleep == [CREATE_CONTEXT_RETRY_INTERVAL] * CREATE_CONTEXT_MAX_RETRIES
    assert manager._sessions["default"]["page"] is fake_page
    assert manager._current_page is fake_page


def test_first_attempt_success_no_retry(monkeypatch, no_sleep) -> None:
    """首次即成功：不重试、不等待。"""
    manager = _make_manager()
    fake_page = MagicMock()

    async def ok_create_page():
        return fake_page

    monkeypatch.setattr(manager, "_async_create_page", ok_create_page)

    page = manager.create_context()

    assert page is fake_page
    assert no_sleep == []


def test_reuses_active_session_without_retry(monkeypatch, no_sleep) -> None:
    """已有活跃会话时直接复用，不进入重试逻辑。"""
    manager = _make_manager()
    existing_page = MagicMock()
    existing_page.is_closed.return_value = False
    manager._sessions["default"] = {"context": manager._browser_context, "page": existing_page}

    async def should_not_be_called():
        raise AssertionError("_async_create_page should not be called")

    monkeypatch.setattr(manager, "_async_create_page", should_not_be_called)

    page = manager.create_context()

    assert page is existing_page
    assert no_sleep == []


# ========== 最终失败路径 ==========


def test_fails_after_max_retries_with_diagnostics(monkeypatch, no_sleep, caplog) -> None:
    """持续失败：共尝试 3 次（首次 + 2 次重试），最终抛错并输出诊断日志。"""
    manager = _make_manager()

    async def always_fail():
        raise RuntimeError("Target page, context or browser has been closed")

    monkeypatch.setattr(manager, "_async_create_page", always_fail)
    monkeypatch.setattr(manager, "_browser_context_is_valid", lambda: False)

    with caplog.at_level(logging.ERROR, logger="worker.platforms.web"):
        with pytest.raises(RuntimeError, match="Target page, context or browser has been closed"):
            manager.create_context()

    assert no_sleep == [CREATE_CONTEXT_RETRY_INTERVAL] * CREATE_CONTEXT_MAX_RETRIES
    # 诊断日志包含关键状态，便于定位问题
    diag = "\n".join(r.getMessage() for r in caplog.records)
    assert "Web create_context failed" in diag
    assert "[diag] is_available=" in diag
    assert "[diag] has_active_session=" in diag
    assert "[diag] context.pages accessible" in diag
    assert "[diag] _browser_context_is_valid=False" in diag
    # 上下文失效时重置状态，让下次任务可重启浏览器（自愈）
    assert manager._browser_context is None
    assert manager._started is False


def test_no_reset_when_context_still_valid(monkeypatch, no_sleep) -> None:
    """最终失败但 context 仍有效（如页面短暂抖动）：不重置状态。"""
    manager = _make_manager()

    async def always_fail():
        raise RuntimeError("Target page, context or browser has been closed")

    monkeypatch.setattr(manager, "_async_create_page", always_fail)
    monkeypatch.setattr(manager, "_browser_context_is_valid", lambda: True)
    reset_calls: list[bool] = []
    monkeypatch.setattr(manager, "_reset_browser_state", lambda: reset_calls.append(True))

    with pytest.raises(RuntimeError, match="Failed to create Web context"):
        manager.create_context()

    assert reset_calls == []
    assert manager._browser_context is not None


def test_not_started_raises_without_retry(monkeypatch, no_sleep) -> None:
    """平台未启动：直接抛错，不重试。"""
    manager = _make_manager()
    manager._started = False

    async def should_not_be_called():
        raise AssertionError("_async_create_page should not be called")

    monkeypatch.setattr(manager, "_async_create_page", should_not_be_called)

    with pytest.raises(RuntimeError, match="Web platform not started"):
        manager.create_context()

    assert no_sleep == []


def test_platform_stopped_during_retry_raises(monkeypatch, no_sleep) -> None:
    """等待期间平台被停止：重新检查后立即抛错，不再继续创建。"""
    manager = _make_manager()

    async def fail_once():
        raise RuntimeError("Target page, context or browser has been closed")

    monkeypatch.setattr(manager, "_async_create_page", fail_once)
    monkeypatch.setattr(manager, "_browser_context_is_valid", lambda: False)

    original_is_available = manager.is_available

    def stopped_after_first_wait() -> bool:
        # 第一次重试等待后模拟平台被停止
        if no_sleep:
            return False
        return original_is_available()

    monkeypatch.setattr(manager, "is_available", stopped_after_first_wait)

    with pytest.raises(RuntimeError, match="Web platform not started"):
        manager.create_context()

    # 只等待了一次，第二次等待前就检测到平台已停止
    assert no_sleep == [CREATE_CONTEXT_RETRY_INTERVAL]


# ========== _async_create_page 跳过已关闭页面 ==========


def test_async_create_page_skips_closed_pages() -> None:
    """context.pages[0] 已关闭时跳过，使用下一个活跃页面。"""
    manager = _make_manager()
    closed_page = MagicMock()
    closed_page.is_closed.return_value = True
    live_page = MagicMock()
    live_page.is_closed.return_value = False
    manager._browser_context.pages = [closed_page, live_page]

    page = asyncio.run(manager._async_create_page())

    assert page is live_page


def test_async_create_page_creates_new_when_all_closed() -> None:
    """所有页面都已关闭时创建新页面。"""
    manager = _make_manager()
    closed_page = MagicMock()
    closed_page.is_closed.return_value = True
    manager._browser_context.pages = [closed_page]
    new_page = MagicMock()
    manager._browser_context.new_page = AsyncMock(return_value=new_page)

    page = asyncio.run(manager._async_create_page())

    assert page is new_page
    manager._browser_context.new_page.assert_called_once()

"""
Web 端 conftest —— 提供 Playwright 相关的 fixtures。

这里的 fixture 只对 web/tests/ 下的用例生效。
"""

import pytest
from playwright.sync_api import sync_playwright, Page, Browser

from common.config import Config
from web.steps.login_steps import LoginSteps


@pytest.fixture(scope="session")
def browser(config: Config):
    """启动浏览器实例，整个测试会话共享。

    Yields:
        Browser: Playwright Browser 对象。
    """
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    yield browser
    browser.close()
    pw.stop()


@pytest.fixture
def page(browser: Browser, config: Config) -> Page:
    """为每条用例创建新的浏览器页面，用例结束后自动关闭。

    自动设置 base_url，页面操作中可以使用相对路径。

    Yields:
        Page: Playwright Page 对象。
    """
    context = browser.new_context(base_url=config.base_url)
    pg = context.new_page()
    yield pg
    pg.close()
    context.close()


@pytest.fixture
def login_steps(page: Page) -> LoginSteps:
    """Web 端登录流程 steps。

    用例中直接使用:
        def test_xxx(self, login_steps):
            login_steps.login_as("user", "pass")

    Returns:
        LoginSteps: 登录流程封装对象。
    """
    return LoginSteps(page)

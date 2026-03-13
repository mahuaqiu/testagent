"""
App 端 conftest —— 提供 Appium 相关的 fixtures。

这里的 fixture 只对 app/tests/ 下的用例生效。
"""

import pytest
from appium import webdriver as appium_webdriver
from appium.options.common import AppiumOptions

from common.config import Config
from app.steps.login_steps import LoginSteps


@pytest.fixture(scope="session")
def driver(config: Config):
    """启动 Appium Driver，整个测试会话共享。

    从 config 中读取 Appium server 地址和 desired capabilities。

    Yields:
        AppiumDriver: Appium 驱动对象。
    """
    server_url = config.get("app.appium_server")
    caps = config.get("app.desired_caps", {})

    options = AppiumOptions()
    for key, value in caps.items():
        options.set_capability(key, value)

    drv = appium_webdriver.Remote(command_executor=server_url, options=options)
    drv.implicitly_wait(10)
    yield drv
    drv.quit()


@pytest.fixture
def login_steps(driver) -> LoginSteps:
    """App 端登录流程 steps。

    Returns:
        LoginSteps: 登录流程封装对象。
    """
    return LoginSteps(driver)

"""
Android 端 conftest —— 提供 Appium 相关的 fixtures。
"""

import pytest
from appium import webdriver as appium_webdriver
from appium.options.common import AppiumOptions

from common.config import Config
from android.steps.login_steps import LoginSteps


@pytest.fixture(scope="session")
def driver(config: Config):
    """启动 Android Appium Driver，整个测试会话共享。

    Yields:
        AppiumDriver: Appium 驱动对象。
    """
    server_url = config.get("android.appium_server")
    caps = config.get("android.desired_caps", {})

    options = AppiumOptions()
    for key, value in caps.items():
        options.set_capability(key, value)

    drv = appium_webdriver.Remote(command_executor=server_url, options=options)
    drv.implicitly_wait(10)
    yield drv
    drv.quit()


@pytest.fixture
def login_steps(driver) -> LoginSteps:
    """Android 端登录流程 steps。"""
    return LoginSteps(driver)
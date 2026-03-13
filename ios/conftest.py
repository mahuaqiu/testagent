"""
iOS 端 conftest —— 提供 Appium 相关的 fixtures。
"""

import pytest
from appium import webdriver as appium_webdriver
from appium.options.common import AppiumOptions

from common.config import Config


@pytest.fixture(scope="session")
def driver(config: Config):
    """启动 iOS Appium Driver。"""
    server_url = config.get("ios.appium_server")
    caps = config.get("ios.desired_caps", {})

    options = AppiumOptions()
    for key, value in caps.items():
        options.set_capability(key, value)

    drv = appium_webdriver.Remote(command_executor=server_url, options=options)
    drv.implicitly_wait(10)
    yield drv
    drv.quit()
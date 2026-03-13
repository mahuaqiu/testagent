"""
iOS 端 Page Object 基类 —— 所有 iOS 页面类的父类。

基于 Appium (XCUITest)，封装常用的 iOS 元素操作。
新增页面时继承此类，专注编写业务操作。
"""

from appium.webdriver import Remote as AppiumDriver
from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


class BasePage:
    """iOS 端 Page Object 基类（Appium XCUITest）。

    提供通用的 iOS 元素操作方法。
    定位器统一使用 tuple 格式: (by, value)。

    Args:
        driver: Appium Driver 对象。
    """

    def __init__(self, driver: AppiumDriver):
        self.driver = driver

    def find(self, locator: tuple, timeout: float = 10):
        """查找元素，自带显式等待。"""
        by, value = locator
        by_map = {
            "id": AppiumBy.ACCESSIBILITY_ID,
            "xpath": AppiumBy.XPATH,
            "class": AppiumBy.CLASS_NAME,
            "ios_class_chain": AppiumBy.IOS_CLASS_CHAIN,
            "ios_predicate": AppiumBy.IOS_PREDICATE,
        }
        appium_by = by_map.get(by, by)
        return WebDriverWait(self.driver, timeout).until(
            EC.presence_of_element_located((appium_by, value))
        )

    def click(self, locator: tuple):
        """点击元素。"""
        self.find(locator).click()

    def input_text(self, locator: tuple, text: str):
        """清空输入框后输入文本。"""
        el = self.find(locator)
        el.clear()
        el.send_keys(text)

    def get_text(self, locator: tuple) -> str:
        """获取元素文本。"""
        return self.find(locator).text

    def is_displayed(self, locator: tuple, timeout: float = 5) -> bool:
        """判断元素是否显示。"""
        try:
            return self.find(locator, timeout=timeout).is_displayed()
        except Exception:
            return False

    def swipe_up(self, duration: int = 800):
        """向上滑动。"""
        size = self.driver.get_window_size()
        start_x = size["width"] // 2
        start_y = int(size["height"] * 0.8)
        end_y = int(size["height"] * 0.2)
        self.driver.swipe(start_x, start_y, start_x, end_y, duration)

    def swipe_down(self, duration: int = 800):
        """向下滑动。"""
        size = self.driver.get_window_size()
        start_x = size["width"] // 2
        start_y = int(size["height"] * 0.2)
        end_y = int(size["height"] * 0.8)
        self.driver.swipe(start_x, start_y, start_x, end_y, duration)

    def screenshot(self, name: str = "screenshot"):
        """截图保存。"""
        self.driver.save_screenshot(f"data/{name}.png")
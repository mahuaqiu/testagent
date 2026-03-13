"""
App 端 Page Object 基类 —— 所有 App 页面类的父类。

基于 Appium，封装常用的移动端元素操作。
新增页面时继承此类，专注编写业务操作。

Usage:
    class LoginPage(BasePage):
        loc_username = ("id", "com.example:id/username")

        def fill_username(self, text: str):
            self.input_text(self.loc_username, text)
"""

from appium.webdriver import Remote as AppiumDriver
from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


class BasePage:
    """App 端 Page Object 基类（Appium）。

    提供通用的移动端元素操作方法。
    定位器统一使用 tuple 格式: (by, value)，如 ("id", "com.example:id/btn")。

    Args:
        driver: Appium Driver 对象。
    """

    def __init__(self, driver: AppiumDriver):
        self.driver = driver

    def find(self, locator: tuple, timeout: float = 10):
        """查找元素，自带显式等待。

        Args:
            locator: 定位器元组，如 ("id", "com.example:id/username")。
            timeout: 等待超时秒数。

        Returns:
            WebElement: 找到的元素。
        """
        by, value = locator
        by_map = {
            "id": AppiumBy.ID,
            "xpath": AppiumBy.XPATH,
            "accessibility_id": AppiumBy.ACCESSIBILITY_ID,
            "class": AppiumBy.CLASS_NAME,
        }
        appium_by = by_map.get(by, by)
        return WebDriverWait(self.driver, timeout).until(
            EC.presence_of_element_located((appium_by, value))
        )

    def click(self, locator: tuple):
        """点击元素。

        Args:
            locator: 定位器元组。
        """
        self.find(locator).click()

    def input_text(self, locator: tuple, text: str):
        """清空输入框后输入文本。

        Args:
            locator: 输入框定位器。
            text: 要输入的文本。
        """
        el = self.find(locator)
        el.clear()
        el.send_keys(text)

    def get_text(self, locator: tuple) -> str:
        """获取元素文本。

        Args:
            locator: 元素定位器。

        Returns:
            str: 元素文本。
        """
        return self.find(locator).text

    def is_displayed(self, locator: tuple, timeout: float = 5) -> bool:
        """判断元素是否显示。

        Args:
            locator: 元素定位器。
            timeout: 等待超时秒数。

        Returns:
            bool: 元素是否显示。
        """
        try:
            return self.find(locator, timeout=timeout).is_displayed()
        except Exception:
            return False

    def swipe_up(self, duration: int = 800):
        """向上滑动（常用于列表滚动）。

        Args:
            duration: 滑动持续时间毫秒。
        """
        size = self.driver.get_window_size()
        start_x = size["width"] // 2
        start_y = int(size["height"] * 0.8)
        end_y = int(size["height"] * 0.2)
        self.driver.swipe(start_x, start_y, start_x, end_y, duration)

    def swipe_down(self, duration: int = 800):
        """向下滑动（常用于下拉刷新）。

        Args:
            duration: 滑动持续时间毫秒。
        """
        size = self.driver.get_window_size()
        start_x = size["width"] // 2
        start_y = int(size["height"] * 0.2)
        end_y = int(size["height"] * 0.8)
        self.driver.swipe(start_x, start_y, start_x, end_y, duration)

    def back(self):
        """按返回键。"""
        self.driver.back()

    def screenshot(self, name: str = "screenshot"):
        """截图保存到 data/ 目录。

        Args:
            name: 截图文件名（不含扩展名）。
        """
        self.driver.save_screenshot(f"data/{name}.png")

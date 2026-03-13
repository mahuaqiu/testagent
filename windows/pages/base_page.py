"""
Windows 桌面端 Page Object 基类 —— 所有 Windows 页面类的父类。

基于 WinAppDriver / Playwright for Desktop，封装常用的 Windows 元素操作。
新增页面时继承此类，专注编写业务操作。
"""


class BasePage:
    """Windows 桌面端 Page Object 基类。

    提供通用的 Windows 元素操作方法。
    定位器统一使用 tuple 格式: (by, value)。

    Args:
        driver: 驱动对象。
    """

    def __init__(self, driver):
        self.driver = driver

    def find(self, locator: tuple, timeout: float = 10):
        """查找元素，自带显式等待。"""
        # TODO: 实现具体的查找逻辑
        raise NotImplementedError("Windows 端尚未实现")

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

    def screenshot(self, name: str = "screenshot"):
        """截图保存。"""
        self.driver.save_screenshot(f"data/{name}.png")
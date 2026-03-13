"""
Web 端 Page Object 基类 —— 所有 Web 页面类的父类。

基于 Playwright，封装了常用的页面操作方法。
新增页面时继承此类，专注编写业务操作。

Usage:
    class LoginPage(BasePage):
        loc_username = "input[name='username']"

        def fill_username(self, text: str):
            self.fill(self.loc_username, text)
"""

from playwright.sync_api import Page, expect


class BasePage:
    """Web 端 Page Object 基类（Playwright）。

    提供通用的元素操作方法，子类通过定义 loc_* 定位器 + 业务方法来组织页面。

    Args:
        page: Playwright Page 对象。
    """

    def __init__(self, page: Page):
        self.page = page

    def navigate(self, path: str = "/"):
        """导航到指定路径（相对路径，拼接 base_url）。

        Args:
            path: 页面路径，如 "/login"。
        """
        self.page.goto(path)

    def click(self, selector: str):
        """点击元素。

        Args:
            selector: CSS 选择器或 Playwright 选择器。
        """
        self.page.click(selector)

    def fill(self, selector: str, text: str):
        """向输入框填入文本（先清空）。

        Args:
            selector: 输入框选择器。
            text: 要填入的文本。
        """
        self.page.fill(selector, text)

    def get_text(self, selector: str) -> str:
        """获取元素文本内容。

        Args:
            selector: 元素选择器。

        Returns:
            str: 元素的文本内容。
        """
        return self.page.text_content(selector) or ""

    def is_visible(self, selector: str) -> bool:
        """判断元素是否可见。

        Args:
            selector: 元素选择器。

        Returns:
            bool: 元素是否可见。
        """
        return self.page.is_visible(selector)

    def wait_for(self, selector: str, timeout: float = 5000):
        """等待元素出现。

        Args:
            selector: 元素选择器。
            timeout: 超时时间（毫秒）。
        """
        self.page.wait_for_selector(selector, timeout=timeout)

    def expect_visible(self, selector: str):
        """断言元素可见（Playwright 内置断言，自动等待）。

        Args:
            selector: 元素选择器。
        """
        expect(self.page.locator(selector)).to_be_visible()

    def expect_text(self, selector: str, text: str):
        """断言元素包含指定文本。

        Args:
            selector: 元素选择器。
            text: 期望包含的文本。
        """
        expect(self.page.locator(selector)).to_contain_text(text)

    def screenshot(self, name: str = "screenshot"):
        """截图并保存。

        Args:
            name: 截图文件名（不含扩展名）。
        """
        self.page.screenshot(path=f"data/{name}.png")

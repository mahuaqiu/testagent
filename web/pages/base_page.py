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

from typing import Optional

from playwright.sync_api import Page, expect

from common.ocr_client import OCRClient, TextBlock, MatchResult, get_ocr_client


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

    # ==================== OCR 相关方法 ====================

    def _get_screenshot_bytes(self) -> bytes:
        """获取当前页面的截图字节数据。

        Returns:
            bytes: 截图的二进制数据。
        """
        return self.page.screenshot()

    def _get_ocr_client(self) -> OCRClient:
        """获取 OCR 客户端实例。"""
        return get_ocr_client()

    def click_text(
        self,
        text: str,
        match_mode: str = "exact",
        offset: Optional[dict] = None,
        confidence_threshold: float = 0.0,
    ) -> bool:
        """通过 OCR 识别文字并点击。

        Args:
            text: 目标文字。
            match_mode: 匹配模式（exact/fuzzy/regex）。
            offset: 点击偏移量，如 {"x": 10, "y": -5}。
            confidence_threshold: 置信度阈值。

        Returns:
            bool: 是否点击成功。
        """
        screenshot_bytes = self._get_screenshot_bytes()
        client = self._get_ocr_client()

        text_block = client.find_text(
            screenshot_bytes, text, match_mode, confidence_threshold
        )

        if text_block is None:
            return False

        x = text_block.center_x
        y = text_block.center_y

        if offset:
            x += offset.get("x", 0)
            y += offset.get("y", 0)

        self.page.mouse.click(x, y)
        return True

    def click_image(
        self,
        template_path: str,
        threshold: float = 0.8,
        offset: Optional[dict] = None,
        method: str = "template",
    ) -> bool:
        """通过图像匹配并点击。

        Args:
            template_path: 模板图片路径。
            threshold: 匹配阈值（0-1）。
            offset: 点击偏移量。
            method: 匹配方法（template/feature）。

        Returns:
            bool: 是否点击成功。
        """
        screenshot_bytes = self._get_screenshot_bytes()

        with open(template_path, "rb") as f:
            template_bytes = f.read()

        client = self._get_ocr_client()
        match_result = client.find_image(screenshot_bytes, template_bytes, threshold, method)

        if match_result is None:
            return False

        x = match_result.center_x
        y = match_result.center_y

        if offset:
            x += offset.get("x", 0)
            y += offset.get("y", 0)

        self.page.mouse.click(x, y)
        return True

    def find_text(
        self,
        text: str,
        match_mode: str = "exact",
        confidence_threshold: float = 0.0,
    ) -> Optional[TextBlock]:
        """查找指定文字的位置。

        Args:
            text: 目标文字。
            match_mode: 匹配模式。
            confidence_threshold: 置信度阈值。

        Returns:
            TextBlock | None: 找到的文字块，未找到返回 None。
        """
        screenshot_bytes = self._get_screenshot_bytes()
        client = self._get_ocr_client()
        return client.find_text(screenshot_bytes, text, match_mode, confidence_threshold)

    def find_all_texts(
        self,
        text: str,
        confidence_threshold: float = 0.0,
    ) -> list[TextBlock]:
        """查找所有匹配的文字。

        Args:
            text: 目标文字。
            confidence_threshold: 置信度阈值。

        Returns:
            list[TextBlock]: 匹配的文字块列表。
        """
        screenshot_bytes = self._get_screenshot_bytes()
        client = self._get_ocr_client()
        return client.find_all_texts(screenshot_bytes, text, confidence_threshold)

    def is_text_visible(
        self,
        text: str,
        match_mode: str = "exact",
        confidence_threshold: float = 0.0,
    ) -> bool:
        """判断指定文字是否可见。

        Args:
            text: 目标文字。
            match_mode: 匹配模式。
            confidence_threshold: 置信度阈值。

        Returns:
            bool: 文字是否可见。
        """
        return self.find_text(text, match_mode, confidence_threshold) is not None

    def find_image(
        self,
        template_path: str,
        threshold: float = 0.8,
        method: str = "template",
    ) -> Optional[MatchResult]:
        """查找模板图像的位置。

        Args:
            template_path: 模板图片路径。
            threshold: 匹配阈值。
            method: 匹配方法。

        Returns:
            MatchResult | None: 匹配结果，未找到返回 None。
        """
        screenshot_bytes = self._get_screenshot_bytes()

        with open(template_path, "rb") as f:
            template_bytes = f.read()

        client = self._get_ocr_client()
        return client.find_image(screenshot_bytes, template_bytes, threshold, method)

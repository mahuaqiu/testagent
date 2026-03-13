"""
Windows 桌面端 Page Object 基类 —— 所有 Windows 页面类的父类。

基于 WinAppDriver / pyautogui，封装常用的 Windows 元素操作。
新增页面时继承此类，专注编写业务操作。

Usage:
    class LoginPage(BasePage):
        loc_username = ("id", "username")

        def fill_username(self, text: str):
            self.input_text(self.loc_username, text)
"""

import os
from typing import Optional

import pyautogui

from common.ocr_client import OCRClient, TextBlock, MatchResult, get_ocr_client


class BasePage:
    """Windows 桌面端 Page Object 基类。

    提供通用的 Windows 元素操作方法。
    定位器统一使用 tuple 格式: (by, value)。

    Args:
        driver: 驱动对象（WinAppDriver 或 None）。
    """

    def __init__(self, driver=None):
        self.driver = driver

    def find(self, locator: tuple, timeout: float = 10):
        """查找元素，自带显式等待。

        Args:
            locator: 定位器元组。
            timeout: 等待超时秒数。

        Returns:
            WebElement: 找到的元素。
        """
        if self.driver is None:
            raise NotImplementedError("Windows driver not initialized")

        # WinAppDriver 使用 Selenium 风格的定位
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

        by, value = locator
        by_map = {
            "id": By.ID,
            "name": By.NAME,
            "class": By.CLASS_NAME,
            "xpath": By.XPATH,
            "accessibility_id": By.ACCESSIBILITY_ID,
        }
        win_by = by_map.get(by, by)
        return WebDriverWait(self.driver, timeout).until(
            EC.presence_of_element_located((win_by, value))
        )

    def click(self, locator: tuple):
        """点击元素。

        Args:
            locator: 定位器元组。
        """
        if self.driver:
            self.find(locator).click()
        else:
            raise NotImplementedError("Use click_text or click_image for OCR-based clicking")

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

    def screenshot(self, name: str = "screenshot"):
        """截图保存。

        Args:
            name: 截图文件名（不含扩展名）。
        """
        os.makedirs("data", exist_ok=True)
        if self.driver:
            self.driver.save_screenshot(f"data/{name}.png")
        else:
            pyautogui.screenshot(f"data/{name}.png")

    # ==================== OCR 相关方法 ====================

    def _get_screenshot_bytes(self) -> bytes:
        """获取当前屏幕的截图字节数据。

        Returns:
            bytes: 截图的二进制数据。
        """
        if self.driver:
            import base64
            return base64.b64decode(self.driver.get_screenshot_as_base64())
        else:
            from io import BytesIO
            buffer = BytesIO()
            pyautogui.screenshot().save(buffer, format="PNG")
            return buffer.getvalue()

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

        pyautogui.click(x, y)
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

        pyautogui.click(x, y)
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
"""
RemotePage —— 页面操作封装。

封装 Playwright Page 的常用操作，记录操作日志，支持截图。
提供与现有 BasePage 兼容的接口。

Usage:
    from web.remote.browser import RemoteBrowser
    from web.remote.page import RemotePage

    browser = RemoteBrowser()
    browser.start()
    context = browser.new_context()
    page = context.new_page()

    remote_page = RemotePage(page)
    remote_page.navigate("https://example.com")
    remote_page.fill("input[name='username']", "user")
    remote_page.click("button[type='submit']")
"""

from dataclasses import dataclass
from typing import Optional, Any
import time

from playwright.sync_api import Page, Locator

from web.remote.logger import ActionLogger
from web.remote.screenshot import ScreenshotManager, ScreenshotData
from web.remote.task import Action
from web.remote.result import ActionResult


@dataclass
class PageState:
    """
    页面状态快照。

    Attributes:
        url: 当前 URL
        title: 页面标题
        timestamp: 快照时间
    """

    url: str
    title: str
    timestamp: str

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "title": self.title,
            "timestamp": self.timestamp,
        }


class RemotePage:
    """
    页面操作封装。

    封装 Playwright Page 的常用操作，记录操作日志，支持截图。
    可与现有 BasePage 配合使用。

    Attributes:
        page: Playwright Page 对象
        logger: 操作日志记录器
        screenshot_manager: 截图管理器
        session_id: 所属会话 ID
    """

    def __init__(
        self,
        page: Page,
        logger: Optional[ActionLogger] = None,
        screenshot_manager: Optional[ScreenshotManager] = None,
        session_id: str = "",
    ):
        """
        初始化 RemotePage。

        Args:
            page: Playwright Page 对象
            logger: 操作日志记录器
            screenshot_manager: 截图管理器
            session_id: 所属会话 ID
        """
        self._page = page
        self._logger = logger or ActionLogger(session_id=session_id)
        self._screenshot_manager = screenshot_manager or ScreenshotManager()
        self._session_id = session_id

    @property
    def page(self) -> Page:
        """获取内部 Playwright Page 对象。"""
        return self._page

    @property
    def logger(self) -> ActionLogger:
        return self._logger

    @property
    def session_id(self) -> str:
        return self._session_id

    @session_id.setter
    def session_id(self, value: str) -> None:
        self._session_id = value
        self._logger.session_id = value

    # ==================== 基础操作 ====================

    def navigate(self, url: str, wait_until: str = "load") -> str:
        """
        导航到指定 URL。

        Args:
            url: 目标 URL
            wait_until: 等待条件 (load/domcontentloaded/networkidle)

        Returns:
            str: 最终 URL（可能有重定向）
        """
        start_time = time.time()
        try:
            response = self._page.goto(url, wait_until=wait_until)
            final_url = self._page.url
            status = response.status if response else 200
            duration_ms = int((time.time() - start_time) * 1000)
            self._logger.log_navigation(url, status, duration_ms)
            return final_url
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            self._logger.log_error("navigate", e, {"url": url, "duration_ms": duration_ms})
            raise

    def click(
        self,
        selector: str,
        timeout: int = 30000,
        force: bool = False,
        no_wait_after: bool = False,
    ) -> None:
        """
        点击元素。

        Args:
            selector: 元素选择器
            timeout: 超时时间（毫秒）
            force: 是否跳过可操作性检查
            no_wait_after: 是否等待后续操作
        """
        start_time = time.time()
        try:
            self._page.click(selector, timeout=timeout, force=force, no_wait_after=no_wait_after)
            duration_ms = int((time.time() - start_time) * 1000)
            self._logger.log_click(selector, success=True, duration_ms=duration_ms)
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            self._logger.log_click(selector, success=False, error=str(e), duration_ms=duration_ms)
            raise

    def fill(self, selector: str, text: str, timeout: int = 30000) -> None:
        """
        向输入框填入文本（先清空）。

        Args:
            selector: 输入框选择器
            text: 要填入的文本
            timeout: 超时时间（毫秒）
        """
        start_time = time.time()
        try:
            self._page.fill(selector, text, timeout=timeout)
            duration_ms = int((time.time() - start_time) * 1000)
            self._logger.log_fill(selector, text, success=True, duration_ms=duration_ms)
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            self._logger.log_fill(selector, text, success=False, error=str(e), duration_ms=duration_ms)
            raise

    def type(self, selector: str, text: str, delay: int = 0, timeout: int = 30000) -> None:
        """
        逐字输入文本（不清空）。

        Args:
            selector: 输入框选择器
            text: 要输入的文本
            delay: 每个字符间的延迟（毫秒）
            timeout: 超时时间（毫秒）
        """
        start_time = time.time()
        try:
            self._page.type(selector, text, delay=delay, timeout=timeout)
            duration_ms = int((time.time() - start_time) * 1000)
            self._logger.log_fill(selector, text, success=True, duration_ms=duration_ms)
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            self._logger.log_fill(selector, text, success=False, error=str(e), duration_ms=duration_ms)
            raise

    def get_text(self, selector: str, timeout: int = 30000) -> str:
        """
        获取元素文本内容。

        Args:
            selector: 元素选择器
            timeout: 超时时间（毫秒）

        Returns:
            str: 元素的文本内容
        """
        start_time = time.time()
        try:
            text = self._page.text_content(selector, timeout=timeout) or ""
            duration_ms = int((time.time() - start_time) * 1000)
            self._logger.log_info("get_text", f"Got text from {selector}", {"text_length": len(text)})
            return text
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            self._logger.log_error("get_text", e, {"selector": selector, "duration_ms": duration_ms})
            raise

    def get_value(self, selector: str, timeout: int = 30000) -> str:
        """
        获取输入框的值。

        Args:
            selector: 输入框选择器
            timeout: 超时时间（毫秒）

        Returns:
            str: 输入框的值
        """
        return self._page.input_value(selector, timeout=timeout)

    def get_attribute(self, selector: str, name: str, timeout: int = 30000) -> Optional[str]:
        """
        获取元素属性。

        Args:
            selector: 元素选择器
            name: 属性名
            timeout: 超时时间（毫秒）

        Returns:
            str 或 None: 属性值
        """
        return self._page.get_attribute(selector, name, timeout=timeout)

    def is_visible(self, selector: str) -> bool:
        """
        判断元素是否可见。

        Args:
            selector: 元素选择器

        Returns:
            bool: 元素是否可见
        """
        return self._page.is_visible(selector)

    def is_enabled(self, selector: str) -> bool:
        """
        判断元素是否可用。

        Args:
            selector: 元素选择器

        Returns:
            bool: 元素是否可用
        """
        return self._page.is_enabled(selector)

    def is_checked(self, selector: str) -> bool:
        """
        判断复选框/单选框是否选中。

        Args:
            selector: 元素选择器

        Returns:
            bool: 是否选中
        """
        return self._page.is_checked(selector)

    def wait_for(self, selector: str, state: str = "visible", timeout: int = 30000) -> Locator:
        """
        等待元素出现。

        Args:
            selector: 元素选择器
            state: 等待状态 (visible/hidden/attached/detached)
            timeout: 超时时间（毫秒）

        Returns:
            Locator: 元素定位器
        """
        start_time = time.time()
        try:
            locator = self._page.wait_for_selector(selector, state=state, timeout=timeout)
            duration_ms = int((time.time() - start_time) * 1000)
            self._logger.log_wait(selector, timeout, success=True, duration_ms=duration_ms)
            return locator
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            self._logger.log_wait(selector, timeout, success=False, duration_ms=duration_ms)
            raise

    def wait_for_timeout(self, timeout: int) -> None:
        """
        等待指定时间。

        Args:
            timeout: 等待时间（毫秒）
        """
        self._page.wait_for_timeout(timeout)
        self._logger.log_info("wait", f"Waited {timeout}ms", {"timeout": timeout})

    def screenshot(self, name: Optional[str] = None, full_page: bool = False) -> ScreenshotData:
        """
        截图。

        Args:
            name: 截图名称
            full_page: 是否截取整个页面

        Returns:
            ScreenshotData: 截图数据
        """
        start_time = time.time()
        try:
            screenshot = self._screenshot_manager.capture(self._page, name=name, full_page=full_page)
            duration_ms = int((time.time() - start_time) * 1000)
            self._logger.log_screenshot(name or screenshot.name, success=True, duration_ms=duration_ms)
            return screenshot
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            self._logger.log_screenshot(name or "unknown", success=False, duration_ms=duration_ms)
            raise

    def evaluate(self, script: str, arg: Any = None) -> Any:
        """
        执行 JavaScript。

        Args:
            script: JavaScript 代码
            arg: 传递给脚本的参数

        Returns:
            Any: 脚本返回值
        """
        start_time = time.time()
        try:
            result = self._page.evaluate(script, arg)
            duration_ms = int((time.time() - start_time) * 1000)
            self._logger.log_info("evaluate", "Executed JavaScript", {"duration_ms": duration_ms})
            return result
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            self._logger.log_error("evaluate", e, {"duration_ms": duration_ms})
            raise

    # ==================== 高级操作 ====================

    def hover(self, selector: str, timeout: int = 30000) -> None:
        """
        悬停在元素上。

        Args:
            selector: 元素选择器
            timeout: 超时时间（毫秒）
        """
        self._page.hover(selector, timeout=timeout)
        self._logger.log_info("hover", f"Hovered on {selector}", {"selector": selector})

    def select_option(
        self,
        selector: str,
        value: Optional[str] = None,
        label: Optional[str] = None,
        index: Optional[int] = None,
        timeout: int = 30000,
    ) -> list[str]:
        """
        选择下拉框选项。

        Args:
            selector: 下拉框选择器
            value: 选项值
            label: 选项文本
            index: 选项索引
            timeout: 超时时间（毫秒）

        Returns:
            list[str]: 选中的选项值列表
        """
        option = {}
        if value is not None:
            option["value"] = value
        if label is not None:
            option["label"] = label
        if index is not None:
            option["index"] = index

        result = self._page.select_option(selector, **option, timeout=timeout)
        self._logger.log_info("select", f"Selected option in {selector}", {"selector": selector, "option": option})
        return result

    def check(self, selector: str, timeout: int = 30000) -> None:
        """
        勾选复选框。

        Args:
            selector: 复选框选择器
            timeout: 超时时间（毫秒）
        """
        self._page.check(selector, timeout=timeout)
        self._logger.log_info("check", f"Checked {selector}", {"selector": selector})

    def uncheck(self, selector: str, timeout: int = 30000) -> None:
        """
        取消勾选复选框。

        Args:
            selector: 复选框选择器
            timeout: 超时时间（毫秒）
        """
        self._page.uncheck(selector, timeout=timeout)
        self._logger.log_info("uncheck", f"Unchecked {selector}", {"selector": selector})

    def press(self, selector: str, key: str, timeout: int = 30000) -> None:
        """
        按下键盘按键。

        Args:
            selector: 元素选择器
            key: 按键（如 Enter, Tab, Escape, ArrowDown）
            timeout: 超时时间（毫秒）
        """
        self._page.press(selector, key, timeout=timeout)
        self._logger.log_info("press", f"Pressed {key} on {selector}", {"selector": selector, "key": key})

    def goto(self, url: str, **kwargs) -> str:
        """navigate 的别名，兼容 BasePage。"""
        return self.navigate(url, **kwargs)

    # ==================== 状态获取 ====================

    def get_page_state(self) -> PageState:
        """
        获取页面状态快照。

        Returns:
            PageState: 页面状态
        """
        return PageState(
            url=self._page.url,
            title=self._page.title(),
            timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
        )

    def get_url(self) -> str:
        """获取当前 URL。"""
        return self._page.url

    def get_title(self) -> str:
        """获取页面标题。"""
        return self._page.title()

    # ==================== 断言方法 ====================

    def expect_visible(self, selector: str, timeout: int = 30000) -> bool:
        """
        断言元素可见。

        Args:
            selector: 元素选择器
            timeout: 超时时间（毫秒）

        Returns:
            bool: 是否可见
        """
        try:
            self._page.wait_for_selector(selector, state="visible", timeout=timeout)
            self._logger.log_assertion("visible", selector, success=True)
            return True
        except Exception as e:
            self._logger.log_assertion("visible", selector, success=False)
            raise AssertionError(f"Element {selector} is not visible: {e}")

    def expect_text(self, selector: str, expected: str, timeout: int = 30000) -> bool:
        """
        断言元素包含指定文本。

        Args:
            selector: 元素选择器
            expected: 期望文本
            timeout: 超时时间（毫秒）

        Returns:
            bool: 是否匹配
        """
        try:
            actual = self._page.text_content(selector, timeout=timeout) or ""
            if expected not in actual:
                self._logger.log_assertion(
                    "text_contains", expected, actual=actual, success=False
                )
                raise AssertionError(f"Expected '{expected}' in '{actual}'")
            self._logger.log_assertion("text_contains", expected, actual=actual, success=True)
            return True
        except Exception as e:
            if isinstance(e, AssertionError):
                raise
            self._logger.log_assertion("text_contains", expected, success=False)
            raise

    def expect_url(self, expected: str) -> bool:
        """
        断言当前 URL 包含指定字符串。

        Args:
            expected: 期望 URL 片段

        Returns:
            bool: 是否匹配
        """
        actual = self._page.url
        if expected not in actual:
            self._logger.log_assertion("url", expected, actual=actual, success=False)
            raise AssertionError(f"Expected URL to contain '{expected}', got '{actual}'")
        self._logger.log_assertion("url", expected, actual=actual, success=True)
        return True

    # ==================== 关闭和清理 ====================

    def close(self) -> None:
        """关闭页面。"""
        try:
            self._page.close()
        except Exception:
            pass

    def __repr__(self) -> str:
        return f"RemotePage(url={self._page.url!r}, session_id={self._session_id!r})"
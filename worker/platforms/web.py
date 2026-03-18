"""
Web 平台执行引擎。

基于 Playwright 实现，支持 OCR/图像识别定位。
使用 async_api 以支持在 asyncio 环境中正确运行。
"""

import asyncio
import concurrent.futures
import logging
import sys
import threading
import time
from typing import Any, Dict, Optional, Set

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

from worker.platforms.base import PlatformManager
from worker.task import Action, ActionResult, ActionStatus
from worker.config import PlatformConfig

logger = logging.getLogger(__name__)

# 使用全局工作线程和事件循环
_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
_event_loop: Optional[asyncio.AbstractEventLoop] = None
_event_loop_lock = threading.Lock()
_event_loop_started = threading.Event()


def _run_async_worker():
    """工作线程函数，运行持久的事件循环。"""
    global _event_loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _event_loop = loop
    _event_loop_started.set()
    loop.run_forever()


# 启动工作线程
_worker_thread = threading.Thread(target=_run_async_worker, daemon=True)
_worker_thread.start()
_event_loop_started.wait()


def _run_async(coro):
    """在工作线程的事件循环中运行协程。"""
    global _event_loop
    if _event_loop is None or _event_loop.is_closed():
        raise RuntimeError("Event loop is not available")
    
    # 使用 run_coroutine_threadsafe 在工作线程的事件循环中运行
    future = asyncio.run_coroutine_threadsafe(coro, _event_loop)
    return future.result()


class WebPlatformManager(PlatformManager):
    """
    Web 平台管理器。

    使用 Playwright 控制 Web 浏览器，支持 OCR/图像识别定位。
    """

    # Web 平台特有动作
    SUPPORTED_ACTIONS: Set[str] = {"navigate", "start_app", "stop_app"}

    def __init__(self, config: PlatformConfig, ocr_client=None):
        super().__init__(config, ocr_client)

        self._playwright = None
        self._browser: Optional[Browser] = None
        # 会话管理：key="default", value={"browser", "context", "page"}
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self.headless = config.headless
        self.browser_type = config.browser_type
        self.timeout = config.timeout

    @property
    def platform(self) -> str:
        return "web"

    def start(self) -> None:
        """启动 Playwright 和浏览器。"""
        if self._started:
            return

        try:
            # 使用 _run_async 在同步环境中启动 async playwright
            _run_async(self._async_start())
            self._started = True
            logger.info(f"Web platform started (browser={self.browser_type}, headless={self.headless})")

        except Exception as e:
            logger.error(f"Failed to start Web platform: {e}")
            raise

    async def _async_start(self) -> None:
        """异步启动 Playwright 和浏览器。"""
        self._playwright = await async_playwright().start()

        # 选择浏览器类型
        if self.browser_type == "firefox":
            browser_launcher = self._playwright.firefox
        elif self.browser_type == "webkit":
            browser_launcher = self._playwright.webkit
        else:
            browser_launcher = self._playwright.chromium

        self._browser = await browser_launcher.launch(
            headless=self.headless,
            timeout=self.timeout,
        )

    def stop(self) -> None:
        """停止浏览器和 Playwright。"""
        # 关闭所有上下文
        for context_id in list(self._contexts.keys()):
            try:
                self.close_context(self._contexts[context_id])
            except Exception as e:
                logger.warning(f"Failed to close context: {e}")
        self._contexts.clear()

        # 关闭浏览器和 Playwright
        if self._browser or self._playwright:
            try:
                _run_async(self._async_stop())
            except Exception as e:
                logger.warning(f"Failed to stop Web platform: {e}")

        self._started = False
        logger.info("Web platform stopped")

    async def _async_stop(self) -> None:
        """异步停止浏览器和 Playwright。"""
        # 关闭浏览器
        if self._browser:
            await self._browser.close()
            self._browser = None

        # 停止 Playwright
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

    def is_available(self) -> bool:
        """检查平台是否可用。"""
        return self._started and self._browser is not None

    # ========== 会话管理方法 ==========

    def has_active_session(self, device_id: Optional[str] = None) -> bool:
        """检查是否有活跃的会话（page 存在且未关闭）。"""
        if "default" not in self._sessions:
            return False
        page = self._sessions["default"].get("page")
        if page is None:
            return False
        # 检查页面是否已关闭
        try:
            return not page.is_closed()
        except Exception:
            return False

    def get_session_context(self, device_id: Optional[str] = None) -> Any:
        """获取当前会话的上下文。"""
        session = self._sessions.get("default")
        if not session:
            return None
        page = session.get("page")
        if page is None:
            return None
        # 检查页面是否已关闭
        try:
            if page.is_closed():
                return None
        except Exception:
            return None
        return page

    def close_session(self, device_id: Optional[str] = None) -> None:
        """关闭会话（由 stop_app 调用）。"""
        session = self._sessions.get("default")
        if session:
            try:
                # 关闭 page 和 context
                page = session.get("page")
                if page:
                    _run_async(self._async_close_page(page))

                # 关闭 browser（完全关闭）
                if self._browser:
                    _run_async(self._browser.close())
                    self._browser = None

                # 清理 playwright
                if self._playwright:
                    _run_async(self._playwright.stop())
                    self._playwright = None

                self._sessions.clear()
                self._started = False
                logger.info("Web session closed")
            except Exception as e:
                logger.warning(f"Failed to close session: {e}")

    async def _async_close_page(self, page: Page) -> None:
        """异步关闭页面（不关闭 browser）。"""
        try:
            browser_context = page.context
            await page.close()
            if browser_context:
                await browser_context.close()
        except Exception as e:
            logger.warning(f"Failed to close page: {e}")

    def create_context(self, device_id: Optional[str] = None, options: Optional[Dict] = None) -> Any:
        """
        创建浏览器上下文（BrowserContext + Page）。

        如果已有活跃会话，复用已有的 page。

        Args:
            device_id: 不使用
            options: 上下文选项

        Returns:
            Page: Playwright Page 对象
        """
        if not self.is_available():
            raise RuntimeError("Web platform not started")

        # 检查是否有活跃会话，有则复用
        if self.has_active_session():
            existing_page = self.get_session_context()
            if existing_page:
                logger.info("Reusing existing Web context")
                return existing_page

        # 创建新会话
        page = _run_async(self._async_create_context(options or {}))

        # 存储到会话中
        context = page.context if page else None
        self._sessions["default"] = {
            "browser": self._browser,
            "context": context,
            "page": page,
        }

        logger.info(f"Web context created")
        return page

    async def _async_create_context(self, context_options: Dict) -> Page:
        """异步创建浏览器上下文。"""
        context = await self._browser.new_context(**context_options)
        page = await context.new_page()
        page.set_default_timeout(self.timeout)
        return page

    def close_context(self, context: Any, close_session: bool = False) -> None:
        """
        关闭浏览器上下文。

        Args:
            context: 执行上下文
            close_session: 是否关闭整个会话（True=关闭 browser，False=保持 page 打开）
        """
        if context and isinstance(context, Page):
            try:
                if close_session:
                    # 关闭整个会话（调用 close_session）
                    self.close_session()
                else:
                    # 不关闭 page，只打印日志
                    # page 保持打开状态，下次请求可以复用
                    logger.info("Web context detached (keeping page open for session)")
            except Exception as e:
                logger.error(f"Failed to close context: {e}")

    async def _async_close_context(self, page: Page) -> None:
        """异步关闭浏览器上下文。"""
        try:
            browser_context = page.context
            await page.close()
            if browser_context:
                await browser_context.close()
            logger.info("Web context closed")
        except Exception as e:
            logger.error(f"Failed to close context: {e}")

    def execute_action(self, context: Any, action: Action) -> ActionResult:
        """执行动作。"""
        start_time = time.time()

        # stop_app 不需要 context，可以直接处理
        if action.action_type == "stop_app":
            result = self._action_stop_app(None, action)
            duration_ms = int((time.time() - start_time) * 1000)
            result.duration_ms = duration_ms
            return result

        page: Page = context
        if not page:
            return ActionResult(
                index=0,
                action_type=action.action_type,
                status=ActionStatus.FAILED,
                error="Page context is invalid",
            )

        try:
            # 其他动作需要 page context
            if action.action_type == "start_app":
                result = self._action_start_app(page, action)
            elif action.action_type == "navigate":
                result = self._action_navigate(page, action)
            elif action.action_type == "ocr_click":
                result = self._action_ocr_click(page, action)
            elif action.action_type == "image_click":
                result = self._action_image_click(page, action)
            elif action.action_type == "click":
                result = self._action_click(page, action)
            elif action.action_type == "ocr_input":
                result = self._action_ocr_input(page, action)
            elif action.action_type == "input":
                result = self._action_input(page, action)
            elif action.action_type == "press":
                result = self._action_press(page, action)
            elif action.action_type == "swipe":
                result = self._action_swipe(page, action)
            elif action.action_type == "screenshot":
                result = self._action_screenshot(page, action)
            elif action.action_type == "wait":
                result = self._action_wait(page, action)
            elif action.action_type == "ocr_wait":
                result = self._action_ocr_wait(page, action)
            elif action.action_type == "image_wait":
                result = self._action_image_wait(page, action)
            elif action.action_type == "ocr_assert":
                result = self._action_ocr_assert(page, action)
            elif action.action_type == "image_assert":
                result = self._action_image_assert(page, action)
            elif action.action_type == "ocr_get_text":
                result = self._action_ocr_get_text(page, action)
            else:
                result = ActionResult(
                    index=0,
                    action_type=action.action_type,
                    status=ActionStatus.FAILED,
                    error=f"Unknown action type: {action.action_type}",
                )

            duration_ms = int((time.time() - start_time) * 1000)
            result.duration_ms = duration_ms

            return result

        except Exception as e:
            exc_type, exc_value, exc_tb = sys.exc_info()
            line_no = exc_tb.tb_lineno if exc_tb else "unknown"
            duration_ms = int((time.time() - start_time) * 1000)
            return ActionResult(
                index=0,
                action_type=action.action_type,
                status=ActionStatus.FAILED,
                duration_ms=duration_ms,
                error=f"Line {line_no}: {e}",
            )

    def get_screenshot(self, context: Any) -> bytes:
        """获取当前页面截图。"""
        page: Page = context
        if page:
            # 同步获取截图
            try:
                return _run_async(page.screenshot(type="png"))
            except Exception:
                return b""
        return b""

    # ========== 动作实现 ==========

    def _action_start_app(self, page: Page, action: Action) -> ActionResult:
        """启动/新建浏览器页面（实际上是通过新建 Page 来实现）。"""
        browser_name = action.value or self.browser_type

        # 如果已有会话，先关闭旧页面再创建新页面（不关闭浏览器）
        if self.has_active_session():
            logger.info("Closing existing page before starting new one")
            old_session = self._sessions.get("default")
            if old_session:
                old_page = old_session.get("page")
                if old_page:
                    try:
                        _run_async(self._async_close_page(old_page))
                    except Exception as e:
                        logger.warning(f"Failed to close old page: {e}")

        if self._browser:
            try:
                # 创建新的上下文和页面
                context = _run_async(self._browser.new_context())
                new_page = _run_async(context.new_page())
                new_page.set_default_timeout(self.timeout)

                # 更新会话存储
                self._sessions["default"] = {
                    "browser": self._browser,
                    "context": context,
                    "page": new_page,
                }

                # 更新 contexts 存储新的 page（兼容旧逻辑）
                for cid, p in list(self._contexts.items()):
                    if p == page:
                        self._contexts[cid] = new_page
                        break

                return ActionResult(
                    index=0,
                    action_type="start_app",
                    status=ActionStatus.SUCCESS,
                    output=f"Started new page for browser: {browser_name}",
                )
            except Exception as e:
                return ActionResult(
                    index=0,
                    action_type="start_app",
                    status=ActionStatus.FAILED,
                    error=f"Failed to start app: {e}",
                )
        else:
            return ActionResult(
                index=0,
                action_type="start_app",
                status=ActionStatus.FAILED,
                error="Browser not started, please call start() first",
            )

    def _action_stop_app(self, page: Page, action: Action) -> ActionResult:
        """关闭浏览器（结束会话）。"""
        if page or self.has_active_session():
            try:
                # 调用 close_session 完全关闭浏览器
                self.close_session()
                return ActionResult(
                    index=0,
                    action_type="stop_app",
                    status=ActionStatus.SUCCESS,
                    output="Closed browser session",
                )
            except Exception as e:
                return ActionResult(
                    index=0,
                    action_type="stop_app",
                    status=ActionStatus.FAILED,
                    error=f"Failed to stop app: {e}",
                )
        else:
            return ActionResult(
                index=0,
                action_type="stop_app",
                status=ActionStatus.FAILED,
                error="No session to close",
            )

    def _action_navigate(self, page: Page, action: Action) -> ActionResult:
        """导航到 URL。"""
        url = action.value
        if not url:
            return ActionResult(
                index=0,
                action_type="navigate",
                status=ActionStatus.FAILED,
                error="URL is required",
            )

        try:
            # 同步调用 async 方法
            _run_async(page.goto(url, timeout=action.timeout))
            return ActionResult(
                index=0,
                action_type="navigate",
                status=ActionStatus.SUCCESS,
                output=page.url,
            )
        except Exception as e:
            return ActionResult(
                index=0,
                action_type="navigate",
                status=ActionStatus.FAILED,
                error=str(e),
            )

    def _action_ocr_click(self, page: Page, action: Action) -> ActionResult:
        """OCR 文字点击。"""
        # 获取截图
        screenshot = _run_async(page.screenshot(type="png"))

        # 查找文字位置
        position = self._find_text_position(screenshot, action.value, action.match_mode)
        if not position:
            return ActionResult(
                index=0,
                action_type="ocr_click",
                status=ActionStatus.FAILED,
                error=f"Text not found: {action.value}",
            )

        # 应用偏移
        x, y = self._apply_offset(position[0], position[1], action.offset)

        # 记录 OCR 定位结果
        logger.debug(f"OCR located: text=\"{action.value}\", position=({x}, {y})")

        # 点击
        _run_async(page.mouse.click(x, y))

        return ActionResult(
            index=0,
            action_type="ocr_click",
            status=ActionStatus.SUCCESS,
            output=f"Clicked at ({x}, {y})",
        )

    def _action_image_click(self, page: Page, action: Action) -> ActionResult:
        """图像匹配点击。"""
        if not action.image_path:
            return ActionResult(
                index=0,
                action_type="image_click",
                status=ActionStatus.FAILED,
                error="image_path is required",
            )

        # 获取截图
        screenshot = _run_async(page.screenshot(type="png"))

        # 查找图像位置
        position = self._find_image_position(screenshot, action.image_path, action.threshold)
        if not position:
            return ActionResult(
                index=0,
                action_type="image_click",
                status=ActionStatus.FAILED,
                error=f"Image not found: {action.image_path}",
            )

        # 应用偏移
        x, y = self._apply_offset(position[0], position[1], action.offset)

        # 记录图像匹配结果
        logger.debug(f"Image matched: position=({x}, {y}), threshold={action.threshold or 0.8}")

        # 点击
        _run_async(page.mouse.click(x, y))

        return ActionResult(
            index=0,
            action_type="image_click",
            status=ActionStatus.SUCCESS,
            output=f"Clicked at ({x}, {y})",
        )

    def _action_click(self, page: Page, action: Action) -> ActionResult:
        """坐标点击。"""
        if action.x is None or action.y is None:
            return ActionResult(
                index=0,
                action_type="click",
                status=ActionStatus.FAILED,
                error="x and y coordinates are required",
            )

        _run_async(page.mouse.click(action.x, action.y))

        return ActionResult(
            index=0,
            action_type="click",
            status=ActionStatus.SUCCESS,
            output=f"Clicked at ({action.x}, {action.y})",
        )

    def _action_ocr_input(self, page: Page, action: Action) -> ActionResult:
        """OCR 文字附近输入。"""
        # 获取截图
        screenshot = _run_async(page.screenshot(type="png"))

        # 查找文字位置
        position = self._find_text_position(screenshot, action.value, action.match_mode)
        if not position:
            return ActionResult(
                index=0,
                action_type="ocr_input",
                status=ActionStatus.FAILED,
                error=f"Text not found: {action.value}",
            )

        # 应用偏移
        x, y = self._apply_offset(position[0], position[1], action.offset)

        # 记录 OCR 定位结果
        logger.debug(f"OCR located: text=\"{action.value}\", position=({x}, {y})")

        # 点击输入框
        _run_async(page.mouse.click(x, y))

        # 输入文本
        if action.value:
            _run_async(page.keyboard.type(action.value))

        return ActionResult(
            index=0,
            action_type="ocr_input",
            status=ActionStatus.SUCCESS,
            output=f"Input at ({x}, {y})",
        )

    def _action_input(self, page: Page, action: Action) -> ActionResult:
        """坐标输入。"""
        if action.x is None or action.y is None:
            return ActionResult(
                index=0,
                action_type="input",
                status=ActionStatus.FAILED,
                error="x and y coordinates are required",
            )

        # 点击
        _run_async(page.mouse.click(action.x, action.y))

        # 输入
        if action.value:
            _run_async(page.keyboard.type(action.value))

        return ActionResult(
            index=0,
            action_type="input",
            status=ActionStatus.SUCCESS,
            output=f"Input at ({action.x}, {action.y})",
        )

    def _action_press(self, page: Page, action: Action) -> ActionResult:
        """按键。"""
        if not action.value:
            return ActionResult(
                index=0,
                action_type="press",
                status=ActionStatus.FAILED,
                error="Key is required",
            )

        _run_async(page.keyboard.press(action.value))

        return ActionResult(
            index=0,
            action_type="press",
            status=ActionStatus.SUCCESS,
            output=f"Pressed: {action.value}",
        )

    def _action_swipe(self, page: Page, action: Action) -> ActionResult:
        """滑动（鼠标拖拽）。"""
        if action.x is None or action.y is None:
            return ActionResult(
                index=0,
                action_type="swipe",
                status=ActionStatus.FAILED,
                error="Start coordinates are required",
            )

        end_x = action.end_x if action.end_x is not None else action.x
        end_y = action.end_y if action.end_y is not None else action.y

        _run_async(page.mouse.move(action.x, action.y))
        _run_async(page.mouse.down())
        _run_async(page.mouse.move(end_x, end_y))
        _run_async(page.mouse.up())

        return ActionResult(
            index=0,
            action_type="swipe",
            status=ActionStatus.SUCCESS,
            output=f"Swiped from ({action.x}, {action.y}) to ({end_x}, {end_y})",
        )

    def _action_screenshot(self, page: Page, action: Action) -> ActionResult:
        """截图。"""
        screenshot = _run_async(page.screenshot(type="png"))
        screenshot_base64 = self._bytes_to_base64(screenshot)

        name = action.value or "screenshot"

        return ActionResult(
            index=0,
            action_type="screenshot",
            status=ActionStatus.SUCCESS,
            output=name,
            screenshot=screenshot_base64,
        )

    def _action_wait(self, page: Page, action: Action) -> ActionResult:
        """固定等待。"""
        wait_time = action.wait or int(action.value or 1000)
        self._wait(wait_time)

        return ActionResult(
            index=0,
            action_type="wait",
            status=ActionStatus.SUCCESS,
            output=f"Waited {wait_time}ms",
        )

    def _action_ocr_wait(self, page: Page, action: Action) -> ActionResult:
        """等待文字出现。"""
        start_time = time.time()
        timeout = action.timeout / 1000

        while time.time() - start_time < timeout:
            screenshot = _run_async(page.screenshot(type="png"))
            position = self._find_text_position(screenshot, action.value, action.match_mode)

            if position:
                return ActionResult(
                    index=0,
                    action_type="ocr_wait",
                    status=ActionStatus.SUCCESS,
                    output=f"Text appeared: {action.value}",
                )

            time.sleep(0.5)

        return ActionResult(
            index=0,
            action_type="ocr_wait",
            status=ActionStatus.FAILED,
            error=f"Text not appeared within timeout: {action.value}",
        )

    def _action_image_wait(self, page: Page, action: Action) -> ActionResult:
        """等待图像出现。"""
        if not action.image_path:
            return ActionResult(
                index=0,
                action_type="image_wait",
                status=ActionStatus.FAILED,
                error="image_path is required",
            )

        start_time = time.time()
        timeout = action.timeout / 1000

        while time.time() - start_time < timeout:
            screenshot = _run_async(page.screenshot(type="png"))
            position = self._find_image_position(screenshot, action.image_path, action.threshold)

            if position:
                return ActionResult(
                    index=0,
                    action_type="image_wait",
                    status=ActionStatus.SUCCESS,
                    output=f"Image appeared: {action.image_path}",
                )

            time.sleep(0.5)

        return ActionResult(
            index=0,
            action_type="image_wait",
            status=ActionStatus.FAILED,
            error=f"Image not appeared within timeout: {action.image_path}",
        )

    def _action_ocr_assert(self, page: Page, action: Action) -> ActionResult:
        """OCR 文字断言。"""
        screenshot = _run_async(page.screenshot(type="png"))
        position = self._find_text_position(screenshot, action.value, action.match_mode)

        if position:
            return ActionResult(
                index=0,
                action_type="ocr_assert",
                status=ActionStatus.SUCCESS,
                output=f"Text found: {action.value}",
            )
        else:
            return ActionResult(
                index=0,
                action_type="ocr_assert",
                status=ActionStatus.FAILED,
                error=f"Text not found: {action.value}",
            )

    def _action_image_assert(self, page: Page, action: Action) -> ActionResult:
        """图像断言。"""
        if not action.image_path:
            return ActionResult(
                index=0,
                action_type="image_assert",
                status=ActionStatus.FAILED,
                error="image_path is required",
            )

        screenshot = _run_async(page.screenshot(type="png"))
        position = self._find_image_position(screenshot, action.image_path, action.threshold)

        if position:
            return ActionResult(
                index=0,
                action_type="image_assert",
                status=ActionStatus.SUCCESS,
                output=f"Image found: {action.image_path}",
            )
        else:
            return ActionResult(
                index=0,
                action_type="image_assert",
                status=ActionStatus.FAILED,
                error=f"Image not found: {action.image_path}",
            )

    def _action_ocr_get_text(self, page: Page, action: Action) -> ActionResult:
        """获取 OCR 文字区域内容。"""
        if not self.ocr_client:
            return ActionResult(
                index=0,
                action_type="ocr_get_text",
                status=ActionStatus.FAILED,
                error="OCR client not available",
            )

        screenshot = _run_async(page.screenshot(type="png"))
        texts = self.ocr_client.recognize(screenshot)

        all_text = " ".join([t.text for t in texts])

        return ActionResult(
            index=0,
            action_type="ocr_get_text",
            status=ActionStatus.SUCCESS,
            output=all_text,
        )
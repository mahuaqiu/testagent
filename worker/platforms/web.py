"""
Web 平台执行引擎。

基于 Playwright 实现，支持 OCR/图像识别定位。
使用 async_api 以支持在 asyncio 环境中正确运行。
"""

import asyncio
import concurrent.futures
import logging
import os
import shutil
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Set

from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Playwright

from worker.platforms.base import PlatformManager
from worker.task import Action, ActionResult, ActionStatus
from worker.config import PlatformConfig
from worker.actions import ActionRegistry

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

        self._playwright: Optional[Playwright] = None
        self._browser_context: Optional[BrowserContext] = None  # 持久化浏览器上下文
        self._current_page: Optional[Page] = None  # 当前页面，用于基础能力操作
        # 会话管理：key="default", value={"context", "page"}
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self.headless = config.headless
        self.browser_type = config.browser_type
        self.timeout = config.timeout
        self.ignore_https_errors = config.ignore_https_errors
        self.permissions = config.permissions
        self.user_data_dir = config.user_data_dir
        self.clear_profile_on_start = config.clear_profile_on_start  # 启动前清理 Default 目录
        self.request_blacklist = config.request_blacklist  # 请求黑名单

    def _get_app_dir(self) -> str:
        """获取应用目录（打包后使用 EXE 目录）。"""
        if getattr(sys, 'frozen', False):
            return os.path.dirname(sys.executable)
        # 开发模式使用项目根目录
        return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    def _get_user_data_dir(self) -> str:
        """获取用户数据目录的绝对路径。"""
        app_dir = self._get_app_dir()
        return os.path.join(app_dir, self.user_data_dir)

    def _clear_profile_data(self, user_data_dir: str) -> None:
        """清理表单相关数据（保留 HTTP 缓存和其他非敏感数据）。"""
        default_dir = os.path.join(user_data_dir, "Default")
        if not os.path.exists(default_dir):
            return

        # 需要删除相关文件和目录
        form_items = [
            "Local Storage"
        ]

        for item in form_items:
            item_path = os.path.join(default_dir, item)
            if os.path.exists(item_path):
                try:
                    if os.path.isdir(item_path):
                        shutil.rmtree(item_path, ignore_errors=True)
                    else:
                        os.remove(item_path)
                    logger.debug(f"Removed form data: {item}")
                except Exception as e:
                    logger.warning(f"Failed to remove {item}: {e}")

        logger.info(f"Cleared form/profile data in {default_dir}, kept cache dirs")

        logger.info(f"Cleared form/profile data in {default_dir}, kept cache dirs")

    @property
    def platform(self) -> str:
        return "web"

    # ========== 生命周期管理 ==========

    def start(self) -> None:
        """启动 Playwright 和浏览器。"""
        if self._started:
            return

        try:
            _run_async(self._async_start())
            self._started = True
            logger.info(f"Web platform started (browser={self.browser_type}, headless={self.headless})")
        except Exception as e:
            logger.error(f"Failed to start Web platform: {e}")
            raise

    async def _async_start(self) -> None:
        """异步启动 Playwright 和浏览器（使用持久化用户数据目录）。"""
        self._playwright = await async_playwright().start()

        # 获取用户数据目录
        user_data_dir = self._get_user_data_dir()

        # 确保用户数据目录存在
        if not os.path.exists(user_data_dir):
            os.makedirs(user_data_dir, exist_ok=True)

        # 如果配置了启动前清理，删除 Default 目录数据（保留 Cache）
        if self.clear_profile_on_start:
            self._clear_profile_data(user_data_dir)

        # 选择浏览器类型
        if self.browser_type == "firefox":
            browser_launcher = self._playwright.firefox
        elif self.browser_type == "webkit":
            browser_launcher = self._playwright.webkit
        else:
            browser_launcher = self._playwright.chromium

        # 使用持久化上下文启动浏览器（保留缓存、Cookie等）
        context_options = {
            "headless": self.headless,
        }
        if self.ignore_https_errors:
            context_options["ignore_https_errors"] = True
        if self.permissions:
            context_options["permissions"] = self.permissions

        self._browser_context = await browser_launcher.launch_persistent_context(
            user_data_dir=user_data_dir,
            **context_options
        )

        # 在 context 级别设置请求黑名单拦截（对所有页面生效）
        if self.request_blacklist:
            await self._setup_context_blacklist()

        # 关闭可能已存在的空白页面，避免它们加载任何内容
        for page in self._browser_context.pages:
            try:
                # 如果页面是空白页（about:blank），可以保留
                if page.url == "about:blank":
                    continue
                await page.close()
                logger.info("Closed existing page before route setup")
            except Exception as e:
                logger.warning(f"Failed to close existing page: {e}")

        logger.info(f"Browser started, user_data_dir={user_data_dir}, clear_profile={self.clear_profile_on_start}")

    async def _setup_context_blacklist(self) -> None:
        """在 context 级别设置请求黑名单拦截。"""
        # 拦截所有请求，然后在 handler 中判断
        async def handler(route):
            url = route.request.url
            # 检查是否匹配黑名单
            for item in self.request_blacklist:
                pattern = item.get("pattern", "")
                action = item.get("action", "abort")
                if pattern in url:
                    if action == "abort":
                        logger.info(f"[Blacklist] Aborted: {url}")
                        await route.abort()
                        return
                    elif action == "404":
                        logger.info(f"[Blacklist] 404: {url}")
                        await route.fulfill(status=404, body="Not Found")
                        return
                    elif action == "empty":
                        logger.info(f"[Blacklist] Empty: {url}")
                        await route.fulfill(status=200, body="", content_type="application/javascript")
                        return
            # 不在黑名单中，继续请求
            await route.continue_()

        await self._browser_context.route("**", handler)
        patterns = [item.get("pattern", "") for item in self.request_blacklist]
        logger.info(f"Set up context blacklist拦截所有请求，黑名单: {patterns}")

    def stop(self) -> None:
        """停止浏览器和 Playwright。"""
        for context_id in list(self._contexts.keys()):
            try:
                self.close_context(self._contexts[context_id])
            except Exception as e:
                logger.warning(f"Failed to close context: {e}")
        self._contexts.clear()

        if self._browser or self._playwright:
            try:
                _run_async(self._async_stop())
            except Exception as e:
                logger.warning(f"Failed to stop Web platform: {e}")

        self._started = False
        logger.info("Web platform stopped")

    async def _async_stop(self) -> None:
        """异步停止浏览器和 Playwright。"""
        if self._browser_context:
            await self._browser_context.close()
            self._browser_context = None

        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

    def is_available(self) -> bool:
        """检查平台是否可用。"""
        return self._started and self._browser_context is not None

    # ========== 会话管理方法 ==========

    def has_active_session(self, device_id: Optional[str] = None) -> bool:
        """检查是否有活跃的会话（page 存在且未关闭）。"""
        if "default" not in self._sessions:
            return False
        page = self._sessions["default"].get("page")
        if page is None:
            return False
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
                page = session.get("page")
                if page:
                    _run_async(self._async_close_page(page))

                if self._browser_context:
                    _run_async(self._browser_context.close())
                    self._browser_context = None

                if self._playwright:
                    _run_async(self._playwright.stop())
                    self._playwright = None

                self._sessions.clear()
                self._started = False
                logger.info("Web session closed")
            except Exception as e:
                logger.warning(f"Failed to close session: {e}")

    async def _async_close_page(self, page: Page) -> None:
        """异步关闭页面。"""
        try:
            browser_context = page.context
            await page.close()
            if browser_context:
                await browser_context.close()
        except Exception as e:
            logger.warning(f"Failed to close page: {e}")

    # ========== 上下文管理 ==========

    def create_context(self, device_id: Optional[str] = None, options: Optional[Dict] = None) -> Any:
        """创建浏览器页面（基于持久化上下文）。"""
        if not self.is_available():
            raise RuntimeError("Web platform not started")

        if self.has_active_session():
            existing_page = self.get_session_context()
            if existing_page:
                logger.info("Reusing existing Web context")
                self._current_page = existing_page
                return existing_page

        page = _run_async(self._async_create_page())

        self._sessions["default"] = {
            "context": self._browser_context,
            "page": page,
        }
        self._current_page = page

        logger.info("Web context created")
        return page

    async def _async_create_page(self) -> Page:
        """异步获取或创建页面。"""
        pages = self._browser_context.pages
        if pages:
            page = pages[0]
        else:
            page = await self._browser_context.new_page()
        page.set_default_timeout(self.timeout)
        return page

    def close_context(self, context: Any, close_session: bool = False) -> None:
        """关闭浏览器上下文。"""
        if context and isinstance(context, Page):
            try:
                if close_session:
                    self.close_session()
                else:
                    logger.info("Web context detached (keeping page open for session)")
            except Exception as e:
                logger.error(f"Failed to close context: {e}")

    # ========== 基础能力实现 ==========

    def click(self, x: int, y: int, context: Any = None) -> None:
        """点击指定坐标。"""
        page = context or self._current_page
        if page:
            _run_async(page.mouse.click(x, y))

    def input_text(self, text: str, context: Any = None) -> None:
        """输入文本。"""
        page = context or self._current_page
        if page:
            _run_async(page.keyboard.type(text))

    def swipe(self, start_x: int, start_y: int, end_x: int, end_y: int, context: Any = None) -> None:
        """滑动/拖拽。"""
        page = context or self._current_page
        if page:
            _run_async(page.mouse.move(start_x, start_y))
            _run_async(page.mouse.down())
            _run_async(page.mouse.move(end_x, end_y))
            _run_async(page.mouse.up())

    def press(self, key: str, context: Any = None) -> None:
        """按键。"""
        page = context or self._current_page
        if page:
            _run_async(page.keyboard.press(key))

    def take_screenshot(self, context: Any = None) -> bytes:
        """获取截图。"""
        page = context or self._current_page
        if page:
            try:
                return _run_async(page.screenshot(type="png"))
            except Exception:
                return b""
        return b""

    def get_screenshot(self, context: Any) -> bytes:
        """获取当前页面截图（兼容旧接口）。"""
        return self.take_screenshot(context)

    # ========== 动作执行 ==========

    def execute_action(self, context: Any, action: Action) -> ActionResult:
        """执行动作。"""
        start_time = time.time()

        try:
            # 更新当前页面引用
            if context and isinstance(context, Page):
                self._current_page = context

            # 平台特有动作
            if action.action_type == "start_app":
                result = self._action_start_app(action)
            elif action.action_type == "stop_app":
                result = self._action_stop_app(action)
            elif action.action_type == "navigate":
                result = self._action_navigate(action, context)
            else:
                # 使用 ActionRegistry 执行通用动作
                executor = ActionRegistry.get(action.action_type)
                if executor:
                    result = executor.execute(self, action, context)
                else:
                    result = ActionResult(
                        number=0,
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
                number=0,
                action_type=action.action_type,
                status=ActionStatus.FAILED,
                duration_ms=duration_ms,
                error=f"Line {line_no}: {e}",
            )

    # ========== 平台特有动作实现 ==========

    def _browser_context_is_valid(self) -> bool:
        """检查浏览器上下文是否仍然有效（未被手动关闭）。"""
        if not self._browser_context:
            return False
        try:
            # 直接尝试创建一个新页面来验证 context 是否有效
            # 这是最可靠的方式
            test_page = _run_async(self._browser_context.new_page())
            # 如果成功，立即关闭测试页面
            _run_async(test_page.close())
            return True
        except Exception as e:
            logger.info(f"Browser context check failed: {e}")
            return False

    def _reset_browser_state(self) -> None:
        """重置浏览器状态，准备重新启动。"""
        self._started = False
        self._browser_context = None
        self._playwright = None
        self._sessions.clear()
        self._current_page = None

    def _action_start_app(self, action: Action) -> ActionResult:
        """启动/新建浏览器页面。"""
        browser_name = action.value or self.browser_type

        # 尝试复用已有会话
        if self.has_active_session():
            existing_page = self.get_session_context()
            if existing_page:
                try:
                    if not existing_page.is_closed():
                        self._current_page = existing_page
                        logger.info("Reusing existing browser session for start_app")
                        return ActionResult(
                            number=0,
                            action_type="start_app",
                            status=ActionStatus.SUCCESS,
                            output=f"Reused existing page for browser: {browser_name}",
                            context=existing_page,
                        )
                except Exception:
                    logger.info("Existing page check failed, will restart browser")

        # 启动或重试逻辑（最多 2 次）
        max_retries = 2
        for retry in range(max_retries):
            # 检查并重置无效的 context
            if not self._browser_context_is_valid():
                logger.info(f"Browser context is invalid, resetting... (retry {retry + 1})")
                self._reset_browser_state()

            # 启动浏览器
            if not self._browser_context:
                try:
                    _run_async(self._async_start())
                    self._started = True
                    logger.info(f"Browser started: {browser_name}")
                except Exception as e:
                    if retry < max_retries - 1:
                        logger.warning(f"Browser start failed, retrying: {e}")
                        continue
                    return ActionResult(
                        number=0,
                        action_type="start_app",
                        status=ActionStatus.FAILED,
                        error=f"Failed to start browser: {e}",
                    )

            # 创建或获取页面
            try:
                pages = self._browser_context.pages
                # 找一个未关闭的页面
                new_page = None
                for p in pages:
                    try:
                        if not p.is_closed():
                            new_page = p
                            break
                    except Exception:
                        continue

                if not new_page:
                    new_page = _run_async(self._browser_context.new_page())

                new_page.set_default_timeout(self.timeout)

                self._sessions["default"] = {
                    "context": self._browser_context,
                    "page": new_page,
                }
                self._current_page = new_page

                return ActionResult(
                    number=0,
                    action_type="start_app",
                    status=ActionStatus.SUCCESS,
                    output=f"Started page for browser: {browser_name}",
                    context=new_page,
                )
            except Exception as e:
                logger.warning(f"Failed to get/create page: {e}")
                # 创建失败，重置后重试
                self._reset_browser_state()
                if retry < max_retries - 1:
                    logger.info("Retrying...")
                    continue
                return ActionResult(
                    number=0,
                    action_type="start_app",
                    status=ActionStatus.FAILED,
                    error=f"Failed to start app: {e}",
                )

        return ActionResult(
            number=0,
            action_type="start_app",
            status=ActionStatus.FAILED,
            error="Unexpected state in start_app",
        )

    def _action_stop_app(self, action: Action) -> ActionResult:
        """关闭浏览器。"""
        if self.has_active_session():
            try:
                self.close_session()
                return ActionResult(
                    number=0,
                    action_type="stop_app",
                    status=ActionStatus.SUCCESS,
                    output="Closed browser session",
                )
            except Exception as e:
                return ActionResult(
                    number=0,
                    action_type="stop_app",
                    status=ActionStatus.FAILED,
                    error=f"Failed to stop app: {e}",
                )
        else:
            return ActionResult(
                number=0,
                action_type="stop_app",
                status=ActionStatus.FAILED,
                error="No session to close",
            )

    def _action_navigate(self, action: Action, context: Any = None) -> ActionResult:
        """导航到 URL。"""
        url = action.value
        if not url:
            return ActionResult(
                number=0,
                action_type="navigate",
                status=ActionStatus.FAILED,
                error="URL is required",
            )

        page = context or self._current_page
        if not page:
            return ActionResult(
                number=0,
                action_type="navigate",
                status=ActionStatus.FAILED,
                error="No active page",
            )

        try:
            _run_async(page.goto(url, timeout=action.timeout))
            return ActionResult(
                number=0,
                action_type="navigate",
                status=ActionStatus.SUCCESS,
                output=page.url,
            )
        except Exception as e:
            return ActionResult(
                number=0,
                action_type="navigate",
                status=ActionStatus.FAILED,
                error=str(e),
            )
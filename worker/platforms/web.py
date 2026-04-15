"""
Web 平台执行引擎。

基于 Playwright 实现，支持 OCR/图像识别定位。
使用 async_api 以支持在 asyncio 环境中正确运行。
支持系统级操作（pyautogui）处理原生对话框等场景。
"""

import asyncio
import base64
import concurrent.futures
import io
import logging
import os
import shutil
import sys
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional, Set

from PIL import Image
from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Playwright

from worker.platforms.base import PlatformManager
from worker.task import Action, ActionResult, ActionStatus
from worker.config import PlatformConfig
from worker.actions import ActionRegistry

logger = logging.getLogger(__name__)

try:
    import mss
    import pyautogui
    SYSTEM_LEVEL_AVAILABLE = True
except ImportError:
    SYSTEM_LEVEL_AVAILABLE = False

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
    SUPPORTED_ACTIONS: Set[str] = {"navigate", "start_app", "stop_app", "get_token", "new_page", "switched_page", "close_page"}

    def __init__(self, config: PlatformConfig, ocr_client=None):
        super().__init__(config, ocr_client)

        self._playwright: Optional[Playwright] = None
        self._browser_context: Optional[BrowserContext] = None  # 持久化浏览器上下文
        self._current_page: Optional[Page] = None  # 当前页面，用于基础能力操作
        self._current_level: str = "browser"  # 当前执行层级："browser" 或 "system"
        self._current_monitor: int = 1  # 当前截取的显示器：1 或 2
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

        # Token 捕获
        self._token_headers: List[str] = config.token_headers or []
        self._captured_tokens: Dict[str, str] = {}  # 存储捕获的 token

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

        # 选择浏览器类型和启动器
        # Playwright 的 browser_type 只有 chromium/firefox/webkit 三种
        # 要使用系统 Chrome/Edge，需要用 chromium 启动器 + channel 参数
        if self.browser_type == "firefox":
            browser_launcher = self._playwright.firefox
        elif self.browser_type == "webkit":
            browser_launcher = self._playwright.webkit
        else:
            # chromium 类型（包括 chrome、msedge、chromium）
            browser_launcher = self._playwright.chromium

        # 使用持久化上下文启动浏览器（保留缓存、Cookie等）
        context_options = {
            "headless": self.headless,
        }

        # 系统浏览器支持：使用 channel 参数指定系统安装的 Chrome/Edge
        # browser_type='chrome' -> channel='chrome' (使用系统 Chrome)
        # browser_type='msedge' -> channel='msedge' (使用系统 Edge)
        # browser_type='chromium' -> 无 channel (使用 Playwright 内置 Chromium)
        if self.browser_type == "chrome":
            context_options["channel"] = "chrome"
            logger.info("Using system Chrome browser")
        elif self.browser_type == "msedge" or self.browser_type == "edge":
            context_options["channel"] = "msedge"
            logger.info("Using system Edge browser")

        if self.ignore_https_errors:
            context_options["ignore_https_errors"] = True
        if self.permissions:
            context_options["permissions"] = self.permissions

        self._browser_context = await browser_launcher.launch_persistent_context(
            user_data_dir=user_data_dir,
            **context_options
        )

        # 设置 Token 捕获监听（必须最早设置，否则可能错过响应）
        if self._token_headers:
            self._setup_token_capture()

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

    def _setup_token_capture(self) -> None:
        """设置请求头 Token 捕获监听。"""
        async def on_request(request):
            headers = request.headers
            for header_name in self._token_headers:
                # HTTP headers 在 Playwright 中是小写的
                value = headers.get(header_name.lower())
                if value:
                    self._captured_tokens[header_name] = value
                    logger.debug(f"Captured token: {header_name}={value}")

        self._browser_context.on("request", on_request)
        logger.info(f"Token capture enabled for headers: {self._token_headers}")

    def get_captured_tokens(self) -> Dict[str, str]:
        """返回捕获的 tokens dict 副本。"""
        return dict(self._captured_tokens)

    async def _setup_context_blacklist(self) -> None:
        """在 context 级别设置请求黑名单拦截。"""
        # 拦截所有请求，然后在 handler 中判断
        async def handler(route):
            try:
                url = route.request.url

                # 只处理 http/https 请求，跳过特殊 URL（data:, about:, blob: 等）
                if not url.startswith(("http://", "https://")):
                    await route.continue_()
                    return

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
            except Exception as e:
                # 异常时必须继续请求，否则请求会永远 pending
                logger.warning(f"[Blacklist] Handler error: {e}, forcing continue")
                try:
                    await route.continue_()
                except Exception:
                    # 如果 continue 也失败，尝试 abort
                    try:
                        await route.abort()
                    except Exception:
                        pass  # 无法处理，忽略

        await self._browser_context.route("**", handler)
        patterns = [item.get("pattern", "") for item in self.request_blacklist]
        logger.info(f"Set up context blacklist，黑名单: {patterns}")

    def stop(self) -> None:
        """停止浏览器和 Playwright。"""
        for context_id in list(self._contexts.keys()):
            try:
                self.close_context(self._contexts[context_id])
            except Exception as e:
                logger.warning(f"Failed to close context: {e}")
        self._contexts.clear()

        if self._browser_context or self._playwright:
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

    def click(self, x: int, y: int, context: Any = None, level: str = None) -> None:
        """点击指定坐标。根据 _current_level 决定使用 Playwright 还是系统级操作。"""
        effective_level = level or self._current_level
        if effective_level == "system":
            self._system_click(x, y)
            return
        page = context or self._current_page
        if page:
            _run_async(page.mouse.click(x, y))

    def _system_click(self, x: int, y: int) -> None:
        """系统级点击（使用 pyautogui）。"""
        if not SYSTEM_LEVEL_AVAILABLE:
            raise RuntimeError("System-level operations not available (mss/pyautogui not installed)")
        pyautogui.click(x, y)
        logger.debug(f"System-level click at ({x}, {y})")

    def double_click(self, x: int, y: int, context: Any = None, level: str = None) -> None:
        """双击指定坐标。"""
        effective_level = level or self._current_level
        if effective_level == "system":
            self._system_double_click(x, y)
            return
        page = context or self._current_page
        if page:
            _run_async(page.mouse.click(x, y, click_count=2))

    def _system_double_click(self, x: int, y: int) -> None:
        """系统级双击（使用 pyautogui）。"""
        if not SYSTEM_LEVEL_AVAILABLE:
            raise RuntimeError("System-level operations not available")
        pyautogui.doubleClick(x, y)
        logger.debug(f"System-level double click at ({x}, {y})")

    def move(self, x: int, y: int, context: Any = None, level: str = None) -> None:
        """移动鼠标到指定坐标。"""
        effective_level = level or self._current_level
        if effective_level == "system":
            self._system_move(x, y)
            return
        page = context or self._current_page
        if page:
            _run_async(page.mouse.move(x, y))

    def _system_move(self, x: int, y: int) -> None:
        """系统级移动鼠标（使用 pyautogui）。"""
        if not SYSTEM_LEVEL_AVAILABLE:
            raise RuntimeError("System-level operations not available")
        pyautogui.moveTo(x, y)
        logger.debug(f"System-level move to ({x}, {y})")

    def input_text(self, text: str, context: Any = None, level: str = None) -> None:
        """输入文本。"""
        effective_level = level or self._current_level
        if effective_level == "system":
            self._system_input(text)
            return
        page = context or self._current_page
        if page:
            _run_async(page.keyboard.type(text))

    def _system_input(self, text: str) -> None:
        """系统级输入文本（使用 pyautogui）。"""
        if not SYSTEM_LEVEL_AVAILABLE:
            raise RuntimeError("System-level operations not available")
        pyautogui.write(text)
        logger.debug(f"System-level input: {text}")

    def swipe(self, start_x: int, start_y: int, end_x: int, end_y: int, context: Any = None, level: str = None) -> None:
        """滑动/拖拽。"""
        effective_level = level or self._current_level
        if effective_level == "system":
            self._system_drag(start_x, start_y, end_x, end_y)
            return
        page = context or self._current_page
        if page:
            _run_async(page.mouse.move(start_x, start_y))
            _run_async(page.mouse.down())
            _run_async(page.mouse.move(end_x, end_y))
            _run_async(page.mouse.up())

    def _system_drag(self, start_x: int, start_y: int, end_x: int, end_y: int) -> None:
        """系统级拖拽（使用 pyautogui）。"""
        if not SYSTEM_LEVEL_AVAILABLE:
            raise RuntimeError("System-level operations not available")
        pyautogui.moveTo(start_x, start_y)
        pyautogui.drag(end_x - start_x, end_y - start_y)
        logger.debug(f"System-level drag from ({start_x}, {start_y}) to ({end_x}, {end_y})")

    def press(self, key: str, context: Any = None, level: str = None) -> None:
        """按键。"""
        effective_level = level or self._current_level
        if effective_level == "system":
            self._system_press(key)
            return
        page = context or self._current_page
        if page:
            _run_async(page.keyboard.press(key))

    def _system_press(self, key: str) -> None:
        """系统级按键（使用 pyautogui）。"""
        if not SYSTEM_LEVEL_AVAILABLE:
            raise RuntimeError("System-level operations not available")
        # 转换 Playwright 键名到 pyautogui 键名
        key_map = {
            "Enter": "enter",
            "Tab": "tab",
            "Escape": "escape",
            "Backspace": "backspace",
            "Delete": "delete",
            "ArrowUp": "up",
            "ArrowDown": "down",
            "ArrowLeft": "left",
            "ArrowRight": "right",
            "Control": "ctrl",
            "Alt": "alt",
            "Shift": "shift",
            "Meta": "win",  # Windows 键
        }
        pyautogui_key = key_map.get(key, key.lower())
        pyautogui.press(pyautogui_key)
        logger.debug(f"System-level press: {key} -> {pyautogui_key}")

    def take_screenshot(self, context: Any = None, level: str = None, monitor: int = None) -> bytes:
        """获取截图。根据 _current_level 决定使用 Playwright 还是系统级截图。"""
        effective_level = level or self._current_level
        effective_monitor = monitor or self._current_monitor
        logger.debug(f"take_screenshot: level={effective_level}, monitor={effective_monitor}")
        if effective_level == "system":
            return self._take_system_screenshot(effective_monitor)
        page = context or self._current_page
        if page:
            try:
                return _run_async(page.screenshot(type="png"))
            except Exception:
                return b""
        return b""

    def _take_system_screenshot(self, monitor: int = None) -> bytes:
        """系统级截图（使用 mss，截取指定显示器）。

        显示器编号规则（与用户直觉一致）：
        - monitor=1: 主屏幕（left=0 的显示器）
        - monitor=2: 副屏幕（另一个显示器）

        注意：mss 库的原始编号顺序与 Windows 主屏幕设置无关，
        这里做了映射处理使其符合用户直觉。
        """
        if not SYSTEM_LEVEL_AVAILABLE:
            raise RuntimeError("System-level operations not available (mss/pyautogui not installed)")
        try:
            effective_monitor = monitor or self._current_monitor
            logger.info(f"Taking system-level screenshot (mss), monitor={effective_monitor}")
            with mss.mss() as sct:
                monitors = sct.monitors
                logger.debug(f"Available monitors: {len(monitors)} total")
                for i, m in enumerate(monitors):
                    logger.debug(f"  monitor[{i}]: {m}")

                # 重新映射显示器编号，使其符合用户直觉
                # mss 原始编号：monitors[1] 可能是任意显示器，取决于连接顺序
                # 映射规则：monitor=1 选择 left=0 的（主屏幕），monitor=2 选择另一个
                if len(monitors) > 2:  # 有多个显示器
                    # 找到 left=0 的显示器索引（主屏幕）
                    primary_index = None
                    secondary_index = None
                    for i in range(1, len(monitors)):
                        if monitors[i]['left'] == 0:
                            primary_index = i
                        else:
                            secondary_index = i

                    if primary_index is None:
                        # 没找到 left=0，使用 mss 默认编号
                        logger.warning("Could not find primary monitor (left=0), using mss default order")
                        target_index = effective_monitor
                    else:
                        # 映射：monitor=1 -> 主屏幕，monitor=2 -> 副屏幕
                        if effective_monitor == 1:
                            target_index = primary_index
                        elif effective_monitor == 2:
                            target_index = secondary_index if secondary_index else primary_index
                        else:
                            target_index = effective_monitor

                    logger.debug(f"Monitor mapping: user requested {effective_monitor} -> mss index {target_index}")
                    target_monitor = monitors[target_index]
                else:
                    # 只有一个显示器，直接使用 monitors[1]
                    target_monitor = monitors[1] if len(monitors) > 1 else monitors[0]

                screenshot = sct.grab(target_monitor)
                # 转换为 PNG bytes
                img = Image.frombytes("RGB", screenshot.size, screenshot.rgb)
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                logger.debug(f"System-level screenshot size: {len(buf.getvalue())} bytes, dimensions: {screenshot.size}")
                return buf.getvalue()
        except Exception as e:
            logger.error(f"System-level screenshot failed: {e}")
            return b""

    def get_screenshot(self, context: Any) -> bytes:
        """获取当前页面截图（兼容旧接口）。"""
        return self.take_screenshot(context)

    # ========== 动作执行 ==========

    def execute_action(self, context: Any, action: Action) -> ActionResult:
        """执行动作。"""
        start_time = time.time()

        try:
            # 重置执行层级和显示器为默认值（确保每个 action 独立控制）
            self._current_level = "browser"
            self._current_monitor = 1

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
            elif action.action_type == "new_page":
                result = self._action_new_page(action)
            elif action.action_type == "switched_page":
                result = self._action_switched_page(action)
            elif action.action_type == "close_page":
                result = self._action_close_page(action)
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
        """检查浏览器上下文是否仍然有效（未被手动关闭）。

        避免创建测试页面，通过检查现有页面状态来判断。
        """
        if not self._browser_context:
            return False
        try:
            # 检查现有页面状态，避免创建新页面产生副作用
            pages = self._browser_context.pages
            if not pages:
                # 没有页面不一定意味着 context 无效，需要进一步验证
                # 尝试获取 context 的浏览器连接状态（轻量操作）
                # 注意：BrowserContext 没有 closed 属性，但可以通过尝试访问 pages 来间接判断
                return True  # context 存在且 pages 可访问，认为有效

            # 检查是否有未关闭的页面
            for page in pages:
                try:
                    if not page.is_closed():
                        # 有活跃页面，context 一定有效
                        return True
                except Exception:
                    # 页面检查异常，可能页面已被销毁
                    continue

            # 所有页面都已关闭，context 可能仍然有效（可以创建新页面）
            # 但为安全起见，返回 False 触发重启
            logger.info("All pages are closed, context may need restart")
            return False
        except Exception as e:
            logger.info(f"Browser context check failed: {e}")
            return False

    def _reset_browser_state(self) -> None:
        """重置浏览器状态，准备重新启动。

        注意：不清除 _captured_tokens，token 缓存与浏览器生命周期解耦。
        """
        self._started = False
        self._browser_context = None
        self._playwright = None
        self._sessions.clear()
        self._current_page = None
        # 不清除 _captured_tokens，保持跨会话持久化

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
        """导航到 URL。

        使用 wait_until="domcontentloaded" 而不是默认的 "load"，
        避免 JS 文件加载慢导致超时。
        """
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
            # 使用 domcontentloaded 而不是默认的 load
            # load 事件等待所有资源加载完成（包括 JS、CSS、图片等）
            # domcontentloaded 只等待 DOM 解析完成，更快且更可靠
            _run_async(page.goto(url, timeout=action.timeout, wait_until="domcontentloaded"))
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

    # ========== 页面管理动作 ==========

    def _get_page_index(self, page: Page) -> int:
        """获取页面在有效页面列表中的索引（从 1 开始）。

        Args:
            page: 目标页面

        Returns:
            页面索引（从 1 开始），如果页面不在列表中或已关闭返回 0
        """
        if not self._browser_context or not page:
            return 0

        try:
            if page.is_closed():
                return 0
        except Exception:
            return 0

        pages = [p for p in self._browser_context.pages if not p.is_closed()]
        for i, p in enumerate(pages):
            if p == page:
                return i + 1
        return 0

    def _action_switched_page(self, action: Action) -> ActionResult:
        """切换到指定页面。

        Args:
            action: 动作参数，value 为页面索引（从 1 开始）

        Returns:
            ActionResult: 动作执行结果
        """
        # 验证浏览器上下文
        if not self._browser_context:
            return ActionResult(
                number=0,
                action_type="switched_page",
                status=ActionStatus.FAILED,
                error="Browser context not available",
            )

        # 解析索引
        if not action.value:
            return ActionResult(
                number=0,
                action_type="switched_page",
                status=ActionStatus.FAILED,
                error="Page index is required",
            )

        try:
            index = int(action.value)
        except (ValueError, TypeError):
            return ActionResult(
                number=0,
                action_type="switched_page",
                status=ActionStatus.FAILED,
                error=f"Invalid page index: {action.value}",
            )

        # 获取有效页面列表
        pages = [p for p in self._browser_context.pages if not p.is_closed()]

        # 验证范围
        if index < 1 or index > len(pages):
            return ActionResult(
                number=0,
                action_type="switched_page",
                status=ActionStatus.FAILED,
                error=f"Page index {index} out of range, only {len(pages)} pages available",
            )

        # 切换页面
        target_page = pages[index - 1]
        self._current_page = target_page
        self._sessions["default"] = {
            "context": self._browser_context,
            "page": target_page,
        }

        logger.info(f"Switched to page {index}")
        return ActionResult(
            number=0,
            action_type="switched_page",
            status=ActionStatus.SUCCESS,
            output=f"Switched to page {index}",
            context=target_page,
        )

    def _action_close_page(self, action: Action) -> ActionResult:
        """关闭当前页面并自动切换焦点。

        Args:
            action: 动作参数

        Returns:
            ActionResult: 动作执行结果
        """
        # 验证浏览器上下文
        if not self._browser_context:
            return ActionResult(
                number=0,
                action_type="close_page",
                status=ActionStatus.FAILED,
                error="Browser context not available",
            )

        # 验证当前页面
        if not self._current_page:
            return ActionResult(
                number=0,
                action_type="close_page",
                status=ActionStatus.FAILED,
                error="No active page to close",
            )

        # 获取有效页面列表
        pages = [p for p in self._browser_context.pages if not p.is_closed()]

        # 不允许关闭最后一页
        if len(pages) <= 1:
            return ActionResult(
                number=0,
                action_type="close_page",
                status=ActionStatus.FAILED,
                error="Cannot close the last page",
            )

        # 关闭当前页面
        old_index = self._get_page_index(self._current_page)
        try:
            _run_async(self._current_page.close())
            logger.info(f"Closed page {old_index}")
        except Exception as e:
            return ActionResult(
                number=0,
                action_type="close_page",
                status=ActionStatus.FAILED,
                error=f"Failed to close page: {e}",
            )

        # 刷新页面列表，找到新的焦点页面
        pages = [p for p in self._browser_context.pages if not p.is_closed()]
        if not pages:
            return ActionResult(
                number=0,
                action_type="close_page",
                status=ActionStatus.FAILED,
                error="No pages available after close",
            )

        # 更新状态（取第一个有效页面作为新焦点）
        new_page = pages[0]
        new_index = self._get_page_index(new_page)
        self._current_page = new_page
        self._sessions["default"] = {
            "context": self._browser_context,
            "page": new_page,
        }

        logger.info(f"Auto switched to page {new_index}")
        return ActionResult(
            number=0,
            action_type="close_page",
            status=ActionStatus.SUCCESS,
            output=f"Closed page {old_index}, now on page {new_index}",
            context=new_page,
        )

    
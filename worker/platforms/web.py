"""
Web 平台执行引擎。

基于 Playwright 实现，支持 OCR/图像识别定位。
"""

import logging
import time
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page

from worker.platforms.base import PlatformManager, Session
from worker.task import Action, ActionResult, ActionStatus
from worker.config import PlatformConfig

logger = logging.getLogger(__name__)


class WebPlatformManager(PlatformManager):
    """
    Web 平台管理器。

    使用 Playwright 控制 Web 浏览器，支持 OCR/图像识别定位。
    """

    def __init__(self, config: PlatformConfig, ocr_client=None):
        super().__init__(config, ocr_client)

        self._playwright = None
        self._browser: Optional[Browser] = None
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
            self._playwright = sync_playwright().start()

            # 选择浏览器类型
            if self.browser_type == "firefox":
                browser_launcher = self._playwright.firefox
            elif self.browser_type == "webkit":
                browser_launcher = self._playwright.webkit
            else:
                browser_launcher = self._playwright.chromium

            self._browser = browser_launcher.launch(
                headless=self.headless,
                timeout=self.timeout,
            )

            self._started = True
            logger.info(f"Web platform started (browser={self.browser_type}, headless={self.headless})")

        except Exception as e:
            logger.error(f"Failed to start Web platform: {e}")
            raise

    def stop(self) -> None:
        """停止浏览器和 Playwright。"""
        # 关闭所有会话
        for session_id in list(self.sessions.keys()):
            try:
                self.close_session(session_id)
            except Exception as e:
                logger.warning(f"Failed to close session {session_id}: {e}")

        # 关闭浏览器
        if self._browser:
            try:
                self._browser.close()
            except Exception as e:
                logger.warning(f"Failed to close browser: {e}")
            self._browser = None

        # 停止 Playwright
        if self._playwright:
            try:
                self._playwright.stop()
            except Exception as e:
                logger.warning(f"Failed to stop Playwright: {e}")
            self._playwright = None

        self._started = False
        logger.info("Web platform stopped")

    def is_available(self) -> bool:
        """检查平台是否可用。"""
        return self._started and self._browser is not None

    def create_session(self, device_id: Optional[str] = None, options: Optional[Dict] = None) -> Session:
        """
        创建浏览器会话（BrowserContext）。

        Args:
            device_id: 不使用
            options: 上下文选项

        Returns:
            Session: 会话对象
        """
        if not self.is_available():
            raise RuntimeError("Web platform not started")

        session_id = str(uuid.uuid4())[:8]

        # 创建新的浏览器上下文
        context_options = options or {}
        context = self._browser.new_context(**context_options)

        # 创建页面
        page = context.new_page()
        page.set_default_timeout(self.timeout)

        session = Session(
            session_id=session_id,
            platform=self.platform,
            context=context,
            metadata={"page": page},
        )

        self.sessions[session_id] = session
        logger.info(f"Web session created: {session_id}")

        return session

    def close_session(self, session_id: str) -> bool:
        """关闭浏览器会话。"""
        session = self.sessions.get(session_id)
        if not session:
            return False

        try:
            context = session.context
            if context:
                context.close()

            del self.sessions[session_id]
            logger.info(f"Web session closed: {session_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to close session {session_id}: {e}")
            return False

    def execute_action(self, session: Session, action: Action) -> ActionResult:
        """执行动作。"""
        start_time = time.time()

        page: Page = session.metadata.get("page")
        if not page:
            return ActionResult(
                index=0,
                action_type=action.action_type,
                status=ActionStatus.FAILED,
                error="Page not found in session",
            )

        try:
            # 根据动作类型执行
            if action.action_type == "navigate":
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

            # 更新会话活动时间
            self._update_session_activity(session.session_id)

            duration_ms = int((time.time() - start_time) * 1000)
            result.duration_ms = duration_ms

            return result

        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            return ActionResult(
                index=0,
                action_type=action.action_type,
                status=ActionStatus.FAILED,
                duration_ms=duration_ms,
                error=str(e),
            )

    def get_screenshot(self, session: Session) -> bytes:
        """获取当前页面截图。"""
        page: Page = session.metadata.get("page")
        if page:
            return page.screenshot(type="png")
        return b""

    # ========== 动作实现 ==========

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

        page.goto(url, timeout=action.timeout)
        return ActionResult(
            index=0,
            action_type="navigate",
            status=ActionStatus.SUCCESS,
            output=page.url,
        )

    def _action_ocr_click(self, page: Page, action: Action) -> ActionResult:
        """OCR 文字点击。"""
        # 获取截图
        screenshot = page.screenshot(type="png")

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
        page.mouse.click(x, y)

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
        screenshot = page.screenshot(type="png")

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
        page.mouse.click(x, y)

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

        page.mouse.click(action.x, action.y)

        return ActionResult(
            index=0,
            action_type="click",
            status=ActionStatus.SUCCESS,
            output=f"Clicked at ({action.x}, {action.y})",
        )

    def _action_ocr_input(self, page: Page, action: Action) -> ActionResult:
        """OCR 文字附近输入。"""
        # 获取截图
        screenshot = page.screenshot(type="png")

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
        page.mouse.click(x, y)

        # 输入文本
        if action.value:
            page.keyboard.type(action.value)

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
        page.mouse.click(action.x, action.y)

        # 输入
        if action.value:
            page.keyboard.type(action.value)

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

        page.keyboard.press(action.value)

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

        page.mouse.move(action.x, action.y)
        page.mouse.down()
        page.mouse.move(end_x, end_y)
        page.mouse.up()

        return ActionResult(
            index=0,
            action_type="swipe",
            status=ActionStatus.SUCCESS,
            output=f"Swiped from ({action.x}, {action.y}) to ({end_x}, {end_y})",
        )

    def _action_screenshot(self, page: Page, action: Action) -> ActionResult:
        """截图。"""
        screenshot = page.screenshot(type="png")
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
            screenshot = page.screenshot(type="png")
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
            screenshot = page.screenshot(type="png")
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
        screenshot = page.screenshot(type="png")
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

        screenshot = page.screenshot(type="png")
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

        screenshot = page.screenshot(type="png")
        texts = self.ocr_client.recognize(screenshot)

        all_text = " ".join([t.text for t in texts])

        return ActionResult(
            index=0,
            action_type="ocr_get_text",
            status=ActionStatus.SUCCESS,
            output=all_text,
        )
"""
iOS 平台执行引擎。

基于 Appium 实现，支持 OCR/图像识别定位。
"""

import logging
import time
from typing import Any, Dict, Optional, Set

from appium import webdriver
from appium.options.ios import XCUITestOptions
from appium.webdriver.common.appiumby import AppiumBy

from worker.platforms.base import PlatformManager
from worker.task import Action, ActionResult, ActionStatus
from worker.config import PlatformConfig

logger = logging.getLogger(__name__)


class iOSPlatformManager(PlatformManager):
    """
    iOS 平台管理器。

    使用 Appium (XCUITest) 控制 iOS 设备，支持 OCR/图像识别定位。
    """

    # iOS 平台特有动作
    SUPPORTED_ACTIONS: Set[str] = {"start_app", "stop_app"}

    def __init__(self, config: PlatformConfig, ocr_client=None):
        super().__init__(config, ocr_client)

        self.appium_server = config.appium_server  # 必须从配置文件读取
        self.timeout = config.timeout

    @property
    def platform(self) -> str:
        return "ios"

    def start(self) -> None:
        """启动 iOS 平台（检查 Appium Server 连接）。"""
        if self._started:
            return

        # 检查 Appium Server 是否可用
        try:
            import httpx
            response = httpx.get(f"{self.appium_server}/status", timeout=5)
            if response.status_code != 200:
                raise RuntimeError(f"Appium Server not healthy: {response.status_code}")
        except Exception as e:
            logger.warning(f"Appium Server check failed: {e}")

        self._started = True
        logger.info(f"iOS platform started (server={self.appium_server})")

    def stop(self) -> None:
        """停止 iOS 平台。"""
        # 关闭所有上下文（driver）
        for device_id in list(self._contexts.keys()):
            try:
                self.close_context(self._contexts[device_id])
            except Exception as e:
                logger.warning(f"Failed to close driver: {e}")
        self._contexts.clear()

        self._started = False
        logger.info("iOS platform stopped")

    def is_available(self) -> bool:
        """检查平台是否可用。"""
        return self._started

    def create_context(self, device_id: Optional[str] = None, options: Optional[Dict] = None) -> Any:
        """
        创建 Appium Driver。

        Args:
            device_id: iOS 设备 UDID（必填）
            options: Appium 选项

        Returns:
            WebDriver: Appium Driver
        """
        if not self.is_available():
            raise RuntimeError("iOS platform not started")

        if not device_id:
            raise ValueError("device_id is required for iOS platform")

        # 配置 Appium 选项
        appium_options = options or {}
        caps = appium_options.get("capabilities", {})

        options_obj = XCUITestOptions()
        options_obj.platform_name = "iOS"
        options_obj.automation_name = "XCUITest"
        options_obj.udid = device_id

        # 应用额外 capabilities
        for key, value in caps.items():
            options_obj.set_capability(key, value)

        # 创建 driver
        driver = webdriver.Remote(
            command_executor=self.appium_server,
            options=options_obj
        )
        driver.implicitly_wait(10)

        # 缓存 driver
        self._contexts[device_id] = driver

        logger.info(f"iOS driver created (device={device_id})")

        return driver

    def close_context(self, context: Any) -> None:
        """关闭 Appium Driver。"""
        if context:
            try:
                context.quit()
                logger.info("iOS driver closed")
            except Exception as e:
                logger.error(f"Failed to close driver: {e}")

    def execute_action(self, context: Any, action: Action) -> ActionResult:
        """执行动作。"""
        start_time = time.time()

        driver = context
        if not driver:
            return ActionResult(
                index=0,
                action_type=action.action_type,
                status=ActionStatus.FAILED,
                error="Driver context is invalid",
            )

        try:
            # 根据动作类型执行
            if action.action_type == "start_app":
                result = self._action_start_app(driver, action)
            elif action.action_type == "stop_app":
                result = self._action_stop_app(driver, action)
            elif action.action_type == "ocr_click":
                result = self._action_ocr_click(driver, action)
            elif action.action_type == "image_click":
                result = self._action_image_click(driver, action)
            elif action.action_type == "click":
                result = self._action_click(driver, action)
            elif action.action_type == "ocr_input":
                result = self._action_ocr_input(driver, action)
            elif action.action_type == "input":
                result = self._action_input(driver, action)
            elif action.action_type == "press":
                result = self._action_press(driver, action)
            elif action.action_type == "swipe":
                result = self._action_swipe(driver, action)
            elif action.action_type == "screenshot":
                result = self._action_screenshot(driver, action)
            elif action.action_type == "wait":
                result = self._action_wait(driver, action)
            elif action.action_type == "ocr_wait":
                result = self._action_ocr_wait(driver, action)
            elif action.action_type == "image_wait":
                result = self._action_image_wait(driver, action)
            elif action.action_type == "ocr_assert":
                result = self._action_ocr_assert(driver, action)
            elif action.action_type == "image_assert":
                result = self._action_image_assert(driver, action)
            elif action.action_type == "ocr_get_text":
                result = self._action_ocr_get_text(driver, action)
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
            duration_ms = int((time.time() - start_time) * 1000)
            return ActionResult(
                index=0,
                action_type=action.action_type,
                status=ActionStatus.FAILED,
                duration_ms=duration_ms,
                error=str(e),
            )

    def get_screenshot(self, context: Any) -> bytes:
        """获取当前屏幕截图。"""
        driver = context
        if driver:
            return driver.get_screenshot_as_png()
        return b""

    # ========== 动作实现 ==========

    def _action_start_app(self, driver, action: Action) -> ActionResult:
        """启动应用。"""
        bundle_id = action.bundle_id or action.value
        if not bundle_id:
            return ActionResult(
                index=0,
                action_type="start_app",
                status=ActionStatus.FAILED,
                error="bundle_id is required",
            )

        driver.activate_app(bundle_id)

        return ActionResult(
            index=0,
            action_type="start_app",
            status=ActionStatus.SUCCESS,
            output=f"Started: {bundle_id}",
        )

    def _action_stop_app(self, driver, action: Action) -> ActionResult:
        """关闭应用。"""
        bundle_id = action.bundle_id or action.value
        if bundle_id:
            driver.terminate_app(bundle_id)
        else:
            driver.close_app()

        return ActionResult(
            index=0,
            action_type="stop_app",
            status=ActionStatus.SUCCESS,
            output=f"Stopped: {bundle_id}",
        )

    def _action_ocr_click(self, driver, action: Action) -> ActionResult:
        """OCR 文字点击。"""
        # 获取截图
        screenshot = driver.get_screenshot_as_png()

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
        driver.tap([(x, y)])

        return ActionResult(
            index=0,
            action_type="ocr_click",
            status=ActionStatus.SUCCESS,
            output=f"Clicked at ({x}, {y})",
        )

    def _action_image_click(self, driver, action: Action) -> ActionResult:
        """图像匹配点击。"""
        if not action.image_path:
            return ActionResult(
                index=0,
                action_type="image_click",
                status=ActionStatus.FAILED,
                error="image_path is required",
            )

        # 获取截图
        screenshot = driver.get_screenshot_as_png()

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
        driver.tap([(x, y)])

        return ActionResult(
            index=0,
            action_type="image_click",
            status=ActionStatus.SUCCESS,
            output=f"Clicked at ({x}, {y})",
        )

    def _action_click(self, driver, action: Action) -> ActionResult:
        """坐标点击。"""
        if action.x is None or action.y is None:
            return ActionResult(
                index=0,
                action_type="click",
                status=ActionStatus.FAILED,
                error="x and y coordinates are required",
            )

        driver.tap([(action.x, action.y)])

        return ActionResult(
            index=0,
            action_type="click",
            status=ActionStatus.SUCCESS,
            output=f"Clicked at ({action.x}, {action.y})",
        )

    def _action_ocr_input(self, driver, action: Action) -> ActionResult:
        """OCR 文字附近输入。"""
        # 获取截图
        screenshot = driver.get_screenshot_as_png()

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
        driver.tap([(x, y)])

        # 输入文本
        if action.value:
            driver.find_element(AppiumBy.CLASS_NAME, "XCUIElementTypeTextField").send_keys(action.value)

        return ActionResult(
            index=0,
            action_type="ocr_input",
            status=ActionStatus.SUCCESS,
            output=f"Input at ({x}, {y})",
        )

    def _action_input(self, driver, action: Action) -> ActionResult:
        """坐标输入。"""
        if action.x is None or action.y is None:
            return ActionResult(
                index=0,
                action_type="input",
                status=ActionStatus.FAILED,
                error="x and y coordinates are required",
            )

        # 点击
        driver.tap([(action.x, action.y)])

        # 输入
        if action.value:
            driver.find_element(AppiumBy.CLASS_NAME, "XCUIElementTypeTextField").send_keys(action.value)

        return ActionResult(
            index=0,
            action_type="input",
            status=ActionStatus.SUCCESS,
            output=f"Input at ({action.x}, {action.y})",
        )

    def _action_press(self, driver, action: Action) -> ActionResult:
        """按键（iOS 使用 press_button）。"""
        button = action.value

        try:
            if button:
                driver.execute_script("mobile: pressButton", {"name": button})
        except Exception as e:
            return ActionResult(
                index=0,
                action_type="press",
                status=ActionStatus.FAILED,
                error=str(e),
            )

        return ActionResult(
            index=0,
            action_type="press",
            status=ActionStatus.SUCCESS,
            output=f"Pressed: {action.value}",
        )

    def _action_swipe(self, driver, action: Action) -> ActionResult:
        """滑动。"""
        if action.x is None or action.y is None:
            return ActionResult(
                index=0,
                action_type="swipe",
                status=ActionStatus.FAILED,
                error="Start coordinates are required",
            )

        end_x = action.end_x if action.end_x is not None else action.x
        end_y = action.end_y if action.end_y is not None else action.y

        driver.swipe(action.x, action.y, end_x, end_y, duration=500)

        return ActionResult(
            index=0,
            action_type="swipe",
            status=ActionStatus.SUCCESS,
            output=f"Swiped from ({action.x}, {action.y}) to ({end_x}, {end_y})",
        )

    def _action_screenshot(self, driver, action: Action) -> ActionResult:
        """截图。"""
        screenshot = driver.get_screenshot_as_png()
        screenshot_base64 = self._bytes_to_base64(screenshot)

        name = action.value or "screenshot"

        return ActionResult(
            index=0,
            action_type="screenshot",
            status=ActionStatus.SUCCESS,
            output=name,
            screenshot=screenshot_base64,
        )

    def _action_wait(self, driver, action: Action) -> ActionResult:
        """固定等待。"""
        wait_time = action.wait or int(action.value or 1000)
        self._wait(wait_time)

        return ActionResult(
            index=0,
            action_type="wait",
            status=ActionStatus.SUCCESS,
            output=f"Waited {wait_time}ms",
        )

    def _action_ocr_wait(self, driver, action: Action) -> ActionResult:
        """等待文字出现。"""
        start_time = time.time()
        timeout = action.timeout / 1000

        while time.time() - start_time < timeout:
            screenshot = driver.get_screenshot_as_png()
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

    def _action_image_wait(self, driver, action: Action) -> ActionResult:
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
            screenshot = driver.get_screenshot_as_png()
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

    def _action_ocr_assert(self, driver, action: Action) -> ActionResult:
        """OCR 文字断言。"""
        screenshot = driver.get_screenshot_as_png()
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

    def _action_image_assert(self, driver, action: Action) -> ActionResult:
        """图像断言。"""
        if not action.image_path:
            return ActionResult(
                index=0,
                action_type="image_assert",
                status=ActionStatus.FAILED,
                error="image_path is required",
            )

        screenshot = driver.get_screenshot_as_png()
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

    def _action_ocr_get_text(self, driver, action: Action) -> ActionResult:
        """获取 OCR 文字区域内容。"""
        if not self.ocr_client:
            return ActionResult(
                index=0,
                action_type="ocr_get_text",
                status=ActionStatus.FAILED,
                error="OCR client not available",
            )

        screenshot = driver.get_screenshot_as_png()
        texts = self.ocr_client.recognize(screenshot)

        all_text = " ".join([t.text for t in texts])

        return ActionResult(
            index=0,
            action_type="ocr_get_text",
            status=ActionStatus.SUCCESS,
            output=all_text,
        )
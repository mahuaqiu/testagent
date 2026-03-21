"""
Android 平台执行引擎。

基于 Appium 实现，支持 OCR/图像识别定位。
"""

import logging
import time
from typing import Any, Dict, Optional, Set

from appium import webdriver
from appium.options.android import UiAutomator2Options
from appium.webdriver.common.appiumby import AppiumBy

from worker.platforms.base import PlatformManager
from worker.task import Action, ActionResult, ActionStatus
from worker.config import PlatformConfig
from worker.actions import ActionRegistry

logger = logging.getLogger(__name__)


class AndroidPlatformManager(PlatformManager):
    """
    Android 平台管理器。

    使用 Appium (UiAutomator2) 控制 Android 设备，支持 OCR/图像识别定位。
    """

    # Android 平台特有动作
    SUPPORTED_ACTIONS: Set[str] = {"start_app", "stop_app"}

    # 按键映射
    KEY_MAP = {
        "HOME": 3,
        "BACK": 4,
        "MENU": 82,
        "ENTER": 66,
        "SEARCH": 84,
    }

    def __init__(self, config: PlatformConfig, ocr_client=None):
        super().__init__(config, ocr_client)

        self.appium_server = config.appium_server
        self.timeout = config.timeout
        self._current_driver = None  # 当前 driver，用于基础能力操作
        # 会话管理：key=device_id, value={"driver": driver, "package": package_name}
        self._sessions: Dict[str, Dict[str, Any]] = {}

    @property
    def platform(self) -> str:
        return "android"

    # ========== 生命周期管理 ==========

    def start(self) -> None:
        """启动 Android 平台（检查 Appium Server 连接）。"""
        if self._started:
            return

        try:
            import httpx
            response = httpx.get(f"{self.appium_server}/status", timeout=5)
            if response.status_code != 200:
                raise RuntimeError(f"Appium Server not healthy: {response.status_code}")
        except Exception as e:
            logger.warning(f"Appium Server check failed: {e}")

        self._started = True
        logger.info(f"Android platform started (server={self.appium_server})")

    def stop(self) -> None:
        """停止 Android 平台。"""
        for device_id in list(self._contexts.keys()):
            try:
                self.close_context(self._contexts[device_id])
            except Exception as e:
                logger.warning(f"Failed to close driver: {e}")
        self._contexts.clear()

        self._started = False
        logger.info("Android platform stopped")

    def is_available(self) -> bool:
        """检查平台是否可用。"""
        return self._started

    # ========== 会话管理方法 ==========

    def has_active_session(self, device_id: Optional[str] = None) -> bool:
        """检查是否有活跃的会话。"""
        if device_id:
            return device_id in self._sessions and self._sessions[device_id].get("driver") is not None
        return any(s.get("driver") is not None for s in self._sessions.values())

    def get_session_context(self, device_id: Optional[str] = None) -> Any:
        """获取当前会话的上下文。"""
        if device_id:
            session = self._sessions.get(device_id)
            return session.get("driver") if session else None
        for session in self._sessions.values():
            if session.get("driver"):
                return session.get("driver")
        return None

    def close_session(self, device_id: Optional[str] = None) -> None:
        """关闭会话。"""
        if device_id:
            session = self._sessions.get(device_id)
            if session:
                driver = session.get("driver")
                if driver:
                    try:
                        driver.quit()
                    except Exception as e:
                        logger.warning(f"Failed to close driver: {e}")
                del self._sessions[device_id]
                if device_id in self._contexts:
                    del self._contexts[device_id]
            logger.info(f"Android session closed (device={device_id})")
        else:
            for sid in list(self._sessions.keys()):
                session = self._sessions[sid]
                driver = session.get("driver")
                if driver:
                    try:
                        driver.quit()
                    except Exception as e:
                        logger.warning(f"Failed to close driver: {e}")
            self._sessions.clear()
            self._contexts.clear()
            logger.info("All Android sessions closed")

    # ========== 上下文管理 ==========

    def create_context(self, device_id: Optional[str] = None, options: Optional[Dict] = None) -> Any:
        """创建 Appium Driver。"""
        if not self.is_available():
            raise RuntimeError("Android platform not started")

        if not device_id:
            raise ValueError("device_id is required for Android platform")

        if device_id in self._sessions:
            existing_driver = self._sessions[device_id].get("driver")
            if existing_driver:
                logger.info(f"Reusing existing Android driver (device={device_id})")
                self._current_driver = existing_driver
                return existing_driver

        appium_options = options or {}
        caps = appium_options.get("capabilities", {})

        options_obj = UiAutomator2Options()
        options_obj.platform_name = "Android"
        options_obj.automation_name = "UiAutomator2"
        options_obj.udid = device_id

        for key, value in caps.items():
            options_obj.set_capability(key, value)

        driver = webdriver.Remote(
            command_executor=self.appium_server,
            options=options_obj
        )
        driver.implicitly_wait(10)

        self._sessions[device_id] = {"driver": driver, "package": None}
        self._contexts[device_id] = driver
        self._current_driver = driver

        logger.info(f"Android driver created (device={device_id})")
        return driver

    def close_context(self, context: Any, close_session: bool = False) -> None:
        """关闭 Appium Driver。"""
        device_id = None
        for did, drv in self._contexts.items():
            if drv == context:
                device_id = did
                break

        if context:
            try:
                if close_session:
                    self.close_session(device_id)
                else:
                    if device_id and device_id in self._contexts:
                        del self._contexts[device_id]
                    if device_id and device_id in self._sessions:
                        self._sessions[device_id]["driver"] = None
                    logger.info("Android driver detached (session kept)")
            except Exception as e:
                logger.error(f"Failed to close context: {e}")

    # ========== 基础能力实现 ==========

    def click(self, x: int, y: int, context: Any = None) -> None:
        """点击指定坐标。"""
        driver = context or self._current_driver
        if driver:
            driver.tap([(x, y)])

    def input_text(self, text: str, context: Any = None) -> None:
        """输入文本。"""
        driver = context or self._current_driver
        if driver:
            try:
                driver.find_element(AppiumBy.CLASS_NAME, "android.widget.EditText").send_keys(text)
            except Exception:
                # 如果找不到 EditText，尝试使用剪贴板
                import pyperclip
                pyperclip.copy(text)
                driver.press_keycode(279)  # KEYCODE_PASTE

    def swipe(self, start_x: int, start_y: int, end_x: int, end_y: int, context: Any = None) -> None:
        """滑动。"""
        driver = context or self._current_driver
        if driver:
            driver.swipe(start_x, start_y, end_x, end_y, duration=500)

    def press(self, key: str, context: Any = None) -> None:
        """按键。"""
        driver = context or self._current_driver
        if driver:
            key_name = key.upper() if key else ""
            key_code = self.KEY_MAP.get(key_name)

            if key_code:
                driver.press_keycode(key_code)
            elif key and key.isdigit():
                driver.press_keycode(int(key))
            else:
                raise ValueError(f"Unknown key: {key}")

    def take_screenshot(self, context: Any = None) -> bytes:
        """获取截图。"""
        driver = context or self._current_driver
        if driver:
            return driver.get_screenshot_as_png()
        return b""

    def get_screenshot(self, context: Any) -> bytes:
        """获取当前屏幕截图（兼容旧接口）。"""
        return self.take_screenshot(context)

    # ========== 动作执行 ==========

    def execute_action(self, context: Any, action: Action) -> ActionResult:
        """执行动作。"""
        start_time = time.time()

        driver = context
        if not driver and not action.action_type in ("start_app", "stop_app"):
            return ActionResult(
                index=0,
                action_type=action.action_type,
                status=ActionStatus.FAILED,
                error="Driver context is invalid",
            )

        # 更新当前 driver 引用
        if driver:
            self._current_driver = driver

        try:
            # 平台特有动作
            if action.action_type == "start_app":
                result = self._action_start_app(driver, action)
            elif action.action_type == "stop_app":
                result = self._action_stop_app(driver, action)
            else:
                # 使用 ActionRegistry 执行通用动作
                executor = ActionRegistry.get(action.action_type)
                if executor:
                    result = executor.execute(self, action, driver)
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

    # ========== 平台特有动作实现 ==========

    def _action_start_app(self, driver, action: Action) -> ActionResult:
        """启动应用。"""
        package = action.package_name or action.value
        if not package:
            return ActionResult(
                index=0,
                action_type="start_app",
                status=ActionStatus.FAILED,
                error="package_name is required",
            )

        if driver:
            driver.activate_app(package)

        # 记录当前应用包名到会话
        device_id = None
        for did, drv in self._contexts.items():
            if drv == driver:
                device_id = did
                break
        if device_id and device_id in self._sessions:
            self._sessions[device_id]["package"] = package

        return ActionResult(
            index=0,
            action_type="start_app",
            status=ActionStatus.SUCCESS,
            output=f"Started: {package}",
        )

    def _action_stop_app(self, driver, action: Action) -> ActionResult:
        """关闭应用（结束会话）。"""
        device_id = None
        for did, drv in self._contexts.items():
            if drv == driver:
                device_id = did
                break

        if device_id:
            self.close_session(device_id)
            return ActionResult(
                index=0,
                action_type="stop_app",
                status=ActionStatus.SUCCESS,
                output=f"Closed driver session (device={device_id})",
            )
        else:
            return ActionResult(
                index=0,
                action_type="stop_app",
                status=ActionStatus.FAILED,
                error="No driver session to close",
            )
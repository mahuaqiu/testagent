"""
Android 平台执行引擎。

基于 uiautomator2 直连实现，支持 OCR/图像识别定位。
"""

import logging
import time
from typing import Any, Dict, Optional, Set

import uiautomator2 as u2

from worker.platforms.base import PlatformManager
from worker.task import Action, ActionResult, ActionStatus
from worker.config import PlatformConfig
from worker.actions import ActionRegistry

logger = logging.getLogger(__name__)


class AndroidPlatformManager(PlatformManager):
    """
    Android 平台管理器。

    使用 uiautomator2 直连控制 Android 设备。
    """

    SUPPORTED_ACTIONS: Set[str] = {"start_app", "stop_app"}

    KEY_MAP = {
        "HOME": 3,
        "BACK": 4,
        "MENU": 82,
        "ENTER": 66,
        "SEARCH": 84,
    }

    def __init__(self, config: PlatformConfig, ocr_client=None):
        super().__init__(config, ocr_client)
        self._device_clients: Dict[str, u2.Device] = {}
        self._current_device: Optional[str] = None

    @property
    def platform(self) -> str:
        return "android"

    def start(self) -> None:
        """启动 Android 平台（检查环境）。"""
        if self._started:
            return

        try:
            import subprocess
            result = subprocess.run(["adb", "version"], capture_output=True, timeout=5)
            if result.returncode != 0:
                logger.warning("ADB not available")
        except Exception as e:
            logger.warning(f"ADB check failed: {e}")

        self._started = True
        logger.info("Android platform started (uiautomator2 mode)")

    def stop(self) -> None:
        """停止 Android 平台。"""
        self._device_clients.clear()
        self._started = False
        logger.info("Android platform stopped")

    def is_available(self) -> bool:
        """检查平台是否可用。"""
        return self._started

    # ========== 设备服务管理 ==========

    def ensure_device_service(self, udid: str) -> tuple[str, str]:
        """确保设备服务可用（由 DeviceMonitor 调用）。"""
        try:
            device = self._device_clients.get(udid)
            if device and device.ping():
                return ("online", "OK")

            device = u2.connect(udid)
            if device.ping():
                self._device_clients[udid] = device
                logger.info(f"Android device service ready: {udid}")
                return ("online", "OK")
            else:
                return ("faulty", "Service not responding")
        except Exception as e:
            logger.error(f"Failed to ensure device service: {udid}, {e}")
            return ("faulty", str(e))

    def mark_device_faulty(self, udid: str) -> None:
        """标记设备为异常。"""
        if udid in self._device_clients:
            del self._device_clients[udid]
            logger.info(f"Android device marked faulty: {udid}")

    def get_online_devices(self) -> list[str]:
        """获取在线设备列表。"""
        return list(self._device_clients.keys())

    # ========== 上下文管理 ==========

    def create_context(self, device_id: Optional[str] = None, options: Optional[Dict] = None) -> u2.Device:
        """获取已有的设备连接。"""
        if not self.is_available():
            raise RuntimeError("Android platform not started")

        if not device_id:
            raise ValueError("device_id is required for Android platform")

        device = self._device_clients.get(device_id)
        if device is None:
            raise RuntimeError(f"Device service not ready: {device_id}")

        self._current_device = device_id
        logger.info(f"Android context created: {device_id}")
        return device

    def close_context(self, context: Any, close_session: bool = False) -> None:
        """关闭上下文。"""
        if close_session:
            for udid, client in list(self._device_clients.items()):
                if client == context:
                    del self._device_clients[udid]
                    break
        logger.info("Android context closed")

    # ========== 会话管理（兼容旧接口） ==========

    def has_active_session(self, device_id: Optional[str] = None) -> bool:
        """检查是否有活跃的会话。"""
        if device_id:
            return device_id in self._device_clients
        return len(self._device_clients) > 0

    def get_session_context(self, device_id: Optional[str] = None) -> Any:
        """获取当前会话的上下文。"""
        if device_id:
            return self._device_clients.get(device_id)
        if self._current_device:
            return self._device_clients.get(self._current_device)
        return None

    def close_session(self, device_id: Optional[str] = None) -> None:
        """关闭会话。"""
        if device_id:
            if device_id in self._device_clients:
                del self._device_clients[device_id]
            logger.info(f"Android session closed (device={device_id})")
        else:
            self._device_clients.clear()
            logger.info("All Android sessions closed")

    # ========== 基础能力实现 ==========

    def click(self, x: int, y: int, context: Any = None) -> None:
        """点击指定坐标。"""
        device = context or self._device_clients.get(self._current_device)
        if device:
            device.click(x, y)

    def double_click(self, x: int, y: int, context: Any = None) -> None:
        """双击指定坐标（模拟两次快速点击）。"""
        device = context or self._device_clients.get(self._current_device)
        if device:
            # 模拟双击：快速两次点击，间隔100ms
            device.click(x, y)
            import time
            time.sleep(0.1)
            device.click(x, y)

    def move(self, x: int, y: int, context: Any = None) -> None:
        """移动鼠标（移动端不支持）。"""
        raise NotImplementedError("move action is not supported on mobile platforms")

    def input_text(self, text: str, context: Any = None) -> None:
        """输入文本。"""
        device = context or self._device_clients.get(self._current_device)
        if device:
            device.send_keys(text)

    def swipe(self, start_x: int, start_y: int, end_x: int, end_y: int, context: Any = None) -> None:
        """滑动。"""
        device = context or self._device_clients.get(self._current_device)
        if device:
            device.swipe(start_x, start_y, end_x, end_y, duration=0.5)

    def press(self, key: str, context: Any = None) -> None:
        """按键。"""
        device = context or self._device_clients.get(self._current_device)
        if device:
            key_name = key.upper() if key else ""
            key_code = self.KEY_MAP.get(key_name)

            if key_code:
                device.press(key_code)
            elif key and key.isdigit():
                device.press(int(key))
            else:
                raise ValueError(f"Unknown key: {key}")

    def take_screenshot(self, context: Any = None) -> bytes:
        """获取截图。"""
        device = context or self._device_clients.get(self._current_device)
        if device:
            from io import BytesIO
            img = device.screenshot()
            buffer = BytesIO()
            img.save(buffer, format="PNG")
            return buffer.getvalue()
        return b""

    def get_screenshot(self, context: Any) -> bytes:
        """获取当前屏幕截图（兼容旧接口）。"""
        return self.take_screenshot(context)

    # ========== 动作执行 ==========

    def execute_action(self, context: Any, action: Action) -> ActionResult:
        """执行动作。"""
        start_time = time.time()

        device = context
        if not device and action.action_type not in ("start_app", "stop_app"):
            return ActionResult(
                number=0,
                action_type=action.action_type,
                status=ActionStatus.FAILED,
                error="Device context is invalid",
            )

        if device:
            for udid, client in self._device_clients.items():
                if client == device:
                    self._current_device = udid
                    break

        try:
            if action.action_type == "start_app":
                result = self._action_start_app(device, action)
            elif action.action_type == "stop_app":
                result = self._action_stop_app(device, action)
            else:
                executor = ActionRegistry.get(action.action_type)
                if executor:
                    result = executor.execute(self, action, device)
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
            duration_ms = int((time.time() - start_time) * 1000)
            return ActionResult(
                number=0,
                action_type=action.action_type,
                status=ActionStatus.FAILED,
                duration_ms=duration_ms,
                error=str(e),
            )

    # ========== 平台特有动作实现 ==========

    def _action_start_app(self, device, action: Action) -> ActionResult:
        """启动应用。"""
        package = action.package_name or action.value
        if not package:
            return ActionResult(
                number=0,
                action_type="start_app",
                status=ActionStatus.FAILED,
                error="package_name is required",
            )

        if device:
            device.app_start(package)

        return ActionResult(
            number=0,
            action_type="start_app",
            status=ActionStatus.SUCCESS,
            output=f"Started: {package}",
        )

    def _action_stop_app(self, device, action: Action) -> ActionResult:
        """关闭应用。"""
        if device and self._current_device:
            package = action.package_name or action.value
            if package:
                device.app_stop(package)
            return ActionResult(
                number=0,
                action_type="stop_app",
                status=ActionStatus.SUCCESS,
                output=f"Stopped app",
            )
        else:
            return ActionResult(
                number=0,
                action_type="stop_app",
                status=ActionStatus.FAILED,
                error="No device context",
            )
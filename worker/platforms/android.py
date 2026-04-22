"""
Android 平台执行引擎。

基于 uiautomator2 直连实现，支持 OCR/图像识别定位。
使用 minicap 截图，支持绑过 FLAG_SECURE 防截屏限制。
"""

import logging
import time
from typing import Any, Optional

import uiautomator2 as u2

from common.utils import run_cmd
from worker.actions import ActionRegistry
from worker.config import PlatformConfig
from worker.discovery.android import get_adb_cmd
from worker.platforms.base import PlatformManager
from worker.platforms.minicap import Minicap
from worker.platforms.minicap.minicap import MinicapError
from worker.task import Action, ActionResult, ActionStatus

logger = logging.getLogger(__name__)


class AndroidPlatformManager(PlatformManager):
    """
    Android 平台管理器。

    使用 uiautomator2 直连控制 Android 设备。
    """

    SUPPORTED_ACTIONS: set[str] = {"start_app", "stop_app", "unlock_screen", "pinch"}

    # Android 按键映射：标准按键名 → KeyCode
    # 参考：https://developer.android.com/reference/android/view/KeyEvent
    KEY_MAP = {
        # 导航键
        "HOME": 3,      # 主屏幕键
        "BACK": 4,      # 返回键
        "MENU": 82,     # 菜单键
        "SEARCH": 84,   # 搜索键
        # 功能键
        "ENTER": 66,    # 回车键
        "ESCAPE": 111,  # ESC 键
        "TAB": 61,      # Tab 键
        "BACKSPACE": 67, # 退格键
        "DELETE": 67,   # 删除键（同退格）
        # 方向键
        "ARROWUP": 19,
        "ARROWDOWN": 20,
        "ARROWLEFT": 21,
        "ARROWRIGHT": 22,
        # 音量键
        "VOLUME_UP": 24,
        "VOLUMEUP": 24,
        "VOLUME_DOWN": 25,
        "VOLUMEDOWN": 25,
        # 电源键
        "POWER": 26,
        "LOCK": 26,
        # 相机键
        "CAMERA": 27,
        # 媒体控制
        "MEDIA_PLAY_PAUSE": 85,
        "MEDIA_STOP": 86,
        "MEDIA_NEXT": 87,
        "MEDIA_PREVIOUS": 88,
    }

    def __init__(self, config: PlatformConfig, ocr_client=None, unlock_config=None):
        super().__init__(config, ocr_client)
        self._device_clients: dict[str, u2.Device] = {}
        self._current_device: str | None = None
        self._unlock_config = unlock_config or {}  # 解锁配置
        self._minicap_instances: dict[str, Minicap] = {}  # minicap 实例

    @property
    def platform(self) -> str:
        return "android"

    def start(self) -> None:
        """启动 Android 平台（检查环境）。"""
        if self._started:
            return

        try:
            result = run_cmd(get_adb_cmd("version"), timeout=5)
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
            if device:
                # uiautomator2 3.x 移除了 ping()，用 info 属性检查连接
                try:
                    device.info  # noqa: F841
                    return ("online", "OK")
                except Exception:
                    pass  # 连接失效，重新连接

            device = u2.connect(udid)
            # uiautomator2 3.x: info 属性可获取设备信息，失败则抛异常
            device.info  # noqa: F841
            self._device_clients[udid] = device
            logger.info(f"Android device service ready: {udid}")

            # 安装 minicap
            try:
                minicap = Minicap(udid)
                minicap.install()
                self._minicap_instances[udid] = minicap
                logger.info(f"Minicap installed for device: {udid}")
            except MinicapError as e:
                logger.warning(f"Minicap installation failed: {e}, will use fallback")

            return ("online", "OK")
        except Exception as e:
            logger.error(f"Failed to ensure device service: {udid}, {e}")
            return ("faulty", str(e))

    def mark_device_faulty(self, udid: str) -> None:
        """标记设备为异常。"""
        if udid in self._device_clients:
            del self._device_clients[udid]
        if udid in self._minicap_instances:
            del self._minicap_instances[udid]
        logger.info(f"Android device marked faulty: {udid}")

    def get_online_devices(self) -> list[str]:
        """获取在线设备列表。"""
        return list(self._device_clients.keys())

    # ========== 上下文管理 ==========

    def create_context(self, device_id: str | None = None, options: dict | None = None) -> u2.Device:
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

    def has_active_session(self, device_id: str | None = None) -> bool:
        """检查是否有活跃的会话。"""
        if device_id:
            return device_id in self._device_clients
        return len(self._device_clients) > 0

    def get_session_context(self, device_id: str | None = None) -> Any:
        """获取当前会话的上下文。"""
        if device_id:
            return self._device_clients.get(device_id)
        if self._current_device:
            return self._device_clients.get(self._current_device)
        return None

    def close_session(self, device_id: str | None = None) -> None:
        """关闭会话。"""
        if device_id:
            if device_id in self._device_clients:
                del self._device_clients[device_id]
            logger.info(f"Android session closed (device={device_id})")
        else:
            self._device_clients.clear()
            logger.info("All Android sessions closed")

    # ========== 基础能力实现 ==========

    def click(self, x: int, y: int, duration: int = 0, context: Any = None) -> None:
        """点击指定坐标，支持长按。

        Args:
            x: X 坐标
            y: Y 坐标
            duration: 点击持续时间（毫秒），0=普通点击，>0=长按
            context: 执行上下文
        """
        device = context or self._device_clients.get(self._current_device)
        if device:
            if duration > 0:
                # 长按：使用 long_click，单位转换 毫秒 → 秒
                duration_sec = duration / 1000.0
                logger.debug(f"Long click at ({x}, {y}) for {duration}ms")
                device.long_click(x, y, duration=duration_sec)
            else:
                logger.debug(f"Click at ({x}, {y})")
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

    def swipe(self, start_x: int, start_y: int, end_x: int, end_y: int,
              duration: int = 500, steps: Optional[int] = None, context: Any = None) -> None:
        """滑动，默认使用 steps=5 平滑滑动。

        Args:
            start_x: 起始 X 坐标
            start_y: 起始 Y 坐标
            end_x: 结束 X 坐标
            end_y: 结束 Y 坐标
            duration: 滑动持续时间（毫秒），默认 500ms（steps 为 None 时使用）
            steps: 滑动步数，控制轨迹平滑度。None 时默认使用 5 实现平滑滑动
            context: 执行上下文
        """
        device = context or self._device_clients.get(self._current_device)
        if device:
            # 默认使用 steps=5 实现平滑滑动
            actual_steps = steps if steps is not None else 5
            logger.debug(f"Swipe with steps={actual_steps}")
            device.swipe(start_x, start_y, end_x, end_y, steps=actual_steps)

    def pinch(self, direction: str, scale: float = 0.5,
              duration: int = 500, context: Any = None) -> None:
        """双指缩放手势。

        使用 uiautomator2 的 pinch 方法实现。

        Args:
            direction: "in" 缩小 / "out" 放大
            scale: 缩放比例
            duration: 持续时间（毫秒）
            context: 执行上下文
        """
        device = context or self._device_clients.get(self._current_device)
        if not device:
            raise RuntimeError("No device context")

        duration_sec = duration / 1000.0

        if direction == "in":
            # 缩小：从外向内
            device.pinch_in(percent=scale, duration=duration_sec)
        else:
            # 放大：从内向外
            device.pinch_out(percent=scale, duration=duration_sec)

        logger.debug(f"pinch {direction} executed: scale={scale}, duration={duration}ms")

    def press(self, key: str, context: Any = None) -> None:
        """按键。

        Android 支持的按键：HOME, BACK, MENU, ENTER, SEARCH, VOLUME_UP, VOLUME_DOWN, POWER 等
        详见 KEY_MAP 定义。
        """
        device = context or self._device_clients.get(self._current_device)
        if device:
            key_name = key.upper() if key else ""
            key_code = self.KEY_MAP.get(key_name)

            if key_code:
                device.press(key_code)
            elif key and key.isdigit():
                # 支持直接传入 KeyCode 数字
                device.press(int(key))
            else:
                supported = ", ".join(sorted(self.KEY_MAP.keys()))
                raise ValueError(f"Unsupported key '{key}' for Android. Supported keys: {supported}")

    def take_screenshot(self, context: Any = None) -> bytes:
        """获取截图。"""
        device_id = self._current_device

        # 优先使用 minicap（绑过 FLAG_SECURE）
        minicap = self._minicap_instances.get(device_id)
        if minicap:
            try:
                return minicap.get_screenshot_png()
            except MinicapError as e:
                logger.warning(f"Minicap screenshot failed: {e}, falling back to uiautomator2")

        # 回退到 uiautomator2
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
            elif action.action_type == "unlock_screen":
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
        """启动应用（含锁屏检测）。"""
        package = action.package_name or action.value
        if not package:
            return ActionResult(
                number=0,
                action_type="start_app",
                status=ActionStatus.FAILED,
                error="package_name is required",
            )

        if not device or not self._current_device:
            return ActionResult(
                number=0,
                action_type="start_app",
                status=ActionStatus.FAILED,
                error="No device context",
            )

        # 检测锁屏状态，如果锁屏则先解锁
        try:
            info = device.info
            is_screen_on = info.get("screenOn", True)
            if not is_screen_on:
                logger.info("Screen is off, performing auto unlock before start_app")
                unlock_result = self._auto_unlock(device)
                if unlock_result.status != ActionStatus.SUCCESS:
                    return ActionResult(
                        number=0,
                        action_type="start_app",
                        status=ActionStatus.FAILED,
                        error=f"Auto unlock failed: {unlock_result.error}",
                    )
                logger.info("Auto unlock completed, proceeding with start_app")
        except Exception as e:
            logger.warning(f"Failed to check screen status: {e}")

        device.app_start(package)

        return ActionResult(
            number=0,
            action_type="start_app",
            status=ActionStatus.SUCCESS,
            output=f"Started: {package}",
        )

    def _auto_unlock(self, device) -> ActionResult:
        """自动解锁屏幕（使用配置密码）。"""
        from worker.actions import ActionRegistry
        from worker.task import Action

        # 从配置读取密码
        password = self._unlock_config.get("password", "123456")

        unlock_action = Action(
            action_type="unlock_screen",
            value=password,
        )

        executor = ActionRegistry.get("unlock_screen")
        if executor:
            return executor.execute(self, unlock_action, device)
        else:
            return ActionResult(
                number=0,
                action_type="unlock_screen",
                status=ActionStatus.FAILED,
                error="unlock_screen executor not found",
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
                output="Stopped app",
            )
        else:
            return ActionResult(
                number=0,
                action_type="stop_app",
                status=ActionStatus.FAILED,
                error="No device context",
            )

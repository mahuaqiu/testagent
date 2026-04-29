"""
解锁屏幕 Action 执行器。

用于 iOS/Android 移动设备解锁屏幕。
"""

import logging
import time
from typing import TYPE_CHECKING

from worker.actions.base import ActionExecutor
from worker.task import Action, ActionResult, ActionStatus

if TYPE_CHECKING:
    from worker.platforms.base import PlatformManager

logger = logging.getLogger(__name__)


class UnlockScreenAction(ActionExecutor):
    """
    解锁屏幕 Action。

    执行流程：
    1. 检测锁屏状态
    2. 唤醒屏幕（如熄屏）
    3. 滑动解锁界面
    4. 输入密码（固定坐标点击）
    5. 验证解锁成功
    """

    name = "unlock_screen"
    requires_context = True  # 需要 context（WDA client / u2 device）来执行操作

    # 默认密码键盘坐标配置（物理分辨率，后备）
    # iPhone 8: 750x1334
    DEFAULT_IOS_KEYPAD = {
        "1": {"x": 92, "y": 570},
        "2": {"x": 250, "y": 570},
        "3": {"x": 408, "y": 570},
        "4": {"x": 92, "y": 670},
        "5": {"x": 250, "y": 670},
        "6": {"x": 408, "y": 670},
        "7": {"x": 92, "y": 770},
        "8": {"x": 250, "y": 770},
        "9": {"x": 408, "y": 770},
        "0": {"x": 250, "y": 870},
    }

    # Android 1080x2400
    DEFAULT_ANDROID_KEYPAD = {
        "1": {"x": 180, "y": 850},
        "2": {"x": 540, "y": 850},
        "3": {"x": 900, "y": 850},
        "4": {"x": 180, "y": 950},
        "5": {"x": 540, "y": 950},
        "6": {"x": 900, "y": 950},
        "7": {"x": 180, "y": 1050},
        "8": {"x": 540, "y": 1050},
        "9": {"x": 900, "y": 1050},
        "0": {"x": 540, "y": 1150},
    }

    def execute(
        self,
        platform: "PlatformManager",
        action: Action,
        context: object | None = None
    ) -> ActionResult:
        """执行解锁屏幕动作。

        支持两种场景：
        - 无密码设备：唤醒屏幕 + 滑动解锁
        - 有密码设备：唤醒屏幕 + 滑动 + 输入密码
        """
        start_time = time.time()

        # 获取密码（可选，无密码设备不需要）
        password = action.value or getattr(action, "password", None)

        # 获取点击间隔（毫秒）
        click_interval = self._get_click_interval(platform)

        # 获取设备分辨率和缩放因子（用于 iOS 坐标转换）
        resolution = self._get_device_resolution(platform, context)
        scale_factor = self._get_scale_factor(platform, context, resolution or (750, 1334))
        logger.info(f"Scale factor for coordinate conversion: {scale_factor}")

        try:
            # 1. 检测锁屏状态
            is_locked = self._check_locked(platform, context)
            logger.info(f"Screen locked status: {is_locked}")

            # 2. 检测屏幕亮度（判断是否熄屏）
            is_screen_on = self._check_screen_brightness(platform, context)
            logger.info(f"Screen brightness status: {'on' if is_screen_on else 'off'}")

            if not is_locked and is_screen_on:
                logger.info("Screen already unlocked and on, skipping")
                return ActionResult(
                    number=0,
                    action_type=self.name,
                    status=ActionStatus.SUCCESS,
                    output="Screen already unlocked and on",
                )

            # 3. 唤醒屏幕（如熄屏）
            if not is_screen_on:
                logger.info("Screen is off, waking up...")
                self._wake_screen(platform, context)
                time.sleep(1.0)  # 等待屏幕亮起

                # 3.1 再次检测亮度，验证唤醒成功
                is_screen_on_after = self._check_screen_brightness(platform, context)
                logger.info(f"Screen brightness after wake: {'on' if is_screen_on_after else 'off'}")

                if not is_screen_on_after:
                    logger.warning("Screen still off after wake attempt")
                    return ActionResult(
                        number=0,
                        action_type=self.name,
                        status=ActionStatus.FAILED,
                        duration_ms=int((time.time() - start_time) * 1000),
                        error="Screen still off after wake attempt",
                    )

            # 4. 触发解锁界面（根据机型配置选择方式）
            unlock_method = self._get_unlock_method(platform, resolution)
            logger.info(f"Using unlock method: {unlock_method}")
            self._trigger_password_screen(platform, context, unlock_method)
            time.sleep(1.0)  # 等待解锁界面出现

            # 5. 根据是否有密码，执行不同的解锁流程
            if not password:
                # 无密码场景：只需唤醒 + 滑动，等待解锁完成
                logger.info("No password provided, performing swipe unlock only")
                duration_ms = int((time.time() - start_time) * 1000)

                # 等待解锁完成
                time.sleep(1.5)

                # 验证解锁成功
                is_locked_after = self._check_locked(platform, context)
                if not is_locked_after:
                    logger.info("Screen unlocked successfully (no password needed)")
                    return ActionResult(
                        number=0,
                        action_type=self.name,
                        status=ActionStatus.SUCCESS,
                        duration_ms=duration_ms,
                        output="Unlocked without password (swipe only)",
                    )
                else:
                    logger.warning("Screen still locked after swipe - device may require password")
                    return ActionResult(
                        number=0,
                        action_type=self.name,
                        status=ActionStatus.FAILED,
                        duration_ms=duration_ms,
                        error="Screen still locked after swipe - device may require password",
                    )

            # 有密码场景：输入密码
            # 获取密码键盘坐标配置（根据设备分辨率自动匹配）
            keypad_coords = self._get_keypad_coords(platform, context)

            for digit in password:
                if digit not in keypad_coords:
                    logger.warning(f"Invalid password digit: {digit}, skipping")
                    continue

                coord = keypad_coords[digit]
                x, y = coord["x"], coord["y"]

                # 点击数字（使用缩放因子转换坐标）
                self._tap_digit(platform, context, x, y, scale_factor)
                logger.info(f"Tapped password digit '{digit}' at physical ({x}, {y}), logical ({x//scale_factor}, {y//scale_factor})")

                # 点击间隔
                time.sleep(click_interval / 1000.0)

            # 6. 等待解锁完成
            time.sleep(1.0)

            # 7. 验证解锁成功
            is_locked_after = self._check_locked(platform, context)
            duration_ms = int((time.time() - start_time) * 1000)

            if not is_locked_after:
                logger.info("Screen unlocked successfully")
                return ActionResult(
                    number=0,
                    action_type=self.name,
                    status=ActionStatus.SUCCESS,
                    duration_ms=duration_ms,
                    output=f"Unlocked with password: {password}",
                )
            else:
                logger.warning("Screen still locked after unlock attempt")
                return ActionResult(
                    number=0,
                    action_type=self.name,
                    status=ActionStatus.FAILED,
                    duration_ms=duration_ms,
                    error="Screen still locked after password input",
                )

        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            logger.error(f"Unlock screen failed: {e}")
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                duration_ms=duration_ms,
                error=str(e),
            )

    def _get_click_interval(self, platform: "PlatformManager") -> int:
        """获取点击间隔（毫秒）。"""
        # 从配置读取，默认 150ms
        unlock_config = getattr(platform, "_unlock_config", {})
        return unlock_config.get("click_interval", 150)

    def _get_keypad_coords(self, platform: "PlatformManager", context: object) -> dict:
        """获取密码键盘坐标配置（根据设备分辨率自动匹配）。"""
        unlock_config = getattr(platform, "_unlock_config", {})
        platform_type = platform.platform

        # 获取设备分辨率
        resolution = self._get_device_resolution(platform, context)
        resolution_key = f"{resolution[0]}x{resolution[1]}" if resolution else "default"

        logger.info(f"Device resolution: {resolution_key}")

        if platform_type == "ios":
            keypad = unlock_config.get("ios_keypad", {})
            # 先尝试按分辨率匹配，找不到则用 default
            coords = keypad.get(resolution_key, keypad.get("default", self.DEFAULT_IOS_KEYPAD))
            logger.info(f"Using keypad config for: {resolution_key if resolution_key in keypad else 'default'}")
            return coords
        elif platform_type == "android":
            keypad = unlock_config.get("android_keypad", {})
            coords = keypad.get(resolution_key, keypad.get("default", self.DEFAULT_ANDROID_KEYPAD))
            logger.info(f"Using keypad config for: {resolution_key if resolution_key in keypad else 'default'}")
            return coords
        else:
            logger.warning(f"Unsupported platform for unlock: {platform_type}")
            return {}

    def _get_device_resolution(self, platform: "PlatformManager", context: object) -> tuple[int, int] | None:
        """获取设备屏幕物理分辨率（通过截图尺寸获取）。"""
        try:
            # 通过截图获取物理分辨率（真实像素尺寸）
            screenshot_bytes = platform.take_screenshot(context)
            if screenshot_bytes:
                import io

                from PIL import Image
                img = Image.open(io.BytesIO(screenshot_bytes))
                width, height = img.size
                logger.info(f"Got physical resolution from screenshot: {width}x{height}")
                return (width, height)
        except Exception as e:
            logger.warning(f"Failed to get physical resolution from screenshot: {e}")

        # 后备方案：尝试通过 API 获取
        platform_type = platform.platform

        if platform_type == "android":
            device = context or platform._device_clients.get(platform._current_device)
            if device:
                try:
                    info = device.info
                    # Android displayWidth/displayHeight 是物理分辨率
                    return (info.get("displayWidth", 1080), info.get("displayHeight", 2400))
                except Exception as e:
                    logger.warning(f"Failed to get Android screen size: {e}")

        return None

    def _get_scale_factor(self, platform: "PlatformManager", context: object, physical_size: tuple[int, int]) -> int:
        """获取 iOS 设备的缩放因子（物理分辨率 / 逻辑分辨率）。"""
        platform_type = platform.platform

        if platform_type == "ios":
            client = context or platform._device_clients.get(platform._current_device)
            if client:
                try:
                    session_id = client._get_session()
                    response = client.session.get(
                        f"{client.base_url}/session/{session_id}/window/size"
                    )
                    if response.status_code == 200:
                        data = response.json()
                        size = data.get("value", {})
                        logical_width = size.get("width", 375)
                        logical_height = size.get("height", 667)

                        physical_width, physical_height = physical_size
                        # 计算缩放因子
                        scale_x = physical_width // logical_width
                        scale_y = physical_height // logical_height
                        scale = max(scale_x, scale_y, 1)

                        logger.info(f"iOS scale factor: {scale} (physical={physical_size}, logical=({logical_width}, {logical_height}))")
                        return scale
                except Exception as e:
                    logger.warning(f"Failed to get iOS logical size: {e}")

            # 默认 2x 缩放（大多数 iPhone）
            return 2

        elif platform_type == "android":
            # Android 不需要缩放
            return 1

        return 1

    def _check_locked(self, platform: "PlatformManager", context: object) -> bool:
        """检测锁屏状态。"""
        platform_type = platform.platform

        if platform_type == "ios":
            # iOS: 通过 WDA /wda/locked 检测
            client = context or platform._device_clients.get(platform._current_device)
            if client and hasattr(client, "is_locked"):
                return client.is_locked()
            return True

        elif platform_type == "android":
            # Android: 通过 uiautomator2 检测屏幕状态
            device = context or platform._device_clients.get(platform._current_device)
            if device:
                info = device.info
                return not info.get("screenOn", True)
            return True

        return True

    def _check_screen_brightness(self, platform: "PlatformManager", context: object) -> bool:
        """检测屏幕是否亮着（通过截图亮度判断）。"""
        platform_type = platform.platform

        if platform_type == "android":
            # Android: 直接通过 uiautomator2 检测
            device = context or platform._device_clients.get(platform._current_device)
            if device:
                info = device.info
                return info.get("screenOn", True)
            return True

        # iOS: 通过截图亮度判断（WDA 没有 screenOn API）
        try:
            screenshot_bytes = platform.take_screenshot(context)
            if not screenshot_bytes:
                logger.warning("Failed to take screenshot, assuming screen is off")
                return False

            import io
            from PIL import Image

            img = Image.open(io.BytesIO(screenshot_bytes))
            # 转换为灰度图
            gray_img = img.convert("L")
            # 计算平均亮度
            pixels = list(gray_img.getdata())
            avg_brightness = sum(pixels) / len(pixels)
            logger.info(f"Screenshot average brightness: {avg_brightness}")

            # 亮度阈值：低于 10 认为屏幕熄灭（全黑）
            # 熄屏时截图通常是纯黑（亮度接近 0）
            is_screen_on = avg_brightness > 10
            logger.info(f"Screen on detected via brightness: {is_screen_on}")
            return is_screen_on

        except Exception as e:
            logger.warning(f"Failed to check screen brightness: {e}")
            # 检测失败时假设屏幕亮着（安全起见）
            return True

    def _wake_screen(self, platform: "PlatformManager", context: object) -> None:
        """唤醒屏幕。"""
        platform_type = platform.platform

        if platform_type == "ios":
            client = context or platform._device_clients.get(platform._current_device)
            if client and hasattr(client, "wake_screen"):
                client.wake_screen()
                logger.info("iOS screen awakened via HOME key")

        elif platform_type == "android":
            device = context or platform._device_clients.get(platform._current_device)
            if device:
                device.screen_on()
                logger.info("Android screen awakened")

    def _swipe_unlock(self, platform: "PlatformManager", context: object) -> None:
        """滑动解锁界面（旧方法，保留兼容）。"""
        platform_type = platform.platform

        if platform_type == "ios":
            client = context or platform._device_clients.get(platform._current_device)
            if client and hasattr(client, "swipe_up_for_unlock"):
                client.swipe_up_for_unlock()
                logger.info("iOS swipe up for unlock")

        elif platform_type == "android":
            device = context or platform._device_clients.get(platform._current_device)
            if device:
                device.unlock()
                logger.info("Android unlock via swipe")

    def _get_unlock_method(self, platform: "PlatformManager", resolution: tuple[int, int] | None) -> str:
        """获取解锁方式（home_key 或 swipe_up）。"""
        unlock_config = getattr(platform, "_unlock_config", {})
        resolution_key = f"{resolution[0]}x{resolution[1]}" if resolution else "default"

        # iOS 解锁方式配置
        if platform.platform == "ios":
            methods = unlock_config.get("ios_unlock_method", {})
            method = methods.get(resolution_key, methods.get("default", "home_key"))
            logger.info(f"iOS unlock method for {resolution_key}: {method}")
            return method

        # Android 默认使用 swipe
        return "swipe_up"

    def _trigger_password_screen(
        self, platform: "PlatformManager", context: object, method: str
    ) -> None:
        """触发密码输入界面。"""
        platform_type = platform.platform

        if platform_type == "ios":
            client = context or platform._device_clients.get(platform._current_device)
            if client:
                if method == "home_key":
                    # iPhone 8/SE 等机型：唤醒后再按 HOME 键出现密码界面
                    client.press_button("home")
                    logger.info("iOS pressed HOME key to show password screen")
                elif method == "swipe_up":
                    # iPhone X/11/14 等机型：向上滑动出现密码界面
                    client.swipe_up_for_unlock()
                    logger.info("iOS swipe up for unlock")

        elif platform_type == "android":
            device = context or platform._device_clients.get(platform._current_device)
            if device:
                device.unlock()
                logger.info("Android unlock via swipe")

    def _tap_digit(self, platform: "PlatformManager", context: object, x: int, y: int, scale_factor: int = 1) -> None:
        """点击密码数字（配置使用物理坐标，点击时转换）。"""
        platform_type = platform.platform

        if platform_type == "ios":
            client = context or platform._device_clients.get(platform._current_device)
            if client:
                # iOS: 物理坐标转换为 WDA 逻辑坐标
                wx = x // scale_factor
                wy = y // scale_factor
                client.tap(wx, wy)

        elif platform_type == "android":
            device = context or platform._device_clients.get(platform._current_device)
            if device:
                # Android: 直接使用物理坐标
                device.click(x, y)

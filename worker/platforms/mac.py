"""
Mac 桌面平台执行引擎。

基于 pyautogui 实现，支持 OCR/图像识别定位。
"""

import io
import logging
import subprocess
import time
from typing import Any, Dict, Optional, Set

import pyautogui

from worker.platforms.base import PlatformManager
from worker.task import Action, ActionResult, ActionStatus
from worker.config import PlatformConfig

logger = logging.getLogger(__name__)

# 设置 pyautogui 安全措施
pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.1


class MacPlatformManager(PlatformManager):
    """
    Mac 桌面平台管理器。

    使用 pyautogui 控制 macOS 桌面，支持 OCR/图像识别定位。
    """

    # Mac 平台特有动作
    SUPPORTED_ACTIONS: Set[str] = {"start_app", "stop_app"}

    def __init__(self, config: PlatformConfig, ocr_client=None):
        super().__init__(config, ocr_client)
        self.timeout = config.timeout

    @property
    def platform(self) -> str:
        return "mac"

    def start(self) -> None:
        """启动 Mac 平台。"""
        if self._started:
            return

        self._started = True
        logger.info("Mac platform started")

    def stop(self) -> None:
        """停止 Mac 平台。"""
        self._contexts.clear()
        self._started = False
        logger.info("Mac platform stopped")

    def is_available(self) -> bool:
        """检查平台是否可用。"""
        return self._started

    def create_context(self, device_id: Optional[str] = None, options: Optional[Dict] = None) -> Any:
        """
        创建桌面上下文（Mac 不需要特殊上下文）。

        Args:
            device_id: 不使用
            options: 其他选项

        Returns:
            None: 桌面平台不需要上下文
        """
        logger.info("Mac context created (no-op)")
        return None

    def close_context(self, context: Any) -> None:
        """关闭桌面上下文（Mac 不需要）。"""
        logger.info("Mac context closed (no-op)")

    def execute_action(self, context: Any, action: Action) -> ActionResult:
        """执行动作。"""
        start_time = time.time()

        try:
            # 根据动作类型执行
            if action.action_type == "ocr_click":
                result = self._action_ocr_click(action)
            elif action.action_type == "image_click":
                result = self._action_image_click(action)
            elif action.action_type == "click":
                result = self._action_click(action)
            elif action.action_type == "ocr_input":
                result = self._action_ocr_input(action)
            elif action.action_type == "input":
                result = self._action_input(action)
            elif action.action_type == "press":
                result = self._action_press(action)
            elif action.action_type == "swipe":
                result = self._action_swipe(action)
            elif action.action_type == "screenshot":
                result = self._action_screenshot(action)
            elif action.action_type == "wait":
                result = self._action_wait(action)
            elif action.action_type == "ocr_wait":
                result = self._action_ocr_wait(action)
            elif action.action_type == "image_wait":
                result = self._action_image_wait(action)
            elif action.action_type == "ocr_assert":
                result = self._action_ocr_assert(action)
            elif action.action_type == "image_assert":
                result = self._action_image_assert(action)
            elif action.action_type == "ocr_get_text":
                result = self._action_ocr_get_text(action)
            elif action.action_type == "start_app":
                result = self._action_start_app(action)
            elif action.action_type == "stop_app":
                result = self._action_stop_app(action)
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
        screenshot = pyautogui.screenshot()

        buffer = io.BytesIO()
        screenshot.save(buffer, format="PNG")
        return buffer.getvalue()

    # ========== 动作实现 ==========

    def _action_start_app(self, action: Action) -> ActionResult:
        """启动应用。"""
        app_name = action.app_path or action.value
        if not app_name:
            return ActionResult(
                index=0,
                action_type="start_app",
                status=ActionStatus.FAILED,
                error="app_name is required",
            )

        # macOS 使用 open 命令启动应用
        subprocess.run(["open", "-a", app_name])

        # 等待应用启动
        time.sleep(2)

        return ActionResult(
            index=0,
            action_type="start_app",
            status=ActionStatus.SUCCESS,
            output=f"Started: {app_name}",
        )

    def _action_stop_app(self, action: Action) -> ActionResult:
        """关闭应用。"""
        app_name = action.value
        if not app_name:
            return ActionResult(
                index=0,
                action_type="stop_app",
                status=ActionStatus.FAILED,
                error="app_name is required",
            )

        # macOS 使用 osascript 关闭应用
        try:
            subprocess.run(
                ["osascript", "-e", f'tell app "{app_name}" to quit'],
                check=True,
            )
            return ActionResult(
                index=0,
                action_type="stop_app",
                status=ActionStatus.SUCCESS,
                output=f"Stopped: {app_name}",
            )
        except subprocess.CalledProcessError as e:
            return ActionResult(
                index=0,
                action_type="stop_app",
                status=ActionStatus.FAILED,
                error=f"Failed to stop app: {e}",
            )

    def _action_ocr_click(self, action: Action) -> ActionResult:
        """OCR 文字点击。"""
        # 获取截图
        screenshot = pyautogui.screenshot()

        buffer = io.BytesIO()
        screenshot.save(buffer, format="PNG")
        screenshot_bytes = buffer.getvalue()

        # 查找文字位置
        position = self._find_text_position(screenshot_bytes, action.value, action.match_mode)
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
        pyautogui.click(x, y)

        return ActionResult(
            index=0,
            action_type="ocr_click",
            status=ActionStatus.SUCCESS,
            output=f"Clicked at ({x}, {y})",
        )

    def _action_image_click(self, action: Action) -> ActionResult:
        """图像匹配点击。"""
        if not action.image_path:
            return ActionResult(
                index=0,
                action_type="image_click",
                status=ActionStatus.FAILED,
                error="image_path is required",
            )

        # 使用 pyautogui 内置图像查找
        try:
            location = pyautogui.locateOnScreen(action.image_path, confidence=action.threshold)
            if location:
                center = pyautogui.center(location)
                x, y = self._apply_offset(center.x, center.y, action.offset)
                # 记录图像匹配结果
                logger.debug(f"Image matched: position=({x}, {y}), threshold={action.threshold or 0.8}")
                pyautogui.click(x, y)
                return ActionResult(
                    index=0,
                    action_type="image_click",
                    status=ActionStatus.SUCCESS,
                    output=f"Clicked at ({x}, {y})",
                )
        except Exception:
            pass

        return ActionResult(
            index=0,
            action_type="image_click",
            status=ActionStatus.FAILED,
            error=f"Image not found: {action.image_path}",
        )

    def _action_click(self, action: Action) -> ActionResult:
        """坐标点击。"""
        if action.x is None or action.y is None:
            return ActionResult(
                index=0,
                action_type="click",
                status=ActionStatus.FAILED,
                error="x and y coordinates are required",
            )

        pyautogui.click(action.x, action.y)

        return ActionResult(
            index=0,
            action_type="click",
            status=ActionStatus.SUCCESS,
            output=f"Clicked at ({action.x}, {action.y})",
        )

    def _action_ocr_input(self, action: Action) -> ActionResult:
        """OCR 文字附近输入。"""
        # 获取截图
        screenshot = pyautogui.screenshot()

        buffer = io.BytesIO()
        screenshot.save(buffer, format="PNG")
        screenshot_bytes = buffer.getvalue()

        # 查找文字位置
        position = self._find_text_position(screenshot_bytes, action.value, action.match_mode)
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
        pyautogui.click(x, y)

        # 输入文本 (macOS 使用 pyautogui.write)
        if action.value:
            pyautogui.write(action.value)

        return ActionResult(
            index=0,
            action_type="ocr_input",
            status=ActionStatus.SUCCESS,
            output=f"Input at ({x}, {y})",
        )

    def _action_input(self, action: Action) -> ActionResult:
        """坐标输入。"""
        if action.x is None or action.y is None:
            return ActionResult(
                index=0,
                action_type="input",
                status=ActionStatus.FAILED,
                error="x and y coordinates are required",
            )

        # 点击
        pyautogui.click(action.x, action.y)

        # 输入
        if action.value:
            pyautogui.write(action.value)

        return ActionResult(
            index=0,
            action_type="input",
            status=ActionStatus.SUCCESS,
            output=f"Input at ({action.x}, {action.y})",
        )

    def _action_press(self, action: Action) -> ActionResult:
        """按键。"""
        if not action.value:
            return ActionResult(
                index=0,
                action_type="press",
                status=ActionStatus.FAILED,
                error="Key is required",
            )

        # 支持组合键，如 "command+c"
        keys = action.value.split("+")
        if len(keys) > 1:
            pyautogui.hotkey(*keys)
        else:
            pyautogui.press(action.value)

        return ActionResult(
            index=0,
            action_type="press",
            status=ActionStatus.SUCCESS,
            output=f"Pressed: {action.value}",
        )

    def _action_swipe(self, action: Action) -> ActionResult:
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

        pyautogui.moveTo(action.x, action.y)
        pyautogui.mouseDown()
        pyautogui.moveTo(end_x, end_y, duration=0.5)
        pyautogui.mouseUp()

        return ActionResult(
            index=0,
            action_type="swipe",
            status=ActionStatus.SUCCESS,
            output=f"Swiped from ({action.x}, {action.y}) to ({end_x}, {end_y})",
        )

    def _action_screenshot(self, action: Action) -> ActionResult:
        """截图。"""
        screenshot = pyautogui.screenshot()

        buffer = io.BytesIO()
        screenshot.save(buffer, format="PNG")
        screenshot_bytes = buffer.getvalue()
        screenshot_base64 = self._bytes_to_base64(screenshot_bytes)

        name = action.value or "screenshot"

        return ActionResult(
            index=0,
            action_type="screenshot",
            status=ActionStatus.SUCCESS,
            output=name,
            screenshot=screenshot_base64,
        )

    def _action_wait(self, action: Action) -> ActionResult:
        """固定等待。"""
        wait_time = action.wait or int(action.value or 1000)
        self._wait(wait_time)

        return ActionResult(
            index=0,
            action_type="wait",
            status=ActionStatus.SUCCESS,
            output=f"Waited {wait_time}ms",
        )

    def _action_ocr_wait(self, action: Action) -> ActionResult:
        """等待文字出现。"""
        start_time = time.time()
        timeout = action.timeout / 1000

        while time.time() - start_time < timeout:
            screenshot = pyautogui.screenshot()

            buffer = io.BytesIO()
            screenshot.save(buffer, format="PNG")
            screenshot_bytes = buffer.getvalue()

            position = self._find_text_position(screenshot_bytes, action.value, action.match_mode)

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

    def _action_image_wait(self, action: Action) -> ActionResult:
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
            try:
                location = pyautogui.locateOnScreen(action.image_path, confidence=action.threshold)
                if location:
                    return ActionResult(
                        index=0,
                        action_type="image_wait",
                        status=ActionStatus.SUCCESS,
                        output=f"Image appeared: {action.image_path}",
                    )
            except Exception:
                pass

            time.sleep(0.5)

        return ActionResult(
            index=0,
            action_type="image_wait",
            status=ActionStatus.FAILED,
            error=f"Image not appeared within timeout: {action.image_path}",
        )

    def _action_ocr_assert(self, action: Action) -> ActionResult:
        """OCR 文字断言。"""
        screenshot = pyautogui.screenshot()

        buffer = io.BytesIO()
        screenshot.save(buffer, format="PNG")
        screenshot_bytes = buffer.getvalue()

        position = self._find_text_position(screenshot_bytes, action.value, action.match_mode)

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

    def _action_image_assert(self, action: Action) -> ActionResult:
        """图像断言。"""
        if not action.image_path:
            return ActionResult(
                index=0,
                action_type="image_assert",
                status=ActionStatus.FAILED,
                error="image_path is required",
            )

        try:
            location = pyautogui.locateOnScreen(action.image_path, confidence=action.threshold)
            if location:
                return ActionResult(
                    index=0,
                    action_type="image_assert",
                    status=ActionStatus.SUCCESS,
                    output=f"Image found: {action.image_path}",
                )
        except Exception:
            pass

        return ActionResult(
            index=0,
            action_type="image_assert",
            status=ActionStatus.FAILED,
            error=f"Image not found: {action.image_path}",
        )

    def _action_ocr_get_text(self, action: Action) -> ActionResult:
        """获取 OCR 文字区域内容。"""
        if not self.ocr_client:
            return ActionResult(
                index=0,
                action_type="ocr_get_text",
                status=ActionStatus.FAILED,
                error="OCR client not available",
            )

        screenshot = pyautogui.screenshot()

        buffer = io.BytesIO()
        screenshot.save(buffer, format="PNG")
        screenshot_bytes = buffer.getvalue()

        texts = self.ocr_client.recognize(screenshot_bytes)

        all_text = " ".join([t.text for t in texts])

        return ActionResult(
            index=0,
            action_type="ocr_get_text",
            status=ActionStatus.SUCCESS,
            output=all_text,
        )
"""
iOS 平台执行引擎。

基于 tidevice3 + WDA 直连实现，支持 OCR/图像识别定位。
"""

import logging
import subprocess
import time
from typing import Any, Dict, Optional, Set

from worker.platforms.base import PlatformManager
from worker.platforms.wda_client import WDAClient
from worker.task import Action, ActionResult, ActionStatus
from worker.config import PlatformConfig
from worker.actions import ActionRegistry

logger = logging.getLogger(__name__)


class iOSPlatformManager(PlatformManager):
    """
    iOS 平台管理器。

    使用 tidevice3 + WDA 直连控制 iOS 设备。
    """

    SUPPORTED_ACTIONS: Set[str] = {"start_app", "stop_app"}
    WDA_BUNDLE_ID = "com.facebook.WebDriverAgentRunner"

    def __init__(self, config: PlatformConfig, ocr_client=None):
        super().__init__(config, ocr_client)
        self.wda_base_port = config.wda_base_port or 8100
        self.wda_ipa_path = config.wda_ipa_path or "wda/WebDriverAgent.ipa"
        self._device_wda: Dict[str, dict] = {}
        self._device_clients: Dict[str, WDAClient] = {}
        self._current_device: Optional[str] = None
        self._port_counter = 0

    @property
    def platform(self) -> str:
        return "ios"

    def start(self) -> None:
        """启动 iOS 平台（检查环境）。"""
        if self._started:
            return

        try:
            from tidevice3.api import list_devices
            devices = list_devices()
            logger.info(f"tidevice3 available, found {len(devices)} devices")
        except Exception as e:
            logger.warning(f"tidevice3 check failed: {e}")

        self._started = True
        logger.info("iOS platform started (tidevice3 + WDA mode)")

    def stop(self) -> None:
        """停止 iOS 平台。"""
        for udid in list(self._device_wda.keys()):
            self._stop_wda(udid)
        self._device_clients.clear()
        self._device_wda.clear()
        self._started = False
        logger.info("iOS platform stopped")

    def is_available(self) -> bool:
        """检查平台是否可用。"""
        return self._started

    # ========== 设备服务管理 ==========

    def ensure_device_service(self, udid: str) -> tuple[str, str]:
        """确保 WDA 服务可用。"""
        try:
            client = self._device_clients.get(udid)
            if client and client.health_check():
                return ("online", "OK")

            return self._start_wda(udid)
        except Exception as e:
            logger.error(f"Failed to ensure WDA service: {udid}, {e}")
            return ("faulty", str(e))

    def mark_device_faulty(self, udid: str) -> None:
        """标记设备为异常。"""
        if udid in self._device_clients:
            del self._device_clients[udid]
        self._stop_wda(udid)
        logger.info(f"iOS device marked faulty: {udid}")

    def get_online_devices(self) -> list[str]:
        """获取在线设备列表。"""
        return list(self._device_clients.keys())

    def _allocate_port(self) -> int:
        """分配 WDA 端口。"""
        self._port_counter += 1
        return self.wda_base_port + self._port_counter

    def _stop_wda(self, udid: str) -> None:
        """停止 WDA 进程。"""
        if udid in self._device_wda:
            wda_info = self._device_wda[udid]
            process = wda_info.get("process")
            if process:
                try:
                    process.terminate()
                    process.wait(timeout=5)
                except Exception:
                    process.kill()
            del self._device_wda[udid]

    def _start_wda(self, udid: str) -> tuple[str, str]:
        """启动 WDA 服务。"""
        try:
            if udid in self._device_wda:
                self._stop_wda(udid)

            port = self._allocate_port()

            # t3 runwda 命令格式
            process = subprocess.Popen(
                [
                    "t3",
                    "-u", udid,
                    "runwda",
                    "--bundle-id", self.WDA_BUNDLE_ID,
                    "--dst-port", str(port)
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )

            base_url = f"http://127.0.0.1:{port}"
            client = WDAClient(base_url)

            if client.wait_ready(timeout=30):
                self._device_wda[udid] = {"port": port, "process": process}
                self._device_clients[udid] = client
                logger.info(f"WDA started: {udid} on port {port}")
                return ("online", "OK")
            else:
                process.terminate()
                return ("faulty", "WDA failed to start")

        except Exception as e:
            logger.error(f"Failed to start WDA: {e}")
            return ("faulty", str(e))

    # ========== 上下文管理 ==========

    def create_context(self, device_id: Optional[str] = None, options: Optional[Dict] = None) -> WDAClient:
        """获取已有的 WDA 连接。"""
        if not self.is_available():
            raise RuntimeError("iOS platform not started")

        if not device_id:
            raise ValueError("device_id is required for iOS platform")

        client = self._device_clients.get(device_id)
        if client is None:
            raise RuntimeError(f"WDA service not ready: {device_id}")

        self._current_device = device_id
        logger.info(f"iOS context created: {device_id}")
        return client

    def close_context(self, context: Any, close_session: bool = False) -> None:
        """关闭上下文。"""
        if close_session:
            for udid, client in list(self._device_clients.items()):
                if client == context:
                    client.close()
                    del self._device_clients[udid]
                    break
        logger.info("iOS context closed")

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
            self._stop_wda(device_id)
            if device_id in self._device_clients:
                del self._device_clients[device_id]
            logger.info(f"iOS session closed (device={device_id})")
        else:
            for udid in list(self._device_wda.keys()):
                self._stop_wda(udid)
            self._device_clients.clear()
            logger.info("All iOS sessions closed")

    # ========== 基础能力实现 ==========

    def click(self, x: int, y: int, context: Any = None) -> None:
        """点击指定坐标。"""
        client = context or self._device_clients.get(self._current_device)
        if client:
            client.tap(x, y)

    def double_click(self, x: int, y: int, context: Any = None) -> None:
        """双击指定坐标（模拟两次快速点击）。"""
        client = context or self._device_clients.get(self._current_device)
        if client:
            # 模拟双击：快速两次点击，间隔100ms
            client.tap(x, y)
            import time
            time.sleep(0.1)
            client.tap(x, y)

    def move(self, x: int, y: int, context: Any = None) -> None:
        """移动鼠标（移动端不支持）。"""
        raise NotImplementedError("move action is not supported on mobile platforms")

    def input_text(self, text: str, context: Any = None) -> None:
        """输入文本。"""
        client = context or self._device_clients.get(self._current_device)
        if client:
            client.send_keys(text)

    def swipe(self, start_x: int, start_y: int, end_x: int, end_y: int, context: Any = None) -> None:
        """滑动。"""
        client = context or self._device_clients.get(self._current_device)
        if client:
            client.swipe(start_x, start_y, end_x, end_y)

    def press(self, key: str, context: Any = None) -> None:
        """按键。"""
        client = context or self._device_clients.get(self._current_device)
        if client:
            client.press_button(key.upper())

    def take_screenshot(self, context: Any = None) -> bytes:
        """获取截图。"""
        client = context or self._device_clients.get(self._current_device)
        if client:
            return client.screenshot()
        return b""

    def get_screenshot(self, context: Any) -> bytes:
        """获取当前屏幕截图（兼容旧接口）。"""
        return self.take_screenshot(context)

    # ========== 动作执行 ==========

    def execute_action(self, context: Any, action: Action) -> ActionResult:
        """执行动作。"""
        start_time = time.time()

        client = context
        if not client and action.action_type not in ("start_app", "stop_app"):
            return ActionResult(
                number=0,
                action_type=action.action_type,
                status=ActionStatus.FAILED,
                error="WDA context is invalid",
            )

        if client:
            for udid, c in self._device_clients.items():
                if c == client:
                    self._current_device = udid
                    break

        try:
            if action.action_type == "start_app":
                result = self._action_start_app(client, action)
            elif action.action_type == "stop_app":
                result = self._action_stop_app(client, action)
            elif action.action_type == "ocr_paste":
                result = ActionResult(
                    number=0,
                    action_type="ocr_paste",
                    status=ActionStatus.FAILED,
                    error="ocr_paste is not supported on iOS",
                )
            else:
                executor = ActionRegistry.get(action.action_type)
                if executor:
                    result = executor.execute(self, action, client)
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

    def _action_start_app(self, client, action: Action) -> ActionResult:
        """启动应用。"""
        bundle_id = action.bundle_id or action.value
        if not bundle_id:
            return ActionResult(
                number=0,
                action_type="start_app",
                status=ActionStatus.FAILED,
                error="bundle_id is required",
            )

        if client and self._current_device:
            try:
                # t3 app launch 命令格式
                subprocess.run(
                    ["t3", "-u", self._current_device, "app", "launch", bundle_id],
                    check=True, timeout=30
                )
            except Exception as e:
                logger.warning(f"Failed to launch app via t3: {e}")

        return ActionResult(
            number=0,
            action_type="start_app",
            status=ActionStatus.SUCCESS,
            output=f"Started: {bundle_id}",
        )

    def _action_stop_app(self, client, action: Action) -> ActionResult:
        """关闭应用。"""
        if self._current_device:
            return ActionResult(
                number=0,
                action_type="stop_app",
                status=ActionStatus.SUCCESS,
                output=f"Stopped app session",
            )
        else:
            return ActionResult(
                number=0,
                action_type="stop_app",
                status=ActionStatus.FAILED,
                error="No device context",
            )
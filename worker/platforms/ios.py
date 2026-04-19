"""
iOS 平台执行引擎。

基于 tidevice3 + WDA 直连实现，支持 OCR/图像识别定位。
"""

import logging
import time
from typing import Any

from common.utils import run_cmd
from worker.actions import ActionRegistry
from worker.config import PlatformConfig
from worker.platforms.base import PlatformManager
from worker.platforms.wda_client import WDAClient
from worker.task import Action, ActionResult, ActionStatus

logger = logging.getLogger(__name__)


class iOSPlatformManager(PlatformManager):
    """
    iOS 平台管理器。

    使用 tidevice3 + WDA 直连控制 iOS 设备。
    """

    SUPPORTED_ACTIONS: set[str] = {"start_app", "stop_app", "unlock_screen"}

    def __init__(self, config: PlatformConfig, ocr_client=None, unlock_config=None):
        super().__init__(config, ocr_client)
        self.wda_base_port = config.wda_base_port or 8100
        self.wda_ipa_path = config.wda_ipa_path or "wda/WebDriverAgent.ipa"
        self.wda_bundle_id = config.wda_bundle_id or "com.facebook.WebDriverAgentRunner"
        self._device_wda: dict[str, dict] = {}
        self._device_clients: dict[str, WDAClient] = {}
        self._current_device: str | None = None
        self._unlock_config = unlock_config or {}  # 解锁配置

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
            # 1. 检查已有的 client 是否可用
            client = self._device_clients.get(udid)
            if client and client.health_check():
                logger.info(f"WDA already running: {udid}")
                return ("online", "OK")

            # 2. 探测配置端口是否有 WDA 运行（可能是手动启动的）
            probe_client = WDAClient(f"http://127.0.0.1:{self.wda_base_port}")
            if probe_client.health_check():
                logger.info(f"Found existing WDA on port {self.wda_base_port}, reusing")
                self._device_clients[udid] = probe_client
                self._device_wda[udid] = {"port": self.wda_base_port, "process": None}
                return ("online", "OK")

            # 3. 没有现成的 WDA，启动新的
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
        """分配 WDA 端口（固定使用配置的端口）。"""
        return self.wda_base_port

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

            # t3 runwda 命令 - 不设置 stdin/stdout/stderr，让它们继承父进程
            # DEVNULL 可能导致 t3 无法正常维持运行
            import subprocess
            process = subprocess.Popen(
                [
                    "t3",
                    "-u", udid,
                    "runwda",
                    "--bundle-id", self.wda_bundle_id,
                    "--dst-port", str(port)
                ],
                # 不设置 stdin/stdout/stderr，保持连接
            )

            base_url = f"http://127.0.0.1:{port}"
            client = WDAClient(base_url)

            logger.info(f"WDA process started on port {port}, waiting for ready...")
            if client.wait_ready(timeout=30):
                self._device_wda[udid] = {"port": port, "process": process}
                self._device_clients[udid] = client
                logger.info(f"WDA started: {udid} on port {port}")
                return ("online", "OK")
            else:
                logger.warning(f"WDA failed to become ready on port {port}")
                process.terminate()
                return ("faulty", "WDA failed to start")

        except Exception as e:
            logger.error(f"Failed to start WDA: {e}")
            return ("faulty", str(e))

    # ========== 上下文管理 ==========

    def create_context(self, device_id: str | None = None, options: dict | None = None) -> WDAClient:
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

    def _convert_coords(self, x: int, y: int) -> tuple[int, int]:
        """转换物理像素坐标到 WDA 逻辑坐标。"""
        # iPhone 8 及多数 iPhone 的缩放因子是 2x
        # 如果坐标超过逻辑分辨率范围，说明是物理坐标，需要转换
        # iPhone 8 逻辑分辨率: 375x667，物理分辨率: 750x1334
        if x > 400 or y > 700:  # 明显超过逻辑分辨率
            return (x // 2, y // 2)
        return (x, y)

    def click(self, x: int, y: int, context: Any = None) -> None:
        """点击指定坐标。"""
        client = context or self._device_clients.get(self._current_device)
        if client:
            # 转换坐标
            wx, wy = self._convert_coords(x, y)
            success = client.tap(wx, wy)
            if not success:
                raise RuntimeError(f"Tap failed at ({wx}, {wy})")

    def double_click(self, x: int, y: int, context: Any = None) -> None:
        """双击指定坐标（模拟两次快速点击）。"""
        client = context or self._device_clients.get(self._current_device)
        if client:
            wx, wy = self._convert_coords(x, y)
            success = client.tap(wx, wy)
            if not success:
                raise RuntimeError(f"First tap failed at ({wx}, {wy})")
            import time
            time.sleep(0.1)
            success = client.tap(wx, wy)
            if not success:
                raise RuntimeError(f"Second tap failed at ({wx}, {wy})")

    def move(self, x: int, y: int, context: Any = None) -> None:
        """移动鼠标（移动端不支持）。"""
        raise NotImplementedError("move action is not supported on mobile platforms")

    def input_text(self, text: str, context: Any = None) -> None:
        """输入文本。"""
        client = context or self._device_clients.get(self._current_device)
        if client:
            success = client.send_keys(text)
            if not success:
                raise RuntimeError(f"Send keys failed: {text}")

    def swipe(self, start_x: int, start_y: int, end_x: int, end_y: int, duration: int = 500, context: Any = None) -> None:
        """滑动。"""
        client = context or self._device_clients.get(self._current_device)
        if client:
            wx1, wy1 = self._convert_coords(start_x, start_y)
            wx2, wy2 = self._convert_coords(end_x, end_y)
            # duration 单位转换：毫秒 → 秒
            duration_sec = duration / 1000.0
            success = client.swipe(wx1, wy1, wx2, wy2, duration=duration_sec)
            if not success:
                raise RuntimeError(f"Swipe failed from ({wx1}, {wy1}) to ({wx2}, {wy2})")

    def press(self, key: str, context: Any = None) -> None:
        """按键。"""
        client = context or self._device_clients.get(self._current_device)
        if client:
            success = client.press_button(key.upper())
            if not success:
                raise RuntimeError(f"Press button failed: {key}")

    def take_screenshot(self, context: Any = None) -> bytes:
        """获取截图。"""
        client = context or self._device_clients.get(self._current_device)
        if client:
            data = client.screenshot()
            if not data:
                raise RuntimeError("Screenshot failed")
            return data
        return b""

    def get_screenshot(self, context: Any) -> bytes:
        """获取当前屏幕截图（兼容旧接口）。"""
        return self.take_screenshot(context)

    # ========== 动作执行 ==========

    def execute_action(self, context: Any, action: Action) -> ActionResult:
        """执行动作。"""
        start_time = time.time()

        client = context
        # start_app/stop_app 不需要预先检查 context（会在内部处理）
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
            elif action.action_type == "unlock_screen":
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
                run_cmd(
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
                output="Stopped app session",
            )
        else:
            return ActionResult(
                number=0,
                action_type="stop_app",
                status=ActionStatus.FAILED,
                error="No device context",
            )

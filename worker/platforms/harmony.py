"""
鸿蒙平台执行引擎。

以官方 HOScrcpy Java 会话作为低延迟视频和输入主通道，HDC 保留为
设备发现、按键、文本、应用管理和故障回退通道。
"""

import logging
import time
import tempfile
import os
import io
from typing import Any, Optional

from worker.actions import ActionRegistry
from worker.config import PlatformConfig
from worker.platforms.base import PlatformManager
from worker.platforms.harmony_hdc import (
    HarmonyHdcWrapper,
    HarmonyError,
    DeviceNotFoundError,
    list_devices,
    _find_hdc_path,
)
from worker.platforms.harmony_hdc_process import stop_owned_hdc_processes
from worker.platforms.harmony_keycodes import HARMONY_KEY_MAP
from worker.platforms.harmony_official import (
    HarmonyOfficialError,
    HarmonyOfficialSession,
    HarmonyOfficialSessionManager,
)
from worker.task import Action, ActionResult, ActionStatus

logger = logging.getLogger(__name__)


class HarmonyPlatformManager(PlatformManager):
    """
    鸿蒙平台管理器。

    使用官方 Java 会话控制视频、触摸和鼠标，HDC 提供兼容能力。
    """

    # 鸿蒙没有 sidecar，因此录屏、实时窗口流和宿主机命令不加入白名单。
    MOBILE_ACTIONS: set[str] = {
        "click", "double_click", "swipe", "drag", "input", "press", "screenshot", "wait",
        "start_app", "stop_app", "unlock_screen",
        "ocr_click", "ocr_input", "ocr_wait", "ocr_assert", "ocr_get_text",
        "ocr_double_click", "ocr_exist", "ocr_get_position",
        "image_click", "image_wait", "image_assert", "image_double_click",
        "image_exist", "image_get_position", "image_click_near_text",
        "ocr_click_same_row_text", "ocr_click_same_row_image",
        "ocr_check_same_row_text", "ocr_check_same_row_image",
    }
    PC_ACTIONS: set[str] = {
        "click", "double_click", "right_click", "move", "swipe", "drag", "input", "press", "screenshot", "wait",
        "start_app", "stop_app", "unlock_screen", "activate_window",
        "ocr_click", "ocr_input", "ocr_wait", "ocr_assert", "ocr_get_text", "ocr_double_click",
        "ocr_exist", "ocr_get_position", "image_click", "image_wait", "image_assert",
        "image_double_click", "image_exist", "image_get_position", "image_click_near_text",
        "ocr_click_same_row_text", "ocr_click_same_row_image",
        "ocr_check_same_row_text", "ocr_check_same_row_image",
    }

    KEY_MAP = HARMONY_KEY_MAP

    def __init__(
        self,
        config: PlatformConfig,
        ocr_client=None,
        unlock_config=None,
        device_type: str = "harmony_mobile",
        official_config: Optional[dict[str, Any]] = None,
    ):
        """
        初始化鸿蒙平台管理器。

        Args:
            config: 平台配置
            ocr_client: OCR 客户端
            unlock_config: 解锁配置（可选）
        """
        super().__init__(config, ocr_client)
        self._device_clients: dict[str, HarmonyHdcWrapper] = {}
        self._hdc_path: Optional[str] = None
        self._current_device: Optional[str] = None
        self._unlock_config = unlock_config or {}  # 解锁配置
        self._device_type = device_type
        self._official_sessions = HarmonyOfficialSessionManager(
            device_type,
            official_config,
        )
        self._official_sessions.set_device_lock_checker(self._is_device_locked_for_official)

    @property
    def platform(self) -> str:
        """平台名称。"""
        return self._device_type

    def get_supported_actions(self) -> set[str]:
        """返回当前鸿蒙设备形态真实支持的动作。"""
        if self._device_type == "harmony_mobile":
            return set(self.MOBILE_ACTIONS)
        return set(self.PC_ACTIONS)

    def start(self) -> None:
        """
        启动鸿蒙平台。

        检查 HDC 工具是否可用。
        """
        if self._started:
            return

        # 查找 HDC 工具路径
        self._hdc_path = _find_hdc_path(self.config.hdc_path)

        if self._hdc_path is None:
            logger.warning("HDC 工具未找到，鸿蒙平台可能不可用")
        else:
            logger.info(f"HDC 工具已就绪: {self._hdc_path}")
        self._official_sessions.set_hdc_path(self._hdc_path)

        self._started = True
        logger.info("Harmony platform started")

    def stop(self) -> None:
        """
        停止鸿蒙平台。

        清理所有设备连接。
        """
        self._official_sessions.stop_all()
        self._device_clients.clear()
        self._current_device = None
        stop_owned_hdc_processes()
        self._started = False
        logger.info("Harmony platform stopped")

    def is_available(self) -> bool:
        """
        检查平台是否可用。

        Returns:
            bool: 平台是否可用（HDC 工具存在）
        """
        return self._started and self._hdc_path is not None

    # ========== 设备服务管理 ==========

    def ensure_device_service(self, udid: str) -> tuple[str, str]:
        """
        确保设备服务可用（由 DeviceMonitor 调用）。

        Args:
            udid: 设备 UDID（序列号）

        Returns:
            tuple[str, str]: (status, message) - status 为 "online" 或 "faulty"
        """
        try:
            # 尝试获取或创建设备客户端
            client = self._device_clients.get(udid)

            if client:
                # 检查现有连接是否有效
                if client.is_online():
                    if self._official_sessions.prewarm_on_device_ready:
                        self._prewarm_if_screen_available(udid, client)
                    return ("online", "OK")
                else:
                    # 连接失效，移除旧的客户端
                    self._official_sessions.stop_session(udid)
                    del self._device_clients[udid]

            # 创建新的客户端
            client = HarmonyHdcWrapper(udid, self._hdc_path)
            self._device_clients[udid] = client

            logger.info(f"Harmony device service ready: {udid}")
            if self._official_sessions.prewarm_on_device_ready:
                self._prewarm_if_screen_available(udid, client)
            return ("online", "OK")

        except DeviceNotFoundError as e:
            logger.error(f"设备未找到: {udid}, {e}")
            return ("faulty", str(e))
        except HarmonyError as e:
            logger.error(f"设备服务初始化失败: {udid}, {e}")
            return ("faulty", str(e))
        except Exception as e:
            logger.error(f"Failed to ensure device service: {udid}, {e}")
            return ("faulty", str(e))

    @staticmethod
    def _is_locked_client(client: HarmonyHdcWrapper) -> bool:
        """查询客户端锁屏状态；查询失败时按锁屏处理。"""
        try:
            return bool(client.is_locked())
        except Exception:
            return True

    def _is_device_locked_for_official(self, udid: str) -> bool:
        """供官方会话管理器判断设备是否可启动屏幕采集。"""
        client = self._device_clients.get(udid)
        return self._is_locked_client(client) if client is not None else False

    def _prewarm_if_screen_available(self, udid: str, client: HarmonyHdcWrapper) -> None:
        """屏幕已解锁时才预热官方链路，避免设备启动阶段反复拉起 Java。"""
        if self._is_locked_client(client):
            logger.debug("Harmony 设备处于锁屏状态，跳过官方会话预热: %s", udid)
            return
        self._official_sessions.prewarm(udid)

    def mark_device_faulty(self, udid: str) -> None:
        """
        标记设备为异常。

        Args:
            udid: 设备 UDID（序列号）
        """
        if udid in self._device_clients:
            del self._device_clients[udid]
        self._official_sessions.stop_session(udid)
        if self._current_device == udid:
            self._current_device = None
        logger.info(f"Harmony device marked faulty: {udid}")

    def get_online_devices(self) -> list[str]:
        """
        获取在线设备列表。

        Returns:
            list[str]: 在线设备 UDID（序列号）列表
        """
        if not self._hdc_path:
            logger.warning("HDC 工具未找到，无法列出设备")
            return []

        try:
            devices = list_devices(self._hdc_path)
            logger.debug(f"在线鸿蒙设备: {devices}")
            return devices
        except Exception as e:
            logger.error(f"获取在线设备列表失败: {e}")
            return []

    def get_current_device(self) -> Optional[str]:
        """
        获取当前设备 ID。

        Returns:
            Optional[str]: 当前设备 ID
        """
        return self._current_device

    # ========== 执行上下文管理 ==========

    def create_context(self, device_id: Optional[str] = None, options: Optional[dict] = None) -> Any:
        """
        创建执行上下文。

        Args:
            device_id: 设备 ID（序列号，鸿蒙平台必填）
            options: 其他选项（可选）

        Returns:
            HarmonyHdcWrapper: HDC wrapper 实例

        Raises:
            ValueError: 未提供 device_id
            DeviceNotFoundError: 设备未在线
        """
        if not device_id:
            raise ValueError("鸿蒙平台必须提供 device_id")

        # 尝试获取已有的客户端
        client = self._device_clients.get(device_id)

        if client and client.is_online():
            logger.debug(f"使用已有的设备客户端: {device_id}")
            self._current_device = device_id
            return client

        # 创建新的客户端
        try:
            client = HarmonyHdcWrapper(device_id, self._hdc_path)
            self._device_clients[device_id] = client
            self._current_device = device_id
            logger.info(f"创建新的设备客户端: {device_id}")
            return client
        except HarmonyError as e:
            logger.error(f"创建设备客户端失败: {device_id}, {e}")
            raise

    def close_context(self, context: Any, close_session: bool = False) -> None:
        """
        关闭执行上下文。

        Args:
            context: 执行上下文（HarmonyHdcWrapper）
            close_session: 是否关闭整个会话（True=移除客户端，False=保持客户端）
        """
        if not isinstance(context, HarmonyHdcWrapper):
            logger.warning(f"无效的上下文类型: {type(context)}")
            return

        if close_session:
            # 移除客户端
            serial = context.serial
            if serial in self._device_clients:
                del self._device_clients[serial]
                if self._current_device == serial:
                    self._current_device = None
                logger.info(f"关闭设备会话: {serial}")
            self._official_sessions.stop_session(serial)
        else:
            # HDC 客户端可复用；官方 Java 会话释放任务租约后进入 10 分钟空闲保活。
            self._official_sessions.release(context.serial, f"task:{id(context)}")
            logger.debug(f"保持设备客户端用于复用: {context.serial}")

    # ========== 基础操作方法 ==========

    def get_screenshot(self, context: Any) -> bytes:
        """
        获取截图。

        Args:
            context: 执行上下文（HarmonyHdcWrapper）

        Returns:
            bytes: 截图数据（JPEG 格式）
        """
        client: HarmonyHdcWrapper = context
        serial, official_session = self._get_official_session_for_client(client)
        if official_session:
            try:
                return official_session.get_latest_jpeg(timeout=5.0)
            except HarmonyOfficialError as exc:
                self._handle_official_failure(serial, "截图", exc)
        with tempfile.NamedTemporaryFile(suffix=".jpeg", delete=False) as f:
            temp_path = f.name
        try:
            if not client.screenshot(temp_path):
                raise HarmonyError("HDC 截图失败")
            with open(temp_path, "rb") as f:
                data = f.read()
            if not data:
                raise HarmonyError("HDC 截图为空")
            try:
                from PIL import Image

                with Image.open(io.BytesIO(data)) as image:
                    image.verify()
            except Exception as exc:
                raise HarmonyError(f"HDC 截图格式无效: {exc}") from exc
            return data
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def take_screenshot(self, context=None) -> bytes:
        """
        基类方法别名。

        Args:
            context: 执行上下文（可选）

        Returns:
            bytes: 截图数据
        """
        client = context or self._device_clients.get(self._current_device)
        if not client:
            raise HarmonyError("No device context")
        return self.get_screenshot(client)

    def get_official_session(self, udid: str) -> Optional[HarmonyOfficialSession]:
        """获取或启动设备级官方会话（兼容旧调用方）。

        ``auto`` 模式失败时返回 ``None``，调用方继续走 HDC；``official``
        模式失败时抛出异常，避免静默退回高延迟路径。
        """
        return self._official_sessions.get_or_start(udid)

    def acquire_official_session(self, udid: str, owner: str) -> Optional[HarmonyOfficialSession]:
        """获取一份带生命周期租约的官方会话。"""
        return self._official_sessions.acquire(udid, owner)

    def is_device_locked(self, udid: str) -> bool:
        """查询设备锁屏状态，供实时 H.264 链路等待解锁使用。"""
        return self._official_sessions.is_device_locked(udid)

    def release_official_session(self, udid: str, owner: str) -> None:
        """释放一份官方会话租约。"""
        self._official_sessions.release(udid, owner)

    def stop_official_sessions(self) -> None:
        """升级或退出前立即停止本平台的 Java Bridge。"""
        self._official_sessions.stop_all()

    def prewarm_official_session(self, udid: str, owner: str | None = None) -> None:
        """后台预热官方会话，让后续截图/推流直接复用已解码链路。"""
        self._official_sessions.prewarm(udid, owner=owner)

    def _get_official_session_for_client(
        self,
        client: Any,
    ) -> tuple[str | None, HarmonyOfficialSession | None]:
        """为真实 HDC 上下文获取官方会话，兼容轻量测试上下文。

        平台层历史上允许测试和扩展传入只实现动作方法的上下文对象；这类对象
        没有 ``serial`` 时继续直接执行 HDC 回退。
        """
        serial = getattr(client, "serial", None)
        if not serial:
            return None, None
        return serial, self.acquire_official_session(serial, f"task:{id(client)}")

    def _handle_official_failure(
        self,
        udid: str,
        operation: str,
        exc: Exception,
    ) -> None:
        """记录官方链路运行期故障，并按配置决定是否允许 HDC 回退。"""
        if not isinstance(exc, HarmonyOfficialError):
            exc = HarmonyOfficialError(str(exc))
        self._official_sessions.stop_session(udid)
        if not self._official_sessions.fallback_to_legacy:
            raise exc
        logger.warning(
            "鸿蒙官方%s失败，回退 HDC: device=%s, error=%s",
            operation,
            udid,
            exc,
        )

    def click(self, x: int, y: int, duration: int = 0, context=None) -> None:
        """
        点击屏幕坐标。

        Args:
            x: X 坐标
            y: Y 坐标
            duration: 按压时长（毫秒，未使用）
            context: 执行上下文（可选）
        """
        client = context or self._device_clients.get(self._current_device)
        if not client:
            raise HarmonyError("No device context")
        serial, official_session = self._get_official_session_for_client(client)
        if official_session:
            try:
                official_session.tap(x, y, duration)
                return
            except HarmonyOfficialError as exc:
                self._handle_official_failure(serial, "点击", exc)
        success = client.long_tap(x, y, duration) if duration > 0 else client.tap(x, y)
        if not success:
            raise HarmonyError(f"HDC 点击失败: ({x}, {y})")

    def double_click(self, x: int, y: int, context=None) -> None:
        """执行双击。"""
        client = context or self._device_clients.get(self._current_device)
        if not client:
            raise HarmonyError("No device context")
        serial, official_session = self._get_official_session_for_client(client)
        if official_session:
            try:
                official_session.double_click(x, y)
                return
            except HarmonyOfficialError as exc:
                self._handle_official_failure(serial, "双击", exc)
        if not client.double_tap(x, y):
            raise HarmonyError(f"HDC 双击失败: ({x}, {y})")

    def right_click(self, x: int, y: int, context=None) -> None:
        """鸿蒙 PC 右键：uitest 无鼠标右键命令，但长按（longClick）会触发
        上下文菜单，真机实测等价于右键，故用 long_tap 映射。"""
        client = context or self._device_clients.get(self._current_device)
        if not client:
            raise HarmonyError("No device context")
        serial, official_session = self._get_official_session_for_client(client)
        if official_session and self._device_type == "harmony_pc":
            try:
                official_session.right_click(x, y)
                return
            except HarmonyOfficialError as exc:
                self._handle_official_failure(serial, "右键", exc)
        if not client.long_tap(x, y):
            raise HarmonyError(f"HDC 右键（长按）失败: ({x}, {y})")

    def swipe(self, start_x: int, start_y: int, end_x: int, end_y: int, duration: int = 500, steps: Optional[int] = None, context=None) -> None:
        """
        滑动操作。

        Args:
            start_x: 起点 X 坐标
            start_y: 起点 Y 坐标
            end_x: 终点 X 坐标
            end_y: 终点 Y 坐标
            duration: 滑动时长（毫秒）
            steps: 步数（未使用）
            context: 执行上下文（可选）
        """
        client = context or self._device_clients.get(self._current_device)
        if not client:
            raise HarmonyError("No device context")
        serial, official_session = self._get_official_session_for_client(client)
        if official_session:
            try:
                official_session.swipe(
                    start_x,
                    start_y,
                    end_x,
                    end_y,
                    duration,
                    steps,
                )
                return
            except HarmonyOfficialError as exc:
                self._handle_official_failure(serial, "滑动", exc)
        distance = abs(end_x - start_x) + abs(end_y - start_y)
        speed = int(distance * 1000 / duration) if duration > 0 else 1000
        speed = max(200, min(speed, 40000))
        if not client.swipe(start_x, start_y, end_x, end_y, speed):
            raise HarmonyError("HDC 滑动失败")

    def move(self, x: int, y: int, context=None) -> None:
        """
        通过 HDC uinput 移动鼠标。

        Args:
            x: X 坐标
            y: Y 坐标
            context: 执行上下文（可选）
        """
        client = context or self._device_clients.get(self._current_device)
        if not client:
            raise HarmonyError("No device context")
        if self._device_type != "harmony_pc":
            raise NotImplementedError("move action is only supported on Harmony PC")
        serial, official_session = self._get_official_session_for_client(client)
        if official_session:
            try:
                official_session.move_mouse(x, y)
                return
            except HarmonyOfficialError as exc:
                self._handle_official_failure(serial, "鼠标移动", exc)
        if not client.move_mouse(x, y):
            raise HarmonyError(f"HDC 鼠标移动失败: ({x}, {y})")

    def input_text(self, text: str, context=None) -> None:
        """
        输入文本。

        注意：需要先点击输入框获取焦点。

        Args:
            text: 要输入的文本
            context: 执行上下文（可选）
        """
        client = context or self._device_clients.get(self._current_device)
        if not client:
            raise HarmonyError("No device context")
        if not client.input_text(text):
            raise HarmonyError("HDC 文本输入失败")

    def input_text_at(self, x: int, y: int, text: str, context=None) -> None:
        """文本输入。

        坐标 (0, 0) 作为哨兵：前端远程输入固定发送 (0, 0)+text，表示
        "输入到当前聚焦框"，此时走 uitest uiInput text（焦点注入）；
        有真实坐标时用 uitest uiInput inputText x y（坐标点输入）。
        """
        client = context or self._device_clients.get(self._current_device)
        if not client:
            raise HarmonyError("No device context")
        # 无有效坐标（哨兵 0,0）：输入到当前聚焦框
        if x <= 0 and y <= 0:
            if not client.input_text(text):
                raise HarmonyError("HDC 文本输入失败（聚焦框）")
            return
        if not client.input_text_at(x, y, text):
            raise HarmonyError(f"HDC 文本输入失败: ({x}, {y})")

    def press(self, key: str, context=None) -> None:
        """
        按键操作。

        Args:
            key: 按键名称或数字键
            context: 执行上下文（可选）

        Raises:
            ValueError: 不支持的按键
        """
        client = context or self._device_clients.get(self._current_device)
        if not client:
            raise HarmonyError("No device context")
        key_upper = key.upper() if key else ""
        key_code = self.KEY_MAP.get(key_upper)
        if "+" in key:
            raise NotImplementedError("Harmony HDC 暂不支持组合键")
        if key_code:
            if not client.send_key(key_code):
                raise HarmonyError(f"HDC 按键失败: {key}")
        elif key and key.isdigit():
            if not client.send_key(int(key)):
                raise HarmonyError(f"HDC 按键失败: {key}")
        else:
            supported = ", ".join(sorted(self.KEY_MAP.keys()))
            raise ValueError(f"Unsupported key '{key}'. Supported: {supported}")

    # ========== 动作执行 ==========

    def execute_action(self, context: Any, action: Action) -> ActionResult:
        """
        执行动作。

        Args:
            context: 执行上下文（HarmonyHdcWrapper）
            action: 动作对象

        Returns:
            ActionResult: 动作执行结果
        """
        client: HarmonyHdcWrapper = context

        # 平台特有动作
        if action.action_type == "start_app":
            return self._action_start_app(client, action)
        elif action.action_type == "stop_app":
            return self._action_stop_app(client, action)
        elif action.action_type == "activate_window":
            return self._action_activate_window(client, action)
        elif action.action_type == "unlock_screen":
            # 使用 ActionRegistry 执行
            executor = ActionRegistry.get(action.action_type)
            if executor:
                return executor.execute(self, action, context)
            else:
                return ActionResult(action.number, action.action_type, ActionStatus.FAILED, error="unlock_screen executor not found")

        # 通用动作（通过 ActionRegistry）
        executor = ActionRegistry.get(action.action_type)
        if executor:
            return executor.execute(self, action, context)

        return ActionResult(action.number, action.action_type, ActionStatus.FAILED, error=f"Unsupported action: {action.action_type}")

    def _action_start_app(self, client: HarmonyHdcWrapper, action: Action) -> ActionResult:
        """
        启动应用。

        Args:
            client: HDC wrapper 实例
            action: 动作对象

        Returns:
            ActionResult: 执行结果
        """
        package = action.value
        if not package:
            return ActionResult(action.number, "start_app", ActionStatus.FAILED, error="Missing package name")

        try:
            # 检查屏幕状态
            if not client.is_screen_on():
                client.wakeup()
                time.sleep(0.5)

            # 启动应用（默认 EntryAbility）
            ability = (action.params or {}).get("ability", "EntryAbility")
            if not client.start_app(package, ability):
                raise HarmonyError(f"HDC 启动应用失败: {package}/{ability}")

            return ActionResult(action.number, "start_app", ActionStatus.SUCCESS, output=f"App started: {package}")
        except Exception as e:
            logger.error(f"start_app failed: {e}")
            return ActionResult(action.number, "start_app", ActionStatus.FAILED, error=str(e))

    def _action_stop_app(self, client: HarmonyHdcWrapper, action: Action) -> ActionResult:
        """
        停止应用。

        Args:
            client: HDC wrapper 实例
            action: 动作对象

        Returns:
            ActionResult: 执行结果
        """
        package = action.value
        if not package:
            return ActionResult(action.number, "stop_app", ActionStatus.FAILED, error="Missing package name")

        try:
            if not client.stop_app(package):
                raise HarmonyError(f"HDC 停止应用失败: {package}")
            return ActionResult(action.number, "stop_app", ActionStatus.SUCCESS, output=f"App stopped: {package}")
        except Exception as e:
            logger.error(f"stop_app failed: {e}")
            return ActionResult(action.number, "stop_app", ActionStatus.FAILED, error=str(e))

    def _action_activate_window(self, client: HarmonyHdcWrapper, action: Action) -> ActionResult:
        """通过 Bundle 名称和 Ability 名称激活鸿蒙 PC 窗口。

        参数约定：
          value  — Bundle 名称（包名），如 "com.example.app"
          name   — Ability 名称，如 "MainAbility"
        """
        action_number = getattr(action, "number", 0)
        if self._device_type != "harmony_pc":
            return ActionResult(
                action_number,
                "activate_window",
                ActionStatus.FAILED,
                error="activate_window action is only supported on Harmony PC",
            )

        bundle_name = action.value
        ability_name = action.name

        if not bundle_name or not ability_name:
            return ActionResult(
                action_number,
                "activate_window",
                ActionStatus.FAILED,
                error="value（bundle 名称）和 name（ability 名称）均为必填",
            )

        if not isinstance(bundle_name, str) or not isinstance(ability_name, str):
            return ActionResult(
                action_number,
                "activate_window",
                ActionStatus.FAILED,
                error="bundle 名称和 ability 名称必须为字符串",
            )

        try:
            if not client.activate_window(bundle_name, ability_name):
                raise HarmonyError(f"HDC 激活窗口失败: {bundle_name}/{ability_name}")
            return ActionResult(
                action_number,
                "activate_window",
                ActionStatus.SUCCESS,
                output=f"Window activated: {bundle_name}/{ability_name}",
            )
        except Exception as e:
            logger.error(f"activate_window failed: {e}")
            return ActionResult(action_number, "activate_window", ActionStatus.FAILED, error=str(e))

"""
iOS 平台执行引擎。

基于 go-ios + WDA 直连实现，支持 OCR/图像识别定位。
"""

import logging
import time
import subprocess
import threading
from typing import Any, Callable, Optional

from common.utils import run_cmd
from worker.actions import ActionRegistry
from worker.config import PlatformConfig
from worker.platforms.base import PlatformManager
from worker.platforms.go_ios_client import GoIOSClient
from worker.platforms.wda_client import WDAClient
from worker.task import Action, ActionResult, ActionStatus

logger = logging.getLogger(__name__)


class iOSPlatformManager(PlatformManager):
    """
    iOS 平台管理器。

    使用 go-ios + WDA 直连控制 iOS 设备。
    """

    SUPPORTED_ACTIONS: set[str] = {"start_app", "stop_app", "unlock_screen", "pinch"}

    # iOS 按键映射：标准按键名 → WDA 按键名
    # WDA 实际支持的按键：home, volumeup, volumedown（所有机型统一）
    # 注意：WDA 虚拟化了 HOME 键功能，Face ID 机型也可以使用
    # lock 按键 WDA 不支持，需要通过其他方式唤醒屏幕
    KEY_MAP = {
        "HOME": "home",  # WDA 虚拟 HOME 键（所有机型可用）
        "VOLUME_UP": "volumeup",
        "VOLUMEUP": "volumeup",
        "VOLUME_DOWN": "volumedown",
        "VOLUMEDOWN": "volumedown",
    }

    # Face ID 机型 product_type 列表（iPhone X 及之后）
    # iPhone10,3/10,6 = iPhone X, iPhone11,* = iPhone XS/XR, iPhone12,* = iPhone 11
    # iPhone13,* = iPhone 12, iPhone14,* = iPhone 13, iPhone15,* = iPhone 14, iPhone16,* = iPhone 15
    FACE_ID_MODELS = {
        "iPhone10,3", "iPhone10,6",  # iPhone X
        # iPhone 11 系列（iPhone11,x）
        "iPhone11,2", "iPhone11,4", "iPhone11,6", "iPhone11,8",
        # iPhone 12 系列（iPhone13,x）
        "iPhone13,2", "iPhone13,3", "iPhone13,4", "iPhone13,5",
        # iPhone 13 系列（iPhone14,x）
        "iPhone14,2", "iPhone14,3", "iPhone14,4", "iPhone14,5",
        # iPhone 14 系列（iPhone15,x）
        "iPhone15,2", "iPhone15,3", "iPhone15,4", "iPhone15,5",
        # iPhone 15 系列（iPhone16,x）
        "iPhone16,1", "iPhone16,2", "iPhone16,3", "iPhone16,4",
        # iPhone 16 系列（iPhone17,x）- 新增
        "iPhone17,1", "iPhone17,2", "iPhone17,3", "iPhone17,4",
    }

    # WDA 不支持的按键（用于错误提示）
    UNSUPPORTED_KEYS = {
        "BACK": "iOS 无物理返回键，请使用 OCR 点击导航栏返回按钮",
        "ENTER": "iOS 无物理回车键，请使用 OCR 点击键盘上的完成/搜索按钮",
        "ESCAPE": "iOS 无 ESC 键",
        "TAB": "iOS 无 Tab 键",
        "ARROWUP": "iOS 无方向键",
        "ARROWDOWN": "iOS 无方向键",
        "ARROWLEFT": "iOS 无方向键",
        "ARROWRIGHT": "iOS 无方向键",
        "LOCK": "WDA 不支持 LOCK 按键，请使用 HOME 键唤醒屏幕或 unlock_screen 动作",
        "POWER": "WDA 不支持 POWER 按键，请使用 HOME 键唤醒屏幕或 unlock_screen 动作",
    }

    def __init__(self, config: PlatformConfig, ocr_client=None, unlock_config=None):
        super().__init__(config, ocr_client)
        # go-ios 配置
        self.go_ios_path = config.go_ios_path or "tools/go-ios/ios.exe"
        self.agent_port = config.agent_port or 60105
        self.wda_base_port = config.wda_base_port or 8100
        self.mjpeg_base_port = config.mjpeg_base_port or 9100
        self.wda_bundle_id = config.wda_bundle_id or "com.facebook.WebDriverAgentRunner"
        self.wda_testrunner_bundle_id = config.wda_testrunner_bundle_id or self.wda_bundle_id
        self.wda_xctest_config = config.wda_xctest_config or "WebDriverAgentRunner.xctest"

        # GoIOSClient 实例
        self._go_ios: Optional[GoIOSClient] = None

        # 设备状态管理
        self._device_wda: dict[str, dict] = {}  # udid -> {port, mjpeg_port, process, forward_process}
        self._device_clients: dict[str, WDAClient] = {}  # udid -> WDAClient
        self._device_tunnel_info: dict[str, dict] = {}  # udid -> tunnel info
        self._device_product_types: dict[str, str] = {}  # udid -> product_type（用于判断按键支持）
        self._current_device: str | None = None
        self._unlock_config = unlock_config or {}

        # Agent 进程引用（用于异常时重启）
        self._agent_process: subprocess.Popen | None = None

        # Agent 就绪回调（供外部设置，如 Worker 设置触发设备发现）
        self._on_agent_ready: Optional[Callable[[], None]] = None

        # 设备服务启动锁（防止重复启动）
        self._service_locks: dict[str, threading.Lock] = {}

    def set_on_agent_ready(self, callback: Callable[[], None]) -> None:
        """设置 agent 就绪回调。"""
        self._on_agent_ready = callback

    def _get_service_lock(self, udid: str) -> threading.Lock:
        """获取设备服务启动锁。"""
        if udid not in self._service_locks:
            self._service_locks[udid] = threading.Lock()
        return self._service_locks[udid]

    @property
    def platform(self) -> str:
        return "ios"

    # ========== 生命周期管理 ==========

    def start(self) -> None:
        """启动 iOS 平台（后台启动 go-ios agent，不阻塞主线程）。"""
        if self._started:
            return

        # 1. 创建 GoIOSClient
        self._go_ios = GoIOSClient(
            go_ios_path=self.go_ios_path,
            agent_port=self.agent_port,
        )

        # 2. 先设置设备发现模块的 GoIOSClient（让设备发现可以工作）
        from worker.discovery.ios import iOSDiscoverer
        iOSDiscoverer.set_go_ios_client(self._go_ios)

        # 3. 后台启动 agent（不阻塞主线程）
        import threading
        self._agent_thread = threading.Thread(
            target=self._ensure_agent_running_async,
            daemon=True,
        )
        self._agent_thread.start()

        self._started = True
        logger.info("iOS platform started (go-ios + WDA mode, agent starting in background)")

    def _ensure_agent_running_async(self) -> None:
        """后台确保 go-ios agent 运行，成功后触发设备发现。"""
        try:
            self._ensure_agent_running()
            # Agent 启动成功后，触发回调（如触发设备发现）
            if self._on_agent_ready:
                logger.info("Calling agent ready callback")
                self._on_agent_ready()
        except Exception as e:
            logger.error(f"Failed to start go-ios agent in background: {e}")

    def stop(self) -> None:
        """停止 iOS 平台（不关闭进程，保持复用）。"""
        # 只清理内存引用，不关闭 agent、runwda、forward 进程
        self._device_clients.clear()
        self._device_wda.clear()
        self._device_tunnel_info.clear()

        if self._go_ios:
            self._go_ios.close()
            self._go_ios = None

        # 不关闭 agent 进程，保持运行以便下次复用
        # self._agent_process = None

        self._started = False
        logger.info("iOS platform stopped (processes preserved for reuse)")

    def is_available(self) -> bool:
        """检查平台是否可用。"""
        return self._started and self._go_ios is not None

    # ========== Agent 管理 ==========

    def _ensure_agent_running(self) -> None:
        """确保 go-ios agent 正在运行，异常则重启。"""
        # 1. 先检查健康状态
        if self._go_ios.check_agent_health():
            logger.info("go-ios agent already running, reusing")
            return

        # 2. 健康检查失败，先杀掉可能占用端口的进程
        logger.info("go-ios agent health check failed, checking port occupation...")
        self._kill_agent_processes()

        # 3. 启动新 agent
        logger.info("go-ios agent not running, starting...")
        self._agent_process = self._go_ios.start_agent()

        # 4. 等待就绪
        if not self._go_ios.wait_agent_ready(timeout=30):
            logger.error("go-ios agent failed to start within 30s")
            # 再次尝试杀掉进程
            self._kill_agent_processes()
            raise RuntimeError("go-ios agent failed to start")

        logger.info("go-ios agent started successfully")

    def _kill_agent_processes(self) -> None:
        """杀掉 go-ios agent 相关进程。"""
        try:
            # 通过端口查找并杀掉进程
            result = run_cmd(["netstat", "-ano"], check=True, timeout=10)
            for line in result.stdout.splitlines():
                if f":{self.agent_port}" in line and "LISTENING" in line:
                    parts = line.split()
                    if len(parts) >= 5:
                        pid = parts[-1]
                        logger.info(f"Killing agent process {pid}")
                        run_cmd(["taskkill", "/F", "/PID", pid], check=True, timeout=10)
        except Exception as e:
            logger.warning(f"Failed to kill agent processes: {e}")

    # ========== 设备服务管理 ==========

    def _is_ios17_plus(self, version: str) -> bool:
        """检测 iOS 版本是否 >= 17。"""
        try:
            major = int(version.split('.')[0])
            return major >= 17
        except (ValueError, IndexError):
            return False

    def _get_device_version(self, udid: str) -> str:
        """获取设备 iOS 版本。"""
        return self._go_ios.get_device_version(udid)

    def _get_device_index(self, udid: str) -> int:
        """根据设备列表位置计算索引（用于端口分配）。"""
        devices = self._go_ios.list_devices()
        for i, d in enumerate(devices):
            if d["udid"] == udid:
                return i
        return 0

    def _allocate_ports(self, udid: str) -> tuple[int, int]:
        """分配 WDA 和 MJPEG 端口。"""
        index = self._get_device_index(udid)
        wda_port = self.wda_base_port + index
        mjpeg_port = self.mjpeg_base_port + index
        return wda_port, mjpeg_port

    def _get_tunnel_info(self, udid: str, timeout: int = 30) -> Optional[dict]:
        """获取 iOS 17+ 设备的 tunnel 信息（等待建立）。"""
        # 先检查是否已缓存
        if udid in self._device_tunnel_info:
            return self._device_tunnel_info[udid]

        # 等待 agent 建立 tunnel
        start = time.time()
        while time.time() - start < timeout:
            info = self._go_ios.get_tunnel_info(udid)
            if info and info.get("address"):
                self._device_tunnel_info[udid] = info
                return info
            time.sleep(1)

        logger.warning(f"Tunnel not established for {udid} within {timeout}s")
        return None

    def ensure_device_service(self, udid: str) -> tuple[str, str]:
        """确保 WDA 服务可用。"""
        # 获取锁防止重复启动
        lock = self._get_service_lock(udid)
        if not lock.acquire(blocking=False):
            logger.info(f"Service start already in progress for {udid}, skipping")
            return ("pending", "Service start in progress")

        try:
            # 0. 获取设备版本和型号
            device_version = self._get_device_version(udid)
            device_info = self._go_ios.get_device_info(udid) if self._go_ios else {}
            product_type = device_info.get("model", "")
            if product_type:
                self._device_product_types[udid] = product_type
                is_face_id = product_type in self.FACE_ID_MODELS
                logger.info(f"Device {udid}: product_type={product_type}, Face ID={is_face_id}")

            # 1. iOS 17+ 设备获取 tunnel 信息
            if device_version and self._is_ios17_plus(device_version):
                tunnel_info = self._get_tunnel_info(udid)
                if not tunnel_info:
                    return ("faulty", f"iOS 17+ device {udid} tunnel not established")
                logger.info(f"Tunnel info for {udid}: address={tunnel_info.get('address')}, rsdPort={tunnel_info.get('rsdPort')}")

            # 2. 检查已有的 client 是否可用
            client = self._device_clients.get(udid)
            if client and client.health_check():
                logger.info(f"WDA already running: {udid}")
                return ("online", "OK")

            # 3. 分配端口
            wda_port, mjpeg_port = self._allocate_ports(udid)

            # 4. 检查端口是否被占用
            port_occupied = self._check_port_occupied(wda_port)

            if port_occupied:
                # 端口被占用，探测是否有可用的 WDA
                probe_client = WDAClient(f"http://127.0.0.1:{wda_port}")
                retry_count = 5
                for i in range(retry_count):
                    if probe_client.health_check():
                        logger.info(f"Found existing WDA on port {wda_port}, reusing")
                        self._device_clients[udid] = probe_client
                        # 补充 MJPEG 端口转发（如未启动）
                        mjpeg_process = self._start_mjpeg_forward(udid, mjpeg_port)
                        self._device_wda[udid] = {
                            "port": wda_port,
                            "mjpeg_port": mjpeg_port,
                            "process": None,  # WDA 进程不是我们启动的
                            "forward_process": mjpeg_process,
                        }
                        return ("online", "OK")
                    time.sleep(1)

                # 重试后仍失败，杀掉占用端口的进程
                logger.warning(f"Port {wda_port} occupied but WDA not responding, killing process")
                self._kill_port_process(wda_port)

            # 5. 启动新的 WDA
            return self._start_wda(udid, wda_port, mjpeg_port)

        except Exception as e:
            logger.error(f"Failed to ensure WDA service: {udid}, {e}")
            return ("faulty", str(e))
        finally:
            lock.release()

    def _start_wda(self, udid: str, wda_port: int, mjpeg_port: int) -> tuple[str, str]:
        """启动 WDA 服务（含端口转发）。"""
        try:
            # 清理已有进程
            if udid in self._device_wda:
                self._stop_wda(udid)

            # 启动 WDA（不手动传递 tunnel 参数，让 go-ios 自动从 agent 获取）
            logger.info(f"Starting WDA for {udid} on port {wda_port}")
            wda_process = self._go_ios.start_wda(
                udid=udid,
                bundle_id=self.wda_bundle_id,
                testrunner_bundle_id=self.wda_testrunner_bundle_id,
                xctest_config=self.wda_xctest_config,
            )

            # 等待 WDA 在设备上初始化（1-2秒）
            logger.info(f"WDA process started, waiting 2 seconds for initialization...")
            time.sleep(2)

            # 启动 WDA 端口转发（设备 8100 -> 本地 wda_port）
            forward_process = self._go_ios.forward_port(
                udid=udid,
                local_port=wda_port,
                device_port=8100,
            )

            # 启动 MJPEG 端口转发（设备 9100 -> 本地 mjpeg_port）
            mjpeg_process = self._start_mjpeg_forward(udid, mjpeg_port)

            base_url = f"http://127.0.0.1:{wda_port}"
            client = WDAClient(base_url)

            logger.info(f"WDA process started on port {wda_port}, waiting for ready...")
            if client.wait_ready(timeout=30):
                self._device_wda[udid] = {
                    "port": wda_port,
                    "mjpeg_port": mjpeg_port,
                    "process": wda_process,
                    "forward_process": forward_process,
                    "mjpeg_process": mjpeg_process,
                }
                self._device_clients[udid] = client
                logger.info(f"WDA started: {udid} on port {wda_port}, MJPEG on port {mjpeg_port}")
                return ("online", "OK")
            else:
                logger.warning(f"WDA failed to become ready on port {wda_port}")
                # 不主动杀进程，让其保持运行以便下次复用或手动清理
                return ("faulty", "WDA failed to start")

        except Exception as e:
            logger.error(f"Failed to start WDA: {e}")
            return ("faulty", str(e))

    def _start_mjpeg_forward(self, udid: str, mjpeg_port: int) -> subprocess.Popen:
        """启动 MJPEG 端口转发。"""
        return self._go_ios.forward_port(
            udid=udid,
            local_port=mjpeg_port,
            device_port=9100,
        )

    def _stop_wda(self, udid: str) -> None:
        """停止 WDA 相关进程（不主动停止，保留引用清理）。"""
        if udid in self._device_wda:
            # 不主动停止进程，只清理引用
            # 进程使用 DETACHED_PROCESS 独立运行，会在设备断开时自动退出
            del self._device_wda[udid]

    def _check_port_occupied(self, port: int) -> bool:
        """检查端口是否被占用。"""
        try:
            result = run_cmd(["netstat", "-ano"], check=True, timeout=10)
            for line in result.stdout.splitlines():
                if f":{port}" in line and "LISTENING" in line:
                    return True
            return False
        except Exception as e:
            logger.warning(f"Failed to check port: {e}")
            return False

    def _kill_port_process(self, port: int) -> None:
        """杀掉占用指定端口的进程（Windows）。"""
        try:
            result = run_cmd(["netstat", "-ano"], check=True, timeout=10)
            for line in result.stdout.splitlines():
                if f":{port}" in line and "LISTENING" in line:
                    parts = line.split()
                    if len(parts) >= 5:
                        pid = parts[-1]
                        logger.info(f"Killing process {pid} occupying port {port}")
                        run_cmd(["taskkill", "/F", "/PID", pid], check=True, timeout=10)
                        time.sleep(1)
                        return
            logger.info(f"No process found occupying port {port}")
        except Exception as e:
            logger.warning(f"Failed to kill port process: {e}")

    def mark_device_faulty(self, udid: str) -> None:
        """标记设备为异常。"""
        if udid in self._device_clients:
            del self._device_clients[udid]
        self._stop_wda(udid)
        logger.info(f"iOS device marked faulty: {udid}")

    def get_online_devices(self) -> list[str]:
        """获取在线设备列表。"""
        return list(self._device_clients.keys())

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
        """关闭会话（不关闭 WDA 进程，保持复用）。"""
        if device_id:
            if device_id in self._device_wda:
                del self._device_wda[device_id]
            if device_id in self._device_clients:
                del self._device_clients[device_id]
            logger.info(f"iOS session closed (device={device_id}, WDA preserved)")
        else:
            self._device_wda.clear()
            self._device_clients.clear()
            logger.info("All iOS sessions closed (WDA preserved)")

    # ========== 基础能力实现 ==========

    def _convert_coords(self, x: int, y: int) -> tuple[int, int]:
        """转换物理像素坐标到 WDA 逻辑坐标。

        判断逻辑：
        - 如果坐标超过设备逻辑分辨率 → 物理坐标，需要按缩放因子转换
        - 如果坐标在逻辑分辨率范围内 → 假设为逻辑坐标，不转换

        注意：这种方法对于恰好落在逻辑范围内的物理坐标可能出错。
        建议用户使用物理坐标时确保超过逻辑分辨率阈值。
        """
        # 获取当前设备的逻辑分辨率
        product_type = self._device_product_types.get(self._current_device, "")
        resolution = self._get_logic_resolution(product_type)

        if resolution:
            logic_width, logic_height = resolution
            # 只有当坐标明显超过逻辑分辨率时才转换
            # 使用 5% 容差避免边界情况
            if x > logic_width * 1.05 or y > logic_height * 1.05:
                # 根据机型获取缩放因子
                scale_factor = self._get_scale_factor(product_type)
                logger.debug(f"Converting physical coords ({x}, {y}) to logic ({x//scale_factor}, {y//scale_factor}) with scale {scale_factor}x")
                return (x // scale_factor, y // scale_factor)
            # 在逻辑范围内，假设是逻辑坐标
            logger.debug(f"Coords ({x}, {y}) within logic resolution {logic_width}x{logic_height}, no conversion")
            return (x, y)

        # 未知设备，使用旧逻辑（向后兼容）
        if x > 400 or y > 700:
            return (x // 2, y // 2)
        return (x, y)

    def _get_scale_factor(self, product_type: str) -> int:
        """根据 product_type 获取缩放因子。

        iPhone 缩放因子总结：
        - 2x: iPhone 8, iPhone 8 Plus(约2.6x), iPhone XR, iPhone 11
        - 3x: iPhone X 及之后所有机型（除上述2x机型），包括全系12/13/14/15/16
        """
        # 2x 缩放机型
        scale_2x = {
            "iPhone10,1", "iPhone10,2", "iPhone10,4", "iPhone10,5",  # iPhone 8 系列
            "iPhone11,8",  # iPhone XR
            "iPhone12,1",  # iPhone 11
        }
        # 3x 缩放机型（iPhone X 及之后，除 2x 机型外的所有机型）
        scale_3x = {
            "iPhone10,3", "iPhone10,6",  # iPhone X
            "iPhone11,2", "iPhone11,4", "iPhone11,6",  # iPhone XS/XS Max
            "iPhone12,3", "iPhone12,5",  # iPhone 11 Pro/Pro Max
            "iPhone13,1", "iPhone13,2", "iPhone13,3", "iPhone13,4",  # iPhone 12 全系
            "iPhone14,2", "iPhone14,3", "iPhone14,4", "iPhone14,5",  # iPhone 13 全系
            "iPhone15,2", "iPhone15,3", "iPhone15,4", "iPhone15,5",  # iPhone 14 全系
            "iPhone16,1", "iPhone16,2", "iPhone16,3", "iPhone16,4",  # iPhone 15 全系
            "iPhone17,1", "iPhone17,2", "iPhone17,3", "iPhone17,4",  # iPhone 16 全系
        }

        if product_type in scale_3x:
            return 3
        elif product_type in scale_2x:
            return 2
        else:
            return 2  # 默认 2x

    def _get_logic_resolution(self, product_type: str) -> tuple[int, int] | None:
        """根据 product_type 获取逻辑分辨率（points）。

        iPhone 逻辑分辨率官方数据：
        - iPhone 8 系列: 375×667 (8), 414×736 (8 Plus)
        - iPhone X/XS: 375×812
        - iPhone XR/11: 414×896
        - iPhone 11 Pro: 375×812, Pro Max: 414×896
        - iPhone 12 mini: 360×780, 12/12Pro: 390×844, 12 Pro Max: 428×926
        - iPhone 13 mini: 360×780, 13/13Pro: 390×844, 13 Pro Max: 428×926
        - iPhone 14: 390×844, 14 Plus: 428×926, 14 Pro: 393×852, 14 Pro Max: 430×932
        - iPhone 15: 393×852, 15 Plus: 430×932, 15 Pro: 393×852, 15 Pro Max: 430×932
        - iPhone 16: 393×852, 16 Plus: 430×932, 16 Pro: 393×852, 16 Pro Max: 430×932

        缩放因子：
        - 2x: iPhone 8, 8 Plus(约2.6x), XR, 11
        - 3x: iPhone X 及之后所有机型（除上述2x机型）
        """
        # iPhone 逻辑分辨率映射（points）
        logic_res_map = {
            # iPhone 8 系列（2x 缩放，8 Plus 实际约 2.6x）
            "iPhone10,1": (375, 667),   # iPhone 8
            "iPhone10,2": (414, 736),   # iPhone 8 Plus（物理 1080×1920，约 2.6x）
            "iPhone10,4": (375, 667),   # iPhone 8 (GSM)
            "iPhone10,5": (414, 736),   # iPhone 8 Plus (GSM)
            # iPhone X（3x 缩放）
            "iPhone10,3": (375, 812),   # iPhone X
            "iPhone10,6": (375, 812),   # iPhone X (GSM)
            # iPhone XS/XR 系列
            "iPhone11,2": (375, 812),   # iPhone XS（3x）
            "iPhone11,4": (414, 896),   # iPhone XS Max（3x）
            "iPhone11,6": (414, 896),   # iPhone XS Max (GSM)（3x）
            "iPhone11,8": (414, 896),   # iPhone XR（2x）
            # iPhone 11 系列
            "iPhone12,1": (414, 896),   # iPhone 11（2x）
            "iPhone12,3": (375, 812),   # iPhone 11 Pro（3x）
            "iPhone12,5": (414, 896),   # iPhone 11 Pro Max（3x）
            # iPhone 12 系列（全系 3x）
            "iPhone13,1": (360, 780),   # iPhone 12 mini（3x）
            "iPhone13,2": (390, 844),   # iPhone 12（3x）
            "iPhone13,3": (390, 844),   # iPhone 12 Pro（3x）
            "iPhone13,4": (428, 926),   # iPhone 12 Pro Max（3x）
            # iPhone 13 系列（全系 3x）
            "iPhone14,2": (390, 844),   # iPhone 13 Pro（3x）
            "iPhone14,3": (428, 926),   # iPhone 13 Pro Max（3x）
            "iPhone14,4": (360, 780),   # iPhone 13 mini（3x）
            "iPhone14,5": (390, 844),   # iPhone 13（3x）
            # iPhone 14 系列（全系 3x）
            "iPhone15,2": (393, 852),   # iPhone 14 Pro（3x）
            "iPhone15,3": (430, 932),   # iPhone 14 Pro Max（3x）
            "iPhone15,4": (390, 844),   # iPhone 14（3x）
            "iPhone15,5": (428, 926),   # iPhone 14 Plus（3x）
            # iPhone 15 系列（全系 3x）
            "iPhone16,1": (393, 852),   # iPhone 15 Pro（3x）
            "iPhone16,2": (430, 932),   # iPhone 15 Pro Max（3x）
            "iPhone16,3": (393, 852),   # iPhone 15（3x）
            "iPhone16,4": (430, 932),   # iPhone 15 Plus（3x）
            # iPhone 16 系列（全系 3x）
            "iPhone17,1": (393, 852),   # iPhone 16 Pro（3x）
            "iPhone17,2": (430, 932),   # iPhone 16 Pro Max（3x）
            "iPhone17,3": (393, 852),   # iPhone 16（3x）
            "iPhone17,4": (430, 932),   # iPhone 16 Plus（3x）
        }
        return logic_res_map.get(product_type)

    def click(self, x: int, y: int, duration: int = 0, context: Any = None) -> None:
        """点击指定坐标，支持长按。

        Args:
            x: X 坐标
            y: Y 坐标
            duration: 点击持续时间（毫秒），0=普通点击，>0=长按
            context: 执行上下文
        """
        client = context or self._device_clients.get(self._current_device)
        if client:
            # 转换坐标
            wx, wy = self._convert_coords(x, y)
            if duration > 0:
                # 长按：使用 touch_and_hold，单位转换 毫秒 → 秒
                duration_sec = duration / 1000.0
                logger.debug(f"Long click at ({wx}, {wy}) for {duration}ms")
                success = client.touch_and_hold(wx, wy, duration=duration_sec)
            else:
                logger.debug(f"Click at ({wx}, {wy})")
                success = client.tap(wx, wy)
            if not success:
                raise RuntimeError(f"Click failed at ({wx}, {wy})")

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

    def swipe(self, start_x: int, start_y: int, end_x: int, end_y: int,
              duration: int = 500, steps: Optional[int] = None, context: Any = None) -> None:
        """滑动，支持 duration 参数。

        Args:
            start_x: 起始 X 坐标
            start_y: 起始 Y 坐标
            end_x: 结束 X 坐标
            end_y: 结束 Y 坐标
            duration: 滕动持续时间（毫秒），默认 500ms
            steps: 滕动步数（iOS WDA 不支持，参数忽略）
            context: 执行上下文

        Note:
            iOS WDA 不支持 steps 参数，始终使用 duration 控制滕动时间。
        """
        client = context or self._device_clients.get(self._current_device)
        if client:
            wx1, wy1 = self._convert_coords(start_x, start_y)
            wx2, wy2 = self._convert_coords(end_x, end_y)
            # duration 单位转换：毫秒 → 秒
            duration_sec = duration / 1000.0
            logger.debug(f"Swipe from ({wx1}, {wy1}) to ({wx2}, {wy2}) with duration={duration}ms")
            success = client.swipe(wx1, wy1, wx2, wy2, duration=duration_sec)
            if not success:
                raise RuntimeError(f"Swipe failed from ({wx1}, {wy1}) to ({wx2}, {wy2})")

    # ========== 手势操作 ==========

    def pinch(self, direction: str, scale: float = 0.5,
              duration: int = 500, context: Any = None) -> None:
        """
        双指缩放手势。

        Args:
            direction: "in" 缩小 / "out" 放大
            scale: 缩放比例
            duration: 持续时间（毫秒）
            context: 执行上下文
        """
        client = context or self._device_clients.get(self._current_device)
        if not client:
            raise RuntimeError("No device context")

        duration_sec = duration / 1000.0

        # WDA pinch 方法
        if direction == "in":
            client.pinch(scale=scale, duration=duration_sec)
        else:
            client.pinch(scale=1.0 / scale, duration=duration_sec)

        logger.debug(f"pinch {direction} executed: scale={scale}, duration={duration}ms")

    def press(self, key: str, context: Any = None) -> None:
        """按键。

        iOS 支持的按键（WDA 统一支持）：
        - HOME：虚拟 HOME 键（所有机型可用）
        - VOLUME_UP：音量加
        - VOLUME_DOWN：音量减

        注意：LOCK/POWER 按键 WDA 不支持，需通过其他方式唤醒屏幕。
        """
        client = context or self._device_clients.get(self._current_device)
        if client:
            key_upper = key.upper()

            # 检查是否在不支持的按键列表中
            if key_upper in self.UNSUPPORTED_KEYS:
                raise ValueError(f"Unsupported key '{key}' for iOS. {self.UNSUPPORTED_KEYS[key_upper]}")

            # 按键名映射
            wda_key = self.KEY_MAP.get(key_upper)
            if wda_key:
                success = client.press_button(wda_key)
                if not success:
                    raise RuntimeError(f"Press button failed: {key}")
            else:
                supported = ", ".join(sorted(self.KEY_MAP.keys()))
                raise ValueError(f"Unsupported key '{key}' for iOS. Supported keys: {supported}")

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
        """启动应用（含锁屏检测）。"""
        bundle_id = action.bundle_id or action.value
        if not bundle_id:
            return ActionResult(
                number=0,
                action_type="start_app",
                status=ActionStatus.FAILED,
                error="bundle_id is required",
            )

        if not client or not self._current_device:
            return ActionResult(
                number=0,
                action_type="start_app",
                status=ActionStatus.FAILED,
                error="No device context",
            )

        # 检测锁屏状态，如果锁屏则先解锁
        if hasattr(client, "is_locked"):
            try:
                is_locked = client.is_locked()
                if is_locked:
                    logger.info("Screen is locked, performing auto unlock before start_app")
                    unlock_result = self._auto_unlock(client)
                    if unlock_result.status != ActionStatus.SUCCESS:
                        return ActionResult(
                            number=0,
                            action_type="start_app",
                            status=ActionStatus.FAILED,
                            error=f"Auto unlock failed: {unlock_result.error}",
                        )
                    logger.info("Auto unlock completed, proceeding with start_app")
            except Exception as e:
                logger.warning(f"Failed to check lock status: {e}")

        try:
            # 使用 go-ios launch 命令
            success = self._go_ios.launch_app(
                udid=self._current_device,
                bundle_id=bundle_id,
            )

            if success:
                return ActionResult(
                    number=0,
                    action_type="start_app",
                    status=ActionStatus.SUCCESS,
                    output=f"Started: {bundle_id}",
                )
            else:
                return ActionResult(
                    number=0,
                    action_type="start_app",
                    status=ActionStatus.FAILED,
                    error=f"Failed to launch: {bundle_id}",
                )
        except Exception as e:
            logger.error(f"Failed to launch app: {e}")
            return ActionResult(
                number=0,
                action_type="start_app",
                status=ActionStatus.FAILED,
                error=str(e),
            )

    def _auto_unlock(self, client) -> ActionResult:
        """自动解锁屏幕（使用配置密码）。"""
        from worker.actions import ActionRegistry
        from worker.task import Action

        password = self._unlock_config.get("password", "123456")

        unlock_action = Action(
            action_type="unlock_screen",
            value=password,
        )

        executor = ActionRegistry.get("unlock_screen")
        if executor:
            return executor.execute(self, unlock_action, client)
        else:
            return ActionResult(
                number=0,
                action_type="unlock_screen",
                status=ActionStatus.FAILED,
                error="unlock_screen executor not found",
            )

    def _action_stop_app(self, client, action: Action) -> ActionResult:
        """关闭应用。"""
        bundle_id = action.bundle_id or action.value

        if not client or not self._current_device:
            return ActionResult(
                number=0,
                action_type="stop_app",
                status=ActionStatus.FAILED,
                error="No device context",
            )

        try:
            if bundle_id:
                # 使用 go-ios kill 命令
                success = self._go_ios.kill_app(
                    udid=self._current_device,
                    bundle_id=bundle_id,
                )

                if success:
                    return ActionResult(
                        number=0,
                        action_type="stop_app",
                        status=ActionStatus.SUCCESS,
                        output=f"Stopped: {bundle_id}",
                    )
                else:
                    return ActionResult(
                        number=0,
                        action_type="stop_app",
                        status=ActionStatus.FAILED,
                        error=f"Failed to kill: {bundle_id}",
                    )
            else:
                # 未指定 bundle_id，按 HOME 键回到主屏幕
                if hasattr(client, "press_button"):
                    client.press_button("home")
                return ActionResult(
                    number=0,
                    action_type="stop_app",
                    status=ActionStatus.SUCCESS,
                    output="Pressed HOME key",
                )
        except Exception as e:
            logger.error(f"Failed to stop app: {e}")
            return ActionResult(
                number=0,
                action_type="stop_app",
                status=ActionStatus.FAILED,
                error=str(e),
            )
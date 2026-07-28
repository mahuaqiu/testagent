"""
Worker 主服务。

负责管理设备发现、平台管理器、任务调度、平台上报等核心功能。
"""

import base64
import json
import logging
import os
import sys
import threading
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict

from common.ocr_client import OCRClient
from common.packaging import get_base_dir
from common.request_context import get_request_id
from common.utils import compress_image_to_jpeg
from worker.config import PlatformConfig, WorkerConfig
from worker.device_monitor import DeviceMonitor
from worker.devices.models import DeviceRecord
from worker.discovery.android import AndroidDeviceInfo, AndroidDiscoverer
from worker.discovery.host import HostDiscoverer, HostInfo
from worker.discovery.ios import iOSDeviceInfo, iOSDiscoverer
from worker.platforms.android import AndroidPlatformManager
from worker.platforms.base import PlatformManager
from worker.platforms.harmony import HarmonyPlatformManager
from worker.platforms.ios import iOSPlatformManager
from worker.platforms.mac import MacPlatformManager
from worker.platforms.web import WebPlatformManager
from worker.platforms.windows import WindowsPlatformManager
from worker.reporter import DesktopInfo, HarmonyDeviceInfo, Reporter, WorkerCapabilities, WorkerReport
from worker.task import ActionResult, ActionStatus, Task, TaskResult, TaskStatus
from worker.tools import get_all_script_versions
from worker.runtime import WorkerRuntime
from worker.actions.spec import ActionCancelled, ActionTimedOut, ExecutionControl

logger = logging.getLogger(__name__)


@dataclass
class WorkerStatus:
    """Worker 状态。"""

    status: str  # online / busy / offline
    started_at: datetime
    supported_platforms: list[str]


class Worker:
    """
    Worker 主服务。

    管理设备发现、平台管理器、任务执行、平台上报。
    """

    def __init__(self, config: WorkerConfig, log_path: str | None = None):
        """
        初始化 Worker。

        Args:
            config: Worker 配置
            log_path: 实际使用的日志文件路径
        """
        self.config = config
        self.worker_id = config.id
        self.port = config.port
        self.log_path = log_path  # 存储实际日志路径

        # 状态
        self._status = "offline"
        self._started = False
        self._started_at: datetime | None = None

        # 宿主机信息
        self.host_info: HostInfo | None = None
        self.supported_platforms: list[str] = []

        # 设备信息
        self.android_devices: list[AndroidDeviceInfo] = []
        self.ios_devices: list[iOSDeviceInfo] = []

        # 平台管理器
        self.platform_managers: dict[str, PlatformManager] = {}
        self.android_manager: AndroidPlatformManager | None = None
        self.ios_manager: iOSPlatformManager | None = None
        self.harmony_mobile_manager: HarmonyPlatformManager | None = None
        self.harmony_pc_manager: HarmonyPlatformManager | None = None

        # 任务调度器
        self.runtime = WorkerRuntime(self._execute_task_callback)
        self.scheduler = self.runtime.scheduler
        self.device_registry = self.runtime.device_registry
        self.artifact_service = self.runtime.artifact_service

        # 任务存储（异步任务管理）

        # 上报客户端
        self.reporter: Reporter | None = None

        # OCR 客户端
        self.ocr_client: OCRClient | None = None

        # 后台线程
        self._device_monitor_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        # 设备监控
        self.device_monitor: DeviceMonitor | None = None

        # 缓存清理状态文件路径
        self._cache_clear_status_file = os.path.join(
            get_base_dir(), "data", "cache_clear_status.json"
        )

    @property
    def status(self) -> str:
        return self._status

    def start(self) -> None:
        """启动 Worker。"""
        if self._started:
            logger.warning("Worker already started")
            return

        logger.info(f"Starting Worker {self.worker_id}...")

        # 1. 发现宿主机环境（只获取主机信息，不发现设备）
        self.host_info = HostDiscoverer.discover()
        self.supported_platforms = HostDiscoverer.get_supported_platforms()
        logger.info(f"Host: {self.host_info.hostname} ({self.host_info.os_type})")
        logger.info(f"Supported platforms: {self.supported_platforms}")

        # 2. 初始化 OCR 客户端
        self._init_ocr_client()

        # 3. 初始化平台管理器
        self._init_platform_managers()

        # 4. 启动移动端平台管理器（必须在设备发现之前，否则 GoIOSClient 未初始化）
        for platform in ("android", "ios", "harmony_mobile", "harmony_pc"):
            # 根据开关跳过
            if platform == "android" and not self.config.discover_android_devices:
                continue
            if platform == "ios" and not self.config.discover_ios_devices:
                continue
            if platform == "harmony_mobile" and not self.config.discover_harmony_mobile_devices:
                continue
            if platform == "harmony_pc" and not self.config.discover_harmony_pc_devices:
                continue

            manager = self.platform_managers.get(platform)
            if manager:
                try:
                    manager.start()
                    # iOS platform 设置 agent 就绪回调
                    if platform == "ios" and self.device_monitor:
                        manager.set_on_agent_ready(self.device_monitor.trigger_check)
                except Exception as e:
                    logger.error(f"Failed to start {platform} platform: {e}")

        # 5. 发现移动设备（现在 GoIOSClient 已初始化）
        if self.host_info.os_type == "windows":
            self._discover_mobile_devices()

        # 6. 初始化上报客户端
        self._init_reporter()

        # 7. 启动设备监控（定期上报由 DeviceMonitor 负责）
        if self.device_monitor:
            self.device_monitor.start()

        self.runtime.start()
        self._status = "online"
        self._started = True
        self._started_at = datetime.now()

        logger.info(f"Worker {self.worker_id} started, supported platforms: {self.supported_platforms}")

    def stop(self) -> None:
        """停止 Worker。"""
        if not self._started:
            return

        logger.info(f"Stopping Worker {self.worker_id}...")

        # 鍏堝仠姝换鍔℃湇鍔★紝纭繚鎵ц涓殑浠诲姟閲婃斁璧勬簮绉熺害
        self.runtime.stop()

        # background=true 的命令属于宿主机独立进程，不随 Worker 生命周期回收。

        # 关闭所有 ScreenManager
        from worker.screen.manager import close_all_screen_managers
        close_all_screen_managers()

        # 停止设备监控
        if self.device_monitor:
            self.device_monitor.stop()

        # 停止平台管理器
        for platform, manager in self.platform_managers.items():
            try:
                manager.stop()
            except Exception as e:
                logger.error(f"Failed to stop {platform} platform: {e}\n{traceback.format_exc()}")

        # 关闭上报客户端
        if self.reporter:
            self.reporter.close()

        # 关闭 OCR 客户端
        if self.ocr_client:
            self.ocr_client.close()

        self._status = "offline"
        self._started = False

        logger.info(f"Worker {self.worker_id} stopped")

    def _discover_environment(self) -> None:
        """发现宿主机环境（不含设备发现）。"""
        # 发现宿主机信息
        self.host_info = HostDiscoverer.discover()

        # 根据操作系统决定支持的平台
        self.supported_platforms = HostDiscoverer.get_supported_platforms()

        logger.info(f"Host: {self.host_info.hostname} ({self.host_info.os_type})")
        logger.info(f"Supported platforms: {self.supported_platforms}")

    def _sync_device_registry(self, monitor_devices: dict[str, Any] | None = None) -> None:
        """将发现器和监控器快照同步为统一设备事实。"""
        records: dict[str, list[DeviceRecord]] = {
            "android": [],
            "ios": [],
            "harmony_mobile": [],
            "harmony_pc": [],
        }
        for device in self.android_devices:
            records["android"].append(DeviceRecord(
                device_id=device.udid, platform="android", name=device.name,
                model=device.model, os_version=device.os_version,
                connection_status="connected", metadata=device.to_dict(),
            ))
        for device in self.ios_devices:
            records["ios"].append(DeviceRecord(
                device_id=device.udid, platform="ios", name=device.name,
                model=device.model, os_version=device.os_version,
                connection_status="connected", metadata=device.to_dict(),
            ))
        snapshot = monitor_devices
        if snapshot is None and self.device_monitor:
            snapshot = self.device_monitor.get_all_devices()
        if snapshot:
            for platform in ("android", "ios", "harmony_mobile", "harmony_pc"):
                by_id = {record.device_id: record for record in records[platform]}
                for item in snapshot.get(platform, []):
                    device_id = item.get("udid") or item.get("device_id")
                    if not device_id:
                        continue
                    current = by_id.get(device_id)
                    if current is None:
                        current = DeviceRecord(device_id=device_id, platform=platform)
                        by_id[device_id] = current
                    current.name = item.get("name") or current.name
                    current.model = item.get("model") or current.model
                    current.os_version = item.get("os_version") or item.get("sys_version") or current.os_version
                    current.connection_status = item.get("connection_status", current.connection_status)
                    current.service_status = item.get("service_status", current.service_status)
                    current.health_status = item.get("health_status", current.health_status)
                    current.capabilities = list(item.get("capabilities", current.capabilities))
                    current.metadata.update(item)
                records[platform] = list(by_id.values())

        for platform, values in records.items():
            if values:
                self.device_registry.replace_platform(platform, values)
            if snapshot:
                faulty = snapshot.get(f"faulty_{platform}", [])
                for item in faulty:
                    device_id = item.get("udid") or item.get("device_id")
                    if device_id:
                        self.device_registry.update_status(
                            platform, device_id, connection_status="disconnected",
                            health_status="unhealthy",
                        )

    def _discover_mobile_devices(self) -> None:
        """发现移动设备。"""
        # Android 设备
        if self.config.discover_android_devices:
            if AndroidDiscoverer.check_adb_available():
                self.android_devices = AndroidDiscoverer.discover()
                logger.info(f"Found {len(self.android_devices)} Android devices")
                self._sync_device_registry()
            else:
                logger.warning("ADB not available, skipping Android device discovery")
        else:
            logger.info("Android device discovery disabled")

        # iOS 设备
        if self.config.discover_ios_devices:
            if iOSDiscoverer.check_go_ios_available():
                self.ios_devices = iOSDiscoverer.discover()
                logger.info(f"Found {len(self.ios_devices)} iOS devices")
                self._sync_device_registry()
            else:
                logger.warning("go-ios not available, skipping iOS device discovery")
        else:
            logger.info("iOS device discovery disabled")

    def _init_ocr_client(self) -> None:
        """初始化 OCR 客户端。"""
        try:
            self.ocr_client = OCRClient(
                base_url=self.config.ocr_service,
            )
            logger.info(f"OCR client initialized: {self.config.ocr_service}")
        except Exception as e:
            logger.warning(f"Failed to initialize OCR client: {e}\n{traceback.format_exc()}")

    def _init_platform_managers(self) -> None:
        """初始化平台管理器。"""
        unlock_config = self.config.unlock  # 获取解锁配置

        for platform in self.supported_platforms:
            # Android/iOS/Harmony 平台根据开关跳过初始化
            if platform == "android" and not self.config.discover_android_devices:
                logger.info("Android platform skipped: discover_android_devices=false")
                continue
            if platform == "ios" and not self.config.discover_ios_devices:
                logger.info("iOS platform skipped: discover_ios_devices=false")
                continue
            if platform == "harmony_mobile" and not self.config.discover_harmony_mobile_devices:
                logger.info("Harmony mobile platform skipped: discover_harmony_mobile_devices=false")
                continue
            if platform == "harmony_pc" and not self.config.discover_harmony_pc_devices:
                logger.info("Harmony PC platform skipped: discover_harmony_pc_devices=false")
                continue

            platform_config = PlatformConfig.from_dict(
                self.config.get_platform_config(platform)
            )

            try:
                if platform == "web":
                    manager = WebPlatformManager(platform_config, self.ocr_client)
                elif platform == "android":
                    manager = AndroidPlatformManager(platform_config, self.ocr_client, unlock_config)
                    self.android_manager = manager
                elif platform == "ios":
                    manager = iOSPlatformManager(
                        platform_config,
                        self.ocr_client,
                        unlock_config,
                        busy_checker=lambda device_id: self.scheduler.is_busy("ios", device_id),
                    )
                    self.ios_manager = manager
                elif platform in ("harmony_mobile", "harmony_pc"):
                    manager = HarmonyPlatformManager(
                        platform_config,
                        self.ocr_client,
                        unlock_config,
                        device_type=platform,
                    )
                    if platform == "harmony_mobile":
                        self.harmony_mobile_manager = manager
                    else:
                        self.harmony_pc_manager = manager
                elif platform == "windows":
                    manager = WindowsPlatformManager(platform_config, self.ocr_client)
                elif platform == "mac":
                    manager = MacPlatformManager(platform_config, self.ocr_client)
                else:
                    continue

                self.platform_managers[platform] = manager
                logger.info(f"Platform manager initialized: {platform}")

            except Exception as e:
                logger.error(f"Failed to initialize {platform} platform: {e}\n{traceback.format_exc()}")

        # 初始化设备监控（始终创建，用于定期上报）
        self.device_monitor = DeviceMonitor(self.config)
        self.device_monitor.set_platform_managers(
            android_manager=self.android_manager,
            ios_manager=self.ios_manager,
            harmony_mobile_manager=self.harmony_mobile_manager,
            harmony_pc_manager=self.harmony_pc_manager,
        )
        self.device_monitor.on_device_change = self._on_device_change

        # 设置帧捕获失败回调（仅移动端）
        if (
            self.config.discover_android_devices
            or self.config.discover_ios_devices
            or self.config.discover_harmony_mobile_devices
            or self.config.discover_harmony_pc_devices
        ):
            from worker.screen.manager import set_capture_failed_callback
            set_capture_failed_callback(self._on_capture_failed)

    def _init_reporter(self) -> None:
        """初始化上报客户端。"""
        if self.config.platform_api:
            self.reporter = Reporter(self.config)
            logger.info(f"Reporter initialized: {self.config.platform_api}")

    def _report_full(self) -> None:
        """全量上报。"""
        if not self.reporter:
            return

        # 构建设备列表
        devices = []

        # 移动设备
        for device in self.android_devices:
            devices.append(device)
        for device in self.ios_devices:
            devices.append(device)

        # 鸿蒙设备由 DeviceMonitor 维护，保留形态、连接信息和能力后再上报。
        if self.device_monitor:
            harmony_devices = self.device_monitor.get_all_devices()
            for platform, category in (("harmony_mobile", "mobile"), ("harmony_pc", "pc")):
                for device in harmony_devices.get(platform, []):
                    devices.append(HarmonyDeviceInfo(
                        udid=device.get("udid", ""),
                        name=device.get("name", ""),
                        model=device.get("model", ""),
                        sys_version=device.get("sys_version", ""),
                        sdk_version=device.get("sdk_version", ""),
                        display_size=tuple(device.get("display_size", (0, 0))),
                        status="online",
                        device_category=device.get("device_category", category),
                        connection_type=device.get("connection_type", "unknown"),
                        connection_status=device.get("connection_status", "ready"),
                        capabilities=list(device.get("capabilities", [])),
                    ))

        # 桌面信息
        if self.host_info:
            desktop = DesktopInfo(
                platform=self.host_info.os_type,
                resolution=self.host_info.display_resolution,
                scale=self.host_info.display_scale,
            )
            devices.append(desktop)

        # 构建能力
        capabilities = WorkerCapabilities(
            has_ocr=self.ocr_client is not None,
            browsers=["chromium", "firefox", "webkit"] if "web" in self.supported_platforms else [],
            max_sessions=5,
            image_matching=True,
        )

        # 构建上报数据
        report = WorkerReport(
            worker_id=self.worker_id,
            hostname=self.host_info.hostname if self.host_info else "unknown",
            ip_addresses=self.host_info.ip_addresses if self.host_info else [],
            os_type=self.host_info.os_type if self.host_info else "unknown",
            os_version=self.host_info.os_version if self.host_info else "unknown",
            supported_platforms=self.supported_platforms,
            status=self._status,
            port=self.port,
            devices=devices,
            capabilities=capabilities,
        )

        self.reporter.report_full(report)

    def _report_devices(self) -> None:
        """
        使用新格式上报设备信息。

        用于定期上报和设备变化时上报。
        调用 POST /api/core/env/register 接口。
        """
        if not self.reporter:
            return

        # 获取设备信息 - 从 DeviceMonitor 获取最新的设备状态
        if self.device_monitor:
            devices = self.device_monitor.get_all_devices()
            # 使用 set 去重，防止重复上报
            android_udids = list(set([d["udid"] for d in devices.get("android", [])]))
            ios_udids = list(set([d["udid"] for d in devices.get("ios", [])]))
            harmony_mobile_udids = list(set([d["udid"] for d in devices.get("harmony_mobile", [])]))
            harmony_pc_udids = list(set([d["udid"] for d in devices.get("harmony_pc", [])]))
        else:
            # DeviceMonitor 未启动时，使用启动时发现的设备列表
            android_udids = [d.udid for d in self.android_devices]
            ios_udids = [d.udid for d in self.ios_devices]
            harmony_mobile_udids = []
            harmony_pc_udids = []

        # 获取本机 IP
        ip = HostDiscoverer.get_preferred_ip(self.config.ip)

        devices_payload: dict[str, list[str]] = {}

        # 1. 根据操作系统添加桌面平台
        if self.host_info:
            if self.host_info.os_type == "windows":
                devices_payload["windows"] = []
                devices_payload["web"] = []
            elif self.host_info.os_type == "macos":
                devices_payload["mac"] = []

        # 2. Android 设备
        if android_udids:
            devices_payload["android"] = android_udids

        # 3. iOS 设备
        if ios_udids:
            devices_payload["ios"] = ios_udids

        if harmony_mobile_udids:
            devices_payload["harmony_mobile"] = harmony_mobile_udids
        if harmony_pc_udids:
            devices_payload["harmony_pc"] = harmony_pc_udids

        # 调用新的注册接口
        self.reporter.register_env(
            ip=ip,
            port=self.port,
            devices=devices_payload,
            version=self._get_version(),
            config_version=self.config.config_version,
            scripts=get_all_script_versions(),
        )

    def _start_device_monitor(self) -> None:
        """启动设备监控（已由 DeviceMonitor 模块接管）。"""
        # 设备监控已由 DeviceMonitor 模块接管
        # 此方法保留用于兼容，实际初始化在 _init_platform_managers 中完成
        pass

    def _stop_device_monitor(self) -> None:
        """停止设备监控线程。"""
        self._stop_event.set()
        if self._device_monitor_thread:
            self._device_monitor_thread.join(timeout=5)
        logger.info("Device monitor stopped")

    def _device_monitor_loop(self) -> None:
        """设备监控循环（已由 DeviceMonitor 模块接管）。"""
        # 设备监控已由 DeviceMonitor 模块接管
        pass

    def _check_device_changes(self) -> None:
        """检查设备变化。"""
        changes = []

        # 检查 Android 设备
        if AndroidDiscoverer.check_adb_available():
            new_devices = AndroidDiscoverer.discover()
            changes.extend(self._compare_devices("android", self.android_devices, new_devices))
            self.android_devices = new_devices

        # 检查 iOS 设备
        if iOSDiscoverer.check_go_ios_available():
            new_devices = iOSDiscoverer.discover()
            changes.extend(self._compare_devices("ios", self.ios_devices, new_devices))
            self.ios_devices = new_devices

        # 如果有变化，上报
        if changes and self.reporter:
            self._report_devices()
            logger.info(f"Device changes detected: {len(changes)} changes")

    def _compare_devices(self, platform: str, old_list: list, new_list: list) -> list[str]:
        """比较设备列表变化。"""
        changes = []

        old_udids = {d.udid for d in old_list}
        new_udids = {d.udid for d in new_list}

        # 新增设备
        added = new_udids - old_udids
        if added:
            changes.extend([f"+{platform}:{udid}" for udid in added])

        # 移除设备
        removed = old_udids - new_udids
        if removed:
            changes.extend([f"-{platform}:{udid}" for udid in removed])

        return changes

    def _on_device_change(self, devices: dict) -> None:
        """设备状态变更回调。"""
        logger.info(f"Device status changed: {devices}")
        self._sync_device_registry(devices)
        # 设备变化时重新上报
        self._report_devices()

    def _on_capture_failed(self, device_id: str) -> None:
        """帧捕获失败回调（由 ScreenManager 调用）。"""
        logger.warning(f"Frame capture failed for device: {device_id}")

        if not self.device_monitor:
            return

        # 处理带前缀的 device_id（如 "ios/9f39664c476539deff6d5f425e4bb4a53457cc24"）
        # ScreenManager 的 device_id 可能包含平台前缀
        if "/" in device_id:
            platform_prefix, udid = device_id.split("/", 1)
            platform = platform_prefix.lower()
        else:
            # 旧格式：纯 UDID，根据格式判断平台
            # iOS 设备 UDID 格式：00008120-001E0CA601800032（8-4-4-4-12）
            # Android 设备 UDID：通常是纯字母数字或短格式
            udid = device_id
            is_ios = "-" in udid and len(udid) == 36
            platform = "ios" if is_ios else "android"

        self.device_monitor.mark_device_offline(platform, udid)

    # ========== API 方法 ==========

    def _get_version(self) -> str | None:
        """
        获取版本号。

        Returns:
            str | None: 版本号，非 EXE 运行时返回 None
        """
        try:
            from worker._version import VERSION

            return VERSION
        except ImportError:
            return None

    def get_status(self) -> WorkerStatus:
        """获取 Worker 状态。"""
        return WorkerStatus(
            status=self._status,
            started_at=self._started_at or datetime.now(),
            supported_platforms=self.supported_platforms,
        )

    def get_worker_devices(self) -> dict[str, Any]:
        """获取 Worker 状态和设备信息。"""
        devices = self.device_registry.grouped()

        # 使用配置的 IP 或自动获取
        ip = HostDiscoverer.get_preferred_ip(self.config.ip)

        return {
            "status": self._status,
            "started_at": self._started_at,
            "supported_platforms": self.supported_platforms,
            "ip": ip,
            "port": self.port,
            "version": self._get_version(),
            "devices": {
                "windows": [],
                "web": [],
                "mac": [],
                "android": [d for d in devices.get("android", []) if d.get("connection_status") != "disconnected"],
                "ios": [d for d in devices.get("ios", []) if d.get("connection_status") != "disconnected"],
                "harmony_mobile": devices.get("harmony_mobile", []),
                "harmony_pc": devices.get("harmony_pc", []),
            },
            "faulty_devices": {
                "android": devices.get("faulty_android", []),
                "ios": devices.get("faulty_ios", []),
                "harmony_mobile": devices.get("faulty_harmony_mobile", []),
                "harmony_pc": devices.get("faulty_harmony_pc", []),
            },
            "namespace": self.reporter.namespace if self.reporter else "",
            "config_version": self.config.config_version,
            "scripts": get_all_script_versions(),
        }

    def refresh_devices(self) -> dict[str, Any]:
        """刷新设备列表并返回最新的 Worker 状态和设备信息。"""
        self._discover_mobile_devices()
        return self.get_worker_devices()

    def _validate_task(self, task: Task, manager: PlatformManager) -> TaskResult | None:
        """
        验证任务。

        Args:
            task: 任务对象
            manager: 平台管理器

        Returns:
            TaskResult | None: 验证失败返回错误结果，通过返回 None
        """
        request_id = get_request_id()
        host_command_only = bool(task.actions) and all(
            action.action_type == "cmd_exec" for action in task.actions
        )

        # 1. 平台支持验证
        if task.platform not in self.supported_platforms:
            return TaskResult(
                task_id=task.task_id,
                request_id=request_id,
                status=TaskStatus.FAILED,
                platform=task.platform,
                error=f"Platform not supported: {task.platform}",
            )

        # 2. device_id 验证（移动端必填）
        if not host_command_only and task.platform in ["android", "ios", "harmony_mobile", "harmony_pc"]:
            if not task.device_id:
                return TaskResult(
                    task_id=task.task_id,
                    request_id=request_id,
                    status=TaskStatus.FAILED,
                    platform=task.platform,
                    error=f"device_id is required for {task.platform} platform",
                )

            # 验证设备是否连接
            registry = getattr(self, "device_registry", None)
            if registry is not None:
                device_ids = [d.device_id for d in registry.list(task.platform) if d.connection_status != "disconnected"]
            elif self.device_monitor:
                device_ids = self.device_monitor.get_online_devices(task.platform)
            else:
                device_ids = []

            if task.device_id not in device_ids:
                return TaskResult(
                    task_id=task.task_id,
                    request_id=request_id,
                    status=TaskStatus.FAILED,
                    platform=task.platform,
                    error=f"Device not found: {task.device_id}",
                )

        # 3. action_type 验证
        supported_actions = manager.get_supported_actions()
        for i, action in enumerate(task.actions):
            action_type = action.action_type
            if action_type == "cmd_exec" and host_command_only:
                continue
            if action_type not in supported_actions:
                return TaskResult(
                    task_id=task.task_id,
                    request_id=request_id,
                    status=TaskStatus.FAILED,
                    platform=task.platform,
                    error=f"Action not supported: {action_type} on {task.platform}",
                )

        return None

    def _needs_context(self, task: Task) -> bool:
        """
        检查任务是否需要创建 context。

        以下情况不需要 context：
        1. 任务只有 stop_app 动作
        2. 任务中所有动作都不需要 context（如 get_token）
        3. 任务包含 start_app 动作（由 start_app 自己创建 context）
        """
        if not task.actions:
            return True

        from worker.actions import ActionRegistry

        # 如果任务只有 stop_app，则不需要 context
        if all(a.action_type == "stop_app" for a in task.actions):
            return False

        # 如果任务包含 start_app，不预先创建 context（由 start_app 自己创建）
        if any(a.action_type == "start_app" for a in task.actions):
            return False

        # 检查所有动作是否都不需要 context
        # 如果都不需要 context，则不需要创建 context
        all_no_context = True
        for action in task.actions:
            executor = ActionRegistry.get(action.action_type)
            # 如果动作在 Registry 中且有 requires_context 属性
            if executor is not None:
                if executor.requires_context:
                    all_no_context = False
                    break
            # 如果动作不在 Registry 中（如平台特有动作），默认需要 context
            else:
                all_no_context = False
                break

        # 如果所有动作都不需要 context，不创建 context
        if all_no_context:
            return False

        return True

    def _needs_auto_start(self, task: Task) -> bool:
        """
        检查是否需要在执行任务前自动启动平台。

        以下情况不需要自动启动：
        1. 任务包含 start_app 动作（由 start_app 自己控制）
        2. 任务包含 stop_app 动作（不需要启动平台）
        3. 任务中所有动作都不需要 context（如 get_token）
        """
        if not task.actions:
            return True

        from worker.actions import ActionRegistry

        # 如果任务包含 start_app 或 stop_app，则不需要自动启动
        for action in task.actions:
            if action.action_type in ["start_app", "stop_app"]:
                return False

        # 检查所有动作是否都不需要 context
        # 如果都不需要 context，则不需要启动平台
        all_no_context = True
        for action in task.actions:
            executor = ActionRegistry.get(action.action_type)
            # 如果动作在 Registry 中且有 requires_context 属性
            if executor is not None:
                if executor.requires_context:
                    all_no_context = False
                    break
            # 如果动作不在 Registry 中（如平台特有动作），默认需要 context
            else:
                all_no_context = False
                break

        # 如果所有动作都不需要 context，不启动平台
        if all_no_context:
            return False

        return True

    def _get_cache_clear_status(self) -> Dict[str, Any]:
        """获取缓存清理状态。

        Returns:
            Dict 包含 last_clear_timestamp 字段
        """
        if not os.path.exists(self._cache_clear_status_file):
            return {"last_clear_timestamp": 0}

        try:
            with open(self._cache_clear_status_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"last_clear_timestamp": 0}

    def _save_cache_clear_status(self, status: Dict[str, Any]) -> None:
        """保存缓存清理状态。"""
        try:
            os.makedirs(os.path.dirname(self._cache_clear_status_file), exist_ok=True)
            with open(self._cache_clear_status_file, "w", encoding="utf-8") as f:
                json.dump(status, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save cache clear status: {e}")

    def _check_and_clear_web_data(self) -> None:
        """检查并清理 Web 平台数据。

        触发条件：
        - 缓存清理功能已启用
        - 距离上次清理超过配置的间隔
        - 当前状态为 idle（无任务执行）或 clear_on_idle 为 false
        """
        # 获取 Web 平台配置
        web_config = self.config.platforms.get("web")
        if not web_config:
            return

        # 检查是否启用
        if not web_config.get("cache_clear_enabled", True):
            logger.debug("Cache clear is disabled")
            return

        # 检查是否仅在空闲时清理
        clear_on_idle = web_config.get("cache_clear_clear_on_idle", True)
        if clear_on_idle and self._status != "online":
            logger.debug("Not idle, skip cache clear check")
            return

        # 检查时间间隔
        interval_hours = web_config.get("cache_clear_interval_hours", 24)
        interval_seconds = interval_hours * 3600

        status = self._get_cache_clear_status()
        last_clear = status.get("last_clear_timestamp", 0)
        now = time.time()

        if now - last_clear < interval_seconds:
            logger.debug(f"Cache clear interval not reached: {now - last_clear}s < {interval_seconds}s")
            return

        # 获取 Web 平台管理器
        manager = self.platform_managers.get("web")
        if not manager:
            logger.warning("Web platform manager not available")
            return

        # 检查 Web 平台是否已启动
        if not manager.is_available():
            logger.debug("Web platform not started, skip data clear")
            return

        # 执行清理
        try:
            manager.clear_browser_data()
            # 更新清理时间
            self._save_cache_clear_status({
                "last_clear_timestamp": now,
                "last_clear_time": datetime.now().isoformat()
            })
        except Exception as e:
            logger.warning(f"Failed to clear web data: {e}")

    def _execute_task(
        self,
        task: Task,
        *,
        cancel_event: threading.Event | None = None,
    ) -> TaskResult:
        """
        执行任务。

        Args:
            task: 任务对象

        Returns:
            TaskResult: 任务结果
        """
        platform = task.platform
        logger.info(
            f"Task started: task_id={task.task_id}, platform={platform}, "
            f"device_id={task.device_id}"
        )

        # 统一获取 request_id，避免在异常分支中重复调用
        request_id = get_request_id()

        # 获取平台管理器
        manager = self.platform_managers.get(platform)
        if not manager:
            return TaskResult(
                task_id=task.task_id,
                request_id=request_id,
                status=TaskStatus.FAILED,
                platform=platform,
                error=f"Platform manager not available: {platform}",
            )

        # 前置验证
        validation_result = self._validate_task(task, manager)
        if validation_result:
            return validation_result

        # 移动端 start_app/stop_app 需要确保设备服务可用，即使 needs_context=False
        # 因为这些动作依赖 _current_device 和 client 来执行命令
        needs_device_service = (
            platform in ("ios", "android", "harmony_mobile", "harmony_pc")
            and task.device_id
            and any(a.action_type in ("start_app", "stop_app") for a in task.actions)
        )

        # 启动平台（如果未启动）
        needs_auto_start = self._needs_auto_start(task) or needs_device_service
        if needs_auto_start and not manager.is_available():
            try:
                manager.start()
            except Exception as e:
                exc_type, exc_value, exc_tb = sys.exc_info()
                line_no = exc_tb.tb_lineno if exc_tb else "unknown"
                return TaskResult(
                    task_id=task.task_id,
                    request_id=request_id,
                    status=TaskStatus.FAILED,
                    platform=platform,
                    error=f"Line {line_no}: Failed to start platform: {e}",
                )

        context = None
        try:
            self._status = "busy"

            # 检查是否只需要关闭会话（stop_app 动作不需要 context）
            needs_context = self._needs_context(task)

            # 创建执行上下文
            if needs_context or needs_device_service:
                try:
                    # 移动端：确保设备服务可用（启动 WDA/u2）
                    if platform in ("ios", "android", "harmony_mobile", "harmony_pc") and task.device_id:
                        status, message = manager.ensure_device_service(task.device_id)
                        if status != "online":
                            return TaskResult(
                                task_id=task.task_id,
                                request_id=request_id,
                                status=TaskStatus.FAILED,
                                platform=platform,
                                error=f"Device service not available: {message}",
                            )
                        # 服务启动成功，通知 device_monitor 更新设备状态
                        if self.device_monitor:
                            self.device_monitor.mark_device_online(platform, task.device_id)

                    context = manager.create_context(device_id=task.device_id, options=task.metadata)
                except Exception as e:
                    exc_type, exc_value, exc_tb = sys.exc_info()
                    line_no = exc_tb.tb_lineno if exc_tb else "unknown"
                    return TaskResult(
                        task_id=task.task_id,
                        request_id=request_id,
                        status=TaskStatus.FAILED,
                        platform=platform,
                        error=f"Line {line_no}: Failed to create context: {e}",
                    )

            # 执行动作列表
            result = self._execute_actions(manager, context, task, cancel_event=cancel_event)

            return result

        except Exception as e:
            exc_type, exc_value, exc_tb = sys.exc_info()
            line_no = exc_tb.tb_lineno if exc_tb else "unknown"
            return TaskResult(
                task_id=task.task_id,
                request_id=request_id,
                status=TaskStatus.FAILED,
                platform=platform,
                error=f"Line {line_no}: {e}",
            )

        finally:
            self._status = "online"

            # 检查是否需要清理 Web 数据
            self._check_and_clear_web_data()

            # 清理执行上下文（不关闭会话，保持资源复用）
            if context is not None:
                try:
                    manager.close_context(context, close_session=False)
                except Exception as e:
                    logger.warning(f"Failed to close context: {e}\n{traceback.format_exc()}")


    def _attach_action_artifacts(self, task: Task, result: ActionResult) -> list[dict[str, Any]]:
        """登记动作产生的截图和录屏文件。"""
        references: list[dict[str, Any]] = []
        if result.screenshot:
            try:
                data = base64.b64decode(result.screenshot, validate=True)
                reference = self.artifact_service.save_bytes(
                    task.task_id, data, artifact_type="screenshot",
                    mime_type="image/jpeg", extension="jpg", action_number=result.number,
                )
                references.append(reference.to_dict())
            except Exception as exc:
                logger.warning(f"Failed to persist action screenshot: {exc}")
        if result.action_type == "stop_recording" and result.output and os.path.isfile(result.output):
            try:
                reference = self.artifact_service.save_file(
                    task.task_id, result.output, artifact_type="recording",
                    mime_type="video/mp4", extension="mp4", action_number=result.number,
                )
                references.append(reference.to_dict())
            except Exception as exc:
                logger.warning(f"Failed to persist recording artifact: {exc}")
        result.artifacts.extend(references)
        return references

    def _execute_actions(
        self,
        manager: PlatformManager,
        context: Any,
        task: Task,
        cancel_event: threading.Event | None = None,
    ) -> TaskResult:
        """
        执行动作列表。

        Args:
            manager: 平台管理器
            context: 执行上下文
            task: 任务对象
            cancel_event: 取消信号（可选）

        Returns:
            TaskResult: 任务结果
        """
        started_at = datetime.now()
        started_monotonic = time.monotonic()
        timeout_ms = task.config.timeout
        deadline = (
            started_monotonic + timeout_ms / 1000
            if timeout_ms and timeout_ms > 0
            else None
        )
        actions_results = []
        request_id = get_request_id()  # 获取 request_id

        for i, action in enumerate(task.actions):
            # 取消和总超时检查点：动作执行前先结束任务，避免继续占用设备。
            if cancel_event and cancel_event.is_set():
                logger.info(f"Task cancelled at action {i}: task_id={task.task_id}")
                return TaskResult(
                    task_id=task.task_id,
                    request_id=request_id,
                    status=TaskStatus.CANCELLED,
                    platform=task.platform,
                    started_at=started_at,
                    finished_at=datetime.now(),
                    actions=actions_results,
                    error="Task cancelled by user",
                )

            if deadline is not None and time.monotonic() >= deadline:
                logger.warning(f"Task timeout before action {i}: task_id={task.task_id}")
                return TaskResult(
                    task_id=task.task_id,
                    request_id=request_id,
                    status=TaskStatus.TIMEOUT,
                    platform=task.platform,
                    started_at=started_at,
                    finished_at=datetime.now(),
                    actions=actions_results,
                    error=f"Task timeout after {timeout_ms}ms",
                )
            action_started = time.monotonic()
            configured_action_timeout = (
                action.timeout if action.timeout_explicit else task.config.action_timeout
            )
            action_timeout_ms = (
                configured_action_timeout
                if configured_action_timeout and configured_action_timeout > 0
                else timeout_ms
            )
            action_deadline = (
                action_started + action_timeout_ms / 1000
                if action_timeout_ms and action_timeout_ms > 0
                else deadline
            )
            if deadline is not None:
                action_deadline = min(action_deadline, deadline) if action_deadline else deadline
            action.execution_control = ExecutionControl(
                deadline_monotonic=action_deadline,
                cancel_event=cancel_event or threading.Event(),
            )
            try:
                result = manager.execute_action(context, action)
                action.execution_control.checkpoint()
            except ActionCancelled as exc:
                return TaskResult(
                    task_id=task.task_id, request_id=request_id,
                    status=TaskStatus.CANCELLED, platform=task.platform,
                    started_at=started_at, finished_at=datetime.now(),
                    actions=actions_results, error=str(exc),
                )
            except ActionTimedOut:
                result = ActionResult(
                    number=i,
                    action_type=action.action_type,
                    status=ActionStatus.FAILED,
                    error=f"Action timeout after {action_timeout_ms}ms",
                )
                result.duration_ms = int((time.monotonic() - action_started) * 1000)
                result.request_id = request_id
                actions_results.append(result)
                return TaskResult(
                    task_id=task.task_id, request_id=request_id,
                    status=TaskStatus.TIMEOUT, platform=task.platform,
                    started_at=started_at, finished_at=datetime.now(),
                    actions=actions_results, error=result.error,
                )
            finally:
                action.execution_control = None
            if result.duration_ms <= 0:
                result.duration_ms = int((time.monotonic() - action_started) * 1000)
            result.number = i
            result.request_id = request_id  # 填充 request_id
            self._attach_action_artifacts(task, result)
            actions_results.append(result)

            if deadline is not None and time.monotonic() >= deadline:
                logger.warning(f"Task timeout after action {i}: task_id={task.task_id}")
                return TaskResult(
                    task_id=task.task_id,
                    request_id=request_id,
                    status=TaskStatus.TIMEOUT,
                    platform=task.platform,
                    started_at=started_at,
                    finished_at=datetime.now(),
                    actions=actions_results,
                    error=f"Task timeout after {timeout_ms}ms",
                )

            if cancel_event and cancel_event.is_set():
                logger.info(f"Task cancelled after action {i}: task_id={task.task_id}")
                return TaskResult(
                    task_id=task.task_id,
                    request_id=request_id,
                    status=TaskStatus.CANCELLED,
                    platform=task.platform,
                    started_at=started_at,
                    finished_at=datetime.now(),
                    actions=actions_results,
                    error="Task cancelled by user",
                )

            # 如果动作返回了新的 context（如 start_app），更新后续动作使用的 context
            if result.context is not None:
                context = result.context
                logger.debug(f"Context updated after action {i}: {action.action_type}")

            # 记录动作执行结果
            logger.debug(
                f"Action result: number={i}, type={action.action_type}, "
                f"status={result.status}, duration={result.duration_ms}ms"
            )

            # 全局动作间隔延迟：如果不是最后一个 action，且当前和下一个 action 都不是 wait，则等待
            if i < len(task.actions) - 1:
                current_is_wait = action.action_type == "wait"
                next_action = task.actions[i + 1]
                next_is_wait = next_action.action_type == "wait"
                if not current_is_wait and not next_is_wait:
                    if cancel_event is not None:
                        if cancel_event.wait(self.config.action_step_delay):
                            return TaskResult(task_id=task.task_id, request_id=request_id, status=TaskStatus.CANCELLED, platform=task.platform, started_at=started_at, finished_at=datetime.now(), actions=actions_results, error="Task cancelled by user")

            # 如果动作失败且未配置继续，停止执行
            if result.status != ActionStatus.SUCCESS and not task.metadata.get("continue_on_error"):
                logger.warning(
                    f"Action failed: number={i}, type={action.action_type}, "
                    f"error={result.error}"
                )

                # 获取失败截图
                error_screenshot = None
                error_artifacts: list[dict[str, Any]] = []
                try:
                    screenshot_bytes = manager.get_screenshot(context)
                    # 压缩为 JPEG q=80，减少传输体积（返回给调用方查看）
                    compressed = compress_image_to_jpeg(screenshot_bytes, quality=80)
                    error_screenshot = base64.b64encode(compressed).decode("utf-8")
                    reference = self.artifact_service.save_bytes(
                        task.task_id, compressed, artifact_type="error_screenshot",
                        mime_type="image/jpeg", extension="jpg",
                    )
                    error_artifacts.append(reference.to_dict())
                except Exception as e:
                    logger.warning(f"Failed to get error screenshot: {e}\n{traceback.format_exc()}")

                failed_result = TaskResult(
                    task_id=task.task_id,
                    request_id=request_id,  # 填充 request_id
                    status=TaskStatus.FAILED,
                    platform=task.platform,
                    started_at=started_at,
                    finished_at=datetime.now(),
                    actions=actions_results,
                    error=result.error,
                    error_screenshot=error_screenshot,
                    artifacts=[artifact for action_result in actions_results for artifact in action_result.artifacts] + error_artifacts,
                )

                # 打印结果（排除截图的 base64 数据）
                log_dict = failed_result.to_dict()
                if log_dict.get('error_screenshot'):
                    log_dict['error_screenshot'] = '<base64_data>'
                if log_dict.get('actions'):
                    for ar in log_dict['actions']:
                        if ar.get('screenshot'):
                            ar['screenshot'] = '<base64_data>'
                logger.info(f"Task failed: {log_dict}")

                return failed_result

        result = TaskResult(
            task_id=task.task_id,
            request_id=request_id,  # 填充 request_id
            status=TaskStatus.SUCCESS,
            platform=task.platform,
            started_at=started_at,
            finished_at=datetime.now(),
            actions=actions_results,
            artifacts=[artifact for action_result in actions_results for artifact in action_result.artifacts],
        )

        # 打印结果（排除截图的 base64 数据）
        log_dict = result.to_dict()
        if log_dict.get('actions'):
            for ar in log_dict['actions']:
                if ar.get('screenshot'):
                    ar['screenshot'] = '<base64_data>'
        logger.info(f"Task completed: {log_dict}")

        return result

    # ========== 同步/异步执行方法 ==========

    def _execute_task_callback(self, task: Task, cancel_event: threading.Event) -> TaskResult:
        return self._execute_task(task, cancel_event=cancel_event)

    def execute_sync(
        self,
        platform: str,
        actions: list[dict[str, Any]],
        device_id: str | None = None,
        window: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        task = Task.create(
            platform=platform,
            actions=actions,
            device_id=device_id,
            metadata={"window": window} if window else None,
            generate_id=False,
        )
        result = self.runtime.task_service.execute_sync(
            task,
            request_id=get_request_id(),
        )
        return result.to_dict(include_task_id=False)

    def execute_async(
        self,
        platform: str,
        actions: list[dict[str, Any]],
        device_id: str | None = None,
        window: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> tuple[str, str, str | None]:
        task = Task.create(
            platform=platform,
            actions=actions,
            device_id=device_id,
            metadata={"window": window} if window else None,
            generate_id=True,
        )
        task_id, status = self.runtime.task_service.submit_async(
            task,
            request_id=get_request_id(),
            idempotency_key=idempotency_key,
        )
        return task_id, status, self.runtime.task_service.get_request_id(task_id)

    def get_task_result(self, task_id: str) -> dict[str, Any] | None:
        return self.runtime.task_service.get(task_id)

    def cancel_task(self, task_id: str) -> dict[str, Any]:
        return self.runtime.task_service.cancel(task_id)


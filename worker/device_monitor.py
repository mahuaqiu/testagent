"""
设备监控模块。

独立监控设备状态，维护设备服务。
"""

import logging
import threading
from typing import Any, Callable, Dict, List, Optional

from worker.config import WorkerConfig

logger = logging.getLogger(__name__)


class DeviceMonitor:
    """
    设备监控器。

    负责：
    - 定时检测物理设备连接
    - 维护设备服务状态（WDA/uiautomator2）
    - 管理正常/异常设备列表
    - 自动恢复异常设备
    """

    def __init__(self, config: WorkerConfig):
        self.config = config
        self.check_interval = config.device_check_interval
        self.retry_count = config.service_retry_count
        self.retry_interval = config.service_retry_interval

        # 设备列表
        self._android_devices: List[Dict[str, Any]] = []
        self._ios_devices: List[Dict[str, Any]] = []
        self._faulty_android_devices: List[Dict[str, Any]] = []
        self._faulty_ios_devices: List[Dict[str, Any]] = []

        # 平台管理器引用
        self._android_manager: Optional[Any] = None
        self._ios_manager: Optional[Any] = None

        # 线程控制
        self._monitor_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # 回调
        self.on_device_change: Optional[Callable[[Dict], None]] = None

    def set_platform_managers(self, android_manager=None, ios_manager=None) -> None:
        """设置平台管理器引用。"""
        self._android_manager = android_manager
        self._ios_manager = ios_manager

    def start(self) -> None:
        """启动监控。"""
        if self._monitor_thread and self._monitor_thread.is_alive():
            return

        self._stop_event.clear()
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()
        logger.info(f"Device monitor started (interval={self.check_interval}s)")

    def stop(self) -> None:
        """停止监控。"""
        self._stop_event.set()
        if self._monitor_thread:
            self._monitor_thread.join(timeout=5)
        logger.info("Device monitor stopped")

    def _monitor_loop(self) -> None:
        """监控循环。"""
        # 首次立即执行
        self._check_and_maintain()

        while not self._stop_event.is_set():
            self._stop_event.wait(self.check_interval)

            if self._stop_event.is_set():
                break

            self._check_and_maintain()

    def _check_and_maintain(self) -> None:
        """检查和维护设备。"""
        self._detect_physical_devices()
        self._maintain_services()

        if self.on_device_change:
            self.on_device_change(self.get_all_devices())

    def _detect_physical_devices(self) -> None:
        """检测物理设备连接。"""
        # Android 设备检测
        if self._android_manager:
            try:
                from worker.discovery.android import AndroidDiscoverer
                devices = AndroidDiscoverer.discover()

                existing_udids = {d["udid"] for d in self._android_devices}
                existing_udids.update({d["udid"] for d in self._faulty_android_devices})

                for device in devices:
                    if device.udid not in existing_udids:
                        logger.info(f"New Android device detected: {device.udid}")
                        self._add_device("android", {
                            "udid": device.udid,
                            "name": device.name,
                            "model": device.model,
                        })
            except Exception as e:
                logger.error(f"Android device detection failed: {e}")

        # iOS 设备检测
        if self._ios_manager:
            try:
                from worker.discovery.ios import iOSDiscoverer
                devices = iOSDiscoverer.discover()

                existing_udids = {d["udid"] for d in self._ios_devices}
                existing_udids.update({d["udid"] for d in self._faulty_ios_devices})

                for device in devices:
                    if device.udid not in existing_udids:
                        logger.info(f"New iOS device detected: {device.udid}")
                        self._add_device("ios", {
                            "udid": device.udid,
                            "name": device.name,
                            "model": device.model,
                            "os_version": device.os_version,
                        })
            except Exception as e:
                logger.error(f"iOS device detection failed: {e}")

    def _add_device(self, platform: str, device_info: Dict[str, Any]) -> None:
        """添加新设备到异常列表，立即尝试启动服务。"""
        if platform == "android":
            self._faulty_android_devices.append(device_info)
        else:
            self._faulty_ios_devices.append(device_info)

        self._try_start_service(platform, device_info["udid"])

    def _try_start_service(self, platform: str, udid: str) -> None:
        """尝试启动设备服务。"""
        manager = self._android_manager if platform == "android" else self._ios_manager
        if not manager:
            return

        for attempt in range(self.retry_count):
            status, message = manager.ensure_device_service(udid)

            if status == "online":
                if platform == "android":
                    self._faulty_android_devices = [
                        d for d in self._faulty_android_devices if d["udid"] != udid
                    ]
                    self._android_devices.append({"udid": udid})
                else:
                    self._faulty_ios_devices = [
                        d for d in self._faulty_ios_devices if d["udid"] != udid
                    ]
                    self._ios_devices.append({"udid": udid})

                logger.info(f"Device service started: {udid}")
                return

            logger.warning(f"Service start attempt {attempt + 1} failed for {udid}: {message}")

            if attempt < self.retry_count - 1:
                self._stop_event.wait(self.retry_interval)
                if self._stop_event.is_set():
                    return

        logger.error(f"Failed to start service for {udid} after {self.retry_count} attempts")

    def _maintain_services(self) -> None:
        """维护服务状态，检查异常设备恢复。"""
        for device in self._faulty_android_devices[:]:
            self._try_start_service("android", device["udid"])

        for device in self._faulty_ios_devices[:]:
            self._try_start_service("ios", device["udid"])

        self._check_online_devices()

    def _check_online_devices(self) -> None:
        """检查在线设备状态。"""
        if self._android_manager:
            for device in self._android_devices[:]:
                udid = device["udid"]
                manager_devices = self._android_manager.get_online_devices()
                if udid not in manager_devices:
                    self._android_devices = [d for d in self._android_devices if d["udid"] != udid]
                    self._faulty_android_devices.append({"udid": udid})
                    logger.warning(f"Android device went offline: {udid}")

        if self._ios_manager:
            for device in self._ios_devices[:]:
                udid = device["udid"]
                manager_devices = self._ios_manager.get_online_devices()
                if udid not in manager_devices:
                    self._ios_devices = [d for d in self._ios_devices if d["udid"] != udid]
                    self._faulty_ios_devices.append({"udid": udid})
                    logger.warning(f"iOS device went offline: {udid}")

    def get_all_devices(self) -> Dict[str, Any]:
        """获取所有设备状态。"""
        return {
            "android": self._android_devices,
            "ios": self._ios_devices,
            "faulty_android": self._faulty_android_devices,
            "faulty_ios": self._faulty_ios_devices,
        }

    def get_online_devices(self, platform: str) -> List[str]:
        """获取在线设备 UDID 列表。"""
        if platform == "android":
            return [d["udid"] for d in self._android_devices]
        else:
            return [d["udid"] for d in self._ios_devices]

    def is_device_online(self, platform: str, udid: str) -> bool:
        """检查设备是否在线。"""
        return udid in self.get_online_devices(platform)
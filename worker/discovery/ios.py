"""
iOS 设备发现模块。

使用 tidevice3 发现 iOS 设备。
"""

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# iOS 设备型号映射
IOS_DEVICE_MODELS = {
    "iPhone14,2": "iPhone 13 Pro",
    "iPhone14,3": "iPhone 13 Pro Max",
    "iPhone14,4": "iPhone 13 mini",
    "iPhone14,5": "iPhone 13",
    "iPhone15,2": "iPhone 14 Pro",
    "iPhone15,3": "iPhone 14 Pro Max",
    "iPhone15,4": "iPhone 14",
    "iPhone15,5": "iPhone 14 Plus",
    "iPhone16,1": "iPhone 15 Pro",
    "iPhone16,2": "iPhone 15 Pro Max",
    "iPhone16,3": "iPhone 15",
    "iPhone16,4": "iPhone 15 Plus",
}

# 设备分辨率映射
IOS_RESOLUTION_MAP = {
    "iPhone14,2": "1170x2532",
    "iPhone14,3": "1284x2778",
    "iPhone14,4": "1080x2340",
    "iPhone14,5": "1170x2532",
    "iPhone15,2": "1179x2556",
    "iPhone15,3": "1290x2796",
    "iPhone15,4": "1170x2532",
    "iPhone15,5": "1284x2778",
    "iPhone16,1": "1179x2556",
    "iPhone16,2": "1290x2796",
    "iPhone16,3": "1170x2532",
    "iPhone16,4": "1284x2778",
}


@dataclass
class iOSDeviceInfo:
    """iOS 设备信息。"""
    udid: str
    name: str
    model: str
    product_type: str
    os_version: str
    build_version: str
    resolution: str
    status: str

    def to_dict(self) -> Dict:
        """转换为字典。"""
        return {
            "platform": "ios",
            "udid": self.udid,
            "name": self.name,
            "model": self.model,
            "product_type": self.product_type,
            "os_version": self.os_version,
            "build_version": self.build_version,
            "resolution": self.resolution,
            "status": self.status,
        }


class iOSDiscoverer:
    """iOS 设备发现器。"""

    @staticmethod
    def check_tidevice_available() -> bool:
        """检查 tidevice 是否可用。"""
        try:
            import tidevice
            return True
        except ImportError:
            return False

    @staticmethod
    def list_devices() -> List[str]:
        """获取设备 UDID 列表。"""
        try:
            import tidevice
            return tidevice.usb_device_list()
        except Exception as e:
            logger.error(f"Failed to list iOS devices: {e}")
            return []

    @staticmethod
    def get_resolution_by_model(product_type: str) -> str:
        """根据设备型号推断分辨率。"""
        return IOS_RESOLUTION_MAP.get(product_type, "Unknown")

    @staticmethod
    def get_device_info(udid: str, status: str = "online") -> Optional[iOSDeviceInfo]:
        """获取设备详细信息。"""
        if status == "offline":
            return iOSDeviceInfo(
                udid=udid,
                name="Unknown",
                model="Unknown",
                product_type="Unknown",
                os_version="Unknown",
                build_version="Unknown",
                resolution="Unknown",
                status="offline",
            )

        try:
            import tidevice
            d = tidevice.Device(udid)
            product_type = d.product_type or "Unknown"

            return iOSDeviceInfo(
                udid=udid,
                name=d.name or "Unknown",
                model=IOS_DEVICE_MODELS.get(product_type, product_type),
                product_type=product_type,
                os_version=d.product_version or "Unknown",
                build_version=d.build_version or "Unknown",
                resolution=iOSDiscoverer.get_resolution_by_model(product_type),
                status="online",
            )
        except Exception as e:
            logger.error(f"Failed to get device info for {udid}: {e}")
            return None

    @classmethod
    def discover(cls) -> List[iOSDeviceInfo]:
        """发现所有 iOS 设备。"""
        if not cls.check_tidevice_available():
            logger.warning("tidevice not available, skipping iOS discovery")
            return []

        devices = []
        for udid in cls.list_devices():
            info = cls.get_device_info(udid)
            if info:
                devices.append(info)
        return devices

    @classmethod
    def discover_device(cls, udid: str) -> Optional[iOSDeviceInfo]:
        """发现指定设备。"""
        all_udids = cls.list_devices()
        if udid in all_udids:
            return cls.get_device_info(udid)
        return None

    @classmethod
    def check_device_connected(cls, udid: str) -> bool:
        """检查指定设备是否连接。"""
        return udid in cls.list_devices()
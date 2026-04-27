"""
iOS 设备发现模块。

使用 go-ios 发现 iOS 设备。
"""

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# iOS 设备型号映射
IOS_DEVICE_MODELS = {
    "iPhone10,1": "iPhone 8",
    "iPhone10,2": "iPhone 8 Plus",
    "iPhone10,3": "iPhone X",
    "iPhone10,4": "iPhone 8",
    "iPhone10,5": "iPhone 8 Plus",
    "iPhone10,6": "iPhone X",
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
    "iPhone10,1": "750x1334",   # iPhone 8
    "iPhone10,2": "1080x1920",  # iPhone 8 Plus
    "iPhone10,3": "1125x2436",  # iPhone X
    "iPhone10,4": "750x1334",   # iPhone 8 (GSM)
    "iPhone10,5": "1080x1920",  # iPhone 8 Plus (GSM)
    "iPhone10,6": "1125x2436",  # iPhone X (GSM)
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

    _go_ios_client: Optional["GoIOSClient"] = None

    @classmethod
    def set_go_ios_client(cls, client: "GoIOSClient") -> None:
        """设置 GoIOSClient 实例。"""
        cls._go_ios_client = client

    @staticmethod
    def check_go_ios_available() -> bool:
        """检查 go-ios 是否可用。"""
        try:
            from worker.platforms.go_ios_client import GoIOSClient
            return True
        except ImportError:
            return False

    @staticmethod
    def check_tidevice_available() -> bool:
        """检查 tidevice3 是否可用（已废弃，保留向后兼容）。"""
        return iOSDiscoverer.check_go_ios_available()

    @staticmethod
    def list_devices() -> List[str]:
        """获取设备 UDID 列表。"""
        if not iOSDiscoverer._go_ios_client:
            logger.warning("GoIOSClient not initialized")
            return []
        try:
            devices = iOSDiscoverer._go_ios_client.list_devices()
            return [d["udid"] for d in devices if d["udid"]]
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

        if not iOSDiscoverer._go_ios_client:
            return None

        try:
            info = iOSDiscoverer._go_ios_client.get_device_info(udid)
            if not info:
                return None
            product_type = info.get("model", "Unknown")
            return iOSDeviceInfo(
                udid=udid,
                name=info.get("name", "Unknown"),
                model=IOS_DEVICE_MODELS.get(product_type, product_type),
                product_type=product_type,
                os_version=info.get("version", "Unknown"),
                build_version=info.get("build_version", "Unknown"),
                resolution=iOSDiscoverer.get_resolution_by_model(product_type),
                status="online",
            )
        except Exception as e:
            logger.error(f"Failed to get device info for {udid}: {e}")
            return None

    @classmethod
    def discover(cls) -> List[iOSDeviceInfo]:
        """发现所有 iOS 设备。"""
        if not cls._go_ios_client:
            logger.warning("GoIOSClient not initialized, skipping iOS discovery")
            return []

        try:
            devices = cls._go_ios_client.list_devices()
            result = []
            for d in devices:
                udid = d["udid"]
                if udid:
                    info = cls.get_device_info(udid)
                    if info:
                        result.append(info)
            return result
        except Exception as e:
            logger.error(f"Failed to discover iOS devices: {e}")
            return []

    @classmethod
    def discover_device(cls, udid: str) -> Optional[iOSDeviceInfo]:
        """发现指定设备。"""
        return cls.get_device_info(udid)

    @classmethod
    def check_device_connected(cls, udid: str) -> bool:
        """检查指定设备是否连接。"""
        return udid in cls.list_devices()
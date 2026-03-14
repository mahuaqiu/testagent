"""
iOS 设备发现模块。

通过 libimobiledevice (idevice_id, ideviceinfo) 发现连接到本机的 iOS 设备。
"""

import re
import subprocess
from dataclasses import dataclass
from typing import List, Optional, Dict


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
    # 更多型号可根据需要添加
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
    status: str  # online / offline

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
    def check_libimobiledevice_available() -> bool:
        """检查 libimobiledevice 是否可用。"""
        try:
            result = subprocess.run(
                ["idevice_id", "-h"],
                capture_output=True,
                text=True,
                timeout=5
            )
            return True
        except FileNotFoundError:
            return False
        except Exception:
            return True  # 可能返回非0但命令存在

    @staticmethod
    def list_devices() -> List[str]:
        """
        获取已连接的设备 UDID 列表。

        Returns:
            List[str]: 设备 UDID 列表
        """
        try:
            result = subprocess.run(
                ["idevice_id", "-l"],
                capture_output=True,
                text=True,
                timeout=10
            )

            devices = []
            for line in result.stdout.strip().split("\n"):
                line = line.strip()
                if line and len(line) == 40:  # UDID 长度为 40
                    devices.append(line)

            return devices
        except Exception:
            return []

    @staticmethod
    def get_device_property(udid: str, key: str) -> str:
        """
        获取设备属性。

        Args:
            udid: 设备 UDID
            key: 属性名称

        Returns:
            str: 属性值
        """
        try:
            result = subprocess.run(
                ["ideviceinfo", "-u", udid, "-k", key],
                capture_output=True,
                text=True,
                timeout=10
            )
            return result.stdout.strip()
        except Exception:
            return ""

    @staticmethod
    def get_device_info(udid: str, status: str = "online") -> Optional[iOSDeviceInfo]:
        """
        获取设备详细信息。

        Args:
            udid: 设备 UDID
            status: 设备状态

        Returns:
            iOSDeviceInfo | None: 设备信息
        """
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
            # 获取设备属性
            name = iOSDiscoverer.get_device_property(udid, "DeviceName")
            product_type = iOSDiscoverer.get_device_property(udid, "ProductType")
            os_version = iOSDiscoverer.get_device_property(udid, "ProductVersion")
            build_version = iOSDiscoverer.get_device_property(udid, "BuildVersion")

            # 解析设备型号
            model = IOS_DEVICE_MODELS.get(product_type, product_type)

            # 获取分辨率（需要根据设备型号推断）
            resolution = iOSDiscoverer.get_resolution_by_model(product_type)

            return iOSDeviceInfo(
                udid=udid,
                name=name or "Unknown",
                model=model,
                product_type=product_type,
                os_version=os_version or "Unknown",
                build_version=build_version or "Unknown",
                resolution=resolution,
                status="online",
            )
        except Exception:
            return None

    @staticmethod
    def get_resolution_by_model(product_type: str) -> str:
        """
        根据设备型号推断分辨率。

        Args:
            product_type: 设备型号

        Returns:
            str: 分辨率字符串
        """
        # 常见设备分辨率映射
        resolution_map = {
            "iPhone14,2": "1170x2532",  # iPhone 13 Pro
            "iPhone14,3": "1284x2778",  # iPhone 13 Pro Max
            "iPhone14,4": "1080x2340",  # iPhone 13 mini
            "iPhone14,5": "1170x2532",  # iPhone 13
            "iPhone15,2": "1179x2556",  # iPhone 14 Pro
            "iPhone15,3": "1290x2796",  # iPhone 14 Pro Max
            "iPhone15,4": "1170x2532",  # iPhone 14
            "iPhone15,5": "1284x2778",  # iPhone 14 Plus
            "iPhone16,1": "1179x2556",  # iPhone 15 Pro
            "iPhone16,2": "1290x2796",  # iPhone 15 Pro Max
            "iPhone16,3": "1170x2532",  # iPhone 15
            "iPhone16,4": "1284x2778",  # iPhone 15 Plus
        }

        return resolution_map.get(product_type, "Unknown")

    @classmethod
    def discover(cls) -> List[iOSDeviceInfo]:
        """
        发现所有 iOS 设备。

        Returns:
            List[iOSDeviceInfo]: 设备信息列表
        """
        if not cls.check_libimobiledevice_available():
            return []

        devices = []
        udid_list = cls.list_devices()

        for udid in udid_list:
            info = cls.get_device_info(udid)
            if info:
                devices.append(info)

        return devices

    @classmethod
    def discover_device(cls, udid: str) -> Optional[iOSDeviceInfo]:
        """
        发现指定设备。

        Args:
            udid: 设备 UDID

        Returns:
            iOSDeviceInfo | None: 设备信息
        """
        all_udids = cls.list_devices()

        if udid in all_udids:
            return cls.get_device_info(udid)

        return None

    @classmethod
    def check_device_connected(cls, udid: str) -> bool:
        """
        检查指定设备是否连接。

        Args:
            udid: 设备 UDID

        Returns:
            bool: 设备是否连接
        """
        return udid in cls.list_devices()
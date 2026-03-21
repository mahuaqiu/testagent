"""
Android 设备发现模块。

通过 ADB 发现连接到本机的 Android 设备。
"""

import re
import subprocess
from dataclasses import dataclass
from typing import List, Optional, Dict


@dataclass
class AndroidDeviceInfo:
    """Android 设备信息。"""

    udid: str
    model: str
    brand: str
    manufacturer: str
    os_version: str
    sdk_version: int
    resolution: str
    density: int
    cpu_abi: str
    status: str  # online / offline / unauthorized

    def to_dict(self) -> Dict:
        """转换为字典。"""
        return {
            "platform": "android",
            "udid": self.udid,
            "model": self.model,
            "brand": self.brand,
            "manufacturer": self.manufacturer,
            "os_version": self.os_version,
            "sdk_version": self.sdk_version,
            "resolution": self.resolution,
            "density": self.density,
            "cpu_abi": self.cpu_abi,
            "status": self.status,
        }


class AndroidDiscoverer:
    """Android 设备发现器。"""

    @staticmethod
    def check_adb_available() -> bool:
        """检查 ADB 是否可用。"""
        try:
            result = subprocess.run(
                ["adb", "version"],
                capture_output=True,
                text=True,
                timeout=5
            )
            return result.returncode == 0
        except Exception:
            return False

    @staticmethod
    def list_devices() -> List[str]:
        """
        获取已连接的设备 UDID 列表。

        Returns:
            List[str]: 设备 UDID 列表
        """
        try:
            result = subprocess.run(
                ["adb", "devices"],
                capture_output=True,
                text=True,
                timeout=10
            )

            devices = []
            lines = result.stdout.strip().split("\n")[1:]  # 跳过 "List of devices attached" 行

            for line in lines:
                line = line.strip()
                if not line:
                    continue

                parts = line.split("\t")
                if len(parts) >= 2:
                    udid = parts[0]
                    status = parts[1]
                    if status == "device":  # 只返回在线设备
                        devices.append(udid)

            return devices
        except Exception:
            return []

    @staticmethod
    def list_all_devices() -> List[tuple[str, str]]:
        """
        获取所有设备及其状态。

        Returns:
            List[tuple[str, str]]: (udid, status) 列表
        """
        try:
            result = subprocess.run(
                ["adb", "devices"],
                capture_output=True,
                text=True,
                timeout=10
            )

            devices = []
            lines = result.stdout.strip().split("\n")[1:]

            for line in lines:
                line = line.strip()
                if not line:
                    continue

                parts = line.split("\t")
                if len(parts) >= 2:
                    udid = parts[0]
                    status = parts[1]
                    devices.append((udid, status))

            return devices
        except Exception:
            return []

    @staticmethod
    def get_device_property(udid: str, prop: str) -> str:
        """
        获取设备属性。

        Args:
            udid: 设备 UDID
            prop: 属性名称

        Returns:
            str: 属性值
        """
        try:
            result = subprocess.run(
                ["adb", "-s", udid, "shell", "getprop", prop],
                capture_output=True,
                text=True,
                timeout=10
            )
            return result.stdout.strip()
        except Exception:
            return ""

    @staticmethod
    def get_device_info(udid: str, status: str = "online") -> Optional[AndroidDeviceInfo]:
        """
        获取设备详细信息。

        Args:
            udid: 设备 UDID
            status: 设备状态

        Returns:
            AndroidDeviceInfo | None: 设备信息
        """
        if status != "device":
            # 离线或未授权设备，只返回基本信息
            return AndroidDeviceInfo(
                udid=udid,
                model="Unknown",
                brand="Unknown",
                manufacturer="Unknown",
                os_version="Unknown",
                sdk_version=0,
                resolution="Unknown",
                density=0,
                cpu_abi="Unknown",
                status="offline" if status == "offline" else status,
            )

        try:
            # 获取设备属性
            model = AndroidDiscoverer.get_device_property(udid, "ro.product.model")
            brand = AndroidDiscoverer.get_device_property(udid, "ro.product.brand")
            manufacturer = AndroidDiscoverer.get_device_property(udid, "ro.product.manufacturer")
            os_version = AndroidDiscoverer.get_device_property(udid, "ro.build.version.release")
            sdk_version_str = AndroidDiscoverer.get_device_property(udid, "ro.build.version.sdk")
            cpu_abi = AndroidDiscoverer.get_device_property(udid, "ro.product.cpu.abi")
            density_str = AndroidDiscoverer.get_device_property(udid, "ro.sf.lcd_density")

            # 获取分辨率
            resolution = AndroidDiscoverer.get_resolution(udid)

            return AndroidDeviceInfo(
                udid=udid,
                model=model or "Unknown",
                brand=brand or "Unknown",
                manufacturer=manufacturer or "Unknown",
                os_version=os_version or "Unknown",
                sdk_version=int(sdk_version_str) if sdk_version_str.isdigit() else 0,
                resolution=resolution,
                density=int(density_str) if density_str.isdigit() else 0,
                cpu_abi=cpu_abi or "Unknown",
                status="online",
            )
        except Exception:
            return None

    @staticmethod
    def get_resolution(udid: str) -> str:
        """
        获取设备分辨率。

        Args:
            udid: 设备 UDID

        Returns:
            str: 分辨率字符串，如 "1080x2400"
        """
        try:
            result = subprocess.run(
                ["adb", "-s", udid, "shell", "wm", "size"],
                capture_output=True,
                text=True,
                timeout=10
            )

            # 输出格式: Physical size: 1080x2400
            match = re.search(r"(\d+x\d+)", result.stdout)
            if match:
                return match.group(1)
        except Exception:
            pass

        return "Unknown"

    @classmethod
    def discover(cls) -> List[AndroidDeviceInfo]:
        """
        发现所有 Android 设备。

        Returns:
            List[AndroidDeviceInfo]: 设备信息列表
        """
        if not cls.check_adb_available():
            return []

        devices = []
        all_devices = cls.list_all_devices()

        for udid, status in all_devices:
            info = cls.get_device_info(udid, status)
            if info:
                devices.append(info)

        return devices

    @classmethod
    def discover_device(cls, udid: str) -> Optional[AndroidDeviceInfo]:
        """
        发现指定设备。

        Args:
            udid: 设备 UDID

        Returns:
            AndroidDeviceInfo | None: 设备信息
        """
        all_devices = cls.list_all_devices()

        for device_udid, status in all_devices:
            if device_udid == udid:
                return cls.get_device_info(udid, status)

        return None

    @staticmethod
    def check_u2_service(udid: str) -> bool:
        """检查 uiautomator2 服务是否可用。"""
        try:
            import uiautomator2 as u2
            device = u2.connect(udid)
            return device.ping()
        except Exception:
            return False
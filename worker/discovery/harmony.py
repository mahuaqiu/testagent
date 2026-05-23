"""
鸿蒙设备发现模块。

通过 HDC 发现连接到本机的鸿蒙设备。
"""

import logging
from dataclasses import dataclass
from typing import List, Optional, Dict

from worker.platforms.harmony_hdc import list_devices, _find_hdc_path, HarmonyHdcWrapper

logger = logging.getLogger(__name__)


@dataclass
class HarmonyDeviceInfo:
    """鸿蒙设备信息。"""

    udid: str
    name: str
    model: str
    sys_version: str
    sdk_version: str
    display_size: tuple
    status: str

    def to_dict(self) -> Dict:
        """转换为字典。"""
        return {
            "platform": "harmony",
            "udid": self.udid,
            "name": self.name,
            "model": self.model,
            "sys_version": self.sys_version,
            "sdk_version": self.sdk_version,
            "display_size": self.display_size,
            "status": self.status,
        }


class HarmonyDiscoverer:
    """鸿蒙设备发现器。"""

    @staticmethod
    def check_hdc_available() -> bool:
        """检查 HDC 是否可用。"""
        return _find_hdc_path() is not None

    @staticmethod
    def list_devices() -> List[str]:
        """
        获取已连接的设备 UDID 列表。

        Returns:
            List[str]: 设备 UDID 列表
        """
        try:
            return list_devices()
        except Exception as e:
            logger.warning(f"获取鸿蒙设备列表失败: {e}")
            return []

    @staticmethod
    def get_device_info(udid: str) -> Optional[HarmonyDeviceInfo]:
        """
        获取设备详细信息。

        Args:
            udid: 设备 UDID

        Returns:
            HarmonyDeviceInfo | None: 设备信息
        """
        try:
            client = HarmonyHdcWrapper(udid)
            return HarmonyDeviceInfo(
                udid=udid,
                name=client.product_name(),
                model=client.model(),
                sys_version=client.sys_version(),
                sdk_version=client.sdk_version(),
                display_size=client.display_size(),
                status="online",
            )
        except Exception as e:
            logger.warning(f"获取设备 [{udid}] 信息失败: {e}")
            return None

    @classmethod
    def discover(cls) -> List[HarmonyDeviceInfo]:
        """
        发现所有鸿蒙设备。

        Returns:
            List[HarmonyDeviceInfo]: 设备信息列表
        """
        if not cls.check_hdc_available():
            logger.warning("HDC 工具不可用")
            return []

        devices = []
        for udid in cls.list_devices():
            info = cls.get_device_info(udid)
            if info:
                devices.append(info)

        logger.info(f"发现 {len(devices)} 台鸿蒙设备")
        return devices

    @classmethod
    def discover_device(cls, udid: str) -> Optional[HarmonyDeviceInfo]:
        """
        发现指定设备。

        Args:
            udid: 设备 UDID

        Returns:
            HarmonyDeviceInfo | None: 设备信息
        """
        # 检查设备是否在列表中
        device_list = cls.list_devices()
        if udid not in device_list:
            logger.warning(f"设备 [{udid}] 未在线")
            return None

        return cls.get_device_info(udid)
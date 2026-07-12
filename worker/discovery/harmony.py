"""
鸿蒙设备发现模块。

通过 HDC 发现连接到本机的鸿蒙设备。
"""

import logging
from dataclasses import dataclass
from typing import List, Optional, Dict

from worker.platforms.harmony_hdc import (
    HdcTarget,
    list_target_info,
    _find_hdc_path,
    HarmonyHdcWrapper,
)

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
    device_category: str = "unknown"
    connection_type: str = "unknown"
    connection_status: str = "ready"
    capabilities: tuple[str, ...] = ()

    def to_dict(self) -> Dict:
        """转换为字典。"""
        platform = (
            "harmony_mobile"
            if self.device_category == "mobile"
            else "harmony_pc"
            if self.device_category == "pc"
            else "unknown"
        )
        return {
            "platform": platform,
            "udid": self.udid,
            "name": self.name,
            "model": self.model,
            "sys_version": self.sys_version,
            "sdk_version": self.sdk_version,
            "display_size": self.display_size,
            "status": self.status,
            "device_category": self.device_category,
            "connection_type": self.connection_type,
            "connection_status": self.connection_status,
            "capabilities": list(self.capabilities),
        }


class HarmonyDiscoverer:
    """鸿蒙设备发现器。"""

    @staticmethod
    def check_hdc_available(configured_path: Optional[str] = None) -> bool:
        """检查 HDC 是否可用。"""
        return _find_hdc_path(configured_path) is not None

    @staticmethod
    def list_devices(configured_path: Optional[str] = None) -> List[str]:
        """
        获取已连接的设备 UDID 列表。

        Returns:
            List[str]: 设备 UDID 列表
        """
        try:
            return [target.udid for target in list_target_info(configured_path)]
        except Exception as e:
            logger.warning(f"获取鸿蒙设备列表失败: {e}")
            return []

    @staticmethod
    def get_device_info(
        udid: str,
        configured_path: Optional[str] = None,
        target: Optional[HdcTarget] = None,
    ) -> Optional[HarmonyDeviceInfo]:
        """
        获取设备详细信息。

        Args:
            udid: 设备 UDID

        Returns:
            HarmonyDeviceInfo | None: 设备信息
        """
        try:
            client = HarmonyHdcWrapper(udid, _find_hdc_path(configured_path))
            device_category = client.device_category()
            capabilities = (
                ("touch", "keyboard", "screenshot")
                if device_category == "mobile"
                else ("mouse", "keyboard", "screenshot")
                if device_category == "pc"
                else ("screenshot",)
            )
            return HarmonyDeviceInfo(
                udid=udid,
                name=client.product_name(),
                model=client.model(),
                sys_version=client.sys_version(),
                sdk_version=client.sdk_version(),
                display_size=client.display_size(),
                status="online",
                device_category=device_category,
                connection_type=target.connection_type if target else "unknown",
                connection_status=target.status if target else "ready",
                capabilities=capabilities,
            )
        except Exception as e:
            logger.warning(f"获取设备 [{udid}] 信息失败: {e}")
            return None

    @classmethod
    def discover(cls, configured_path: Optional[str] = None) -> List[HarmonyDeviceInfo]:
        """
        发现所有鸿蒙设备。

        Returns:
            List[HarmonyDeviceInfo]: 设备信息列表
        """
        if not cls.check_hdc_available(configured_path):
            logger.warning("HDC 工具不可用")
            return []

        devices = []
        for target in list_target_info(configured_path):
            info = cls.get_device_info(target.udid, configured_path, target)
            if info:
                devices.append(info)

        logger.info(f"发现 {len(devices)} 台鸿蒙设备")
        return devices

    @classmethod
    def discover_device(
        cls, udid: str, configured_path: Optional[str] = None
    ) -> Optional[HarmonyDeviceInfo]:
        """
        发现指定设备。

        Args:
            udid: 设备 UDID

        Returns:
            HarmonyDeviceInfo | None: 设备信息
        """
        # 检查设备是否在列表中
        targets = list_target_info(configured_path)
        target = next((item for item in targets if item.udid == udid), None)
        if target is None:
            logger.warning(f"设备 [{udid}] 未在线")
            return None

        return cls.get_device_info(udid, configured_path, target)

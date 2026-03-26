"""
宿主机信息发现模块。
"""

import logging
import platform
import socket
import subprocess
from dataclasses import dataclass
from typing import List, Optional

import psutil

logger = logging.getLogger(__name__)


@dataclass
class HostInfo:
    """宿主机信息。"""

    os_type: str  # windows / macos
    os_version: str
    hostname: str
    ip_addresses: List[str]
    cpu_info: str
    memory_gb: float
    display_resolution: str
    display_scale: float


class HostDiscoverer:
    """宿主机信息发现器。"""

    @staticmethod
    def get_os_type() -> str:
        """获取操作系统类型。"""
        system = platform.system().lower()
        if system == "windows":
            return "windows"
        elif system == "darwin":
            return "macos"
        else:
            return system

    @staticmethod
    def get_os_version() -> str:
        """获取操作系统版本。"""
        system = platform.system().lower()

        if system == "windows":
            # Windows 版本信息
            try:
                result = subprocess.run(
                    ["wmic", "os", "get", "Caption,Version", "/value"],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                lines = result.stdout.strip().split("\n")
                caption = ""
                version = ""
                for line in lines:
                    if line.startswith("Caption="):
                        caption = line.split("=", 1)[1]
                    elif line.startswith("Version="):
                        version = line.split("=", 1)[1]
                return f"{caption} ({version})".strip()
            except Exception:
                return platform.version()

        elif system == "darwin":
            # macOS 版本信息
            try:
                result = subprocess.run(
                    ["sw_vers"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                lines = result.stdout.strip().split("\n")
                product_name = ""
                product_version = ""
                for line in lines:
                    if line.startswith("ProductName:"):
                        product_name = line.split(":", 1)[1].strip()
                    elif line.startswith("ProductVersion:"):
                        product_version = line.split(":", 1)[1].strip()
                return f"{product_name} {product_version}".strip()
            except Exception:
                return f"macOS {platform.mac_ver()[0]}"

        else:
            return platform.version()

    @staticmethod
    def get_hostname() -> str:
        """获取主机名。"""
        return socket.gethostname()

    @staticmethod
    def get_ip_addresses() -> List[str]:
        """获取所有 IP 地址。"""
        addresses = []
        try:
            hostname = socket.gethostname()
            # 获取所有 IP 地址
            for info in socket.getaddrinfo(hostname, None):
                ip = info[4][0]
                # 过滤掉本地回环地址和 IPv6 本地地址
                if ip != "127.0.0.1" and not ip.startswith("::1"):
                    if ip not in addresses:
                        addresses.append(ip)
        except Exception:
            pass

        # 如果没有获取到，尝试其他方式
        if not addresses:
            try:
                for interface, addrs in psutil.net_if_addrs().items():
                    for addr in addrs:
                        if addr.family == socket.AF_INET:
                            ip = addr.address
                            if ip != "127.0.0.1" and ip not in addresses:
                                addresses.append(ip)
            except Exception:
                pass

        return addresses or ["127.0.0.1"]

    @staticmethod
    def get_preferred_ip(configured_ip: Optional[str] = None) -> str:
        """
        获取优先使用的 IP 地址。

        Args:
            configured_ip: 配置的 IP 地址

        Returns:
            str: IP 地址
        """
        all_ips = HostDiscoverer.get_ip_addresses()

        if configured_ip:
            if configured_ip in all_ips:
                return configured_ip
            else:
                logger.warning(
                    f"Configured IP '{configured_ip}' not found in local interfaces. "
                    f"Available IPs: {all_ips}. Falling back to auto-detection."
                )

        # 未配置或配置无效，返回第一个非回环 IP
        return all_ips[0] if all_ips else "127.0.0.1"

    @staticmethod
    def get_cpu_info() -> str:
        """获取 CPU 信息。"""
        system = platform.system().lower()

        if system == "windows":
            try:
                result = subprocess.run(
                    ["wmic", "cpu", "get", "Name", "/value"],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                for line in result.stdout.strip().split("\n"):
                    if line.startswith("Name="):
                        return line.split("=", 1)[1].strip()
            except Exception:
                pass
            return platform.processor() or "Unknown CPU"

        elif system == "darwin":
            try:
                result = subprocess.run(
                    ["sysctl", "-n", "machdep.cpu.brand_string"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                return result.stdout.strip() or platform.processor() or "Unknown CPU"
            except Exception:
                return platform.processor() or "Unknown CPU"

        else:
            return platform.processor() or "Unknown CPU"

    @staticmethod
    def get_memory_gb() -> float:
        """获取内存大小（GB）。"""
        try:
            mem = psutil.virtual_memory()
            return round(mem.total / (1024 ** 3), 1)
        except Exception:
            return 0.0

    @staticmethod
    def get_display_info() -> tuple[str, float]:
        """获取显示器信息（分辨率和缩放比例）。"""
        resolution = "1920x1080"  # 默认值
        scale = 1.0

        try:
            import pyautogui
            size = pyautogui.size()
            resolution = f"{size.width}x{size.height}"

            # 尝试获取缩放比例
            system = platform.system().lower()
            if system == "windows":
                try:
                    import ctypes
                    user32 = ctypes.windll.user32
                    hdc = user32.GetDC(0)
                    LOGPIXELSX = 88
                    scale = ctypes.windll.gdi32.GetDeviceCaps(hdc, LOGPIXELSX) / 96.0
                except Exception:
                    pass
            elif system == "darwin":
                # macOS 通常为 2x Retina
                try:
                    actual_size = pyautogui.size()
                    # 检测是否为 Retina
                    if actual_size.width >= 2560:
                        scale = 2.0
                except Exception:
                    pass
        except Exception:
            pass

        return resolution, scale

    @classmethod
    def discover(cls) -> HostInfo:
        """
        发现宿主机信息。

        Returns:
            HostInfo: 宿主机信息
        """
        resolution, scale = cls.get_display_info()

        return HostInfo(
            os_type=cls.get_os_type(),
            os_version=cls.get_os_version(),
            hostname=cls.get_hostname(),
            ip_addresses=cls.get_ip_addresses(),
            cpu_info=cls.get_cpu_info(),
            memory_gb=cls.get_memory_gb(),
            display_resolution=resolution,
            display_scale=scale,
        )

    @classmethod
    def get_supported_platforms(cls) -> List[str]:
        """
        根据操作系统类型返回支持的平台列表。

        Returns:
            List[str]: 支持的平台列表
        """
        os_type = cls.get_os_type()

        if os_type == "windows":
            return ["web", "windows", "android", "ios"]
        elif os_type == "macos":
            return ["mac"]
        else:
            return []
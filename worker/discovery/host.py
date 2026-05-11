"""
宿主机信息发现模块。
"""

import logging
import platform
import socket
from dataclasses import dataclass
from typing import List, Optional

import psutil

from common.utils import run_cmd

logger = logging.getLogger(__name__)


def _get_ip_segment_priority(ip: str) -> int:
    """
    根据 IP 地址段计算优先级。

    优先级规则：
    - 10.x.x.x: 4 (最高，通常是企业内网)
    - 192.168.x.x: 3 (其次，常见的家庭/办公网络)
    - 172.16-31.x.x: 2 (再次，企业内网备用)
    - 其他: 1 (最低)

    Args:
        ip: IP 地址字符串

    Returns:
        int: 优先级数值（越高越优先）
    """
    if ip.startswith("10."):
        return 4
    elif ip.startswith("192.168."):
        return 3
    elif ip.startswith("172."):
        # 172.16.0.0 - 172.31.255.255 是私有地址段
        parts = ip.split(".")
        if len(parts) >= 2:
            try:
                second_octet = int(parts[1])
                if 16 <= second_octet <= 31:
                    return 2
            except ValueError:
                pass
    return 1


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
                result = run_cmd(
                    ["wmic", "os", "get", "Caption,Version", "/value"],
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
                result = run_cmd(
                    ["sw_vers"],
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
        """
        获取所有 IP 地址（按优先级排序）。

        优先级策略（按 IP 地址段）：
        1. psutil 遍历物理网卡，按 IP 地址段排序：10.x > 192.168.x > 172.16-31.x > 其他
        2. UDP socket 连接测试（获取实际使用的出口 IP）
        3. 返回 127.0.0.1 作为最终后备

        Returns:
            List[str]: IP 地址列表，按优先级排序
        """
        addresses: List[str] = []
        seen: set = set()

        # 方式 1：psutil 遍历网卡（最可靠）
        # 按 IP 地址段优先级排序：10.x > 192.168.x > 172.16-31.x > 其他
        try:
            net_if_addrs = psutil.net_if_addrs()
            net_if_stats = psutil.net_if_stats()

            # 虚拟网卡过滤关键词（已转为小写）
            virtual_keywords = ['virtual', 'vmware', 'vbox', 'hyper-v', 'loopback', 'bluetooth', 'tunnel', 'vpn', 'vethernet', '虚拟']

            # 收集网卡信息并按 IP 地址段优先级排序
            interface_ips: List[tuple[int, str]] = []  # (priority, ip)

            for interface, addrs in net_if_addrs.items():
                # 检查网卡状态
                stats = net_if_stats.get(interface)
                if not stats or not stats.isup:
                    continue

                # 检查是否为虚拟网卡
                is_virtual = any(kw in interface.lower() for kw in virtual_keywords)
                if is_virtual:
                    continue

                # 获取 IPv4 地址
                for addr in addrs:
                    if addr.family == socket.AF_INET:
                        ip = addr.address
                        # 过滤链路本地地址和回环地址
                        if ip != "127.0.0.1" and not ip.startswith("169.254."):
                            if ip not in seen:
                                # 按 IP 地址段计算优先级
                                priority = _get_ip_segment_priority(ip)
                                interface_ips.append((priority, ip))
                                seen.add(ip)

            # 按优先级排序（高优先级在前）
            interface_ips.sort(key=lambda x: -x[0])
            addresses.extend([ip for _, ip in interface_ips])

            if addresses:
                logger.debug(f"Found IPs via psutil: {addresses}")
                return addresses

        except Exception as e:
            logger.debug(f"psutil method failed: {e}")

        # 方式 2：UDP socket 连接测试（获取出口 IP）
        # 尝试连接公共 IP 来获取本机使用的出口 IP
        try:
            # 使用几个公共 IP 地址尝试（不需要真正连接，只是获取本地地址）
            test_ips = ["8.8.8.8", "1.1.1.1", "192.168.1.1"]
            for test_ip in test_ips:
                try:
                    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                        # 不需要真正连接，只是绑定本地地址
                        s.connect((test_ip, 80))
                        local_ip = s.getsockname()[0]
                        if local_ip != "127.0.0.1" and not local_ip.startswith("169.254.") and local_ip not in seen:
                            addresses.append(local_ip)
                            seen.add(local_ip)
                            logger.debug(f"Found IP via UDP socket test: {local_ip}")
                            break  # 找到一个有效 IP 就停止
                except Exception:
                    continue

            if addresses:
                return addresses

        except Exception as e:
            logger.debug(f"UDP socket method failed: {e}")

        # 最终后备
        logger.warning("All IP detection methods failed, falling back to 127.0.0.1")
        return ["127.0.0.1"]

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
                result = run_cmd(
                    ["wmic", "cpu", "get", "Name", "/value"],
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
                result = run_cmd(
                    ["sysctl", "-n", "machdep.cpu.brand_string"],
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

    @staticmethod
    def get_mac_address() -> str:
        """
        获取本机首选 MAC 地址。

        优先选择有 IPv4 地址且非虚拟的网卡 MAC 地址。

        Returns:
            str: MAC 地址（格式：AA:BB:CC:DD:EE:FF），获取失败返回空字符串
        """
        try:
            # 获取所有网络接口
            net_if_addrs = psutil.net_if_addrs()
            net_if_stats = psutil.net_if_stats()

            # 按优先级选择网卡：
            # 1. 有 IPv4 地址（非 127.0.0.1）
            # 2. 状态为 up
            # 3. 非虚拟网卡（排除明显的虚拟网卡名称）
            virtual_keywords = ['virtual', 'vmware', 'vbox', 'hyper-v', 'loopback', 'bluetooth', 'tunnel']

            for interface, addrs in net_if_addrs.items():
                # 检查是否为虚拟网卡
                is_virtual = any(kw in interface.lower() for kw in virtual_keywords)
                if is_virtual:
                    continue

                # 检查网卡状态
                stats = net_if_stats.get(interface)
                if not stats or not stats.isup:
                    continue

                # 检查是否有 IPv4 地址（非回环）
                has_ipv4 = False
                for addr in addrs:
                    if addr.family == socket.AF_INET:
                        if addr.address != '127.0.0.1':
                            has_ipv4 = True
                            break

                if not has_ipv4:
                    continue

                # 获取 MAC 地址
                for addr in addrs:
                    # AF_LINK (macOS) 或 AF_PACKET (Linux) 或 -1 (Windows psutil 特殊值)
                    if hasattr(socket, 'AF_LINK') and addr.family == socket.AF_LINK:
                        return addr.address.upper()
                    elif hasattr(socket, 'AF_PACKET') and addr.family == socket.AF_PACKET:
                        return addr.address.upper()
                    elif addr.family == -1:  # Windows psutil MAC 地址
                        return addr.address.upper()

            # 如果没有找到合适的，尝试获取第一个有 MAC 地址的网卡
            for interface, addrs in net_if_addrs.items():
                for addr in addrs:
                    if addr.family == -1 or \
                       (hasattr(socket, 'AF_LINK') and addr.family == socket.AF_LINK) or \
                       (hasattr(socket, 'AF_PACKET') and addr.family == socket.AF_PACKET):
                        mac = addr.address.upper()
                        # 排除空 MAC 或全 0 MAC
                        if mac and mac != '00:00:00:00:00:00':
                            return mac

        except Exception as e:
            logger.warning(f"Failed to get MAC address: {e}")

        return ""

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
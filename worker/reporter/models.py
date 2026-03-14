"""
平台上报数据模型。
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import List, Dict, Any, Optional, Union


@dataclass
class AndroidDeviceInfo:
    """Android 设备信息。"""

    udid: str
    model: str
    brand: str
    os_version: str
    resolution: str
    status: str

    def to_dict(self) -> Dict:
        return {
            "platform": "android",
            **asdict(self)
        }


@dataclass
class iOSDeviceInfo:
    """iOS 设备信息。"""

    udid: str
    name: str
    model: str
    os_version: str
    resolution: str
    status: str

    def to_dict(self) -> Dict:
        return {
            "platform": "ios",
            **asdict(self)
        }


@dataclass
class DesktopInfo:
    """桌面平台信息。"""

    platform: str  # windows / macos
    resolution: str
    scale: float

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class WorkerCapabilities:
    """Worker 能力描述。"""

    has_ocr: bool = True
    browsers: List[str] = field(default_factory=lambda: ["chromium"])
    max_sessions: int = 5
    image_matching: bool = True

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class WorkerReport:
    """
    Worker 上报数据模型。

    用于向配置平台上报 Worker 的完整信息。
    """

    worker_id: str
    hostname: str
    ip_addresses: List[str]
    os_type: str  # windows / macos
    os_version: str
    supported_platforms: List[str]
    status: str  # online / busy / offline
    port: int
    devices: List[Union[AndroidDeviceInfo, iOSDeviceInfo, DesktopInfo]]
    capabilities: WorkerCapabilities
    reported_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict:
        """转换为字典格式。"""
        return {
            "worker_id": self.worker_id,
            "hostname": self.hostname,
            "ip_addresses": self.ip_addresses,
            "os_type": self.os_type,
            "os_version": self.os_version,
            "supported_platforms": self.supported_platforms,
            "status": self.status,
            "port": self.port,
            "devices": [d.to_dict() for d in self.devices],
            "capabilities": self.capabilities.to_dict(),
            "reported_at": self.reported_at.isoformat(),
        }


@dataclass
class DeviceChangeEvent:
    """设备变化事件。"""

    event_type: str  # add / remove
    platform: str  # android / ios
    device: Union[AndroidDeviceInfo, iOSDeviceInfo]
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict:
        return {
            "event_type": self.event_type,
            "platform": self.platform,
            "device": self.device.to_dict(),
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class HeartbeatReport:
    """心跳上报数据。"""

    worker_id: str
    status: str
    active_sessions: int
    devices_count: int
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict:
        return {
            "worker_id": self.worker_id,
            "status": self.status,
            "active_sessions": self.active_sessions,
            "devices_count": self.devices_count,
            "timestamp": self.timestamp.isoformat(),
        }
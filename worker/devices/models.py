"""设备状态模型。"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class DeviceRecord:
    """本机设备事实记录，不包含任务 busy 状态。"""

    device_id: str
    platform: str
    physical_id: str = ""
    name: str = ""
    model: str = ""
    os_version: str = ""
    connection_status: str = "connected"
    service_status: str = "unknown"
    health_status: str = "healthy"
    capabilities: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    last_seen_at: datetime = field(default_factory=datetime.now)
    revision: int = 0

    def to_dict(self) -> dict[str, Any]:
        """转换为兼容设备 API 的字典。"""
        return {
            "udid": self.device_id,
            "device_id": self.device_id,
            "platform": self.platform,
            "physical_id": self.physical_id or self.device_id,
            "name": self.name,
            "model": self.model,
            "sys_version": self.os_version,
            "os_version": self.os_version,
            "connection_status": self.connection_status,
            "service_status": self.service_status,
            "health_status": self.health_status,
            "capabilities": list(self.capabilities),
            "last_seen_at": self.last_seen_at.isoformat(),
            "revision": self.revision,
            **self.metadata,
        }

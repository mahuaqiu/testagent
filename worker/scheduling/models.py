"""资源调度模型。"""

from dataclasses import dataclass
from datetime import datetime


def resource_key(platform: str, device_id: str | None = None) -> str:
    """生成全局唯一的本机资源键。"""
    if device_id:
        return f"device:{platform}:{device_id}"
    return f"platform:{platform}"


@dataclass(frozen=True)
class ResourceLease:
    """一次资源占用租约。"""

    resource_key: str
    task_id: str
    acquired_at: datetime

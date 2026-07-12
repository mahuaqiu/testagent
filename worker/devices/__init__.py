"""Worker 设备事实状态。"""

from worker.devices.models import DeviceRecord
from worker.devices.registry import DeviceRegistry

__all__ = ["DeviceRecord", "DeviceRegistry"]

"""Worker 本机资源调度器。"""

from __future__ import annotations

import threading
from datetime import datetime

from worker.scheduling.models import ResourceLease, resource_key


class ResourceScheduler:
    """以单一事实源管理平台和设备资源占用。"""

    def __init__(self):
        self._lock = threading.RLock()
        self._leases: dict[str, ResourceLease] = {}

    def try_acquire(self, platform: str, device_id: str | None, task_id: str) -> ResourceLease | None:
        """原子申请资源，忙碌时返回 None。"""
        return self.try_acquire_key(resource_key(platform, device_id), task_id)

    def try_acquire_key(self, key: str, task_id: str) -> ResourceLease | None:
        """按完整资源键原子申请资源，供独立资源域使用。"""
        with self._lock:
            if key in self._leases:
                return None
            lease = ResourceLease(key, task_id, datetime.now())
            self._leases[key] = lease
            return lease

    def release_lease(self, lease: ResourceLease, reason: str = "completed") -> bool:
        """释放指定租约，防止旧任务误释放新任务租约。"""
        with self._lock:
            current = self._leases.get(lease.resource_key)
            if current != lease:
                return False
            del self._leases[lease.resource_key]
            return True

    def get_busy_task_id(self, platform: str, device_id: str | None = None) -> str | None:
        """获取资源当前占用任务。"""
        return self.get_busy_task_id_by_key(resource_key(platform, device_id))

    def get_busy_task_id_by_key(self, key: str) -> str | None:
        """按完整资源键获取当前占用任务。"""
        with self._lock:
            lease = self._leases.get(key)
            return lease.task_id if lease else None

    def is_busy(self, platform: str, device_id: str | None = None) -> bool:
        """检查资源是否忙碌。"""
        return self.get_busy_task_id(platform, device_id) is not None

    def active_leases(self) -> list[ResourceLease]:
        """返回活动租约快照。"""
        with self._lock:
            return list(self._leases.values())

    def active_count(self) -> int:
        """返回活动任务数。"""
        with self._lock:
            return len(self._leases)

    # 兼容旧 PlatformManager/Worker 调用，新的任务服务使用 try_acquire/release_lease。
    def acquire(
        self,
        platform: str,
        device_id: str | None = None,
        blocking: bool = True,
        timeout: float = -1,
    ) -> bool:
        """兼容旧接口的无主租约申请。"""
        del blocking, timeout
        return self.try_acquire(platform, device_id, f"legacy:{threading.get_ident()}") is not None

    def release(self, platform: str, device_id: str | None = None) -> None:
        """兼容旧接口，释放当前资源。"""
        key = resource_key(platform, device_id)
        with self._lock:
            self._leases.pop(key, None)

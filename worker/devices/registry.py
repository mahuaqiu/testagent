"""设备事实注册表。"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from worker.devices.models import DeviceRecord


class DeviceRegistry:
    """合并 Discoverer 和 DeviceMonitor 的设备快照。"""

    def __init__(self):
        self._lock = threading.RLock()
        self._devices: dict[tuple[str, str], DeviceRecord] = {}

    def upsert(self, device: DeviceRecord, observed_at: datetime | None = None) -> DeviceRecord:
        """写入设备，按观测时间拒绝过期更新。"""
        observed = observed_at or datetime.now()
        key = (device.platform, device.device_id)
        with self._lock:
            current = self._devices.get(key)
            if current and observed < current.last_seen_at:
                return current
            device.last_seen_at = observed
            device.revision = (current.revision + 1) if current else max(device.revision, 1)
            self._devices[key] = device
            return device

    def update_status(
        self,
        platform: str,
        device_id: str,
        *,
        connection_status: str | None = None,
        service_status: str | None = None,
        health_status: str | None = None,
        observed_at: datetime | None = None,
    ) -> DeviceRecord | None:
        """更新设备动态状态。"""
        key = (platform, device_id)
        with self._lock:
            current = self._devices.get(key)
            if current is None:
                return None
            observed = observed_at or datetime.now()
            if observed < current.last_seen_at:
                return current
            if connection_status is not None:
                current.connection_status = connection_status
            if service_status is not None:
                current.service_status = service_status
            if health_status is not None:
                current.health_status = health_status
            current.last_seen_at = observed
            current.revision += 1
            return current

    def list(self, platform: str | None = None) -> list[DeviceRecord]:
        """返回不可变语义的设备快照。"""
        with self._lock:
            values = self._devices.values()
            if platform:
                values = (device for device in values if device.platform == platform)
            return [self._copy(device) for device in values]

    def replace_platform(self, platform: str, devices: Iterable[DeviceRecord]) -> None:
        """以一次发现结果替换指定平台设备，同时保留状态字段。"""
        incoming = {device.device_id: device for device in devices}
        with self._lock:
            existing = {key[1]: value for key, value in self._devices.items() if key[0] == platform}
            for device_id, device in incoming.items():
                old = existing.get(device_id)
                if old:
                    if device.service_status in ("", "unknown"):
                        device.service_status = old.service_status
                    if device.health_status in ("", "unknown"):
                        device.health_status = old.health_status
                self.upsert(device)
            for device_id, old in existing.items():
                if device_id not in incoming:
                    old.connection_status = "disconnected"
                    old.health_status = "unhealthy"
                    old.revision += 1

    @staticmethod
    def merge_monitor_snapshot(
        platform: str,
        discovered: Iterable[DeviceRecord],
        online_items: Iterable[dict[str, Any]],
        faulty_items: Iterable[dict[str, Any]],
    ) -> list[DeviceRecord]:
        """合并设备发现器和监控器快照，保留物理连接与服务健康的区别。"""
        by_id = {record.device_id: record for record in discovered}
        device_items = [(item, False) for item in online_items]
        device_items.extend((item, True) for item in faulty_items)

        for item, is_faulty in device_items:
            device_id = item.get("udid") or item.get("device_id")
            if not device_id:
                continue
            current = by_id.get(device_id)
            raw_connection_status = str(item.get("connection_status", "")).lower()
            if current is None:
                current = DeviceRecord(
                    device_id=device_id,
                    platform=platform,
                    connection_status=(
                        "disconnected"
                        if raw_connection_status in ("disconnected", "offline")
                        else "connected"
                    ),
                    service_status="unavailable" if is_faulty else "ready",
                    health_status="unhealthy" if is_faulty else "healthy",
                )
                by_id[device_id] = current

            current.name = item.get("name") or current.name
            current.model = item.get("model") or current.model
            current.os_version = (
                item.get("os_version") or item.get("sys_version") or current.os_version
            )
            if raw_connection_status:
                current.connection_status = (
                    "disconnected"
                    if raw_connection_status in ("disconnected", "offline")
                    else "connected"
                )
            else:
                # 出现在本轮列表中的设备已被发现；物理离线项会带明确状态。
                current.connection_status = "connected"
            current.service_status = item.get(
                "service_status",
                "unavailable" if is_faulty else "ready",
            )
            current.health_status = item.get(
                "health_status",
                "unhealthy" if is_faulty else "healthy",
            )
            current.capabilities = list(item.get("capabilities", current.capabilities))
            current.metadata.update(item)
            current.metadata["connection_status"] = current.connection_status
            current.metadata["service_status"] = current.service_status
            current.metadata["health_status"] = current.health_status

        return list(by_id.values())

    def get(self, platform: str, device_id: str) -> DeviceRecord | None:
        """获取单个设备快照。"""
        with self._lock:
            device = self._devices.get((platform, device_id))
            return self._copy(device) if device else None

    def grouped(self) -> dict[str, list[dict[str, Any]]]:
        """按平台返回兼容 Worker API 的设备字典。"""
        result: dict[str, list[dict[str, Any]]] = {}
        for device in self.list():
            result.setdefault(device.platform, []).append(device.to_dict())
        return result

    @staticmethod
    def _copy(device: DeviceRecord) -> DeviceRecord:
        return DeviceRecord(
            device_id=device.device_id,
            platform=device.platform,
            physical_id=device.physical_id,
            name=device.name,
            model=device.model,
            os_version=device.os_version,
            connection_status=device.connection_status,
            service_status=device.service_status,
            health_status=device.health_status,
            capabilities=list(device.capabilities),
            metadata=dict(device.metadata),
            last_seen_at=device.last_seen_at,
            revision=device.revision,
        )

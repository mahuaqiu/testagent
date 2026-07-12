"""设备注册表测试。"""

from datetime import datetime, timedelta

from worker.devices.models import DeviceRecord
from worker.devices.registry import DeviceRegistry


def test_registry_separates_platforms_and_returns_copies():
    registry = DeviceRegistry()
    registry.upsert(DeviceRecord(device_id="same", platform="harmony_mobile"))
    registry.upsert(DeviceRecord(device_id="same", platform="harmony_pc"))
    assert len(registry.list()) == 2
    snapshot = registry.get("harmony_mobile", "same")
    assert snapshot is not None
    snapshot.name = "changed outside registry"
    assert registry.get("harmony_mobile", "same").name == ""


def test_registry_ignores_stale_status_update():
    registry = DeviceRegistry()
    current = datetime.now()
    registry.upsert(
        DeviceRecord(device_id="d1", platform="android", last_seen_at=current),
        observed_at=current,
    )
    registry.update_status(
        "android",
        "d1",
        service_status="ready",
        observed_at=current + timedelta(seconds=1),
    )
    registry.update_status(
        "android",
        "d1",
        service_status="faulty",
        observed_at=current - timedelta(seconds=1),
    )
    assert registry.get("android", "d1").service_status == "ready"

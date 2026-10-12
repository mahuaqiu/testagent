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


def test_monitor_snapshot_keeps_faulty_but_connected_devices_visible():
    records = DeviceRegistry.merge_monitor_snapshot(
        "harmony_pc",
        [],
        [],
        [
            {"udid": "pc-1", "device_category": "pc"},
            {"udid": "pc-2", "device_category": "pc"},
        ],
    )

    assert {device.device_id for device in records} == {"pc-1", "pc-2"}
    assert all(device.connection_status == "connected" for device in records)
    assert all(device.service_status == "unavailable" for device in records)


def test_monitor_snapshot_preserves_explicit_disconnection():
    records = DeviceRegistry.merge_monitor_snapshot(
        "harmony_pc",
        [],
        [],
        [{"udid": "pc-offline", "connection_status": "disconnected"}],
    )

    assert records[0].connection_status == "disconnected"


def test_online_snapshot_recovers_service_and_health_status():
    previous = DeviceRecord(
        device_id="pc-1",
        platform="harmony_pc",
        service_status="unavailable",
        health_status="unhealthy",
    )

    records = DeviceRegistry.merge_monitor_snapshot(
        "harmony_pc",
        [previous],
        [{"udid": "pc-1"}],
        [],
    )

    assert records[0].connection_status == "connected"
    assert records[0].service_status == "ready"
    assert records[0].health_status == "healthy"


def test_monitor_snapshot_keeps_multiple_devices_for_other_platforms():
    android = DeviceRegistry.merge_monitor_snapshot(
        "android", [], [{"udid": "android-1"}, {"udid": "android-2"}], []
    )
    ios = DeviceRegistry.merge_monitor_snapshot(
        "ios", [], [{"udid": "ios-1"}, {"udid": "ios-2"}], []
    )

    assert {device.device_id for device in android} == {"android-1", "android-2"}
    assert {device.device_id for device in ios} == {"ios-1", "ios-2"}
    assert all(device.service_status == "ready" for device in android + ios)

"""设备监控首次发现顺序测试。"""

import threading

from worker.config import WorkerConfig
from worker.device_monitor import DeviceMonitor


def test_start_publishes_devices_before_service_maintenance():
    monitor = DeviceMonitor(
        WorkerConfig(id="test-worker", discover_harmony_pc_devices=True)
    )
    discovery_published = threading.Event()
    maintenance_started = threading.Event()
    release_maintenance = threading.Event()
    snapshots = []

    def discover_devices():
        monitor._faulty_harmony_pc_devices.extend(
            [{"udid": "pc-1"}, {"udid": "pc-2"}]
        )

    def maintain_services():
        maintenance_started.set()
        release_maintenance.wait(timeout=2)

    def publish_discovery(snapshot):
        snapshots.append(snapshot)
        discovery_published.set()

    monitor._detect_physical_devices = discover_devices
    monitor._maintain_services = maintain_services
    monitor.on_device_discovery = publish_discovery

    monitor.start()
    try:
        assert discovery_published.is_set()
        assert maintenance_started.wait(timeout=1)
        assert {item["udid"] for item in snapshots[0]["faulty_harmony_pc"]} == {
            "pc-1",
            "pc-2",
        }
    finally:
        release_maintenance.set()
        monitor.stop()

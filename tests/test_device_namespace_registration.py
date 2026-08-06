"""设备级 namespace 注册测试。"""

from types import SimpleNamespace

from worker.config import WorkerConfig
from worker.worker import Worker


def test_namespace_overrides_use_device_then_platform_then_default() -> None:
    """具体设备覆盖应优先于平台覆盖与默认值。"""
    config = WorkerConfig(
        namespace="meeting_public",
        namespace_overrides={
            "android": "meeting_app",
            "android/device-001": "meeting_gamma",
        },
    )

    assert config.get_namespace("windows") == "meeting_public"
    assert config.get_namespace("android", "device-002") == "meeting_app"
    assert config.get_namespace("android", "device-001") == "meeting_gamma"


def test_registration_payloads_are_split_by_namespace() -> None:
    """不同归属设备必须拆成独立平台注册请求。"""
    worker = Worker.__new__(Worker)
    worker.config = WorkerConfig(
        namespace="meeting_public",
        namespace_overrides={
            "android": "meeting_app",
            "harmony_pc": "meeting_av",
            "android/device-001": "meeting_gamma",
        },
    )
    worker.host_info = SimpleNamespace(os_type="windows")

    payloads = worker._build_registration_payloads(
        {
            "android": ["device-001", "device-002"],
            "ios": [],
            "harmony_mobile": [],
            "harmony_pc": ["pc-001"],
        }
    )

    assert payloads == {
        "meeting_public": {"windows": [], "web": []},
        "meeting_gamma": {"android": ["device-001"]},
        "meeting_app": {"android": ["device-002"]},
        "meeting_av": {"harmony_pc": ["pc-001"]},
    }

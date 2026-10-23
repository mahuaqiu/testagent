"""远程配置下发的本机字段保护契约测试。"""

from __future__ import annotations

from worker.config import merge_config_with_local_protection


def test_push_preserves_local_ip_port_and_discovery_switches(tmp_path) -> None:
    """平台下发配置不得覆盖本机 IP、端口和设备发现开关。"""
    existing = tmp_path / "worker.yaml"
    existing.write_text(
        """worker:
  ip: 192.168.1.20
  port: 9090
  namespace: meeting_public
  discover_android_devices: true
  discover_ios_devices: false
  discover_harmony_mobile_devices: true
  discover_harmony_pc_devices: false
external_services:
  platform_api: http://10.0.0.1:8000
""",
        encoding="utf-8",
    )

    pushed = """worker:
  ip: 10.9.9.9
  port: 8080
  namespace: meeting_av
  discover_android_devices: false
  discover_ios_devices: true
  discover_harmony_mobile_devices: false
  discover_harmony_pc_devices: true
external_services:
  platform_api: http://10.0.0.2:8000
"""
    merged = merge_config_with_local_protection(
        pushed, existing_config_path=str(existing)
    )

    worker = merged["worker"]
    assert worker["ip"] == "192.168.1.20"
    assert worker["port"] == 9090
    assert worker["namespace"] == "meeting_av"  # 非本机项随平台下发更新
    assert worker["discover_android_devices"] is True
    assert worker["discover_ios_devices"] is False
    assert worker["discover_harmony_mobile_devices"] is True
    assert worker["discover_harmony_pc_devices"] is False
    assert merged["external_services"]["platform_api"] == "http://10.0.0.2:8000"


def test_push_preserves_local_port_when_existing_missing(tmp_path) -> None:
    """本机配置缺少保护字段时不写入占位值，直接采用平台下发值。"""
    existing = tmp_path / "worker.yaml"
    existing.write_text(
        """worker:
  namespace: meeting_public
""",
        encoding="utf-8",
    )

    merged = merge_config_with_local_protection(
        """worker:
  ip: 10.9.9.9
  port: 8080
  namespace: meeting_av
""",
        existing_config_path=str(existing),
    )

    assert merged["worker"]["port"] == 8080
    assert merged["worker"]["ip"] == "10.9.9.9"

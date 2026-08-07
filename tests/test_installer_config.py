"""安装器配置写入契约测试。"""

from pathlib import Path

from worker.config import WorkerConfig


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INSTALLER_SCRIPT = PROJECT_ROOT / "installer" / "installer.nsi"


def test_worker_config_reads_utf16_config_written_by_legacy_installer() -> None:
    """旧安装包生成的 UTF-16 配置仍应保留安装时填写的参数。"""
    config_path = PROJECT_ROOT / "test_output" / "installer_utf16_worker.yaml"
    config_path.parent.mkdir(exist_ok=True)
    try:
        config_path.write_text(
            """worker:
  ip: 192.168.1.20
  port: 9090
  namespace: meeting_private
  discover_android_devices: true
  discover_ios_devices: false
  discover_harmony_mobile_devices: true
  discover_harmony_pc_devices: false
external_services:
  platform_api: http://192.168.1.100:8000
  ocr_service: http://192.168.1.100:9021
""",
            encoding="utf-16",
        )

        config = WorkerConfig.from_yaml(str(config_path))

        assert config.ip == "192.168.1.20"
        assert config.port == 9090
        assert config.namespace == "meeting_private"
        assert config.platform_api == "http://192.168.1.100:8000"
        assert config.ocr_service == "http://192.168.1.100:9021"
        assert config.discover_android_devices is True
        assert config.discover_ios_devices is False
        assert config.discover_harmony_mobile_devices is True
        assert config.discover_harmony_pc_devices is False
    finally:
        config_path.unlink(missing_ok=True)


def test_installer_persists_every_config_page_field() -> None:
    """安装脚本应覆盖安装页上的全部可配置字段并使用可读编码写回。"""
    assert INSTALLER_SCRIPT.read_bytes().startswith(b"\xef\xbb\xbf")
    script = INSTALLER_SCRIPT.read_text(encoding="utf-8")

    required_replacements = (
        "ip:",
        "port:",
        "namespace:",
        "platform_api:",
        "ocr_service:",
        "discover_android_devices:",
        "discover_ios_devices:",
        "discover_harmony_mobile_devices:",
        "discover_harmony_pc_devices:",
    )
    for field in required_replacements:
        assert field in script

    assert "Set-Content '$9' -Encoding UTF8" in script

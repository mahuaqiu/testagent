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


def test_uninstaller_only_calls_uninstaller_functions() -> None:
    """卸载段调用的自定义函数必须使用 NSIS 要求的 un. 前缀。"""
    script = INSTALLER_SCRIPT.read_text(encoding="utf-8")
    uninstall_section = script.split("Section Uninstall", maxsplit=1)[1].split(
        "SectionEnd", maxsplit=1
    )[0]

    assert "Call un.KillDeviceServiceProcesses" in uninstall_section
    assert "Call un.KillOwnedHdcProcesses" in uninstall_section
    assert "Function un.KillDeviceServiceProcesses" in script
    assert "Function un.KillOwnedHdcProcesses" in script
    assert "IfFileExists \"$2\" 0 un_done_owned_hdc" in script


def test_installer_escapes_powershell_regex_line_anchors() -> None:
    """NSIS 字符串中 PowerShell 正则的行尾锚点必须转义为字面量美元符。"""
    script = INSTALLER_SCRIPT.read_text(encoding="utf-8")

    assert "-replace '(?m)^\\s*ip:.*$$'" in script
    assert "-replace '(?m)^\\s*discover_harmony_pc_devices:.*$$'" in script


def test_oninit_keeps_defaults_when_command_line_option_missing() -> None:
    """未传命令行参数时（GetOptions 返回空串），.onInit 必须保留默认值。

    StrCmp str1 str2 jump_if_equal jump_if_not_equal：
    $1 为空即命令行未传该参数（相等分支），此时必须跳过 StrCpy 以保留默认值；
    $1 非空即命令行传了该参数（不相等分支），此时才应执行 StrCpy 写入命令行值。
    正确写法是 "StrCmp $1 \"\" +2 0"。写反成 "0 +2" 会导致未传参时用空串
    覆盖 Port/Namespace/PlatformApi/OcrService 的默认值。
    """
    script = INSTALLER_SCRIPT.read_text(encoding="utf-8")
    oninit_body = script.split("Function .onInit", maxsplit=1)[1].split(
        "FunctionEnd", maxsplit=1
    )[0]

    assert oninit_body.count('StrCmp $1 "" +2 0') == 5
    assert 'StrCmp $1 "" 0 +2' not in oninit_body


def test_installer_has_autostart_variable_and_registry_logic() -> None:
    """安装脚本应包含开机自启变量、注册表写入（全新安装）、卸载删除逻辑。"""
    script = INSTALLER_SCRIPT.read_text(encoding="utf-8")

    # 变量声明
    assert "Var AutoStart" in script

    # .onInit 默认勾上
    oninit_body = script.split("Function .onInit", maxsplit=1)[1].split(
        "FunctionEnd", maxsplit=1
    )[0]
    assert "StrCpy $AutoStart ${BST_CHECKED}" in oninit_body

    # 安装段：全新安装写注册表，升级跳过
    main_section = script.split('Section "MainSection" SEC01', maxsplit=1)[1].split(
        "SectionEnd", maxsplit=1
    )[0]
    assert "skip_autostart" in main_section
    assert (
        'WriteRegStr HKLM "Software\\Microsoft\\Windows\\CurrentVersion\\Run" "test-worker"'
        in main_section
    )
    assert 'StrCmp $IsUpgrade "1" skip_autostart' in main_section

    # 卸载段：删注册表
    uninstall_section = script.split("Section Uninstall", maxsplit=1)[1].split(
        "SectionEnd", maxsplit=1
    )[0]
    assert (
        'DeleteRegValue HKLM "Software\\Microsoft\\Windows\\CurrentVersion\\Run" "test-worker"'
        in uninstall_section
    )


def test_installer_has_options_page() -> None:
    """安装脚本应新增选项页，承载设备发现与开机自启。"""
    script = INSTALLER_SCRIPT.read_text(encoding="utf-8")

    # 页面顺序：配置页后紧跟选项页
    assert "Page custom OptionsPageCreate OptionsPageLeave" in script
    # 选项页创建与离开函数
    assert "Function OptionsPageCreate" in script
    assert "Function OptionsPageLeave" in script
    # 选项页升级跳过
    options_create = script.split("Function OptionsPageCreate", maxsplit=1)[1].split(
        "FunctionEnd", maxsplit=1
    )[0]
    assert 'StrCmp $IsUpgrade "1" skip_options' in options_create
    # 选项页含开机自启 checkbox
    assert '"开机自动启动"' in options_create


def test_installer_config_page_no_longer_has_device_discovery() -> None:
    """配置页应移除设备发现控件（迁至选项页）。"""
    script = INSTALLER_SCRIPT.read_text(encoding="utf-8")
    config_create = script.split("Function ConfigPageCreate", maxsplit=1)[1].split(
        "FunctionEnd", maxsplit=1
    )[0]
    # 配置页不再创建设备发现 checkbox
    assert "Device Discovery:" not in config_create

"""鸿蒙 HDC 和平台管理器的单元测试。"""

from types import SimpleNamespace

import pytest

from worker.discovery.harmony import HarmonyDeviceInfo
from worker.platforms import harmony_hdc
from worker.platforms.harmony import HarmonyPlatformManager
from worker.platforms.harmony_hdc import (
    CommandResult,
    HdcCommandError,
    HarmonyHdcWrapper,
    classify_harmony_device,
)
from worker.platforms.harmony_keycodes import HARMONY_KEY_MAP
from worker.config import PlatformConfig, WorkerConfig
from worker.task import Action, ActionStatus, Task, TaskStatus
from worker.scheduling.scheduler import ResourceScheduler
from worker.worker import Worker
from worker.actions.unlock import UnlockScreenAction


def test_parse_target_lines_keeps_connection_metadata() -> None:
    output = """
[Empty]
Serial Type Status
mobile-001 USB Ready hdc
pc-001 TCP Ready hdc
offline-001 USB Offline hdc
unauthorized-001 USB Unauthorized hdc
"""

    targets = harmony_hdc.parse_target_lines(output)

    assert [(target.udid, target.connection_type, target.status) for target in targets] == [
        ("mobile-001", "USB", "Ready"),
        ("pc-001", "TCP", "Ready"),
        ("offline-001", "USB", "Offline"),
        ("unauthorized-001", "USB", "Unauthorized"),
    ]


def test_list_target_info_filters_non_ready_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(harmony_hdc, "_find_hdc_path", lambda _: "hdc.exe")
    monkeypatch.setattr(
        harmony_hdc,
        "_execute_hdc_command",
        lambda *args, **kwargs: CommandResult(
            "ready-001 USB Ready\noffline-001 USB Offline\n", "", 0
        ),
    )

    assert [target.udid for target in harmony_hdc.list_target_info("configured-hdc.exe")] == [
        "ready-001"
    ]


def test_list_target_info_rejects_error_text_even_when_exit_code_is_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(harmony_hdc, "_find_hdc_path", lambda _: "hdc.exe")
    monkeypatch.setattr(
        harmony_hdc,
        "_execute_hdc_command",
        lambda *args, **kwargs: CommandResult("error: service unavailable", "", 0),
    )

    with pytest.raises(HdcCommandError):
        harmony_hdc.list_target_info("configured-hdc.exe")


def test_find_hdc_path_accepts_command_line_tools_directory(tmp_path) -> None:
    hdc_path = tmp_path / "sdk" / "default" / "openharmony" / "toolchains" / "hdc.exe"
    hdc_path.parent.mkdir(parents=True)
    hdc_path.write_bytes(b"hdc")

    assert harmony_hdc._find_hdc_path(str(tmp_path)) == str(hdc_path)


def test_execute_hdc_command_retries_transient_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeProcess:
        def __init__(self, result: CommandResult):
            self.result = result
            self.returncode = result.exit_code

        def communicate(self, timeout=None):
            return self.result.output.encode(), self.result.error.encode()

        def kill(self):
            pass

    results = iter(
        [
            CommandResult("", "service unavailable", 1),
            CommandResult("ok", "", 0),
        ]
    )
    monkeypatch.setattr(harmony_hdc, "popen_cmd", lambda *args, **kwargs: FakeProcess(next(results)))
    monkeypatch.setattr(harmony_hdc.time, "sleep", lambda _: None)

    result = harmony_hdc._execute_hdc_command("hdc.exe", ["list"], retries=1)

    assert result == CommandResult("ok", "", 0)


def test_input_text_escapes_single_quotes(monkeypatch: pytest.MonkeyPatch) -> None:
    wrapper = HarmonyHdcWrapper.__new__(HarmonyHdcWrapper)
    commands: list[str] = []
    monkeypatch.setattr(
        wrapper,
        "shell",
        lambda command: commands.append(command) or CommandResult("", "", 0),
    )

    assert wrapper.input_text_at(10, 20, "a'b") is True
    assert commands == ["uitest uiInput inputText 10 20 'a'\"'\"'b'"]


@pytest.mark.parametrize("text", ["中文 空格", 'a"b\\c', "line1\nline2", "$HOME; echo x"])
def test_input_text_quotes_special_characters(
    monkeypatch: pytest.MonkeyPatch, text: str
) -> None:
    wrapper = HarmonyHdcWrapper.__new__(HarmonyHdcWrapper)
    commands: list[str] = []
    monkeypatch.setattr(
        wrapper,
        "shell",
        lambda command: commands.append(command) or CommandResult("", "", 0),
    )

    assert wrapper.input_text_at(12, 34, text)
    assert commands == [
        f"uitest uiInput inputText 12 34 {harmony_hdc._quote_remote_shell_argument(text)}"
    ]


def test_harmony_keycodes_have_single_correct_direction_mapping() -> None:
    assert HarmonyHdcWrapper.KEY_MAP is HARMONY_KEY_MAP
    assert HarmonyPlatformManager.KEY_MAP is HARMONY_KEY_MAP
    assert [HARMONY_KEY_MAP[key] for key in ("DPAD_UP", "DPAD_DOWN", "DPAD_LEFT", "DPAD_RIGHT", "DPAD_CENTER")] == [2012, 2013, 2014, 2015, 2016]


def test_harmony_device_classification_uses_exact_property_values() -> None:
    assert classify_harmony_device({"const.product.type": "tablet"}) == "mobile"
    assert classify_harmony_device({"const.product.device_type": "desktop"}) == "pc"
    assert classify_harmony_device({"const.product.name": "my-pc-phone-shell"}) == "unknown"
    assert classify_harmony_device({"const.product.type": "smartphone-pro"}) == "unknown"


def test_harmony_input_action_uses_located_coordinates() -> None:
    manager = HarmonyPlatformManager(PlatformConfig(), device_type="harmony_mobile")
    calls: list[tuple[int, int, str]] = []
    client = SimpleNamespace(
        input_text_at=lambda x, y, text: calls.append((x, y, text)) or True
    )

    manager.input_text_at(23, 45, "中文 input", context=client)

    assert calls == [(23, 45, "中文 input")]


def test_harmony_device_info_uses_public_platform_type() -> None:
    mobile = HarmonyDeviceInfo("m", "", "", "", "", (0, 0), "online", "mobile")
    pc = HarmonyDeviceInfo("p", "", "", "", "", (0, 0), "online", "pc")
    unknown = HarmonyDeviceInfo("u", "", "", "", "", (0, 0), "online")

    assert mobile.to_dict()["platform"] == "harmony_mobile"
    assert pc.to_dict()["platform"] == "harmony_pc"
    assert unknown.to_dict()["platform"] == "unknown"


def test_harmony_action_whitelists_match_device_shapes() -> None:
    mobile = HarmonyPlatformManager(PlatformConfig(), device_type="harmony_mobile")
    pc = HarmonyPlatformManager(PlatformConfig(), device_type="harmony_pc")

    assert mobile.is_action_supported("unlock_screen")
    assert not pc.is_action_supported("unlock_screen")
    assert pc.is_action_supported("double_click")
    assert not pc.is_action_supported("right_click")
    assert not pc.is_action_supported("start_recording")
    assert not pc.is_action_supported("cmd_exec")


def test_harmony_click_turns_false_hdc_result_into_error() -> None:
    manager = HarmonyPlatformManager(PlatformConfig(), device_type="harmony_pc")
    client = SimpleNamespace(tap=lambda x, y: False)

    with pytest.raises(harmony_hdc.HarmonyError):
        manager.click(1, 2, context=client)


def test_harmony_screenshot_rejects_empty_file(tmp_path) -> None:
    manager = HarmonyPlatformManager(PlatformConfig(), device_type="harmony_mobile")

    def screenshot(path: str, method: str) -> bool:
        open(path, "wb").close()
        return True

    client = SimpleNamespace(screenshot=screenshot)

    with pytest.raises(harmony_hdc.HarmonyError, match="截图为空"):
        manager.get_screenshot(client)


def test_harmony_screenshot_rejects_invalid_image(tmp_path) -> None:
    manager = HarmonyPlatformManager(PlatformConfig(), device_type="harmony_mobile")

    def screenshot(path: str, method: str) -> bool:
        with open(path, "wb") as file:
            file.write(b"not-an-image")
        return True

    client = SimpleNamespace(screenshot=screenshot)

    with pytest.raises(harmony_hdc.HarmonyError, match="截图格式无效"):
        manager.get_screenshot(client)


@pytest.mark.parametrize("platform", ["harmony_mobile", "harmony_pc"])
def test_harmony_task_requires_device_id(platform: str) -> None:
    worker = Worker.__new__(Worker)
    worker.supported_platforms = [platform]
    worker.device_monitor = SimpleNamespace(get_online_devices=lambda _: ["udid-1"])

    manager = HarmonyPlatformManager(PlatformConfig(), device_type=platform)
    task = Task.create(
        platform=platform,
        actions=[{"action_type": "wait", "value": 1}],
        device_id=None,
        generate_id=False,
    )

    result = worker._validate_task(task, manager)

    assert result is not None
    assert result.status == ActionStatus.FAILED
    assert "device_id is required" in result.error


def test_harmony_task_rejects_unknown_device_id() -> None:
    worker = Worker.__new__(Worker)
    worker.supported_platforms = ["harmony_pc"]
    worker.device_monitor = SimpleNamespace(get_online_devices=lambda _: ["udid-1"])
    manager = HarmonyPlatformManager(PlatformConfig(), device_type="harmony_pc")
    task = Task.create(
        platform="harmony_pc",
        actions=[{"action_type": "wait", "value": 1}],
        device_id="missing",
        generate_id=False,
    )

    result = worker._validate_task(task, manager)

    assert result is not None
    assert result.status == ActionStatus.FAILED
    assert result.error == "Device not found: missing"


def test_harmony_scheduler_isolates_platform_and_device() -> None:
    scheduler = ResourceScheduler()

    mobile = scheduler.try_acquire("harmony_mobile", "same-udid", "mobile-task")
    assert mobile is not None
    assert scheduler.is_busy("harmony_mobile", "same-udid")
    assert scheduler.try_acquire("harmony_mobile", "same-udid", "other-task") is None
    pc = scheduler.try_acquire("harmony_pc", "same-udid", "pc-task")
    assert pc is not None

    scheduler.release_lease(mobile)
    scheduler.release_lease(pc)

def test_harmony_unlock_uses_mobile_branch() -> None:
    action = UnlockScreenAction()
    platform = SimpleNamespace(platform="harmony_mobile")
    calls: list[tuple[int, int, int, int]] = []
    client = SimpleNamespace(swipe=lambda x1, y1, x2, y2: calls.append((x1, y1, x2, y2)))

    action._trigger_password_screen(platform, client, "swipe_up")

    assert calls == [(540, 2000, 540, 500)]
    assert action._get_keypad_coords(platform, client)


def test_harmony_device_monitor_preserves_metadata_when_marked_online() -> None:
    from worker.device_monitor import DeviceMonitor
    from worker.config import WorkerConfig

    monitor = DeviceMonitor(WorkerConfig(discover_harmony_devices=True))
    monitor._faulty_harmony_pc_devices.append(
        {
            "udid": "pc-001",
            "device_category": "pc",
            "connection_type": "TCP",
            "capabilities": ["mouse", "keyboard", "screenshot"],
        }
    )

    monitor.mark_device_online("harmony_pc", "pc-001")

    assert monitor._harmony_pc_devices == [
        {
            "udid": "pc-001",
            "device_category": "pc",
            "connection_type": "TCP",
            "capabilities": ["mouse", "keyboard", "screenshot"],
        }
    ]


def test_harmony_device_monitor_refreshes_metadata_and_moves_category() -> None:
    from worker.device_monitor import DeviceMonitor
    from worker.config import WorkerConfig

    monitor = DeviceMonitor(WorkerConfig(discover_harmony_devices=True))
    monitor._harmony_mobile_devices.append({
        "udid": "target-001",
        "device_category": "mobile",
        "connection_type": "USB",
        "capabilities": ["touch"],
    })

    monitor._upsert_harmony_device("harmony_pc", {
        "udid": "target-001",
        "device_category": "pc",
        "connection_type": "TCP",
        "capabilities": ["mouse", "keyboard", "screenshot"],
    })

    assert monitor._harmony_mobile_devices == []
    assert monitor._harmony_pc_devices == [{
        "udid": "target-001",
        "device_category": "pc",
        "connection_type": "TCP",
        "capabilities": ["mouse", "keyboard", "screenshot"],
    }]


def test_harmony_discovery_defaults_are_disabled() -> None:
    config = WorkerConfig()

    assert config.discover_harmony_mobile_devices is False
    assert config.discover_harmony_pc_devices is False


def test_harmony_legacy_discovery_config_enables_both_types(tmp_path) -> None:
    config_path = tmp_path / "worker.yaml"
    config_path.write_text(
        "worker:\n  discover_harmony_devices: true\n",
        encoding="utf-8",
    )

    config = WorkerConfig.from_yaml(str(config_path))

    assert config.discover_harmony_mobile_devices is True
    assert config.discover_harmony_pc_devices is True


def test_harmony_split_discovery_config_takes_priority_over_legacy(tmp_path) -> None:
    config_path = tmp_path / "worker.yaml"
    config_path.write_text(
        "worker:\n"
        "  discover_harmony_devices: true\n"
        "  discover_harmony_mobile_devices: false\n"
        "  discover_harmony_pc_devices: false\n",
        encoding="utf-8",
    )

    config = WorkerConfig.from_yaml(str(config_path))

    assert config.discover_harmony_mobile_devices is False
    assert config.discover_harmony_pc_devices is False


def test_harmony_monitor_keeps_mobile_and_pc_switches_independent() -> None:
    from worker.device_monitor import DeviceMonitor

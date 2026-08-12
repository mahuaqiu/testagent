"""鸿蒙 HDC 和平台管理器的单元测试。"""

import os
from types import SimpleNamespace

import pytest

from worker.discovery.harmony import HarmonyDeviceInfo
from worker.platforms import harmony_hdc
from worker.platforms import harmony_capture
from worker.platforms.harmony_capture import (
    HarmonyCaptureError,
    HarmonyScreenCapture,
    split_jpeg_frames,
)
from worker.platforms.harmony import HarmonyPlatformManager
from worker.platforms.harmony_hdc import (
    CommandResult,
    HdcCommandError,
    HarmonyHdcWrapper,
    classify_harmony_device,
    parse_harmony_display_size,
    parse_harmony_screen_state,
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
3QC0124A10000066\t\tUSB\tConnected\tlocalhost\thdc
"""

    targets = harmony_hdc.parse_target_lines(output)

    assert [(target.udid, target.connection_type, target.status) for target in targets] == [
        ("mobile-001", "USB", "Ready"),
        ("pc-001", "TCP", "Ready"),
        ("offline-001", "USB", "Offline"),
        ("unauthorized-001", "USB", "Unauthorized"),
        ("3QC0124A10000066", "USB", "Connected"),
    ]


def test_hdc_target_treats_connected_as_ready() -> None:
    # 真机（华为 MateBook 2in1）状态列为 Connected，必须视为可用
    assert harmony_hdc.HdcTarget(udid="a", status="Connected").is_ready
    assert harmony_hdc.HdcTarget(udid="a", status="Ready").is_ready
    assert not harmony_hdc.HdcTarget(udid="a", status="Offline").is_ready


def test_list_target_info_filters_non_ready_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(harmony_hdc, "_find_hdc_path", lambda _: "hdc.exe")
    monkeypatch.setattr(
        harmony_hdc,
        "_execute_hdc_command",
        lambda *args, **kwargs: CommandResult(
            "ready-001 USB Ready\n"
            "offline-001 USB Offline\n"
            "connected-001 USB Connected\n"
            "COM1 UART Ready\n",
            "",
            0,
        ),
    )

    assert [target.udid for target in harmony_hdc.list_target_info("configured-hdc.exe")] == [
        "ready-001",
        "connected-001",
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


def _make_command_capture_wrapper(
    monkeypatch: pytest.MonkeyPatch, output: str = "", exit_code: int = 0
) -> tuple[HarmonyHdcWrapper, list[str]]:
    """构造只记录 shell 命令的 wrapper，用于断言 uiInput 命令模板。"""
    wrapper = HarmonyHdcWrapper.__new__(HarmonyHdcWrapper)
    commands: list[str] = []
    monkeypatch.setattr(
        wrapper,
        "shell",
        lambda command, timeout=30: commands.append(command)
        or CommandResult(output, "", exit_code),
    )
    return wrapper, commands


def test_double_tap_uses_native_double_click_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 真机风险预防：两次 tap 模拟的间隔不受控，可能超出系统双击判定窗口，
    # 必须用 uitest 原生 doubleClick（awesome-hdc uiInput 官方命令表）
    wrapper, commands = _make_command_capture_wrapper(monkeypatch)

    assert wrapper.double_tap(100, 200) is True
    assert commands == ["uitest uiInput doubleClick 100 200"]


def test_long_tap_uses_native_long_click_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # uitest uiInput 官方签名是 longClick x y（无时长参数）；
    # 旧实现的 click x y duration 不在官方命令表内，真机可能被当普通点击
    wrapper, commands = _make_command_capture_wrapper(monkeypatch)

    assert wrapper.long_tap(30, 40) is True
    assert wrapper.long_tap(30, 40, duration=2500) is True
    assert commands == [
        "uitest uiInput longClick 30 40",
        "uitest uiInput longClick 30 40",
    ]
    assert wrapper.long_tap(30, 40, duration=0) is False


def test_has_app_rejects_error_output_even_when_exit_code_is_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # bm dump -n 对未安装包仍会输出失败文案且 exit_code 可能为 0，
    # 不能只用“输出非空”判定已安装
    wrapper, _ = _make_command_capture_wrapper(
        monkeypatch, output="error: failed to get information"
    )
    assert wrapper.has_app("com.example.app") is False

    wrapper, _ = _make_command_capture_wrapper(
        monkeypatch, output='{"name": "com.example.app", "versionName": "1.0"}'
    )
    assert wrapper.has_app("com.example.app") is True

    # 输出非空但不含包名（异常回显）同样判未安装
    wrapper, _ = _make_command_capture_wrapper(monkeypatch, output="OK")
    assert wrapper.has_app("com.example.app") is False


def test_shell_passes_bare_command_without_wrapping_quotes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 真机回归：shell() 手工包裹的双引号会透传到设备端，
    # /bin/sh 把整段字符串当单个命令名报 inaccessible or not found，
    # 导致 device_category/display_size 全部失败、设备不入池。
    wrapper = HarmonyHdcWrapper.__new__(HarmonyHdcWrapper)
    executed: list[list[str]] = []
    monkeypatch.setattr(
        wrapper,
        "_execute",
        lambda args, timeout=30: executed.append(args) or CommandResult("ok", "", 0),
    )

    wrapper.shell("param get const.product.devicetype")
    wrapper.shell("hidumper -s 10 -a screen")
    # 旧调用方若自带包裹引号，剥掉后再传递
    wrapper.shell('"param get const.product.model"')

    assert executed == [
        ["shell", "param get const.product.devicetype"],
        ["shell", "hidumper -s 10 -a screen"],
        ["shell", "param get const.product.model"],
    ]


def test_harmony_device_classification_uses_exact_property_values() -> None:
    assert classify_harmony_device({"const.product.type": "tablet"}) == "mobile"
    assert classify_harmony_device({"const.product.device_type": "desktop"}) == "pc"
    assert classify_harmony_device({"const.product.name": "my-pc-phone-shell"}) == "unknown"
    assert classify_harmony_device({"const.product.type": "smartphone-pro"}) == "unknown"
    # 真机（华为 MateBook Pro）只有 const.product.devicetype=2in1
    assert classify_harmony_device({"const.product.devicetype": "2in1"}) == "pc"
    assert classify_harmony_device({"const.product.devicetype": "phone"}) == "mobile"


def test_harmony_device_category_reads_real_pc_property_and_skips_missing_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrapper = HarmonyHdcWrapper.__new__(HarmonyHdcWrapper)
    outputs = {
        "param get const.product.devicetype": "2in1\n",
        "param get const.product.type": "[Fail]Get parameter fail! errNum is:106\n",
        "param get const.product.device_type": "[Fail]Get parameter fail! errNum is:106\n",
        "param get const.product.form": "[Fail]Get parameter fail! errNum is:106\n",
        "param get const.product.family": "[Fail]Get parameter fail! errNum is:106\n",
    }
    monkeypatch.setattr(
        wrapper,
        "shell",
        lambda command: CommandResult(outputs[command], "", 0),
    )

    assert wrapper.device_category() == "pc"


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("width: 3120, height: 2080", (3120, 2080)),
        ("screenWidth=3120\nscreenHeight=2080", (3120, 2080)),
        ("Display 0: 3120x2080", (3120, 2080)),
        ("resolution: 3120 * 2080", (3120, 2080)),
        ("bounds: [0, 0, 3120, 2080]", (3120, 2080)),
        ("no display metrics", (0, 0)),
    ],
)
def test_harmony_display_size_accepts_realistic_hidumper_formats(
    output: str, expected: tuple[int, int]
) -> None:
    assert parse_harmony_display_size(output) == expected


def test_harmony_display_and_state_parse_real_had_w32_output() -> None:
    output = (
        "screen[0]: id=0, powerStatus=POWER_STATUS_ON, "
        "screenType=EXTERNAL_TYPE, render resolution=3120x2080, "
        "physical resolution=3120x2080, isVirtual=false"
    )

    assert parse_harmony_display_size(output) == (3120, 2080)
    assert parse_harmony_screen_state(output) == "AWAKE"
    assert parse_harmony_screen_state("powerStatus=POWER_STATUS_OFF") == "SLEEP"


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
    assert pc.is_action_supported("unlock_screen")
    assert pc.is_action_supported("double_click")
    assert pc.is_action_supported("right_click")
    assert not mobile.is_action_supported("right_click")
    assert not pc.is_action_supported("start_recording")
    assert not pc.is_action_supported("cmd_exec")


def test_harmony_pc_right_click_uses_long_tap() -> None:
    manager = HarmonyPlatformManager(PlatformConfig(), device_type="harmony_pc")
    calls: list[tuple[int, int]] = []
    client = SimpleNamespace(
        long_tap=lambda x, y: calls.append((x, y)) or True,
    )

    manager.right_click(123, 456, context=client)

    assert calls == [(123, 456)]


def test_harmony_pc_move_uses_hdc_uinput() -> None:
    manager = HarmonyPlatformManager(PlatformConfig(), device_type="harmony_pc")
    calls: list[tuple[int, int]] = []
    client = SimpleNamespace(
        move_mouse=lambda x, y: calls.append((x, y)) or True,
    )

    manager.move(123, 456, context=client)

    assert calls == [(123, 456)]


def test_harmony_mobile_move_is_not_supported() -> None:
    manager = HarmonyPlatformManager(PlatformConfig(), device_type="harmony_mobile")
    client = SimpleNamespace(move_mouse=lambda *_: True)

    with pytest.raises(NotImplementedError, match="only supported on Harmony PC"):
        manager.move(1, 2, context=client)


def test_harmony_hdc_move_mouse_uses_uinput_command(monkeypatch: pytest.MonkeyPatch) -> None:
    wrapper, commands = _make_command_capture_wrapper(monkeypatch)

    assert wrapper.move_mouse(123, 456) is True
    assert commands == ["uinput -M -m 123 456"]


def test_harmony_hdc_activate_window_uses_aa_start_command(monkeypatch: pytest.MonkeyPatch) -> None:
    wrapper, commands = _make_command_capture_wrapper(monkeypatch)

    assert wrapper.activate_window("com.example.app", "MainAbility") is True
    assert commands == ["aa start -b 'com.example.app' -a 'MainAbility'"]


def test_harmony_pc_activate_window_accepts_bundle_and_ability_fields() -> None:
    manager = HarmonyPlatformManager(PlatformConfig(), device_type="harmony_pc")
    calls: list[tuple[str, str]] = []
    client = SimpleNamespace(
        activate_window=lambda bundle, ability: calls.append((bundle, ability)) or True,
    )
    action = Action.from_dict({
        "action_type": "activate_window",
        "value": "com.example.app",
        "name": "MainAbility",
    })

    result = manager.execute_action(client, action)

    assert result.status == ActionStatus.SUCCESS
    assert calls == [("com.example.app", "MainAbility")]


def test_harmony_pc_activate_window_requires_both_fields() -> None:
    manager = HarmonyPlatformManager(PlatformConfig(), device_type="harmony_pc")
    action = Action.from_dict({
        "action_type": "activate_window",
        "value": "com.example.app",
    })

    result = manager.execute_action(SimpleNamespace(), action)

    assert result.status == ActionStatus.FAILED
    assert result.error == "value（bundle 名称）和 name（ability 名称）均为必填"


def test_harmony_activate_window_fields_round_trip_from_camel_case() -> None:
    action = Action.from_dict({
        "action_type": "activate_window",
        "value": "com.example.app",
        "name": "MainAbility",
    })

    assert action.value == "com.example.app"
    assert action.name == "MainAbility"
    assert action.to_dict() == {
        "action_type": "activate_window",
        "value": "com.example.app",
        "name": "MainAbility",
    }


def test_harmony_hdc_window_commands_report_remote_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrapper, _ = _make_command_capture_wrapper(
        monkeypatch,
        output="[Fail] command failed",
    )

    assert wrapper.move_mouse(1, 2) is False
    assert wrapper.activate_window("com.example.app", "MainAbility") is False


def test_harmony_click_turns_false_hdc_result_into_error() -> None:
    manager = HarmonyPlatformManager(PlatformConfig(), device_type="harmony_pc")
    client = SimpleNamespace(tap=lambda x, y: False)

    with pytest.raises(harmony_hdc.HarmonyError):
        manager.click(1, 2, context=client)


def test_harmony_screenshot_rejects_empty_file(tmp_path) -> None:
    manager = HarmonyPlatformManager(PlatformConfig(), device_type="harmony_mobile")

    def screenshot(path: str) -> bool:
        open(path, "wb").close()
        return True

    client = SimpleNamespace(screenshot=screenshot)

    with pytest.raises(harmony_hdc.HarmonyError, match="截图为空"):
        manager.get_screenshot(client)


def test_harmony_screenshot_rejects_invalid_image(tmp_path) -> None:
    manager = HarmonyPlatformManager(PlatformConfig(), device_type="harmony_mobile")

    def screenshot(path: str) -> bool:
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


def test_harmony_lock_state_parsing_tolerates_dump_variants() -> None:
    assert harmony_hdc.parse_harmony_lock_state("screenLocked: true") is True
    assert harmony_hdc.parse_harmony_lock_state(" screenLocked          False") is False
    assert harmony_hdc.parse_harmony_lock_state("isScreenLocked = 1") is True
    assert harmony_hdc.parse_harmony_lock_state("screen_locked no") is False
    assert harmony_hdc.parse_harmony_lock_state("no lock info here") is None


def test_harmony_is_locked_prefers_screenlock_service_then_screen_state() -> None:
    wrapper = HarmonyHdcWrapper.__new__(HarmonyHdcWrapper)
    wrapper.shell = lambda cmd, timeout=30: harmony_hdc.CommandResult(  # type: ignore[method-assign]
        "screenLocked: false" if "3704" in cmd else "", "", 0
    )
    assert wrapper.is_locked() is False

    # 锁屏服务 dump 不可用时退化为熄屏代理
    wrapper.shell = lambda cmd, timeout=30: harmony_hdc.CommandResult("", "", 1)  # type: ignore[method-assign]
    wrapper.is_screen_on = lambda: False  # type: ignore[method-assign]
    assert wrapper.is_locked() is True


def test_harmony_wakeup_uses_power_shell_and_falls_back_to_power_key() -> None:
    wrapper = HarmonyHdcWrapper.__new__(HarmonyHdcWrapper)
    commands: list[str] = []

    wrapper.shell = lambda cmd, timeout=30: (  # type: ignore[method-assign]
        commands.append(cmd) or harmony_hdc.CommandResult("", "", 0)
    )
    assert wrapper.wakeup() is True
    assert commands == ["power-shell wakeup"]

    # power-shell 失败时回退 POWER 键
    wrapper.shell = lambda cmd, timeout=30: harmony_hdc.CommandResult("fail", "", 0)  # type: ignore[method-assign]
    pressed: list[str] = []
    wrapper.press_key = lambda key: pressed.append(key) or True  # type: ignore[method-assign]
    assert wrapper.wakeup() is True
    assert pressed == ["POWER"]


def test_harmony_unlock_pc_branch_swipes_and_inputs_password() -> None:
    action = UnlockScreenAction()
    platform = SimpleNamespace(platform="harmony_pc", _unlock_config={})
    swipes: list[tuple[int, int, int, int]] = []
    taps: list[tuple[int, int]] = []
    texts: list[tuple[int, int, str]] = []
    keys: list[str] = []
    client = SimpleNamespace(
        swipe=lambda x1, y1, x2, y2: swipes.append((x1, y1, x2, y2)),
        tap=lambda x, y: taps.append((x, y)),
        input_text_at=lambda x, y, text: texts.append((x, y, text)),
        press_key=lambda key: keys.append(key),
    )

    action._trigger_password_screen(platform, client, "swipe_up")
    action._input_password_pc(platform, client, "123456")

    assert swipes == [(1560, 1600, 1560, 600)]
    box = UnlockScreenAction.DEFAULT_HARMONY_PC_PASSWORD_BOX
    assert taps == [(box["x"], box["y"])]
    assert texts == [(box["x"], box["y"], "123456")]
    assert keys == ["ENTER"]


def test_harmony_unlock_check_locked_uses_hdc_is_locked() -> None:
    action = UnlockScreenAction()
    for device_type in ("harmony_mobile", "harmony_pc"):
        platform = SimpleNamespace(platform=device_type)
        client = SimpleNamespace(is_locked=lambda: False)
        assert action._check_locked(platform, client) is False

        # 查询异常时保守地视为锁屏
        def raise_error() -> bool:
            raise RuntimeError("dump failed")

        client = SimpleNamespace(is_locked=raise_error)
        assert action._check_locked(platform, client) is True


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


# ============================================================================
# 投屏帧流：JPEG 魔数切帧
# ============================================================================


def test_split_jpeg_frames_extracts_frames_and_keeps_partial_tail() -> None:
    frame1 = b"\xff\xd8" + b"a" * 10 + b"\xff\xd9"
    frame2 = b"\xff\xd8" + b"b" * 5 + b"\xff\xd9"
    partial = b"\xff\xd8" + b"c" * 3

    # 粘包：两帧完整 + 尾部半包
    frames, rest = split_jpeg_frames(bytearray(frame1 + frame2 + partial))

    assert frames == [frame1, frame2]
    assert bytes(rest) == partial

    # 半包补齐后能切出完整帧
    frames, rest = split_jpeg_frames(rest + b"cc\xff\xd9")
    assert frames == [b"\xff\xd8" + b"c" * 5 + b"\xff\xd9"]
    assert bytes(rest) == b""


def test_split_jpeg_frames_discards_dirty_data_before_frame_start() -> None:
    frame = b"\xff\xd8data\xff\xd9"

    # 帧头前的脏数据不进入帧内容
    frames, rest = split_jpeg_frames(bytearray(b"noise" + frame))
    assert frames == [frame]
    assert bytes(rest) == b""

    # 只有帧头没有帧尾时，丢弃帧头前的脏数据避免缓冲膨胀
    frames, rest = split_jpeg_frames(bytearray(b"junk\xff\xd8xy"))
    assert frames == []
    assert bytes(rest) == b"\xff\xd8xy"


# ============================================================================
# 投屏帧流：agent 版本回退
# ============================================================================


def _make_stub_capture(monkeypatch: pytest.MonkeyPatch, failing_agents: set[str]):
    """构造内部步骤全部打桩的 HarmonyScreenCapture，记录尝试的 agent。"""
    capture = HarmonyScreenCapture(SimpleNamespace(serial="dev-001"))
    attempted: list[str] = []

    def fake_setup_agent(path: str) -> None:
        name = os.path.basename(path)
        attempted.append(name)
        if name in failing_agents:
            raise HarmonyCaptureError(f"{name} incompatible")

    monkeypatch.setattr(capture, "_setup_device_agent", fake_setup_agent)
    for step in ("_restart_uitest_daemon", "_setup_fport", "_connect_sock",
                 "_start_capture_screen", "_cleanup", "_recv_worker"):
        monkeypatch.setattr(capture, step, lambda: None)
    return capture, attempted


def test_harmony_capture_start_falls_back_to_older_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture, attempted = _make_stub_capture(
        monkeypatch, failing_agents={"uitest_agent_v1.2.2.so"}
    )

    capture.start()

    # 新版失败后回退旧版，且按 AGENT_CANDIDATES 顺序尝试
    assert attempted == list(harmony_capture.AGENT_CANDIDATES)
    assert attempted == ["uitest_agent_v1.2.2.so", "uitest_agent_v1.1.0.so"]


def test_harmony_capture_start_raises_when_all_agents_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture, attempted = _make_stub_capture(
        monkeypatch, failing_agents=set(harmony_capture.AGENT_CANDIDATES)
    )

    with pytest.raises(HarmonyCaptureError):
        capture.start()

    assert attempted == list(harmony_capture.AGENT_CANDIDATES)


# ============================================================================
# HarmonyFrameSource：帧流降级轮询
# ============================================================================


def test_harmony_frame_source_degrades_to_polling_when_stream_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from worker.screen.frame_source import HarmonyFrameSource

    class FailingCapture:
        def __init__(self, hdc):
            pass

        def start(self):
            raise HarmonyCaptureError("daemon not available")

    monkeypatch.setattr(harmony_capture, "HarmonyScreenCapture", FailingCapture)

    source = HarmonyFrameSource("dev-001", SimpleNamespace())
    source.start()

    assert source._capture is None
    assert source._polling is True


def test_harmony_frame_source_poll_reuses_frame_within_min_interval() -> None:
    from worker.screen.frame_source import HarmonyFrameSource

    screenshot_calls: list[str] = []

    def fake_screenshot(local_path: str) -> bool:
        screenshot_calls.append(local_path)
        with open(local_path, "wb") as f:
            f.write(b"\xff\xd8frame\xff\xd9")
        return True

    source = HarmonyFrameSource("dev-001", SimpleNamespace(screenshot=fake_screenshot))
    source._polling = True

    first = source._poll_frame()
    second = source._poll_frame()

    # 间隔小于 POLL_MIN_INTERVAL 时命中缓存，只截图一次
    assert first == second == b"\xff\xd8frame\xff\xd9"
    assert len(screenshot_calls) == 1
    # 临时文件用完即删
    assert not os.path.isfile(screenshot_calls[0])


def test_harmony_frame_source_raises_when_stream_stops() -> None:
    from worker.screen.frame_source import HarmonyFrameSource

    source = HarmonyFrameSource("dev-001", SimpleNamespace())
    source._capture = SimpleNamespace(is_running=False)

    # 帧流中途断开抛 ConnectionError，由 ScreenManager 捕获循环按错误计数处理
    with pytest.raises(ConnectionError):
        source.get_frame()


# ============================================================================
# HDC 端口转发（fport）
# ============================================================================


def test_fport_requires_ok_in_output(monkeypatch: pytest.MonkeyPatch) -> None:
    wrapper = HarmonyHdcWrapper.__new__(HarmonyHdcWrapper)
    executed: list[list[str]] = []
    result_holder = [CommandResult("Forwardport result:OK", "", 0)]
    monkeypatch.setattr(
        wrapper,
        "_execute",
        lambda args, timeout=30: executed.append(args) or result_holder[0],
    )

    assert wrapper.fport(50000, 8012) is True
    assert executed == [["fport", "tcp:50000", "tcp:8012"]]

    # 真机失败输出不含 OK，必须判失败
    result_holder[0] = CommandResult("[Fail]TCP Port listen failed at 50000", "", 0)
    assert wrapper.fport(50000, 8012) is False


def test_fport_rm_requires_success_in_output(monkeypatch: pytest.MonkeyPatch) -> None:
    wrapper = HarmonyHdcWrapper.__new__(HarmonyHdcWrapper)
    executed: list[list[str]] = []
    result_holder = [
        CommandResult("Remove forward ruler success, ruler:tcp:50000 tcp:8012", "", 0)
    ]
    monkeypatch.setattr(
        wrapper,
        "_execute",
        lambda args, timeout=30: executed.append(args) or result_holder[0],
    )

    assert wrapper.fport_rm(50000, 8012) is True
    assert executed == [["fport", "rm", "tcp:50000", "tcp:8012"]]

    result_holder[0] = CommandResult(
        "[Fail]Remove forward ruler failed, ruler is not exist", "", 0
    )
    assert wrapper.fport_rm(50000, 8012) is False


def test_fport_ls_filters_blank_and_empty_lines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrapper = HarmonyHdcWrapper.__new__(HarmonyHdcWrapper)
    result_holder = [CommandResult("[Empty]\n", "", 0)]
    monkeypatch.setattr(
        wrapper, "_execute", lambda args, timeout=30: result_holder[0]
    )

    assert wrapper.fport_ls() == []

    result_holder[0] = CommandResult(
        "tcp:50000 tcp:8012    [Forward]\n\n", "", 0
    )
    assert wrapper.fport_ls() == ["tcp:50000 tcp:8012    [Forward]"]


# ============================================================================
# 分辨率解析：新增格式
# ============================================================================


def test_harmony_display_size_parses_phone_render_service_active_mode() -> None:
    # 手机 hidumper -s RenderService 输出
    output = "supportedMode: 0, activeMode: 1260x2720, refreshrate=120"

    assert parse_harmony_display_size(output) == (1260, 2720)


def test_display_size_falls_back_to_render_service_dump(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrapper = HarmonyHdcWrapper.__new__(HarmonyHdcWrapper)
    outputs = {
        "hidumper -s 10 -a screen": "no display metrics",
        "hidumper -s RenderService -a screen": (
            "activeMode: 1260x2720, refreshrate=120"
        ),
    }
    monkeypatch.setattr(
        wrapper,
        "shell",
        lambda command, timeout=30: CommandResult(outputs[command], "", 0),
    )

    # 数字服务 ID dump 无法解析时，回退按服务名 dump
    assert wrapper.display_size() == (1260, 2720)


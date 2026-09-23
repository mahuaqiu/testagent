"""cmd_exec 命令预处理测试：@tools 占位符兼容与 .ps1 脚本改写。"""

import os
import shutil

import pytest

from worker.actions.cmd_exec import (
    CmdExecAction,
    _resolve_tools_placeholder,
    _wrap_powershell_script,
)
from worker.task import Action, ActionStatus
from worker.tools import get_tools_dir


# ---------- @tools 占位符 ----------


def test_tools_placeholder_forward_slash() -> None:
    expected = os.path.join(get_tools_dir(), "adb") + " devices"
    assert _resolve_tools_placeholder("@tools/adb devices") == expected


def test_tools_placeholder_backslash() -> None:
    assert _resolve_tools_placeholder("@tools\\adb devices") == _resolve_tools_placeholder(
        "@tools/adb devices"
    )


def test_tools_placeholder_inside_quotes() -> None:
    expected = f'"{os.path.join(get_tools_dir(), "x.ps1")}"'
    assert _resolve_tools_placeholder('"@tools/x.ps1"') == expected


def test_execute_backslash_placeholder(tmp_path, monkeypatch) -> None:
    (tmp_path / "hello.txt").write_text("placeholder_ok", encoding="utf-8")
    monkeypatch.setattr("worker.actions.cmd_exec.get_tools_dir", lambda: str(tmp_path))

    result = CmdExecAction().execute(
        None, Action(action_type="cmd_exec", value="type @tools\\hello.txt")
    )

    assert result.status == ActionStatus.SUCCESS
    assert "placeholder_ok" in (result.stdout or "")


def test_execute_forward_slash_placeholder(tmp_path, monkeypatch) -> None:
    (tmp_path / "hello.txt").write_text("placeholder_ok", encoding="utf-8")
    monkeypatch.setattr("worker.actions.cmd_exec.get_tools_dir", lambda: str(tmp_path))

    result = CmdExecAction().execute(
        None, Action(action_type="cmd_exec", value="type @tools/hello.txt")
    )

    assert result.status == ActionStatus.SUCCESS
    assert "placeholder_ok" in (result.stdout or "")


# ---------- 裸 .ps1 命令改写为 powershell -File ----------


@pytest.fixture
def windows_only(monkeypatch):
    monkeypatch.setattr(os, "name", "nt")


def test_wrap_bare_ps1(windows_only) -> None:
    assert _wrap_powershell_script(r"D:\tools\start_app.ps1") == (
        r'powershell -NoProfile -ExecutionPolicy Bypass -File "D:\tools\start_app.ps1"'
    )


def test_wrap_bare_ps1_with_args(windows_only) -> None:
    assert _wrap_powershell_script(r"D:\tools\start_app.ps1 -Name foo") == (
        r'powershell -NoProfile -ExecutionPolicy Bypass -File "D:\tools\start_app.ps1" -Name foo'
    )


def test_wrap_quoted_path_with_args(windows_only) -> None:
    assert _wrap_powershell_script(r'"C:\a b\my.ps1" -Flag 1') == (
        r'powershell -NoProfile -ExecutionPolicy Bypass -File "C:\a b\my.ps1" -Flag 1'
    )


def test_wrap_forward_slash_path(windows_only) -> None:
    assert _wrap_powershell_script(r"D:/tools/start_app.ps1") == (
        r'powershell -NoProfile -ExecutionPolicy Bypass -File "D:/tools/start_app.ps1"'
    )


def test_wrap_call_operator(windows_only) -> None:
    assert _wrap_powershell_script(r'& "C:\tools\x.ps1"') == (
        r'powershell -NoProfile -ExecutionPolicy Bypass -File "C:\tools\x.ps1"'
    )


def test_powershell_explicit_call_unchanged(windows_only) -> None:
    for cmd in (
        "powershell -File x.ps1",
        "POWERSHELL -Command Get-Date",
        "powershell.exe -NoProfile x.ps1",
        "pwsh x.ps1",
    ):
        assert _wrap_powershell_script(cmd) == cmd


def test_non_script_command_unchanged(windows_only) -> None:
    for cmd in ("dir", "adb devices", "echo hello.ps1", "type x.ps1.txt"):
        assert _wrap_powershell_script(cmd) == cmd


def test_posix_no_wrap(monkeypatch) -> None:
    monkeypatch.setattr(os, "name", "posix")
    assert _wrap_powershell_script("C:\\tools\\x.ps1") == "C:\\tools\\x.ps1"


@pytest.mark.skipif(
    os.name != "nt" or shutil.which("powershell") is None,
    reason="需要 Windows PowerShell",
)
def test_execute_runs_ps1_script(tmp_path, monkeypatch) -> None:
    (tmp_path / "hello.ps1").write_text(
        'Write-Output "hello_from_cmd_exec"', encoding="utf-8"
    )
    monkeypatch.setattr("worker.actions.cmd_exec.get_tools_dir", lambda: str(tmp_path))

    result = CmdExecAction().execute(
        None, Action(action_type="cmd_exec", value="@tools\\hello.ps1")
    )

    assert result.status == ActionStatus.SUCCESS
    assert "hello_from_cmd_exec" in (result.stdout or "")

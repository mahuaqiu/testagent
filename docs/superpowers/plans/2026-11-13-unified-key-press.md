# 统一按键输入规范（press 动作）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** press 动作对外统一为 pyautogui 风格（小写、`+` 组合、修饰键在前），各平台内部转换，未知键显式报错。

**Architecture:** 新增共享模块 `worker/platforms/keymap.py` 承载解析/校验/映射；web/windows/mac/android 四个平台的 `press()` 入口统一走它；web system 层改用 `pyautogui.hotkey` 修复组合键；api.yaml 文档同步重写。

**Tech Stack:** Python 3.10+、pyautogui（键名权威来源 `pyautogui.KEYBOARD_KEYS`）、Playwright（browser 层目标格式）、pytest + unittest.mock。

**Spec:** `docs/superpowers/specs/2026-11-13-unified-key-press-design.md`

## Global Constraints

- 对外唯一格式：小写命名键、组合键 `+` 分隔、修饰键在前、最后一段为功能键；单字符保留原大小写（`'A'` = shift+a）。
- 修饰键：`ctrl`、`alt`、`shift`、`win`；别名 `windows/winleft/winright/meta/super→win`、`cmd/command/commandleft/commandright→command`。**不含 `control`**（旧 Playwright 风格必须报错）。
- mac 运行时：`ctrl/win/meta/command` → `command`（browser 层 → Playwright `Meta`）；Windows 运行时：`cmd/command` 报错（提示用 ctrl 或 win），`win/meta` → `winleft`。
- 未知键名一律 `ValueError`（不再静默）；错误信息含原始输入、原因、正确写法提示。
- 组合键规则：≥2 段时前 n-1 段必须全为修饰键、末段必须是功能键。
- 测试命令统一用 `./venv/Scripts/python.exe -m pytest`（Windows 开发机）；提交前跑 `./venv/Scripts/python.exe -m ruff check <改动文件>`。
- 提交信息用中文，遵循仓库现有风格（`feat:`/`test:`/`docs:` 前缀）。

---

### Task 1: 共享按键映射模块 keymap.py

**Files:**
- Create: `worker/platforms/keymap.py`
- Test: `tests/platforms/test_keymap.py`

**Interfaces:**
- Consumes: 运行时 `pyautogui.KEYBOARD_KEYS`（经 `_pyautogui_keys()` 惰性导入，供测试 patch）
- Produces（后续任务依赖的精确签名）:
  - `parse_key_combo(key: str) -> list[str]` — 非法格式抛 `ValueError`
  - `validate_combo(parts: list[str]) -> None` — 违规抛 `ValueError`
  - `to_playwright_combo(parts: list[str], on_mac: bool) -> str` — 返回如 `"Control+a"`；browser 层不支持的键抛 `ValueError`
  - `to_pyautogui_win(part: str) -> str` / `to_pyautogui_mac(part: str) -> str` — 未知键抛 `ValueError`
  - `_pyautogui_keys() -> set[str]`（测试用 patch 点）

- [ ] **Step 1: 写失败测试**

创建 `tests/platforms/test_keymap.py`：

```python
"""统一按键名解析与映射测试。"""

import pytest

from worker.platforms import keymap


def test_parse_strips_and_lowercases_named_keys() -> None:
    assert keymap.parse_key_combo(" ctrl + a ") == ["ctrl", "a"]


def test_parse_single_char_keeps_case() -> None:
    assert keymap.parse_key_combo("A") == ["A"]
    assert keymap.parse_key_combo("ctrl+A") == ["ctrl", "A"]


def test_parse_empty_raises() -> None:
    with pytest.raises(ValueError, match="Key is required"):
        keymap.parse_key_combo("   ")
    with pytest.raises(ValueError, match="空段"):
        keymap.parse_key_combo("ctrl+")


def test_validate_combo_rejects_function_key_first() -> None:
    with pytest.raises(ValueError, match="不是修饰键"):
        keymap.validate_combo(["a", "ctrl"])


def test_validate_combo_rejects_modifier_last() -> None:
    with pytest.raises(ValueError, match="缺少功能键"):
        keymap.validate_combo(["ctrl", "shift"])


def test_playwright_combo_win() -> None:
    assert keymap.to_playwright_combo(["ctrl", "a"], on_mac=False) == "Control+a"
    assert keymap.to_playwright_combo(["esc"], on_mac=False) == "Escape"
    assert keymap.to_playwright_combo(["up"], on_mac=False) == "ArrowUp"
    assert keymap.to_playwright_combo(["win", "e"], on_mac=False) == "Meta+e"
    assert keymap.to_playwright_combo(["alt", "f4"], on_mac=False) == "Alt+F4"
    assert keymap.to_playwright_combo(["delete"], on_mac=False) == "Delete"


def test_playwright_combo_mac_ctrl_is_meta() -> None:
    assert keymap.to_playwright_combo(["ctrl", "c"], on_mac=True) == "Meta+c"


def test_playwright_combo_unsupported_key_hints_system_level() -> None:
    with pytest.raises(ValueError, match="level=system"):
        keymap.to_playwright_combo(["volumeup"], on_mac=False)


def test_win_mapping() -> None:
    assert keymap.to_pyautogui_win("win") == "winleft"
    assert keymap.to_pyautogui_win("meta") == "winleft"
    assert keymap.to_pyautogui_win("ctrl") == "ctrl"
    assert keymap.to_pyautogui_win("enter") == "enter"
    assert keymap.to_pyautogui_win("A") == "A"


def test_win_command_is_mac_semantic() -> None:
    with pytest.raises(ValueError, match="ctrl 或 win"):
        keymap.to_pyautogui_win("command")


def test_win_unknown_key_errors(monkeypatch) -> None:
    monkeypatch.setattr(keymap, "_pyautogui_keys", lambda: {"enter", "esc"})
    with pytest.raises(ValueError, match="未知按键名"):
        keymap.to_pyautogui_win("control")


def test_mac_ctrl_maps_to_command() -> None:
    assert keymap.to_pyautogui_mac("ctrl") == "command"
    assert keymap.to_pyautogui_mac("win") == "command"
    assert keymap.to_pyautogui_mac("command") == "command"


def test_mac_unknown_key_errors(monkeypatch) -> None:
    monkeypatch.setattr(keymap, "_pyautogui_keys", lambda: {"enter", "command"})
    with pytest.raises(ValueError, match="未知按键名"):
        keymap.to_pyautogui_mac("arrowup")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `./venv/Scripts/python.exe -m pytest tests/platforms/test_keymap.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'worker.platforms.keymap'` 或 ImportError）

- [ ] **Step 3: 实现 keymap.py**

创建 `worker/platforms/keymap.py`（完整内容）：

```python
"""统一按键名解析与平台映射（press 动作对外唯一契约）。

对外格式：pyautogui 风格 —— 小写命名键、"+" 组合键、修饰键在前、
最后一段为功能键；单字符保留原大小写（'A' 等价 shift+a）。
Playwright 风格（Control+A、ArrowUp）不再支持，解析时直接报错并提示。
"""

# 修饰键别名（小写）→ 统一修饰键语义名（ctrl/alt/shift/win/command）
_MODIFIER_ALIASES = {
    "ctrlleft": "ctrl",
    "ctrlright": "ctrl",
    "altleft": "alt",
    "altright": "alt",
    "altgr": "alt",
    "shiftleft": "shift",
    "shiftright": "shift",
    # Windows 徽标键（mac 上语义等同 command）
    "win": "win",
    "windows": "win",
    "winleft": "win",
    "winright": "win",
    "meta": "win",
    "super": "win",
    # mac command 键（Windows 运行时无此键，报错提示）
    "command": "command",
    "cmd": "command",
    "commandleft": "command",
    "commandright": "command",
}

# 统一名 → Playwright 键名（实测 usKeyboardLayout + aliases）
PYAUTOGUI_TO_PLAYWRIGHT: dict[str, str] = {
    "enter": "Enter",
    "esc": "Escape",
    "escape": "Escape",
    "backspace": "Backspace",
    "tab": "Tab",
    "del": "Delete",
    "delete": "Delete",
    "space": "Space",
    "up": "ArrowUp",
    "down": "ArrowDown",
    "left": "ArrowLeft",
    "right": "ArrowRight",
    "home": "Home",
    "end": "End",
    "pageup": "PageUp",
    "pagedown": "PageDown",
    "insert": "Insert",
    "capslock": "CapsLock",
    "numlock": "NumLock",
    "scrolllock": "ScrollLock",
    "printscreen": "PrintScreen",
    "print": "PrintScreen",
    "pause": "Pause",
    "backquote": "Backquote",
    "minus": "Minus",
    "equal": "Equal",
    "backslash": "Backslash",
    "bracketleft": "BracketLeft",
    "bracketright": "BracketRight",
    "semicolon": "Semicolon",
    "quote": "Quote",
    "comma": "Comma",
    "period": "Period",
    "slash": "Slash",
}
for _i in range(1, 13):
    PYAUTOGUI_TO_PLAYWRIGHT[f"f{_i}"] = f"F{_i}"
for _i in range(10):
    PYAUTOGUI_TO_PLAYWRIGHT[f"num{_i}"] = f"Numpad{_i}"

_LEGACY_HINT = (
    "请使用统一小写 pyautogui 风格（如 ctrl+a、enter、up、f5）；"
    "Playwright 风格（如 Control+A、ArrowUp）已不支持。"
)


def _pyautogui_keys() -> set[str]:
    """运行时 pyautogui 键名集合（测试可 patch）。"""
    import pyautogui

    return set(pyautogui.KEYBOARD_KEYS)


def parse_key_combo(key: str) -> list[str]:
    """解析按键表达式为段落列表；空输入或含空段抛 ValueError。

    多字符段统一小写，单字符保留原大小写（'A' 等价 shift+a）。
    """
    if key is None or not key.strip():
        raise ValueError("Key is required")
    parts = [part.strip() for part in key.split("+")]
    if any(not part for part in parts):
        raise ValueError(
            f"Unsupported key '{key}': 组合键格式非法（含空段）。"
            "组合键规则：修饰键在前、最后一个为功能键，如 'ctrl+a'。"
        )
    return [part if len(part) == 1 else part.lower() for part in parts]


def _resolve_modifier(part: str) -> str | None:
    """返回统一修饰键语义名（ctrl/alt/shift/win/command），非修饰键返回 None。"""
    return _MODIFIER_ALIASES.get(part.lower())


def validate_combo(parts: list[str]) -> None:
    """组合键规则：前 n-1 段必须全为修饰键、末段必须是功能键。"""
    if len(parts) == 1:
        return
    for part in parts[:-1]:
        if _resolve_modifier(part) is None:
            raise ValueError(
                f"Unsupported combo '{'+'.join(parts)}': '{part}' 不是修饰键。"
                "组合键规则：修饰键在前（ctrl/alt/shift/win）、最后一个为功能键，如 'ctrl+a'。"
                f"{_LEGACY_HINT}"
            )
    if _resolve_modifier(parts[-1]) is not None:
        raise ValueError(
            f"Unsupported combo '{'+'.join(parts)}': 最后一段 '{parts[-1]}' 是修饰键，缺少功能键。"
            "组合键规则：修饰键在前、最后一个为功能键，如 'ctrl+a'。"
        )


def to_pyautogui_win(part: str) -> str:
    """统一按键名 → Windows pyautogui 键名；未知按键抛 ValueError。"""
    modifier = _resolve_modifier(part)
    if modifier is not None:
        if modifier == "command":
            raise ValueError(
                f"Unsupported key '{part}': cmd/command 为 mac 语义，Windows 请使用 ctrl 或 win。"
            )
        if modifier == "win":
            return "winleft"
        return modifier  # ctrl / alt / shift
    if len(part) == 1:
        return part
    if part.lower() in _pyautogui_keys():
        return part.lower()
    raise ValueError(f"Unsupported key '{part}': 未知按键名。{_LEGACY_HINT}")


def to_pyautogui_mac(part: str) -> str:
    """统一按键名 → macOS pyautogui 键名；ctrl/win 自动按 command 注入。"""
    modifier = _resolve_modifier(part)
    if modifier is not None:
        return {
            "ctrl": "command",
            "alt": "alt",
            "shift": "shift",
            "win": "command",
            "command": "command",
        }[modifier]
    if len(part) == 1:
        return part
    if part.lower() in _pyautogui_keys():
        return part.lower()
    raise ValueError(f"Unsupported key '{part}': 未知按键名。{_LEGACY_HINT}")


def to_playwright_combo(parts: list[str], on_mac: bool) -> str:
    """统一按键段落 → Playwright 组合键字符串（如 'Control+a'）。

    mac host 上 ctrl/win/command 均映射为 Meta（mac 的 ctrl→command 语义）；
    Playwright 不支持注入的键（如 volumeup）报错提示改用 level=system。
    """
    validate_combo(parts)
    resolved: list[str] = []
    for part in parts:
        modifier = _resolve_modifier(part)
        if modifier is not None:
            if modifier == "command" and not on_mac:
                raise ValueError(
                    f"Unsupported key '{part}': cmd/command 为 mac 语义，Windows 请使用 ctrl 或 win。"
                )
            if modifier == "ctrl":
                resolved.append("Meta" if on_mac else "Control")
            elif modifier == "alt":
                resolved.append("Alt")
            elif modifier == "shift":
                resolved.append("Shift")
            else:  # win / command
                resolved.append("Meta")
            continue
        if len(part) == 1:
            resolved.append(part)
            continue
        playwright_name = PYAUTOGUI_TO_PLAYWRIGHT.get(part.lower())
        if playwright_name is None:
            raise ValueError(
                f"Unsupported key '{part}': browser 层（Playwright）不支持注入该键。"
                "请改用 level=system 的系统级按键。"
            )
        resolved.append(playwright_name)
    return "+".join(resolved)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `./venv/Scripts/python.exe -m pytest tests/platforms/test_keymap.py -v`
Expected: 14 passed

- [ ] **Step 5: Lint + 提交**

```bash
./venv/Scripts/python.exe -m ruff check worker/platforms/keymap.py tests/platforms/test_keymap.py
git add worker/platforms/keymap.py tests/platforms/test_keymap.py
git commit -m "feat: 新增统一按键名解析与平台映射模块 keymap"
```

---

### Task 2: web.py press 统一（browser/system 双层）

**Files:**
- Modify: `worker/platforms/web.py`（`press` 约 959-991 行、`_system_input` 约 873-879 行、顶部 import）
- Test: `tests/platforms/test_web_press.py`

**Interfaces:**
- Consumes: `keymap.parse_key_combo / validate_combo / to_playwright_combo / to_pyautogui_win / to_pyautogui_mac`
- Produces: `WebPlatformManager.press(key, context=None, level=None)`、`_system_press(key, parts)`（供测试）；对外行为：两层共用统一格式

- [ ] **Step 1: 写失败测试**

创建 `tests/platforms/test_web_press.py`：

```python
"""Web 平台按键（press）统一格式测试。"""

import sys
from unittest.mock import MagicMock, patch

import pytest

from worker.config import PlatformConfig
from worker.platforms.web import WebPlatformManager


def test_browser_level_maps_ctrl_a_to_playwright() -> None:
    manager = WebPlatformManager(PlatformConfig())
    page = MagicMock()
    with patch("worker.platforms.web._run_async"):
        manager.press("ctrl+a", context=page)
    page.keyboard.press.assert_called_once_with("Control+a")


def test_browser_level_maps_esc_and_arrows() -> None:
    manager = WebPlatformManager(PlatformConfig())
    for key, expected in [("esc", "Escape"), ("up", "ArrowUp"), ("enter", "Enter")]:
        page = MagicMock()
        with patch("worker.platforms.web._run_async"):
            manager.press(key, context=page)
        page.keyboard.press.assert_called_once_with(expected)


def test_browser_level_darwin_ctrl_maps_meta() -> None:
    manager = WebPlatformManager(PlatformConfig())
    page = MagicMock()
    with patch("worker.platforms.web._run_async"), patch.object(sys, "platform", "darwin"):
        manager.press("ctrl+a", context=page)
    page.keyboard.press.assert_called_once_with("Meta+a")


def test_system_level_combo_uses_hotkey() -> None:
    manager = WebPlatformManager(PlatformConfig())
    with (
        patch("worker.platforms.web.SYSTEM_LEVEL_AVAILABLE", True),
        patch("worker.platforms.web.pyautogui") as pag,
    ):
        manager._system_press("ctrl+alt+t", ["ctrl", "alt", "t"])
    pag.hotkey.assert_called_once_with("ctrl", "alt", "t")


def test_system_level_single_key_uses_press() -> None:
    manager = WebPlatformManager(PlatformConfig())
    with (
        patch("worker.platforms.web.SYSTEM_LEVEL_AVAILABLE", True),
        patch("worker.platforms.web.pyautogui") as pag,
    ):
        manager._system_press("esc", ["esc"])
    pag.press.assert_called_once_with("esc")


def test_system_level_darwin_maps_ctrl_to_command() -> None:
    manager = WebPlatformManager(PlatformConfig())
    with (
        patch("worker.platforms.web.SYSTEM_LEVEL_AVAILABLE", True),
        patch("worker.platforms.web.pyautogui") as pag,
        patch.object(sys, "platform", "darwin"),
    ):
        manager._system_press("ctrl+v", ["ctrl", "v"])
    pag.hotkey.assert_called_once_with("command", "v")


def test_system_level_darwin_win_maps_to_command(monkeypatch) -> None:
    manager = WebPlatformManager(PlatformConfig())
    monkeypatch.setattr("worker.platforms.keymap._pyautogui_keys", lambda: {"enter", "command"})
    with (
        patch("worker.platforms.web.SYSTEM_LEVEL_AVAILABLE", True),
        patch("worker.platforms.web.pyautogui") as pag,
        patch.object(sys, "platform", "darwin"),
    ):
        manager._system_press("win+e", ["win", "e"])
    pag.hotkey.assert_called_once_with("command", "e")


def test_legacy_playwright_style_rejected() -> None:
    manager = WebPlatformManager(PlatformConfig())
    with pytest.raises(ValueError, match="不是修饰键"):
        manager.press("Control+A", context=MagicMock())


def test_browser_level_system_only_key_rejected() -> None:
    manager = WebPlatformManager(PlatformConfig())
    with pytest.raises(ValueError, match="level=system"):
        manager.press("volumeup", context=MagicMock())


def test_system_level_unknown_key_rejected() -> None:
    manager = WebPlatformManager(PlatformConfig())
    with (
        patch("worker.platforms.web.SYSTEM_LEVEL_AVAILABLE", True),
        patch("worker.platforms.web.pyautogui"),
        patch.object(sys, "platform", "win32"),
    ):
        with pytest.raises(ValueError, match="未知按键名"):
            manager._system_press("Control+A", ["control", "a"])
```

- [ ] **Step 2: 运行测试确认失败**

Run: `./venv/Scripts/python.exe -m pytest tests/platforms/test_web_press.py -v`
Expected: FAIL（当前 browser 层透传 `ctrl+a`、system 层无组合键支持）

- [ ] **Step 3: 修改 web.py**

3a. 顶部 import 区（`from worker.platforms.base import PlatformManager` 附近）加入：

```python
from worker.platforms.keymap import (
    parse_key_combo,
    to_playwright_combo,
    to_pyautogui_mac,
    to_pyautogui_win,
    validate_combo,
)
```

3b. 替换 `press`（原 959-967 行）：

```python
    def press(self, key: str, context: Any = None, level: str = None) -> None:
        """按键。统一 pyautogui 风格（如 ctrl+a），browser/system 两层共用同一格式。"""
        parts = parse_key_combo(key)
        validate_combo(parts)
        effective_level = level or self._current_level
        if effective_level == "system":
            self._system_press(key, parts)
            return
        page = context or self._current_page
        if page:
            playwright_key = to_playwright_combo(parts, on_mac=sys.platform.startswith("darwin"))
            _run_async(page.keyboard.press(playwright_key))
```

（`validate_combo` 已含在上面 import 块中。）

3c. 替换 `_system_press`（原 969-991 行，删除旧 key_map）：

```python
    def _system_press(self, key: str, parts: list[str]) -> None:
        """系统级按键（pyautogui），键名按运行 host 平台适配，支持组合键。"""
        if not SYSTEM_LEVEL_AVAILABLE:
            raise RuntimeError("System-level operations not available")
        on_mac = sys.platform.startswith("darwin")
        native_parts = [to_pyautogui_mac(part) if on_mac else to_pyautogui_win(part) for part in parts]
        if len(native_parts) > 1:
            pyautogui.hotkey(*native_parts)
        else:
            pyautogui.press(native_parts[0])
        logger.debug(f"System-level press: {key} -> {native_parts}")
```

3d. 替换 `_system_input`（原 873-879 行）的 hotkey 行：

```python
    def _system_input(self, text: str) -> None:
        """系统级输入文本（使用剪贴板粘贴，支持特殊字符）。"""
        if not SYSTEM_LEVEL_AVAILABLE:
            raise RuntimeError("System-level operations not available")
        pyperclip.copy(text)
        if sys.platform.startswith("darwin"):
            pyautogui.hotkey("command", "v")
        else:
            pyautogui.hotkey("ctrl", "v")
        logger.debug(f"System-level input: {text}")
```

- [ ] **Step 4: 运行测试确认通过**

Run: `./venv/Scripts/python.exe -m pytest tests/platforms/test_web_press.py tests/platforms/test_keymap.py -v`
Expected: 全部 passed

- [ ] **Step 5: Lint + 提交**

```bash
./venv/Scripts/python.exe -m ruff check worker/platforms/web.py tests/platforms/test_web_press.py
git add worker/platforms/web.py tests/platforms/test_web_press.py
git commit -m "feat: web 平台 press 统一 pyautogui 风格并修复 system 层组合键"
```

---

### Task 3: windows.py 统一校验（消除静默假绿）

**Files:**
- Modify: `worker/platforms/windows.py`（`press` 约 332-346 行、删除 `_normalize_key` 348-356 行、顶部 import）
- Test: `tests/test_windows_press.py`（追加用例）

**Interfaces:**
- Consumes: `keymap.parse_key_combo / validate_combo / to_pyautogui_win`
- Produces: `WindowsPlatformManager.press(key, context=None)`；SAS 与 `winleft` 行为不变（回归）

- [ ] **Step 1: 追加失败测试**

在 `tests/test_windows_press.py` 追加（文件顶部补 `import pytest`）：

```python
def test_windows_legacy_style_raises_with_hint() -> None:
    manager = WindowsPlatformManager(PlatformConfig())
    with pytest.raises(ValueError, match="Playwright"):
        manager.press("Control+A")


def test_windows_unknown_key_raises() -> None:
    manager = WindowsPlatformManager(PlatformConfig())
    with pytest.raises(ValueError, match="未知按键名"):
        manager.press("arrowup")


def test_windows_combo_function_key_first_rejected() -> None:
    manager = WindowsPlatformManager(PlatformConfig())
    with pytest.raises(ValueError, match="不是修饰键"):
        manager.press("a+ctrl")


def test_windows_ctrl_a_uses_hotkey() -> None:
    manager = WindowsPlatformManager(PlatformConfig())
    with patch("worker.platforms.windows.pyautogui.hotkey") as hotkey:
        manager.press("ctrl+a")
    hotkey.assert_called_once_with("ctrl", "a")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `./venv/Scripts/python.exe -m pytest tests/test_windows_press.py -v`
Expected: 新增 4 个用例 FAIL（当前 `Control+A` 静默、`arrowup` 静默、`a+ctrl` 不报错）

- [ ] **Step 3: 修改 windows.py**

3a. 顶部 import 区加入：

```python
from worker.platforms.keymap import parse_key_combo, to_pyautogui_win, validate_combo
```

3b. 替换 `press` 与 `_normalize_key`（原 332-356 行整段）：

```python
    def press(self, key: str, context: Any = None) -> None:
        """按键。统一 pyautogui 风格，支持组合键，如 "ctrl+c"。"""
        parts = parse_key_combo(key)
        validate_combo(parts)

        # Ctrl+Alt+Delete 是 Windows 安全注意序列，普通 SendInput/pyautogui
        # 注入会被 Winlogon 丢弃，必须通过系统 SendSAS 接口触发。
        if len(parts) == 3 and set(parts) == {"ctrl", "alt", "delete"}:
            self._send_secure_attention_sequence()
            return

        keys = [to_pyautogui_win(part) for part in parts]
        if len(keys) > 1:
            pyautogui.hotkey(*keys)
        else:
            pyautogui.press(keys[0])
```

（`_normalize_key` 整个删除，`win/windows/winleft/winright/meta/super` 归一已由 keymap 承担。）

- [ ] **Step 4: 运行测试确认通过**

Run: `./venv/Scripts/python.exe -m pytest tests/test_windows_press.py -v`
Expected: 6 passed（含既有 SAS、winleft 回归）

- [ ] **Step 5: Lint + 提交**

```bash
./venv/Scripts/python.exe -m ruff check worker/platforms/windows.py tests/test_windows_press.py
git add worker/platforms/windows.py tests/test_windows_press.py
git commit -m "feat: windows press 接入统一键名校验，未知键显式报错"
```

---

### Task 4: mac.py ctrl 自动映射 command

**Files:**
- Modify: `worker/platforms/mac.py`（`press` 135-141 行、顶部 import）
- Test: `tests/platforms/test_mac_press.py`

**Interfaces:**
- Consumes: `keymap.parse_key_combo / validate_combo / to_pyautogui_mac`
- Produces: `MacPlatformManager.press(key, context=None)`；`ctrl*/win*/command*` → pyautogui `command`

- [ ] **Step 1: 写失败测试**

创建 `tests/platforms/test_mac_press.py`：

```python
"""Mac 平台按键统一格式测试（ctrl 自动映射 command）。"""

from unittest.mock import MagicMock, patch

import pytest

from worker.config import PlatformConfig
from worker.platforms.mac import MacPlatformManager


def test_ctrl_c_maps_to_command_c() -> None:
    manager = MacPlatformManager(PlatformConfig())
    with patch("worker.platforms.mac.pyautogui") as pag:
        manager.press("ctrl+c")
    pag.hotkey.assert_called_once_with("command", "c")


def test_single_key_press(monkeypatch) -> None:
    manager = MacPlatformManager(PlatformConfig())
    monkeypatch.setattr("worker.platforms.keymap._pyautogui_keys", lambda: {"enter"})
    with patch("worker.platforms.mac.pyautogui") as pag:
        manager.press("enter")
    pag.press.assert_called_once_with("enter")


def test_legacy_style_rejected() -> None:
    manager = MacPlatformManager(PlatformConfig())
    with pytest.raises(ValueError, match="不是修饰键"):
        manager.press("Control+C")


def test_unknown_key_rejected(monkeypatch) -> None:
    manager = MacPlatformManager(PlatformConfig())
    monkeypatch.setattr("worker.platforms.keymap._pyautogui_keys", lambda: {"enter"})
    with pytest.raises(ValueError, match="未知按键名"):
        manager.press("arrowup")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `./venv/Scripts/python.exe -m pytest tests/platforms/test_mac_press.py -v`
Expected: FAIL（当前无别名处理、无校验）

- [ ] **Step 3: 修改 mac.py**

3a. 顶部 import 区（`from worker.platforms.base import PlatformManager` 附近）加入：

```python
from worker.platforms.keymap import parse_key_combo, to_pyautogui_mac, validate_combo
```

3b. 替换 `press`（原 135-141 行）：

```python
    def press(self, key: str, context: Any = None) -> None:
        """按键。统一 pyautogui 风格，支持组合键；ctrl/win 自动按 command 注入。"""
        parts = parse_key_combo(key)
        validate_combo(parts)
        keys = [to_pyautogui_mac(part) for part in parts]
        if len(keys) > 1:
            pyautogui.hotkey(*keys)
        else:
            pyautogui.press(keys[0])
```

- [ ] **Step 4: 运行测试确认通过**

Run: `./venv/Scripts/python.exe -m pytest tests/platforms/test_mac_press.py -v`
Expected: 4 passed

- [ ] **Step 5: Lint + 提交**

```bash
./venv/Scripts/python.exe -m ruff check worker/platforms/mac.py tests/platforms/test_mac_press.py
git add worker/platforms/mac.py tests/platforms/test_mac_press.py
git commit -m "feat: mac press 接入统一键名，ctrl 自动按 command 注入"
```

---

### Task 5: android.py 补统一别名 + 组合键报错

**Files:**
- Modify: `worker/platforms/android.py`（`KEY_MAP` 后加别名表约 75 行、`press` 309-328 行）
- Test: `tests/platforms/test_android_press.py`

**Interfaces:**
- Consumes: 无新依赖（沿用 `.upper()` 查表）
- Produces: `AndroidPlatformManager.press(key, context=None)`；支持 `esc/up/down/left/right/del`；含 `+` 报错

- [ ] **Step 1: 写失败测试**

创建 `tests/platforms/test_android_press.py`：

```python
"""Android 按键统一格式测试。"""

from unittest.mock import MagicMock

import pytest

from worker.config import PlatformConfig
from worker.platforms.android import AndroidPlatformManager


def _device_with_manager():
    manager = AndroidPlatformManager(PlatformConfig())
    device = MagicMock()
    return manager, device


def test_esc_alias_maps_to_keycode_111() -> None:
    manager, device = _device_with_manager()
    manager.press("esc", device)
    device.press.assert_called_once_with(111)


def test_arrow_aliases() -> None:
    manager, device = _device_with_manager()
    for key, code in [("up", 19), ("down", 20), ("left", 21), ("right", 22)]:
        manager.press(key, device)
        device.press.assert_called_with(code)


def test_common_keys_still_work() -> None:
    manager, device = _device_with_manager()
    manager.press("enter", device)
    device.press.assert_called_once_with(66)


def test_combo_rejected() -> None:
    manager, device = _device_with_manager()
    with pytest.raises(ValueError, match="不支持组合键"):
        manager.press("ctrl+a", device)


def test_unknown_key_reports_supported_list() -> None:
    manager, device = _device_with_manager()
    with pytest.raises(ValueError, match="Supported keys"):
        manager.press("f5", device)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `./venv/Scripts/python.exe -m pytest tests/platforms/test_android_press.py -v`
Expected: FAIL（`esc`/`up` 当前 Unsupported、`ctrl+a` 走查表报普通 Unsupported）

- [ ] **Step 3: 修改 android.py**

3a. 在 `KEY_MAP` 定义结束后（同层级，类属性区）加入：

```python
    # 统一 pyautogui 风格别名（press 对外契约，见 keymap.py）
    _KEY_ALIASES = {
        "ESC": "ESCAPE",
        "UP": "ARROWUP",
        "DOWN": "ARROWDOWN",
        "LEFT": "ARROWLEFT",
        "RIGHT": "ARROWRIGHT",
        "DEL": "DELETE",
    }
```

3b. 替换 `press`（原 309-328 行）：

```python
    def press(self, key: str, context: Any = None) -> None:
        """按键。统一小写风格（enter、esc、up…）；Android 不支持组合键。

        支持的按键：home、back、menu、search、enter、escape、tab、backspace、
        delete、up、down、left、right、volume_up、volume_down、power、camera、
        media_* 等，详见 KEY_MAP；数字 KeyCode 直传。
        """
        device = context or self._device_clients.get(self._current_device)
        if not device:
            raise RuntimeError("No device context")
        if "+" in (key or ""):
            raise ValueError(
                f"Unsupported key '{key}': Android 不支持组合键（无物理修饰键盘）。"
                "请拆分为多次 press，或使用 OCR 点击完成对应操作。"
            )
        key_name = (key or "").upper()
        key_name = self._KEY_ALIASES.get(key_name, key_name)
        key_code = self.KEY_MAP.get(key_name)

        if key_code:
            device.press(key_code)
        elif key and key.isdigit():
            # 支持直接传入 KeyCode 数字
            device.press(int(key))
        else:
            supported = ", ".join(sorted(self.KEY_MAP.keys()))
            raise ValueError(f"Unsupported key '{key}' for Android. Supported keys: {supported}")
```

- [ ] **Step 4: 运行测试确认通过**

Run: `./venv/Scripts/python.exe -m pytest tests/platforms/test_android_press.py -v`
Expected: 5 passed

- [ ] **Step 5: Lint + 提交**

```bash
./venv/Scripts/python.exe -m ruff check worker/platforms/android.py tests/platforms/test_android_press.py
git add worker/platforms/android.py tests/platforms/test_android_press.py
git commit -m "feat: android press 补统一别名并显式拒绝组合键"
```

---

### Task 6: 内部调用修正 + api.yaml 文档重写

**Files:**
- Modify: `worker/actions/coordinate.py:245`（`press("Control+v")` → `press("ctrl+v")`）
- Modify: `api.yaml`（`key` 字段说明约 1606-1644 行、press 示例约 1969-1988 行）

**Interfaces:**
- Consumes: Task 2-4 完成后的平台 press（`ctrl+v` 在 web/mac/win 上均正确注入）
- Produces: 对外文档与实现一致

- [ ] **Step 1: 修正内部调用**

`worker/actions/coordinate.py` 粘贴动作中：

```python
            platform.press("ctrl+v", context)
```

（原为 `platform.press("Control+v", context)`；该行在 mac 上原本静默失效，Task 4 后 `ctrl+v` → `command+v` 正确。）

- [ ] **Step 2: 重写 api.yaml 的 key 字段说明**

替换 `api.yaml` 中 `key:` 字段的 description（约 1606-1644 行）为：

```yaml
        key:
          type: string
          description: |
            按键名称，仅 press 动作必填。所有平台统一 pyautogui 风格：
            小写命名键，组合键用 "+" 分隔、修饰键在前、最后一个为功能键；
            单字符保留原大小写（"A" 等价 shift+a）。

            【通用键名】桌面端（Web/Windows/Mac）+ 移动端公共子集：
            - 修饰键: ctrl、alt、shift、win（mac 上 ctrl/win 自动按 command 注入）
            - 功能键: enter、tab、esc、backspace、del、space、up、down、left、right、
              home、end、pageup、pagedown、insert、capslock、f1-f12
            - 字母/数字/符号: 单字符直传，如 a、5、@
            - 组合键示例: ctrl+a、ctrl+shift+t、alt+f4、win+e

            【Windows】cmd/command 为 mac 语义不可用（请用 ctrl 或 win）

            【Android 特有键】home、back、menu、search、volume_up（volumeup）、
            power、camera、media_play_pause/media_stop/media_next/media_previous、
            数字 KeyCode 直传（如 "26" 电源键）；不支持组合键

            【鸿蒙 mobile/PC】通用键名均可用（大小写不敏感），另有 home、back、
            power、volume_up/volume_down/volume_mute、数字原始键码
            （OpenHarmony KeyCode，如 "2055" 退格）；鸿蒙 PC 组合键要求
            修饰键在前、功能键在最后

            【iOS】仅 home、volume_up、volume_down（取决于设备机型；
            Back/Enter 请用 OCR 点击导航按钮）

            注意：Playwright 风格（Control+A、ArrowUp、Meta）已不再支持，
            传错会报错并提示正确写法。
```

- [ ] **Step 3: 重写 api.yaml 的 press 示例**

替换 `api.yaml` press 示例注释块（约 1969-2003 行，从 `# press - 按键操作` 到 iOS `# 注意: iOS 不支持 Back、Enter、Lock 等按键` 行）为：

```yaml
  # press - 按键操作（统一 pyautogui 风格：小写、组合键修饰键在前）
  # 示例 1: 按回车键（桌面端）
  # {"action_type": "press", "key": "enter"}
  # 示例 2: 按 Escape 键（桌面端）
  # {"action_type": "press", "key": "esc"}
  # 示例 3: 按下箭头键（桌面端）
  # {"action_type": "press", "key": "down"}
  # 示例 4: Ctrl+A 全选（桌面端组合键，mac 上自动按 command+a 注入）
  # {"action_type": "press", "key": "ctrl+a"}

  # 鸿蒙按键示例（通用键名可用，大小写不敏感）:
  # 示例 5: 退格键（鸿蒙 PC，uinput -K 通道）
  # {"action_type": "press", "key": "backspace", "platform": "harmony_pc"}
  # 示例 6: Ctrl+C 复制（鸿蒙 PC 组合键）
  # {"action_type": "press", "key": "ctrl+c", "platform": "harmony_pc"}
  # 示例 7: Ctrl+Shift+T 三键组合（鸿蒙 PC）
  # {"action_type": "press", "key": "ctrl+shift+t", "platform": "harmony_pc"}

  # 移动端按键示例（Android）:
  # 示例 8: 返回键
  # {"action_type": "press", "key": "back"}
  # 示例 9: 主屏幕键
  # {"action_type": "press", "key": "home"}
  # 示例 10: 音量增加
  # {"action_type": "press", "key": "volume_up"}
  # 示例 11: 电源键/锁屏
  # {"action_type": "press", "key": "power"}

  # 移动端按键示例（iOS）:
  # 示例 12: 主屏幕键
  # {"action_type": "press", "key": "home"}
  # 示例 13: 音量增加
  # {"action_type": "press", "key": "volume_up"}
  # 注意: iOS 不支持 back、enter、lock 等按键
```

- [ ] **Step 4: 全仓回归**

```bash
./venv/Scripts/python.exe -m ruff check worker/platforms/keymap.py worker/platforms/web.py worker/platforms/windows.py worker/platforms/mac.py worker/platforms/android.py worker/actions/coordinate.py
./venv/Scripts/python.exe -m pytest tests/platforms tests/test_windows_press.py -v
./venv/Scripts/python.exe -m black --check worker/platforms/keymap.py worker/platforms/web.py worker/platforms/windows.py worker/platforms/mac.py worker/platforms/android.py worker/actions/coordinate.py
```

Expected: ruff 0 错误；pytest 全部 passed；black 无 diff（如有 diff 则 `black <文件>` 后重跑测试）

- [ ] **Step 5: 提交**

```bash
git add worker/actions/coordinate.py api.yaml
git commit -m "docs: press 按键统一 pyautogui 风格，重写 api.yaml 按键文档"
```

---

### Task 7: 全量回归收尾

**Files:**
- 无新改动（仅验证；如回归发现问题，回到对应任务修复后重跑）

- [ ] **Step 1: 全量测试**

```bash
./venv/Scripts/python.exe -m pytest tests/ -x -q
```

Expected: 全部 passed（harmony/ios 等未改动平台无回归；`tests/test_harmony_platform.py` 中如有用大写 `Control` 的 harmony 用例不受影响——harmony 解析未改动）

- [ ] **Step 2: 键名一致性抽查（真实 pyautogui，无浏览器）**

```bash
./venv/Scripts/python.exe -I -c "
from worker.platforms.keymap import parse_key_combo, validate_combo, to_pyautogui_win
for k in ['ctrl+a', 'ctrl+shift+t', 'alt+f4', 'win+e', 'enter', 'esc', 'up', 'A']:
    parts = parse_key_combo(k)
    validate_combo(parts)
    print(k, '->', [to_pyautogui_win(p) for p in parts])
"
```

Expected: 全部解析成功，`win+e -> ['winleft', 'e']`、`A -> ['A']`，无异常。

- [ ] **Step 3: 更新任务状态并提交（如有修复）**

```bash
git status
# 如有修复提交：
git add -A && git commit -m "fix: 统一按键规范回归修复"
```

---

## Self-Review 记录

- Spec 覆盖：统一格式（Task 1）、web 双层（Task 2）、windows 校验（Task 3）、mac 映射（Task 4）、android 别名（Task 5）、harmony/ios 不改（无需任务）、内部调用 + api.yaml（Task 6）、回归（Task 7）——全覆盖。
- 类型一致性：`parse_key_combo -> list[str]`、`_system_press(key, parts)`、`to_playwright_combo(parts, on_mac: bool)` 各任务引用一致。
- 已知边界：`+` 键本身无法表达（`+` 是分隔符），由 api.yaml 文档约束用 `input_text`/paste；harmony 保持大小写不敏感（超集兼容，不违反统一契约）。

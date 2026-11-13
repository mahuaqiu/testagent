"""统一按键名解析与平台映射（press 动作对外唯一契约）。

对外格式：pyautogui 风格 —— 小写命名键、"+" 组合键、修饰键在前、
最后一段为功能键；单字符保留原大小写（'A' 等价 shift+a）。
Playwright 风格（Control+A、ArrowUp）不再支持，解析时直接报错并提示。
"""

# 修饰键别名（小写）→ 统一修饰键语义名（ctrl/alt/shift/win/command）
_MODIFIER_ALIASES = {
    "ctrl": "ctrl",
    "ctrlleft": "ctrl",
    "ctrlright": "ctrl",
    "alt": "alt",
    "altleft": "alt",
    "altright": "alt",
    "altgr": "alt",
    "shift": "shift",
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
            else:  # win / command（on_mac）
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

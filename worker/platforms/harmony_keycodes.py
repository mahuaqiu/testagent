"""鸿蒙按键码唯一映射。

键码值取自 OpenHarmony 官方 KeyCode 表
（@ohos.multimodalInput.keyCode，与 key_event.h 一致），按键注入命令
格式与官方 HOScrcpy DEMO 对齐：``uinput -K -d <code> -u <code>``；
组合键按 "修饰键按下 -> 功能键按下并释放 -> 修饰键反序释放" 注入。
"""

HARMONY_KEY_MAP: dict[str, int] = {
    # ---- 系统/导航键（移动端物理键）----
    "HOME": 1,
    "BACK": 2,
    "POWER": 18,
    "VOLUME_UP": 16,
    "VOLUME_DOWN": 17,
    "VOLUME_MUTE": 22,
    "MENU": 2067,
    # ---- 方向键 ----
    "DPAD_UP": 2012,
    "DPAD_DOWN": 2013,
    "DPAD_LEFT": 2014,
    "DPAD_RIGHT": 2015,
    "DPAD_CENTER": 2016,
    # ---- 编辑与导航 ----
    "ENTER": 2054,
    "BACKSPACE": 2055,
    "DELETE": 2071,
    "SPACE": 2050,
    "TAB": 2049,
    "ESCAPE": 2070,
    "INSERT": 2083,
    "PAGE_UP": 2068,
    "PAGE_DOWN": 2069,
    "MOVE_HOME": 2081,
    "MOVE_END": 2082,
    "CAPS_LOCK": 2074,
    "NUM_LOCK": 2102,
    "SCROLL_LOCK": 2075,
    # ---- 功能键 F1-F12 ----
    "F1": 2090,
    "F2": 2091,
    "F3": 2092,
    "F4": 2093,
    "F5": 2094,
    "F6": 2095,
    "F7": 2096,
    "F8": 2097,
    "F9": 2098,
    "F10": 2099,
    "F11": 2100,
    "F12": 2101,
    # ---- 字母键 A-Z ----
    "A": 2017,
    "B": 2018,
    "C": 2019,
    "D": 2020,
    "E": 2021,
    "F": 2022,
    "G": 2023,
    "H": 2024,
    "I": 2025,
    "J": 2026,
    "K": 2027,
    "L": 2028,
    "M": 2029,
    "N": 2030,
    "O": 2031,
    "P": 2032,
    "Q": 2033,
    "R": 2034,
    "S": 2035,
    "T": 2036,
    "U": 2037,
    "V": 2038,
    "W": 2039,
    "X": 2040,
    "Y": 2041,
    "Z": 2042,
    # ---- 数字键 0-9 ----
    "DIGIT_0": 2000,
    "DIGIT_1": 2001,
    "DIGIT_2": 2002,
    "DIGIT_3": 2003,
    "DIGIT_4": 2004,
    "DIGIT_5": 2005,
    "DIGIT_6": 2006,
    "DIGIT_7": 2007,
    "DIGIT_8": 2008,
    "DIGIT_9": 2009,
    # ---- 符号键（主键盘区）----
    "GRAVE": 2056,
    "MINUS": 2057,
    "EQUALS": 2058,
    "LEFT_BRACKET": 2059,
    "RIGHT_BRACKET": 2060,
    "BACKSLASH": 2061,
    "SEMICOLON": 2062,
    "QUOTE": 2063,
    "SLASH": 2064,
    "AT": 2065,
    "PLUS": 2066,
    "COMMA": 2043,
    "PERIOD": 2044,
    "STAR": 2010,
    "POUND": 2011,
    # ---- 小键盘 ----
    "NUMPAD_0": 2103,
    "NUMPAD_1": 2104,
    "NUMPAD_2": 2105,
    "NUMPAD_3": 2106,
    "NUMPAD_4": 2107,
    "NUMPAD_5": 2108,
    "NUMPAD_6": 2109,
    "NUMPAD_7": 2110,
    "NUMPAD_8": 2111,
    "NUMPAD_9": 2112,
    "NUMPAD_DIVIDE": 2113,
    "NUMPAD_MULTIPLY": 2114,
    "NUMPAD_SUBTRACT": 2115,
    "NUMPAD_ADD": 2116,
    "NUMPAD_DOT": 2117,
    "NUMPAD_COMMA": 2118,
    "NUMPAD_ENTER": 2119,
    "NUMPAD_EQUALS": 2120,
}

# 组合键修饰键（官方 DEMO shift 组合使用 2047，其余为 KeyCode 表左键值）
HARMONY_MODIFIER_KEY_MAP: dict[str, int] = {
    "SHIFT": 2047,
    "CTRL": 2072,
    "ALT": 2045,
    "META": 2076,
}

# 常见别名 -> 标准键名（兼容 Playwright / pyautogui / 各平台键名习惯）
HARMONY_KEY_ALIASES: dict[str, str] = {
    "CONTROL": "CTRL",
    "ESC": "ESCAPE",
    "RETURN": "ENTER",
    "BACK_SPACE": "BACKSPACE",
    "BKSP": "BACKSPACE",
    "DEL": "DELETE",
    "SPACEBAR": "SPACE",
    "WIN": "META",
    "WINDOWS": "META",
    "CMD": "META",
    "COMMAND": "META",
    "SUPER": "META",
    "UP": "DPAD_UP",
    "DOWN": "DPAD_DOWN",
    "LEFT": "DPAD_LEFT",
    "RIGHT": "DPAD_RIGHT",
    "ARROWUP": "DPAD_UP",
    "ARROWDOWN": "DPAD_DOWN",
    "ARROWLEFT": "DPAD_LEFT",
    "ARROWRIGHT": "DPAD_RIGHT",
    "PAGEUP": "PAGE_UP",
    "PAGEDOWN": "PAGE_DOWN",
    "END": "MOVE_END",
    "NUMPAD_DECIMAL": "NUMPAD_DOT",
    "NUMPAD0": "NUMPAD_0",
    "NUMPAD1": "NUMPAD_1",
    "NUMPAD2": "NUMPAD_2",
    "NUMPAD3": "NUMPAD_3",
    "NUMPAD4": "NUMPAD_4",
    "NUMPAD5": "NUMPAD_5",
    "NUMPAD6": "NUMPAD_6",
    "NUMPAD7": "NUMPAD_7",
    "NUMPAD8": "NUMPAD_8",
    "NUMPAD9": "NUMPAD_9",
    "DIGIT0": "DIGIT_0",
    "DIGIT1": "DIGIT_1",
    "DIGIT2": "DIGIT_2",
    "DIGIT3": "DIGIT_3",
    "DIGIT4": "DIGIT_4",
    "DIGIT5": "DIGIT_5",
    "DIGIT6": "DIGIT_6",
    "DIGIT7": "DIGIT_7",
    "DIGIT8": "DIGIT_8",
    "DIGIT9": "DIGIT_9",
}


def resolve_harmony_key(name: str) -> tuple[str, int] | None:
    """把按键名（含别名，大小写不敏感）解析为 (标准名, 键码)。

    修饰键和普通键统一查询；无法识别时返回 ``None``。
    """
    normalized = name.strip().upper()
    if not normalized:
        return None
    normalized = HARMONY_KEY_ALIASES.get(normalized, normalized)
    if normalized in HARMONY_MODIFIER_KEY_MAP:
        return normalized, HARMONY_MODIFIER_KEY_MAP[normalized]
    if normalized in HARMONY_KEY_MAP:
        return normalized, HARMONY_KEY_MAP[normalized]
    return None

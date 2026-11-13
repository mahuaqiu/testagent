# 统一按键输入规范设计（press 动作跨平台统一为 pyautogui 风格）

日期：2026-11-13
状态：已确认（用户批准）

## 背景与问题

`press` 动作对外按键格式目前各平台不统一，写用例体验差且存在隐性 bug：

| 平台/层级 | 现状 |
|---|---|
| Web browser 层 | 直接透传 `page.keyboard.press()`，只认 Playwright 风格（`Control+A`、`ArrowUp`），不认 `ctrl+a` |
| Web system 层 | `_system_press` 仅映射单键；组合键 `Control+A` 被当作单个键名传给 `pyautogui.press()`，**静默无效（组合键全坏）** |
| Windows | pyautogui 风格（`ctrl+a`）；传 Playwright 风格时 pyautogui **静默忽略、不报错（用例假绿）** |
| Mac | pyautogui 原生、无别名处理；`Control+C` 静默无效；`actions/coordinate.py` 粘贴动作传 `Control+v`，在 mac 上实际失效 |
| 鸿蒙 | 大小写不敏感 + 别名（`ctrl+c`/`Control+C` 均可），已接近目标形态 |
| Android | `.upper()` 后查表（`enter`/`ENTER` 均可），但缺 `esc/up/del` 等统一别名，不支持组合键 |
| iOS | 仅硬件键 HOME/VOLUME_*，自有命名 |

另外 `api.yaml` 对 Web/Windows/Mac 的 `key` 字段文档写的是 Playwright 风格（`Control+A`、`Alt+F4`），在 Windows/Mac 上并不成立。

**已确认的决策**：
- 对外格式统一为 pyautogui 风格。
- mac 上 `ctrl` 自动按 `command` 注入（用例跨平台复用），非严格 pyautogui 原生语义。
- 严格统一：旧 Playwright 风格写法直接报错并在错误信息中提示正确写法，不做静默兼容。

## 设计

### 1. 统一对外格式（唯一契约）

- 全小写命名键；组合键用 `+` 分隔、修饰键在前、最后一个为功能键。
- 单字符（字母/数字/符号）保留原大小写：`'A'` 等价 shift+a，与 pyautogui 语义一致。
- 修饰键：`ctrl`、`alt`、`shift`、`win`。别名按运行平台归一：win 上 `win`/`windows`/`meta`/`super` → `winleft`（`cmd`/`command` 为 mac 语义，报错并提示用 `ctrl` 或 `win`）；mac 上 `cmd`/`command`/`meta`/`super`/`win` → `command`。
- 常用功能键：`enter`、`tab`、`esc`、`backspace`、`del`、`space`、`up`、`down`、`left`、`right`、`home`、`end`、`pageup`、`pagedown`、`insert`、`capslock`、`f1`-`f12`，以及 pyautogui 其余键名（`printscreen` 等）。
- 组合键示例：`ctrl+a`、`ctrl+shift+t`、`alt+f4`、`win+e`。
- **mac 运行时自动映射**：`ctrl*` → `command`、`win*` → `command`。web browser 层转 `Meta`，system/pyautogui 层转 `command`。web browser 层按 worker 运行 host 平台决定（darwin 时 ctrl→Meta）。
- **`home` 语义按平台**：桌面平台 = 键盘 Home 键；Android/鸿蒙/iOS = 主屏幕键（各平台自然语义，文档标注）。
- **平台特有键保留**：Android/鸿蒙 `home`/`back`/`menu`/`volume_up`/`power`/`camera`/`media_*` + 数字键码直传；iOS 仅 `home`/`volume_up`/`volume_down`。

### 2. 共享模块 `worker/platforms/keymap.py`（新增）

- `parse_key_combo(key: str) -> list[str]`：strip → `+` 拆分 → 空段报错 → 多字符统一小写、单字符保留原样。
- `validate_combo(parts, modifier_names)`：组合键（≥2 段）时前 n-1 段必须全为修饰键、末段必须为功能键（与鸿蒙现有规则一致）。
- `PYAUTOGUI_TO_PLAYWRIGHT: dict`：完整映射表（实测 Playwright `usKeyboardLayout` + `aliases`）：`ctrl→Control`、`alt→Alt`、`shift→Shift`、`win/winleft→Meta`、`enter→Enter`、`esc/escape→Escape`、`backspace→Backspace`、`del/delete→Delete`、`up→ArrowUp`、`down/left/right→Arrow*`、`space→Space`、`home→Home`、`end→End`、`pageup→PageUp`、`pagedown→PageDown`、`insert→Insert`、`capslock→CapsLock`、`printscreen→PrintScreen`、`scrolllock→ScrollLock`、`numlock→NumLock`、`pause→Pause`、`f1-f12→F1-F12`、符号键（`backquote→Backquote` 等）、`numpad*→Numpad*` 系列。
- `to_playwright_key(parts, on_mac: bool) -> str`：web browser 层用；无 Playwright 对应键（如 `volumeup`）报错并提示改用 `level=system`。
- pyautogui 键名校验直接使用运行时 `pyautogui.KEYBOARD_KEYS`（加上单字符规则），各平台不维护独立大表。

### 3. 各平台改造

- **web.py**：`press()` 统一走 parse/validate；browser 层 `to_playwright_key` 后 `page.keyboard.press()`；system 层重写 `_system_press`：单键 `pyautogui.press()`、组合键 `pyautogui.hotkey(*parts)`（修复组合键 bug）；键名按运行 host OS 适配（win：`winleft` 归一；darwin：ctrl/win→command）。`_system_input` 与粘贴相关内部 hotkey 通过 keymap 取 `ctrl+v`/`command+v`（按 host 平台）。
- **windows.py**：保留拆分逻辑、`ctrl+alt+delete` SAS 特例、`win/winleft/windows→winleft` 归一；改为统一校验，未知键报错（消除静默假绿）。
- **mac.py**：新增 `ctrl*/ctrlleft/ctrlright→command`、`win*→command` 映射 + 统一校验；修复粘贴动作在 mac 上隐性失效。
- **android.py**：补统一别名 `esc→ESCAPE`、`up/down/left/right→ARROW*`、`del→DELETE`（现有 `.upper()` 查表流程不变）；含 `+` 的组合键明确报错"Android 不支持组合键"。
- **harmony.py**：已符合统一格式（大小写不敏感 + 别名已覆盖公共子集），不改代码。
- **ios.py**：不改。
- **actions/coordinate.py**：内部 `platform.press("Control+v")` → `platform.press("ctrl+v")`。
- **actions/unlock.py**：内部调用（`ctrl+a`/`BACKSPACE`/`ENTER`）已是统一风格，不改。

### 4. 错误处理（全平台 fail fast）

统一错误信息三要素：原始输入、原因、正确写法。检测到旧 Playwright 风格（`Control+A`、`ArrowUp` 等）时直接给出对应小写写法：

```
Unsupported key 'Control+A': 请使用统一小写格式，如 'ctrl+a'。
支持: ctrl/alt/shift/win + enter/esc/up/f1-f12/...
```

- 组合键违规（`a+ctrl`、`ctrl+`、`ctrl+a+b`）：报错并说明"修饰键在前、最后为功能键"。
- web browser 层不支持注入的键：提示改用 `level=system`。

### 5. api.yaml 更新

`key` 字段说明重写：统一格式（小写 pyautogui 风格、`+` 组合、修饰键在前、mac 自动 ctrl→command）+ 各平台特有键清单（Android/鸿蒙/iOS）；顶层示例全部改为 `{"action_type": "press", "key": "ctrl+a"}` 风格。

### 6. 测试

- 新增 `tests/platforms/test_keymap.py`：parse/validate/映射表单测（含旧格式报错文案）。
- web：browser 层 `ctrl+a→Control+a`、`esc→Escape`；system 层组合键走 `hotkey`；darwin host ctrl→Meta/command。
- windows：未知键与 `Control+A` 报错提示；SAS 与 `win+e→winleft` 回归（`tests/test_windows_press.py`）。
- mac：`ctrl+c→command+c`。
- android：`esc/up` 别名生效、组合键报错。

### 7. 兼容性影响

- 已按 Playwright 风格写的 web 用例（`Control+A`）将开始报错，按提示改小写即可。
- Windows/Mac 上以前静默无效的错误写法变为显式报错（属修 bug，用例问题会提前暴露）。

## 不做的事（Non-goals）

- 不做静默双格式兼容。
- 不引入语义化按键层（select_all 等）。
- 不改 `input`/文本输入行为（文本本身就是平台无关的）。
- 不改 iOS 按键集。

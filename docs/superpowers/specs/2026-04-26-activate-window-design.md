---
name: activate_window
description: Windows/Mac 窗口激活功能设计
type: project
---

# activate_window 窗口激活功能设计

## 概述

新增 `activate_window` action，用于 Windows/Mac 平台将指定窗口带到前台并获取焦点。

## 需求

### 功能目标

- 将指定窗口带到前台并成为活动窗口
- 支持按窗口标题定位（包含匹配）
- 支持按进程名定位

### 使用场景

用户在自动化测试中自行调用，用于：
- 确保目标应用窗口处于前台
- 多窗口轮换操作
- 窗口状态恢复

## API 定义

### action_type

`activate_window`

### 参数

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `value` | string | 是 | - | 窗口标题或进程名（包含匹配） |
| `match_by` | string | 否 | `title` | 定位方式，可选 `title` 或 `process` |

### 使用示例

```json
// 按标题激活（包含匹配）
{"action_type": "activate_window", "value": "计算器"}

// 按进程名激活
{"action_type": "activate_window", "value": "notepad.exe", "match_by": "process"}
```

## 架构设计

### 文件结构

```
worker/actions/
├── window.py          # 新增：ActivateWindowAction 类
├── __init__.py        # 修改：注册新 action
worker/task/
├── action.py          # 修改：新增 match_by 字段
worker/platforms/
├── base.py            # 修改：BASE_SUPPORTED_ACTIONS 添加 activate_window
```

### 组件设计

#### ActivateWindowAction 类

继承 `BaseActionExecutor`，实现跨平台窗口激活。

```python
class ActivateWindowAction(BaseActionExecutor):
    name = "activate_window"
    requires_context = False

    def execute(self, platform, action, context) -> ActionResult:
        value = action.value
        match_by = action.match_by or "title"

        if platform.platform == "windows":
            return self._activate_windows(value, match_by)
        elif platform.platform == "mac":
            return self._activate_mac(value, match_by)
        else:
            return ActionResult(status=FAILED, error="Platform not supported")
```

#### Windows 实现

使用 `pygetwindow` 库（pyautogui 的依赖，无需新增依赖）。

```python
def _activate_windows(self, value: str, match_by: str) -> ActionResult:
    import pygetwindow as gw

    try:
        if match_by == "title":
            windows = gw.getWindowsWithTitle(value)
            if not windows:
                return ActionResult(status=FAILED, error=f"Window not found: {value}")
            windows[0].activate()
            return ActionResult(status=SUCCESS, output=f"Activated window: {value}")
        else:  # process
            # 通过进程名查找窗口
            windows = gw.getAllWindows()
            for win in windows:
                if win._process.lower().contains(value.lower()):
                    win.activate()
                    return ActionResult(status=SUCCESS, output=f"Activated window: {value}")
            return ActionResult(status=FAILED, error=f"Window not found by process: {value}")
    except Exception as e:
        return ActionResult(status=FAILED, error=f"Failed to activate window: {e}")
```

#### Mac 实现

使用 AppleScript 激活窗口。

```python
def _activate_mac(self, value: str, match_by: str) -> ActionResult:
    try:
        if match_by == "title":
            # 通过窗口标题激活
            cmd = f'''
            tell application "System Events"
                set frontmost of (first window whose title contains "{value}") to true
            end tell
            '''
            result = subprocess.run(["osascript", "-e", cmd], capture_output=True, text=True)
            if result.returncode != 0:
                return ActionResult(status=FAILED, error=f"Window not found: {value}")
            return ActionResult(status=SUCCESS, output=f"Activated window: {value}")
        else:  # process
            # 通过应用名激活
            cmd = f'tell application "{value}" to activate'
            result = subprocess.run(["osascript", "-e", cmd], capture_output=True, text=True)
            if result.returncode != 0:
                return ActionResult(status=FAILED, error=f"Application not found: {value}")
            return ActionResult(status=SUCCESS, output=f"Activated application: {value}")
    except Exception as e:
        return ActionResult(status=FAILED, error=f"Failed to activate: {e}")
```

## 数据模型修改

### Action 类新增字段

在 `worker/task/action.py` 中：

```python
match_by: str | None = None  # 定位方式："title" 或 "process"
```

### from_dict 和 to_dict 方法

```python
# from_dict 添加
match_by=data.get("match_by")

# to_dict 添加
if self.match_by is not None:
    result["match_by"] = self.match_by
```

## Action 注册

在 `worker/actions/__init__.py` 中：

```python
from worker.actions.window import ActivateWindowAction

# _register_all_actions 中添加
ActionRegistry.register(ActivateWindowAction())

# __all__ 中添加
"ActivateWindowAction",
```

## BASE_SUPPORTED_ACTIONS 更新

在 `worker/platforms/base.py` 中：

```python
BASE_SUPPORTED_ACTIONS: Set[str] = {
    ...,
    "activate_window",  # 窗口激活（Windows/Mac）
}
```

## 错误处理

### 错误场景

| 场景 | 错误信息 | ActionStatus |
|------|----------|--------------|
| value 为空 | "value is required" | FAILED |
| 未找到窗口 | "Window not found: {value}" | FAILED |
| 平台不支持 | "activate_window is not supported on {platform}" | FAILED |
| 激活失败 | "Failed to activate window: {error}" | FAILED |

### 失败行为

- 返回 FAILED 状态，不抛出异常
- 不获取截图
- 提供清晰错误信息

## 文档更新

### CLAUDE.md

在"动作类型"章节添加：

```markdown
### activate_window 激活窗口（Windows/Mac 专用）

将指定窗口带到前台并获取焦点。支持按标题或进程名定位。

**参数**：
| 参数 | 说明 |
|------|------|
| `value` | 窗口标题或进程名（包含匹配） |
| `match_by` | 定位方式，默认 "title"，可选 "process" |

**使用示例**：
```json
{"action_type": "activate_window", "value": "计算器"}
{"action_type": "activate_window", "value": "notepad.exe", "match_by": "process"}
```
```

## 实现要点

### Windows 注意事项

1. `pygetwindow.getWindowsWithTitle` 使用包含匹配
2. `activate()` 方法将窗口带到前台
3. 需处理窗口最小化状态（activate 会自动恢复）

### Mac 注意事项

1. AppleScript 需要辅助功能权限
2. 窗口标题匹配可能需要额外权限
3. 应用名激活更可靠

## 验证要点

1. Windows 标题激活：打开计算器，测试 `{"action_type": "activate_window", "value": "计算器"}`
2. Windows 进程激活：测试 `{"action_type": "activate_window", "value": "notepad.exe", "match_by": "process"}`
3. Mac 标题激活：测试 Finder 窗口
4. Mac 进程激活：测试 Safari

## 风险评估

- **低风险**：不新增依赖，使用现有库
- **兼容性**：Windows/Mac API 行为可能略有差异
- **权限**：Mac 需要 Auxiliary Permissions

## 实现顺序

1. 新增 `match_by` 字段到 Action 数据模型
2. 创建 `worker/actions/window.py`
3. 注册 Action 到 `__init__.py`
4. 更新 `BASE_SUPPORTED_ACTIONS`
5. 更新 CLAUDE.md 文档
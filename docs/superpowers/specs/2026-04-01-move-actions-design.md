# Move Actions 设计文档

## 概述

新增三个鼠标移动动作：`move`、`image_move`、`ocr_move`。将鼠标移动到指定位置，不执行点击操作。适用于悬停、预定位等场景。

**支持平台**：Web、Windows、Mac（桌面端）
**不支持平台**：Android、iOS（返回错误）

---

## Action 定义

### 1. `move` — 坐标移动

将鼠标移动到指定坐标位置。

| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `x` | int | 是 | 目标 X 坐标 |
| `y` | int | 是 | 目标 Y 坐标 |
| `offset` | dict | 否 | 坐标偏移 `{"x": 10, "y": 5}` |

**示例**：
```json
{
  "action_type": "move",
  "x": 100,
  "y": 200
}
```

**带偏移示例**：
```json
{
  "action_type": "move",
  "x": 100,
  "y": 200,
  "offset": {"x": 10, "y": 5}
}
```

---

### 2. `image_move` — 图像匹配后移动

通过图像模板匹配定位后，移动鼠标到目标位置。

| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `image_base64` | string | 是 | 图像模板 base64 编码 |
| `threshold` | float | 否 | 匹配阈值（默认 0.8） |
| `index` | int | 否 | 选择第几个匹配结果（默认 0） |
| `offset` | dict | 否 | 坐标偏移 `{"x": 10, "y": 5}` |

**示例**：
```json
{
  "action_type": "image_move",
  "image_base64": "<base64_encoded_image>",
  "threshold": 0.85,
  "offset": {"x": 5, "y": 0}
}
```

---

### 3. `ocr_move` — OCR 定位后移动

通过 OCR 文字识别定位后，移动鼠标到目标位置。

| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `value` | string | 是 | 目标文字 |
| `match_mode` | string | 否 | OCR 匹配模式（默认 exact） |
| `index` | int | 否 | 选择第几个匹配结果（默认 0） |
| `offset` | dict | 否 | 坐标偏移 `{"x": 10, "y": 5}` |

**示例**：
```json
{
  "action_type": "ocr_move",
  "value": "登录",
  "match_mode": "exact",
  "offset": {"x": 20, "y": 0}
}
```

---

## 实现方案

### 文件修改清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `worker/platforms/base.py` | 修改 | 添加抽象方法 `move()` |
| `worker/platforms/web.py` | 修改 | 实现 `move()` 使用 Playwright |
| `worker/platforms/windows.py` | 修改 | 实现 `move()` 使用 pyautogui |
| `worker/platforms/mac.py` | 修改 | 实现 `move()` 使用 pyautogui |
| `worker/platforms/android.py` | 修改 | 实现 `move()` 返回错误 |
| `worker/platforms/ios.py` | 修改 | 实现 `move()` 返回错误 |
| `worker/actions/coordinate.py` | 修改 | 添加 `MoveAction` 执行器 |
| `worker/actions/image.py` | 修改 | 添加 `ImageMoveAction` 执行器 |
| `worker/actions/ocr.py` | 修改 | 添加 `OcrMoveAction` 执行器 |
| `worker/actions/__init__.py` | 修改 | 注册三个新 Action |

---

### 1. 平台基类接口

在 `worker/platforms/base.py` 添加抽象方法：

```python
@abstractmethod
def move(self, x: int, y: int, context: Any = None) -> None:
    """
    移动鼠标到指定坐标（不点击）。

    Args:
        x: X 坐标
        y: Y 坐标
        context: 执行上下文（可选）
    """
    pass
```

---

### 2. 桌面平台实现

#### Web 平台 (`worker/platforms/web.py`)

```python
def move(self, x: int, y: int, context: Any = None) -> None:
    """移动鼠标到指定坐标。"""
    page = context or self._current_page
    if page:
        _run_async(page.mouse.move(x, y))
```

#### Windows/Mac 平台

```python
def move(self, x: int, y: int, context: Any = None) -> None:
    """移动鼠标到指定坐标。"""
    pyautogui.moveTo(x, y)
```

---

### 3. 移动端平台处理

Android 和 iOS 平台的 `move()` 方法抛出异常，ActionExecutor 捕获后返回错误 ActionResult。

```python
def move(self, x: int, y: int, context: Any = None) -> None:
    """移动鼠标（移动端不支持）。"""
    raise NotImplementedError("move action is not supported on mobile platforms")
```

---

### 4. ActionExecutor 实现

#### MoveAction (`worker/actions/coordinate.py`)

```python
class MoveAction(BaseActionExecutor):
    """坐标移动。"""

    name = "move"

    def execute(self, platform, action, context=None) -> ActionResult:
        if action.x is None or action.y is None:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error="x and y coordinates are required",
            )

        # 应用偏移
        x, y = self._apply_offset(action.x, action.y, action.offset)

        try:
            platform.move(x, y, context)
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.SUCCESS,
                output=f"Moved to ({x}, {y})",
            )
        except NotImplementedError as e:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error=str(e),
            )
```

#### ImageMoveAction (`worker/actions/image.py`)

与 `ImageClickAction` 相同的定位逻辑，但调用 `platform.move()` 而非 `platform.click()`。

#### OcrMoveAction (`worker/actions/ocr.py`)

与 `OcrClickAction` 相同的定位逻辑，但调用 `platform.move()` 而非 `platform.click()`。

---

### 5. Action 注册

在 `worker/actions/__init__.py` 注册三个新 Action：

```python
from worker.actions.coordinate import MoveAction
from worker.actions.image import ImageMoveAction
from worker.actions.ocr import OcrMoveAction

ActionRegistry.register(MoveAction())
ActionRegistry.register(ImageMoveAction())
ActionRegistry.register(OcrMoveAction())
```

---

## 测试要点

1. **桌面端测试**：验证鼠标确实移动到目标位置，无点击行为
2. **移动端测试**：验证返回正确的错误信息
3. **offset 测试**：验证偏移量正确应用
4. **index 测试**：验证多个匹配结果时选择正确的目标

---

## 文档更新

需同步更新 `CLAUDE.md` 中的动作类型说明，添加三个新 action 的描述。
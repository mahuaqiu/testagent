# Move Actions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增三个鼠标移动动作 `move`、`image_move`、`ocr_move`，支持桌面端（Web/Windows/Mac），移动端返回错误。

**Architecture:**
- 在平台基类添加 `move()` 抽象方法
- 桌面平台实现鼠标移动（Playwright/pyautogui）
- 移动端抛出 NotImplementedError
- 新增三个 ActionExecutor 处理执行逻辑

**Tech Stack:** Playwright (Web), pyautogui (Windows/Mac), OCR/图像识别服务

---

## 文件结构

| 文件 | 责任 |
|------|------|
| `worker/platforms/base.py` | 定义 `move()` 抽象方法；更新 BASE_SUPPORTED_ACTIONS |
| `worker/platforms/web.py` | 实现 Web 平台鼠标移动 |
| `worker/platforms/windows.py` | 实现 Windows 平台鼠标移动 |
| `worker/platforms/mac.py` | 实现 Mac 平台鼠标移动 |
| `worker/platforms/android.py` | 抛出 NotImplementedError |
| `worker/platforms/ios.py` | 抛出 NotImplementedError |
| `worker/actions/coordinate.py` | MoveAction 执行器 |
| `worker/actions/image.py` | ImageMoveAction 执行器 |
| `worker/actions/ocr.py` | OcrMoveAction 执行器 |
| `worker/actions/__init__.py` | 注册 Action；更新 __all__ |
| `CLAUDE.md` | 文档更新 |

---

## Task 1: 平台基类添加 move 方法

**Files:**
- Modify: `worker/platforms/base.py:31-36` (BASE_SUPPORTED_ACTIONS)
- Modify: `worker/platforms/base.py:141-154` (添加抽象方法，在 click 之后)

- [ ] **Step 1: 更新 BASE_SUPPORTED_ACTIONS**

```python
BASE_SUPPORTED_ACTIONS: Set[str] = {
    "ocr_click", "ocr_input", "ocr_wait", "ocr_assert", "ocr_get_text", "ocr_paste",
    "ocr_move",  # 新增
    "image_click", "image_wait", "image_assert", "image_click_near_text",
    "image_move",  # 新增
    "click", "swipe", "input", "press", "screenshot", "wait",
    "move",  # 新增
    "cmd_exec",
}
```

- [ ] **Step 2: 添加 move 抽象方法**（在 click 方法之后）

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

- [ ] **Step 3: 验证语法正确**

Run: `python -c "from worker.platforms.base import PlatformManager; print('OK')"`

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add worker/platforms/base.py
git commit -m "feat: 平台基类添加 move 抽象方法和 BASE_SUPPORTED_ACTIONS"
```

---

## Task 2: Web 平台实现 move 方法

**Files:**
- Modify: `worker/platforms/web.py:396-401` (在 click 方法之后添加)

- [ ] **Step 1: 添加 move 方法**（在 click 方法之后）

```python
def move(self, x: int, y: int, context: Any = None) -> None:
    """移动鼠标到指定坐标。"""
    page = context or self._current_page
    if page:
        _run_async(page.mouse.move(x, y))
```

- [ ] **Step 2: 验证语法正确**

Run: `python -c "from worker.platforms.web import WebPlatformManager; print('OK')"`

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add worker/platforms/web.py
git commit -m "feat: Web 平台实现 move 方法"
```

---

## Task 3: Windows 平台实现 move 方法

**Files:**
- Modify: `worker/platforms/windows.py:77-79` (在 click 方法之后添加)

- [ ] **Step 1: 添加 move 方法**（在 click 方法之后）

```python
def move(self, x: int, y: int, context: Any = None) -> None:
    """移动鼠标到指定坐标。"""
    pyautogui.moveTo(x, y)
```

- [ ] **Step 2: 验证语法正确**

Run: `python -c "from worker.platforms.windows import WindowsPlatformManager; print('OK')"`

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add worker/platforms/windows.py
git commit -m "feat: Windows 平台实现 move 方法"
```

---

## Task 4: Mac 平台实现 move 方法

**Files:**
- Modify: `worker/platforms/mac.py:77-79` (在 click 方法之后添加)

- [ ] **Step 1: 添加 move 方法**（在 click 方法之后）

```python
def move(self, x: int, y: int, context: Any = None) -> None:
    """移动鼠标到指定坐标。"""
    pyautogui.moveTo(x, y)
```

- [ ] **Step 2: 验证语法正确**

Run: `python -c "from worker.platforms.mac import MacPlatformManager; print('OK')"`

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add worker/platforms/mac.py
git commit -m "feat: Mac 平台实现 move 方法"
```

---

## Task 5: Android 平台实现 move 方法

**Files:**
- Modify: `worker/platforms/android.py:158-163` (在 click 方法之后添加)

- [ ] **Step 1: 添加 move 方法**（在 click 方法之后）

```python
def move(self, x: int, y: int, context: Any = None) -> None:
    """移动鼠标（移动端不支持）。"""
    raise NotImplementedError("move action is not supported on mobile platforms")
```

- [ ] **Step 2: 验证语法正确**

Run: `python -c "from worker.platforms.android import AndroidPlatformManager; print('OK')"`

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add worker/platforms/android.py
git commit -m "feat: Android 平台 move 方法抛出 NotImplementedError"
```

---

## Task 6: iOS 平台实现 move 方法

**Files:**
- Modify: `worker/platforms/ios.py:211-214` (在 click 方法之后添加)

- [ ] **Step 1: 添加 move 方法**（在 click 方法之后）

```python
def move(self, x: int, y: int, context: Any = None) -> None:
    """移动鼠标（移动端不支持）。"""
    raise NotImplementedError("move action is not supported on mobile platforms")
```

- [ ] **Step 2: 验证语法正确**

Run: `python -c "from worker.platforms.ios import iOSPlatformManager; print('OK')"`

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add worker/platforms/ios.py
git commit -m "feat: iOS 平台 move 方法抛出 NotImplementedError"
```

---

## Task 7: 添加 MoveAction 执行器

**Files:**
- Modify: `worker/actions/coordinate.py:43` (在 ClickAction 之后添加)

- [ ] **Step 1: 添加 MoveAction 类**（在 ClickAction 类之后）

```python
class MoveAction(BaseActionExecutor):
    """坐标移动。"""

    name = "move"

    def execute(self, platform: "PlatformManager", action: Action, context: Optional[object] = None) -> ActionResult:
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

- [ ] **Step 2: 验证语法正确**

Run: `python -c "from worker.actions.coordinate import MoveAction; print('OK')"`

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add worker/actions/coordinate.py
git commit -m "feat: 添加 MoveAction 执行器"
```

---

## Task 8: 添加 ImageMoveAction 执行器

**Files:**
- Modify: `worker/actions/image.py:235` (在文件末尾添加)

- [ ] **Step 1: 添加 ImageMoveAction 类**（在文件末尾）

```python

class ImageMoveAction(BaseActionExecutor):
    """图像匹配后移动鼠标。"""

    name = "image_move"
    requires_ocr = True

    def execute(self, platform: "PlatformManager", action: Action, context: Optional[object] = None) -> ActionResult:
        # 检查 OCR 客户端
        error = self._check_ocr_client(platform)
        if error:
            return error

        if not action.image_base64:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error="image_base64 is required",
            )

        # 获取截图
        screenshot = platform.take_screenshot(context)

        # 查找图像位置
        threshold = action.threshold if action.threshold is not None else 0.8
        index = action.index if action.index is not None else 0
        position = self._find_image_position(
            platform, screenshot, action.image_base64, threshold, index
        )

        if not position:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error=f"Image not found" + (f" at index {index}" if index > 0 else ""),
            )

        # 应用偏移
        x, y = self._apply_offset(position[0], position[1], action.offset)

        # 移动鼠标（捕获移动端不支持异常）
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

- [ ] **Step 2: 验证语法正确**

Run: `python -c "from worker.actions.image import ImageMoveAction; print('OK')"`

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add worker/actions/image.py
git commit -m "feat: 添加 ImageMoveAction 执行器"
```

---

## Task 9: 添加 OcrMoveAction 执行器

**Files:**
- Modify: `worker/actions/ocr.py:285` (在文件末尾添加)

- [ ] **Step 1: 添加 OcrMoveAction 类**（在文件末尾）

```python

class OcrMoveAction(BaseActionExecutor):
    """OCR 定位后移动鼠标。"""

    name = "ocr_move"
    requires_ocr = True

    def execute(self, platform: "PlatformManager", action: Action, context: Optional[object] = None) -> ActionResult:
        # 检查 OCR 客户端
        error = self._check_ocr_client(platform)
        if error:
            return error

        # 获取截图
        screenshot = platform.take_screenshot(context)

        # 查找文字位置
        index = action.index if action.index is not None else 0
        position = self._find_text_position(
            platform, screenshot, action.value, action.match_mode, index
        )

        if not position:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error=f"Text not found: {action.value}" + (f" at index {index}" if index > 0 else ""),
            )

        # 应用偏移
        x, y = self._apply_offset(position[0], position[1], action.offset)

        # 移动鼠标（捕获移动端不支持异常）
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

- [ ] **Step 2: 验证语法正确**

Run: `python -c "from worker.actions.ocr import OcrMoveAction; print('OK')"`

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add worker/actions/ocr.py
git commit -m "feat: 添加 OcrMoveAction 执行器"
```

---

## Task 10: 注册三个新 Action

**Files:**
- Modify: `worker/actions/__init__.py`

- [ ] **Step 1: 添加导入**

在导入部分添加：

```python
from worker.actions.coordinate import (
    ClickAction,
    MoveAction,  # 新增
    InputAction,
    SwipeAction,
    PressAction,
    ScreenshotAction,
    WaitAction,
)
from worker.actions.image import (
    ImageClickAction,
    ImageWaitAction,
    ImageAssertAction,
    ImageClickNearTextAction,
    ImageMoveAction,  # 新增
)
from worker.actions.ocr import (
    OcrClickAction,
    OcrInputAction,
    OcrWaitAction,
    OcrAssertAction,
    OcrGetTextAction,
    OcrPasteAction,
    OcrMoveAction,  # 新增
)
```

- [ ] **Step 2: 在 _register_all_actions 函数中注册**

```python
    # Coordinate Actions
    ActionRegistry.register(ClickAction())
    ActionRegistry.register(MoveAction())  # 新增
    ActionRegistry.register(InputAction())
    ActionRegistry.register(SwipeAction())
    ActionRegistry.register(PressAction())
    ActionRegistry.register(ScreenshotAction())
    ActionRegistry.register(WaitAction())

    # Image Actions
    ActionRegistry.register(ImageClickAction())
    ActionRegistry.register(ImageWaitAction())
    ActionRegistry.register(ImageAssertAction())
    ActionRegistry.register(ImageClickNearTextAction())
    ActionRegistry.register(ImageMoveAction())  # 新增

    # OCR Actions
    ActionRegistry.register(OcrClickAction())
    ActionRegistry.register(OcrInputAction())
    ActionRegistry.register(OcrWaitAction())
    ActionRegistry.register(OcrAssertAction())
    ActionRegistry.register(OcrGetTextAction())
    ActionRegistry.register(OcrPasteAction())
    ActionRegistry.register(OcrMoveAction())  # 新增
```

- [ ] **Step 3: 更新 __all__ 列表**

```python
__all__ = [
    "ActionExecutor",
    "BaseActionExecutor",
    "ActionRegistry",
    "ActionResult",
    "ActionStatus",
    # OCR Actions
    "OcrClickAction",
    "OcrInputAction",
    "OcrWaitAction",
    "OcrAssertAction",
    "OcrGetTextAction",
    "OcrPasteAction",
    "OcrMoveAction",  # 新增
    # Image Actions
    "ImageClickAction",
    "ImageWaitAction",
    "ImageAssertAction",
    "ImageClickNearTextAction",
    "ImageMoveAction",  # 新增
    # Coordinate Actions
    "ClickAction",
    "MoveAction",  # 新增
    "InputAction",
    "SwipeAction",
    "PressAction",
    "ScreenshotAction",
    "WaitAction",
    # Cmd Exec Action
    "CmdExecAction",
    # Web Token Action
    "GetTokenAction",
]
```

- [ ] **Step 4: 验证注册成功**

Run: `python -c "from worker.actions import ActionRegistry; print('move' in ActionRegistry.list_all(), 'image_move' in ActionRegistry.list_all(), 'ocr_move' in ActionRegistry.list_all())"`

Expected: `True True True`

- [ ] **Step 5: Commit**

```bash
git add worker/actions/__init__.py
git commit -m "feat: 注册 MoveAction、ImageMoveAction、OcrMoveAction"
```

---

## Task 11: 更新 CLAUDE.md 文档

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: 更新动作类型说明**

在"动作类型"部分添加三个新 action 的描述：

```markdown
### 动作类型

所有动作基于 OCR/图像识别或坐标定位。核心动作：
- **OCR 动作**：`ocr_click`, `ocr_input`, `ocr_wait`, `ocr_assert`, `ocr_get_text`, `ocr_paste`, `ocr_move`
- **图像动作**：`image_click`, `image_wait`, `image_assert`, `image_click_near_text`, `image_move`
- **坐标动作**：`click`, `move`, `swipe`, `input`, `press`
- **其他**：`screenshot`, `wait`, `start_app`, `stop_app`
- **Web 特有**：`navigate`
```

同时在动作参数表格中添加 `move` 相关说明：

```markdown
| 参数 | 说明 | 适用动作 |
|------|------|----------|
| `x`, `y` | 目标坐标 | click, move, swipe, input |
| `offset` | 点击偏移 `{"x": 10, "y": 5}` | 所有点击类动作、move 类动作 |
```

- [ ] **Step 2: 验证文档完整性**

Run: `grep -E "(move|image_move|ocr_move)" CLAUDE.md`

Expected: 应看到三个新 action 的提及

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: 更新 CLAUDE.md 动作类型说明，添加 move actions"
```

---

## Task 12: 集成验证

**Files:** 无文件修改

- [ ] **Step 1: 运行完整导入测试**

Run: `python -c "
from worker.platforms.base import PlatformManager
from worker.platforms.web import WebPlatformManager
from worker.platforms.windows import WindowsPlatformManager
from worker.platforms.mac import MacPlatformManager
from worker.platforms.android import AndroidPlatformManager
from worker.platforms.ios import iOSPlatformManager
from worker.actions import ActionRegistry, MoveAction, ImageMoveAction, OcrMoveAction
print('All imports OK')
print('Actions registered:', 'move' in ActionRegistry.list_all(), 'image_move' in ActionRegistry.list_all(), 'ocr_move' in ActionRegistry.list_all())
"`

Expected: `All imports OK` 和 `Actions registered: True True True`

- [ ] **Step 2: 运行代码检查**

Run: `ruff check worker/platforms/ worker/actions/`

Expected: 无错误输出

- [ ] **Step 3: 运行格式检查**

Run: `black --check worker/platforms/ worker/actions/`

Expected: 无错误输出（或仅有格式差异）

- [ ] **Step 4: 最终提交**

```bash
git status
# 确认所有修改已提交
```
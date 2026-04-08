# image_exist / ocr_exist Action 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 ocr_exist/image_exist action，并统一所有 OCR action 的匹配策略。

**Architecture:**
1. 在 `worker/actions/base.py` 新增 `_find_text_with_fallback` 统一匹配方法
2. 所有 OCR action 使用该方法，移除 match_mode 参数使用
3. 新增 OcrExistAction/ImageExistAction，返回 SUCCESS + {"exists": true/false}

**Tech Stack:** Python, pytest, OCR Client

---

## 文件结构

| 文件 | 负责 |
|------|------|
| `worker/actions/base.py` | 新增 `_find_text_with_fallback` 统一匹配方法 |
| `worker/actions/ocr.py` | 新增 OcrExistAction，更新所有 OCR action 使用统一匹配 |
| `worker/actions/image.py` | 新增 ImageExistAction |
| `worker/platforms/base.py` | BASE_SUPPORTED_ACTIONS 添加新 action |
| `worker/actions/__init__.py` | 注册新执行器 |
| `worker/task/action.py` | ActionType 枚举 |

---

### Task 1: 新增统一匹配方法

**Files:**
- Modify: `worker/actions/base.py`

- [ ] **Step 1: 在 BaseActionExecutor 新增 _find_text_with_fallback 方法**

首先确认文件顶部已有 logging 导入（如果没有则添加）：
```python
import logging

logger = logging.getLogger(__name__)
```

然后在 `BaseActionExecutor` 类中添加方法：

```python
def _find_text_with_fallback(
    self,
    platform: "PlatformManager",
    image_bytes: bytes,
    text: str,
    index: int = 0
) -> Optional[tuple[int, int]]:
    """
    使用统一匹配策略查找文字位置：精确匹配 → 模糊匹配。
    reg_ 开头的文字使用正则匹配（不受降级约束）。

    Args:
        platform: 平台管理器
        image_bytes: 图像数据
        text: 目标文字（reg_ 开头表示正则匹配）
        index: 选择第几个匹配结果

    Returns:
        文字中心坐标 (x, y)，未找到返回 None
    """
    # 处理正则快捷模式：reg_ 开头直接使用正则匹配
    if text.startswith("reg_"):
        actual_text = text[4:]  # 去掉前缀
        position = platform._find_text_position(image_bytes, actual_text, "regex", index)
        if position:
            logger.debug(f"Text found with regex match: \"{actual_text}\" (from \"{text}\")")
            return position
        return None

    # 1. 先精确匹配
    position = platform._find_text_position(image_bytes, text, "exact", index)
    if position:
        logger.debug(f"Text found with exact match: \"{text}\"")
        return position

    # 2. 再模糊匹配
    position = platform._find_text_position(image_bytes, text, "fuzzy", index)
    if position:
        logger.debug(f"Text found with fuzzy match: \"{text}\"")
        return position

    return None
```

- [ ] **Step 2: 提交**

```bash
git add worker/actions/base.py
git commit -m "feat: 新增 _find_text_with_fallback 统一匹配方法"
```

---

### Task 2: 更新 OcrClickAction 使用统一匹配

**Files:**
- Modify: `worker/actions/ocr.py:23-66`

- [ ] **Step 1: 更新 OcrClickAction.execute 使用统一匹配**

找到 `OcrClickAction` 类，修改 `execute` 方法：

```python
def execute(self, platform: "PlatformManager", action: Action, context: Optional[object] = None) -> ActionResult:
    # 检查 OCR 客户端
    error = self._check_ocr_client(platform)
    if error:
        return error

    # 获取截图
    screenshot = platform.take_screenshot(context)

    # 查找文字位置（使用统一匹配策略）
    index = action.index if action.index is not None else 0
    position = self._find_text_with_fallback(
        platform, screenshot, action.value, index
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

    # 记录 OCR 定位结果
    logger.debug(f"OCR located: text=\"{action.value}\", position=({x}, {y})")

    # 点击
    platform.click(x, y, context)

    return ActionResult(
        number=0,
        action_type=self.name,
        status=ActionStatus.SUCCESS,
        output=f"Clicked at ({x}, {y})",
    )
```

- [ ] **Step 2: 提交**

```bash
git add worker/actions/ocr.py
git commit -m "refactor: OcrClickAction 使用统一匹配策略"
```

---

### Task 3: 更新其他 OCR action 使用统一匹配

**Files:**
- Modify: `worker/actions/ocr.py`

需要更新的 action：
- `OcrInputAction` (69-116 行)
- `OcrWaitAction` (119-159 行)
- `OcrAssertAction` (162-198 行)
- `OcrPasteAction` (226-287 行)
- `OcrMoveAction` (290-337 行)
- `OcrDoubleClickAction` (340-408 行)

- [ ] **Step 1: 更新 OcrInputAction**

```python
def execute(self, platform: "PlatformManager", action: Action, context: Optional[object] = None) -> ActionResult:
    # 检查 OCR 客户端
    error = self._check_ocr_client(platform)
    if error:
        return error

    # 获取截图
    screenshot = platform.take_screenshot(context)

    # 查找文字位置（使用统一匹配策略）
    index = action.index if action.index is not None else 0
    position = self._find_text_with_fallback(
        platform, screenshot, action.value, index
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

    # 记录 OCR 定位结果
    logger.debug(f"OCR located: text=\"{action.value}\", position=({x}, {y})")

    # 点击输入框
    platform.click(x, y, context)

    # 输入文本
    if action.text:
        platform.input_text(action.text, context)

    return ActionResult(
        number=0,
        action_type=self.name,
        status=ActionStatus.SUCCESS,
        output=f"Input at ({x}, {y})",
    )
```

- [ ] **Step 2: 更新 OcrWaitAction**

```python
def execute(self, platform: "PlatformManager", action: Action, context: Optional[object] = None) -> ActionResult:
    # 检查 OCR 客户端
    error = self._check_ocr_client(platform)
    if error:
        return error

    # 如果有 time 参数，先等待指定秒数
    if action.time:
        time.sleep(action.time)

    start_time = time.time()
    timeout = action.timeout / 1000

    while time.time() - start_time < timeout:
        screenshot = platform.take_screenshot(context)
        position = self._find_text_with_fallback(
            platform, screenshot, action.value
        )

        if position:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.SUCCESS,
                output=f"Text appeared: {action.value}",
            )

        time.sleep(0.5)

    return ActionResult(
        number=0,
        action_type=self.name,
        status=ActionStatus.FAILED,
        error=f"Text not appeared within timeout: {action.value}",
    )
```

- [ ] **Step 3: 更新 OcrAssertAction**

```python
def execute(self, platform: "PlatformManager", action: Action, context: Optional[object] = None) -> ActionResult:
    # 检查 OCR 客户端
    error = self._check_ocr_client(platform)
    if error:
        return error

    screenshot = platform.take_screenshot(context)

    # 使用统一匹配策略
    position = self._find_text_with_fallback(platform, screenshot, action.value)

    if position:
        return ActionResult(
            number=0,
            action_type=self.name,
            status=ActionStatus.SUCCESS,
            output=f"Text found: {action.value}",
        )
    else:
        return ActionResult(
            number=0,
            action_type=self.name,
            status=ActionStatus.FAILED,
            error=f"Text not found: {action.value}",
        )
```

- [ ] **Step 4: 更新 OcrPasteAction**

```python
def execute(self, platform: "PlatformManager", action: Action, context: Optional[object] = None) -> ActionResult:
    # 检查 OCR 客户端
    error = self._check_ocr_client(platform)
    if error:
        return error

    if not action.text:
        return ActionResult(
            number=0,
            action_type=self.name,
            status=ActionStatus.FAILED,
            error="text is required for ocr_paste",
        )

    # 获取截图
    screenshot = platform.take_screenshot(context)

    # 查找文字位置（使用统一匹配策略）
    index = action.index if action.index is not None else 0
    position = self._find_text_with_fallback(
        platform, screenshot, action.value, index
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

    # 记录 OCR 定位结果
    logger.debug(f"OCR located: text=\"{action.value}\", position=({x}, {y})")

    # 点击坐标
    platform.click(x, y, context)

    # 使用剪贴板粘贴
    import pyperclip
    original_clipboard = pyperclip.paste()
    try:
        pyperclip.copy(action.text)
        platform.press("Control+v", context)
    finally:
        # 恢复原始剪贴板内容
        pyperclip.copy(original_clipboard)

    return ActionResult(
        number=0,
        action_type=self.name,
        status=ActionStatus.SUCCESS,
        output=f"Pasted at ({x}, {y})",
    )
```

- [ ] **Step 5: 更新 OcrMoveAction**

```python
def execute(self, platform: "PlatformManager", action: Action, context: Optional[object] = None) -> ActionResult:
    # 检查 OCR 客户端
    error = self._check_ocr_client(platform)
    if error:
        return error

    # 获取截图
    screenshot = platform.take_screenshot(context)

    # 查找文字位置（使用统一匹配策略）
    index = action.index if action.index is not None else 0
    position = self._find_text_with_fallback(
        platform, screenshot, action.value, index
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

- [ ] **Step 6: 更新 OcrDoubleClickAction，删除其内部的 _find_text_with_fallback 方法**

修改 execute 方法使用基类的方法，删除类内部重复定义的 `_find_text_with_fallback` 方法。

```python
def execute(self, platform: "PlatformManager", action: Action, context: Optional[object] = None) -> ActionResult:
    # 检查 OCR 客户端
    error = self._check_ocr_client(platform)
    if error:
        return error

    # 获取截图
    screenshot = platform.take_screenshot(context)

    # 查找文字位置（使用统一匹配策略）
    index = action.index if action.index is not None else 0
    position = self._find_text_with_fallback(platform, screenshot, action.value, index)

    if not position:
        return ActionResult(
            number=0,
            action_type=self.name,
            status=ActionStatus.FAILED,
            error=f"Text not found: {action.value}" + (f" at index {index}" if index > 0 else ""),
        )

    # 应用偏移
    x, y = self._apply_offset(position[0], position[1], action.offset)

    # 记录 OCR 定位结果
    logger.debug(f"OCR located: text=\"{action.value}\", position=({x}, {y})")

    # 双击
    platform.double_click(x, y, context)

    return ActionResult(
        number=0,
        action_type=self.name,
        status=ActionStatus.SUCCESS,
        output=f"Double clicked at ({x}, {y})",
    )
```

删除该类内部的 `_find_text_with_fallback` 方法（383-408 行）。

- [ ] **Step 7: 提交**

```bash
git add worker/actions/ocr.py
git commit -m "refactor: 所有 OCR action 使用统一匹配策略"
```

---

### Task 4: 更新同行定位 action 使用统一匹配

**Files:**
- Modify: `worker/actions/ocr.py`

需要更新的 action：
- `OcrClickSameRowTextAction` (411-518 行)
- `OcrCheckSameRowTextAction` (521-613 行)

- [ ] **Step 1: 更新 OcrClickSameRowTextAction**

修改 execute 方法，使用基类的 `_find_text_with_fallback`，删除类内部重复定义的方法。

```python
def execute(self, platform: "PlatformManager", action: Action, context: Optional[object] = None) -> ActionResult:
    # 检查 OCR 客户端
    error = self._check_ocr_client(platform)
    if error:
        return error

    if not action.anchor_text:
        return ActionResult(
            number=0,
            action_type=self.name,
            status=ActionStatus.FAILED,
            error="anchor_text is required",
        )

    if not action.value:
        return ActionResult(
            number=0,
            action_type=self.name,
            status=ActionStatus.FAILED,
            error="value (target_text) is required",
        )

    # 获取完整截图
    screenshot = platform.take_screenshot(context)

    # 定位锚点文本（使用统一匹配策略）
    anchor_index = action.anchor_index if action.anchor_index is not None else 0
    anchor_position = self._find_text_with_fallback(platform, screenshot, action.anchor_text, anchor_index)

    if not anchor_position:
        return ActionResult(
            number=0,
            action_type=self.name,
            status=ActionStatus.FAILED,
            error=f"Anchor text not found: {action.anchor_text}" + (f" at index {anchor_index}" if anchor_index > 0 else ""),
        )

    anchor_x, anchor_y = anchor_position
    logger.debug(f"Anchor found: text=\"{action.anchor_text}\", position=({anchor_x}, {anchor_y})")

    # 获取截图尺寸
    img = Image.open(io.BytesIO(screenshot))
    img_width, img_height = img.size

    # 裁剪水平带状区域
    row_tolerance = action.row_tolerance if action.row_tolerance is not None else 20
    top = max(0, anchor_y - row_tolerance)
    bottom = min(img_height, anchor_y + row_tolerance + 1)

    cropped = img.crop((0, top, img_width, bottom))

    # 将裁剪后的图片转为bytes
    cropped_bytes_io = io.BytesIO()
    cropped.save(cropped_bytes_io, format="PNG")
    cropped_bytes = cropped_bytes_io.getvalue()

    # 在裁剪区域内查找目标文本（使用统一匹配策略）
    target_index = action.target_index if action.target_index is not None else 0
    target_position = self._find_text_with_fallback(platform, cropped_bytes, action.value, target_index)

    if not target_position:
        return ActionResult(
            number=0,
            action_type=self.name,
            status=ActionStatus.FAILED,
            error=f"Target text not found in row of \"{action.anchor_text}\": {action.value}" + (f" at target_index {target_index}" if target_index > 0 else ""),
        )

    # 计算目标在原图中的坐标（加上裁剪偏移）
    target_x = target_position[0]
    target_y = target_position[1] + top

    logger.debug(f"Target found: text=\"{action.value}\", position=({target_x}, {target_y}) in row")

    # 应用偏移
    x, y = self._apply_offset(target_x, target_y, action.offset)

    # 点击
    platform.click(x, y, context)

    return ActionResult(
        number=0,
        action_type=self.name,
        status=ActionStatus.SUCCESS,
        output=f"Clicked at ({x}, {y}) in row of \"{action.anchor_text}\"",
    )
```

删除该类内部的 `_find_text_with_fallback` 方法（504-518 行）。

- [ ] **Step 2: 更新 OcrCheckSameRowTextAction**

同样修改，使用基类的 `_find_text_with_fallback`，删除内部重复定义的方法。

- [ ] **Step 3: 提交**

```bash
git add worker/actions/ocr.py
git commit -m "refactor: 同行定位 action 使用统一匹配策略"
```

---

### Task 5: 新增 OcrExistAction

**Files:**
- Modify: `worker/actions/ocr.py`
- Modify: `worker/actions/ocr.py` 文件头部注释

- [ ] **Step 1: 更新文件头部注释**

```python
"""
OCR 类 Action 执行器。

包含所有基于 OCR 文字识别的动作：ocr_click, ocr_input, ocr_wait, ocr_assert, ocr_get_text, ocr_paste,
ocr_move, ocr_double_click, ocr_exist,
ocr_click_same_row_text, ocr_check_same_row_text。

统一匹配策略：精确匹配 → 模糊匹配，reg_ 开头使用正则匹配。
"""
```

- [ ] **Step 2: 新增 OcrExistAction 类**

在 `OcrCheckSameRowTextAction` 之后添加：

```python
class OcrExistAction(BaseActionExecutor):
    """检查文字是否存在。"""

    name = "ocr_exist"
    requires_ocr = True

    def execute(self, platform: "PlatformManager", action: Action, context: Optional[object] = None) -> ActionResult:
        # 检查 OCR 客户端
        error = self._check_ocr_client(platform)
        if error:
            return error

        # 检查必填参数
        if not action.value:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error="value is required",
            )

        # 获取截图
        screenshot = platform.take_screenshot(context)

        # 使用统一匹配策略查找文字
        index = action.index if action.index is not None else 0
        position = self._find_text_with_fallback(
            platform, screenshot, action.value, index
        )

        # 返回结果（始终 SUCCESS，通过 output 返回存在性）
        import json
        exists = position is not None
        return ActionResult(
            number=0,
            action_type=self.name,
            status=ActionStatus.SUCCESS,
            output=json.dumps({"exists": exists}),
        )
```

- [ ] **Step 3: 提交**

```bash
git add worker/actions/ocr.py
git commit -m "feat: 新增 ocr_exist action"
```

---

### Task 6: 新增 ImageExistAction

**Files:**
- Modify: `worker/actions/image.py`
- Modify: `worker/actions/image.py` 文件头部注释

- [ ] **Step 1: 更新文件头部注释**

```python
"""
图像类 Action 执行器。

包含所有基于图像匹配的动作：image_click, image_wait, image_assert, image_click_near_text,
image_move, image_double_click, image_exist,
ocr_click_same_row_image, ocr_check_same_row_image。
"""
```

- [ ] **Step 2: 新增 ImageExistAction 类**

在文件末尾添加：

```python
class ImageExistAction(BaseActionExecutor):
    """检查图像是否存在。"""

    name = "image_exist"
    requires_ocr = True

    def execute(self, platform: "PlatformManager", action: Action, context: Optional[object] = None) -> ActionResult:
        # 检查 OCR 客户端
        error = self._check_ocr_client(platform)
        if error:
            return error

        # 检查必填参数
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

        # 返回结果（始终 SUCCESS，通过 output 返回存在性）
        import json
        exists = position is not None
        return ActionResult(
            number=0,
            action_type=self.name,
            status=ActionStatus.SUCCESS,
            output=json.dumps({"exists": exists}),
        )
```

- [ ] **Step 3: 提交**

```bash
git add worker/actions/image.py
git commit -m "feat: 新增 image_exist action"
```

---

### Task 7: 注册新 action 到平台基类

**Files:**
- Modify: `worker/platforms/base.py:31-41`

- [ ] **Step 1: 添加到 BASE_SUPPORTED_ACTIONS**

```python
BASE_SUPPORTED_ACTIONS: Set[str] = {
    "ocr_click", "ocr_input", "ocr_wait", "ocr_assert", "ocr_get_text", "ocr_paste",
    "ocr_move", "ocr_double_click", "ocr_exist",
    "ocr_click_same_row_text", "ocr_check_same_row_text",
    "ocr_click_same_row_image", "ocr_check_same_row_image",
    "image_click", "image_wait", "image_assert", "image_click_near_text",
    "image_move", "image_double_click", "image_exist",
    "click", "double_click", "swipe", "input", "press", "screenshot", "wait",
    "move",
    "cmd_exec",  # 宿主机命令执行
}
```

- [ ] **Step 2: 提交**

```bash
git add worker/platforms/base.py
git commit -m "feat: 注册 ocr_exist/image_exist action"
```

---

### Task 8: 注册新 action 执行器

**Files:**
- Modify: `worker/actions/__init__.py`

- [ ] **Step 1: 添加导入**

在导入部分添加：
```python
from worker.actions.ocr import (
    ...,
    OcrExistAction,  # 新增
)
from worker.actions.image import (
    ...,
    ImageExistAction,  # 新增
)
```

- [ ] **Step 2: 在 _register_all_actions 函数中注册**

在 OCR Actions 部分末尾添加：
```python
ActionRegistry.register(OcrExistAction())
```

在 Image Actions 部分末尾添加：
```python
ActionRegistry.register(ImageExistAction())
```

- [ ] **Step 3: 添加到 __all__ 导出列表**

在 `__all__` 中添加：
```python
"OcrExistAction",
"ImageExistAction",
```

- [ ] **Step 4: 提交**

```bash
git add worker/actions/__init__.py
git commit -m "feat: 注册新 action 执行器"
```

---

### Task 9: 更新 ActionType 枚举

**Files:**
- Modify: `worker/task/action.py:12-43`

- [ ] **Step 1: 添加 OCR_EXIST 和 IMAGE_EXIST**

```python
class ActionType(Enum):
    """动作类型枚举。"""

    # OCR 文字操作
    OCR_CLICK = "ocr_click"           # 点击识别到的文字
    OCR_ASSERT = "ocr_assert"         # 断言文字存在
    OCR_WAIT = "ocr_wait"             # 等待文字出现
    OCR_INPUT = "ocr_input"           # 在文字附近输入
    OCR_GET_TEXT = "ocr_get_text"     # 获取文字区域内容
    OCR_PASTE = "ocr_paste"           # OCR定位后粘贴
    OCR_EXIST = "ocr_exist"           # 检查文字是否存在

    # 图像匹配操作
    IMAGE_CLICK = "image_click"       # 点击匹配的图像
    IMAGE_ASSERT = "image_assert"     # 断言图像存在
    IMAGE_WAIT = "image_wait"         # 等待图像出现
    IMAGE_CLICK_NEAR_TEXT = "image_click_near_text"  # 点击文本附近最近的图像
    IMAGE_EXIST = "image_exist"       # 检查图像是否存在

    # 基础操作（坐标/按键）
    CLICK = "click"                   # 坐标点击 (x, y)
    SWIPE = "swipe"                   # 滑动 (方向/坐标)
    INPUT = "input"                   # 输入文本
    PRESS = "press"                   # 按键
    SCREENSHOT = "screenshot"         # 截图
    WAIT = "wait"                     # 固定等待

    # Web 专用
    NAVIGATE = "navigate"             # 跳转 URL

    # 应用操作
    START_APP = "start_app"          # 启动应用
    STOP_APP = "stop_app"            # 关闭应用
```

- [ ] **Step 2: 提交**

```bash
git add worker/task/action.py
git commit -m "feat: ActionType 添加 OCR_EXIST/IMAGE_EXIST"
```

---

### Task 10: 验证实现

**Files:**
- 无文件修改

- [ ] **Step 1: 运行代码检查**

```bash
ruff check worker/actions/
black worker/actions/ --check
```

预期：无错误

- [ ] **Step 2: 提交最终汇总**

```bash
git commit --allow-empty -m "完成: image_exist/ocr_exist action 及统一匹配策略实现"
```
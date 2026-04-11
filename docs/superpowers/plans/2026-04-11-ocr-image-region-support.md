# OCR/Image Action Region 参数支持实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为所有 OCR/Image 相关 action 添加 `region` 参数支持，实现在指定矩形区域内进行 OCR/图像识别操作。

**Architecture:** 在 action 执行层对截图进行裁剪，调用 OCR/图像匹配后，将返回的相对坐标加上 region 偏移量转换为全局坐标。不修改 OCR 客户端和 Platform 层。

**Tech Stack:** Python, PIL (Pillow), dataclasses, pytest

---

## 文件清单

| 操作 | 文件 | 说明 |
|------|------|------|
| 修改 | `worker/task/action.py` | Action 模型新增 `region` 字段 |
| 修改 | `worker/actions/base.py` | 新增 `_crop_region` 和 `_offset_position` 辅助方法 |
| 修改 | `worker/actions/ocr.py` | 11 个 OCR action 支持 region |
| 修改 | `worker/actions/image.py` | 9 个 Image action 支持 region |
| 创建 | `tests/test_region_support.py` | 测试 region 裁剪和坐标转换逻辑 |

---

### Task 1: Action 模型新增 region 字段

**Files:**
- Modify: `worker/task/action.py:64-139`
- Test: `tests/test_region_support.py` (region 字段解析测试)

- [ ] **Step 1: 编写 region 字段解析测试**

在 `tests/test_region_support.py` 中写入：

```python
"""OCR/Image action region 参数支持测试。"""

from worker.task.action import Action


class TestActionRegionField:
    """测试 Action 模型中 region 字段。"""

    def test_region_from_dict(self):
        """测试从字典解析 region。"""
        action = Action.from_dict({
            "action_type": "ocr_click",
            "value": "测试文字",
            "region": [100, 200, 500, 600],
        })
        assert action.region == [100, 200, 500, 600]

    def test_region_default_none(self):
        """测试 region 默认为 None。"""
        action = Action.from_dict({"action_type": "ocr_click"})
        assert action.region is None

    def test_region_to_dict(self):
        """测试序列化为字典。"""
        action = Action(
            action_type="ocr_click",
            value="测试文字",
            region=[0, 0, 640, 360],
        )
        result = action.to_dict()
        assert result["region"] == [0, 0, 640, 360]

    def test_region_to_dict_omits_none(self):
        """测试 region 为 None 时不序列化。"""
        action = Action(action_type="ocr_click")
        result = action.to_dict()
        assert "region" not in result
```

- [ ] **Step 2: 运行测试确认失败**

```bash
pytest tests/test_region_support.py::TestActionRegionField -v
```
预期：FAIL（`region` 字段不存在）

- [ ] **Step 3: 在 Action dataclass 中新增 region 字段**

在 `worker/task/action.py` 的 Action dataclass 中，`target_index` 字段后面添加：

```python
    region: Optional[List[int]] = None       # 操作区域 [x1, y1, x2, y2]
```

注意文件头部需要确认 `List` 已导入（第 9 行已有 `from typing import Optional, Dict, Any`，需要加上 `List`）：

```python
from typing import Optional, Dict, Any, List
```

- [ ] **Step 4: 在 from_dict 中解析 region**

在 `from_dict` 方法的 `target_index=data.get("target_index"),` 后面添加：

```python
            region=data.get("region"),
```

- [ ] **Step 5: 在 to_dict 中序列化 region**

在 `to_dict` 方法的最后（`target_index` 之后）添加：

```python
        if self.region is not None:
            result["region"] = self.region
```

- [ ] **Step 6: 运行测试确认通过**

```bash
pytest tests/test_region_support.py::TestActionRegionField -v
```
预期：全部 PASS

- [ ] **Step 7: 提交**

```bash
git add worker/task/action.py tests/test_region_support.py
git commit -m "feat: 添加 region 字段到 Action 模型"
```

---

### Task 2: BaseActionExecutor 新增裁剪和坐标转换方法

**Files:**
- Modify: `worker/actions/base.py:67-92` (在 `_apply_offset` 方法后面添加)
- Test: `tests/test_region_support.py` (裁剪和偏移测试)

- [ ] **Step 1: 编写裁剪方法测试**

在 `tests/test_region_support.py` 中添加：

```python
import io
from PIL import Image
from worker.actions.base import BaseActionExecutor


class TestRegionCrop:
    """测试 region 裁剪逻辑。"""

    def test_crop_region(self):
        """测试按 region 裁剪图像。"""
        executor = BaseActionExecutor.__new__(BaseActionExecutor)
        # 创建 200x200 红色图像
        img = Image.new("RGB", (200, 200), color="red")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        image_bytes = buf.getvalue()

        # 裁剪右下区域 [100, 100, 200, 200]
        cropped = executor._crop_region(image_bytes, [100, 100, 200, 200])
        cropped_img = Image.open(io.BytesIO(cropped))
        assert cropped_img.size == (100, 100)

    def test_crop_region_returns_bytes(self):
        """测试裁剪后返回 bytes 类型。"""
        executor = BaseActionExecutor.__new__(BaseActionExecutor)
        img = Image.new("RGB", (100, 100), color="blue")
        buf = io.BytesIO()
        img.save(buf, format="PNG")

        result = executor._crop_region(buf.getvalue(), [0, 0, 50, 50])
        assert isinstance(result, bytes)

    def test_crop_invalid_region_raises(self):
        """测试无效 region 抛出异常。"""
        executor = BaseActionExecutor.__new__(BaseActionExecutor)
        img = Image.new("RGB", (100, 100), color="green")
        buf = io.BytesIO()
        img.save(buf, format="PNG")

        import pytest
        with pytest.raises(ValueError):
            executor._crop_region(buf.getvalue(), [100, 0, 50, 50])  # x1 > x2
```

- [ ] **Step 2: 编写坐标偏移测试**

在 `tests/test_region_support.py` 中添加：

```python
class TestRegionOffset:
    """测试 region 坐标偏移逻辑。"""

    def test_offset_position(self):
        """测试坐标偏移计算。"""
        executor = BaseActionExecutor.__new__(BaseActionExecutor)
        result = executor._offset_position((50, 30), [100, 200, 500, 600])
        assert result == (150, 230)

    def test_offset_position_zero_region(self):
        """测试零偏移 region。"""
        executor = BaseActionExecutor.__new__(BaseActionExecutor)
        result = executor._offset_position((50, 30), [0, 0, 640, 480])
        assert result == (50, 30)
```

- [ ] **Step 3: 运行测试确认失败**

```bash
pytest tests/test_region_support.py::TestRegionCrop tests/test_region_support.py::TestRegionOffset -v
```
预期：FAIL（方法不存在）

- [ ] **Step 4: 实现裁剪和偏移方法**

在 `worker/actions/base.py` 文件头部添加必要的 import（如果还没有）：

```python
import io
from PIL import Image
```

在 `_apply_offset` 方法后面添加：

```python
    def _crop_region(self, image_bytes: bytes, region: list[int]) -> bytes:
        """
        按 region [x1, y1, x2, y2] 裁剪图像。

        Args:
            image_bytes: 原始图像数据
            region: 操作区域 [x1, y1, x2, y2]

        Returns:
            bytes: 裁剪后的图像数据

        Raises:
            ValueError: region 无效
        """
        x1, y1, x2, y2 = region
        if x1 >= x2 or y1 >= y2:
            raise ValueError(f"Invalid region: {region}, x1 must be < x2 and y1 must be < y2")

        img = Image.open(io.BytesIO(image_bytes))
        # PIL.crop 使用 (left, upper, right, lower) 即 (x1, y1, x2, y2)
        cropped = img.crop((x1, y1, x2, y2))
        buf = io.BytesIO()
        cropped.save(buf, format=img.format or "PNG")
        return buf.getvalue()

    def _offset_position(self, position: tuple[int, int], region: list[int]) -> tuple[int, int]:
        """
        将相对于裁剪区域的坐标转换为全局坐标。

        Args:
            position: 相对坐标 (x, y)
            region: 操作区域 [x1, y1, x2, y2]

        Returns:
            tuple[int, int]: 全局坐标 (x+x1, y+y1)
        """
        x1, y1, _, _ = region
        return (position[0] + x1, position[1] + y1)
```

- [ ] **Step 5: 运行测试确认通过**

```bash
pytest tests/test_region_support.py::TestRegionCrop tests/test_region_support.py::TestRegionOffset -v
```
预期：全部 PASS

- [ ] **Step 6: 提交**

```bash
git add worker/actions/base.py tests/test_region_support.py
git commit -m "feat: 添加 _crop_region 和 _offset_position 辅助方法"
```

---

### Task 3: OCR Actions 支持 region 参数

**Files:**
- Modify: `worker/actions/ocr.py` (所有 action 类的 execute 方法)

改造模式统一：每个 action 的 `execute()` 方法中，在获取截图后添加 region 裁剪逻辑，在获取 position 后添加坐标偏移逻辑。

- [ ] **Step 1: OcrClickAction 支持 region**

修改 `worker/actions/ocr.py` 中 `OcrClickAction.execute()` (第 32-69 行)：

```python
    def execute(self, platform: "PlatformManager", action: Action, context: Optional[object] = None) -> ActionResult:
        error = self._check_ocr_client(platform)
        if error:
            return error

        screenshot = platform.take_screenshot(context)
        if action.region:
            screenshot = self._crop_region(screenshot, action.region)

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

        if action.region:
            position = self._offset_position(position, action.region)

        x, y = self._apply_offset(position[0], position[1], action.offset)

        logger.debug(f"OCR located: text=\"{action.value}\", position=({x}, {y})")

        platform.click(x, y, context)

        return ActionResult(
            number=0,
            action_type=self.name,
            status=ActionStatus.SUCCESS,
            output=f"Clicked at ({x}, {y})",
        )
```

- [ ] **Step 2: OcrInputAction 支持 region**

修改第 78-119 行。与 Step 1 完全相同的改造模式：
1. `screenshot = platform.take_screenshot(context)` 后加 `if action.region: screenshot = self._crop_region(screenshot, action.region)`
2. `position = self._find_text_with_fallback(...)` 后、if not position 之后，加 `if action.region: position = self._offset_position(position, action.region)`

- [ ] **Step 3: OcrWaitAction 支持 region**

修改第 128-162 行。注意 `while` 循环内每次截图都需要裁剪：

```python
        while time.time() - start_time < timeout:
            screenshot = platform.take_screenshot(context)
            if action.region:
                screenshot = self._crop_region(screenshot, action.region)
            position = self._find_text_with_fallback(
                platform, screenshot, action.value
            )
            ...
```

- [ ] **Step 4: OcrAssertAction 支持 region**

修改第 171-195 行。截图后裁剪。不需要坐标偏移（只判断文字是否存在）。

- [ ] **Step 5: OcrPasteAction 支持 region**

修改第 229-284 行。截图后裁剪，position 后偏移（同 Step 1 模式）。

- [ ] **Step 6: OcrMoveAction 支持 region**

修改第 293-334 行。截图后裁剪，position 后偏移（同 Step 1 模式）。

- [ ] **Step 7: OcrDoubleClickAction 支持 region**

修改第 343-378 行。截图后裁剪，position 后偏移（同 Step 1 模式）。

- [ ] **Step 8: OcrExistAction 支持 region**

修改第 563-595 行。截图后裁剪。不需要坐标偏移（只返回 exists）。

- [ ] **Step 9: OcrGetTextAction 支持 region**

修改第 204-220 行。截图后裁剪，**不需要坐标偏移**（此 action 返回识别到的所有文字内容，不涉及坐标计算）。

> 注意：裁剪后调用 `platform.ocr_client.recognize(screenshot)` 只返回裁剪区域内的文字结果，这正是多画面场景下需要的行为——只获取指定区域的文字。

- [ ] **Step 10: OcrClickSameRowTextAction 支持 region**

修改第 387-472 行。截图后裁剪，target_position 后偏移。

> 注意：region 裁剪应用于整张截图，anchor 文本和 target 文本都在裁剪区域内查找。这意味着用户传入 region 后，anchor 和 target 都必须在该区域内才能成功。

- [ ] **Step 11: OcrCheckSameRowTextAction 支持 region**

修改第 481-554 行。截图后裁剪，target_position 后偏移。同样，anchor 和 target 都在裁剪区域内查找。

- [ ] **Step 12: 运行所有测试确认通过**

```bash
pytest tests/test_region_support.py -v
```

- [ ] **Step 13: 提交**

```bash
git add worker/actions/ocr.py
git commit -m "feat: OCR actions 支持 region 参数"
```

---

### Task 4: Image Actions 支持 region 参数

**Files:**
- Modify: `worker/actions/image.py` (所有 action 类的 execute 方法)

改造模式与 OCR actions 完全一致。

- [ ] **Step 1: ImageClickAction 支持 region**

修改第 30-76 行。与 OCR action 相同的改造模式：
1. `screenshot = platform.take_screenshot(context)` 后加 `if action.region: screenshot = self._crop_region(screenshot, action.region)`
2. `position = self._find_image_position(...)` 后、if not position 之后，加 `if action.region: position = self._offset_position(position, action.region)`

- [ ] **Step 2: ImageWaitAction 支持 region**

修改第 85-125 行。while 循环内每次截图都需要裁剪（同 OcrWaitAction）。

- [ ] **Step 3: ImageAssertAction 支持 region**

修改第 134-168 行。截图后裁剪。不需要坐标偏移（只判断图像是否存在）。

- [ ] **Step 4: ImageMoveAction 支持 region**

修改第 248-298 行。截图后裁剪，position 后偏移（同 Step 1 模式）。

- [ ] **Step 5: ImageDoubleClickAction 支持 region**

修改第 307-353 行。截图后裁剪，position 后偏移（同 Step 1 模式）。

- [ ] **Step 6: ImageExistAction 支持 region**

修改第 574-607 行。截图后裁剪。不需要坐标偏移（只返回 exists）。

- [ ] **Step 7: ImageClickNearTextAction 支持 region**

修改第 177-239 行。截图后裁剪。注意：此 action 调用 `platform.ocr_client.match_near_text()`，该方法返回的是相对于裁剪区域的坐标，需要偏移：

```python
        if not match:
            return ActionResult(...)

        # 使用局部变量计算，不修改 match 对象
        gx, gy = match.center_x, match.center_y
        if action.region:
            gx, gy = self._offset_position((gx, gy), action.region)

        x, y = self._apply_offset(gx, gy, action.offset)
```

- [ ] **Step 8: OcrClickSameRowImageAction 支持 region**

修改第 362-450 行。截图后裁剪，target_position 后偏移。region 裁剪应用于整张截图，anchor 文本和 target 图像都在裁剪区域内查找。

- [ ] **Step 9: OcrCheckSameRowImageAction 支持 region**

修改第 475-565 行。截图后裁剪，target_position 后偏移。同样，anchor 和 target 都在裁剪区域内查找。

- [ ] **Step 10: 运行全部测试**

```bash
pytest tests/test_region_support.py -v
```

- [ ] **Step 11: 提交**

```bash
git add worker/actions/image.py tests/test_region_support.py
git commit -m "feat: Image actions 支持 region 参数"
```

---

### Task 5: 验证与清理

**Files:**
- Modify: 无（验证性任务）

- [ ] **Step 1: 运行完整测试套件**

```bash
pytest tests/ -v
```
预期：全部 PASS

- [ ] **Step 2: 代码检查**

```bash
ruff check .
black .
```

- [ ] **Step 3: 最终提交（如有遗漏的修改）**

```bash
git status
git add -A
git commit -m "chore: region 参数支持 - 代码清理和最终提交"
```

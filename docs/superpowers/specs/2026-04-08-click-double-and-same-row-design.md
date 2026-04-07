# 点击动作扩展与同行定位功能设计

日期：2026-04-08

## 背景

现有action系统支持多种点击方式（坐标点击、OCR文字点击、图像点击），但缺少双击功能。同时，对于多行功能开关等UI布局，难以定位同一行上的目标元素。

## 需求概述

### 双击功能
为所有现有click action添加对应的double版本，参数保持不变。

### 同行定位功能
通过锚点文本定位水平行，在该行范围内搜索目标文本或图片，支持点击和检查两种操作。

## 设计详情

### 一、双击功能

#### 1.1 新增Action类型

| Action名称 | 所属类别 | 文件位置 |
|-----------|---------|---------|
| `double_click` | 坐标类 | `worker/actions/coordinate.py` |
| `ocr_double_click` | OCR类 | `worker/actions/ocr.py` |
| `image_double_click` | 图像类 | `worker/actions/image.py` |

#### 1.2 参数定义

参数与对应的click action完全一致：

- `double_click`：`x`, `y`, `offset`
- `ocr_double_click`：`value`, `match_mode`, `index`, `offset`
- `image_double_click`：`image_base64`, `threshold`, `index`, `offset`

#### 1.3 平台基础能力扩展

在 `PlatformManager` 基类 (`worker/platforms/base.py`) 新增抽象方法：

```python
@abstractmethod
def double_click(self, x: int, y: int, context: Any = None) -> None:
    """
    双击指定坐标。

    Args:
        x: X 坐标
        y: Y 坐标
        context: 执行上下文（可选）
    """
    pass
```

#### 1.4 各平台实现

| 平台 | 实现方式 |
|------|---------|
| Windows | `pyautogui.doubleClick(x, y)` |
| Mac | `pyautogui.doubleClick(x, y)` |
| Web | `page.mouse.click(x, y, click_count=2)` |
| Android | 模拟两次快速click（间隔100ms） |
| iOS | 模拟两次快速tap（间隔100ms） |

#### 1.5 Action执行器实现

**DoubleClickAction**：
```python
class DoubleClickAction(BaseActionExecutor):
    name = "double_click"

    def execute(self, platform, action, context):
        if action.x is None or action.y is None:
            return ActionResult(status=FAILED, error="x and y required")

        x, y = self._apply_offset(action.x, action.y, action.offset)
        platform.double_click(x, y, context)

        return ActionResult(status=SUCCESS, output=f"Double clicked at ({x}, {y})")
```

**OcrDoubleClickAction**：
- 完全复用 `OcrClickAction` 的定位逻辑
- 定位成功后调用 `platform.double_click()` 而非 `platform.click()`

**ImageDoubleClickAction**：
- 完全复用 `ImageClickAction` 的定位逻辑
- 定位成功后调用 `platform.double_click()` 而非 `platform.click()`

---

### 二、同行定位功能

#### 2.1 新增Action类型

| Action名称 | 功能 | 文件位置 |
|-----------|------|---------|
| `ocr_click_same_row_text` | 点击同行文本 | `worker/actions/ocr.py` |
| `ocr_click_same_row_image` | 点击同行图片 | `worker/actions/image.py` |
| `ocr_check_same_row_text` | 检查同行文本是否存在 | `worker/actions/ocr.py` |
| `ocr_check_same_row_image` | 检查同行图片是否存在 | `worker/actions/image.py` |

#### 2.2 参数定义

点击类Action：
```python
# 锚点参数
anchor_text: str              # 锚点文本（必填）
anchor_index: int = 0         # 锚点索引（第几个匹配）

# 水平带范围
row_tolerance: int = 20       # 上下范围（像素），默认20

# 目标参数
value: str                    # 目标文本（ocr_click_same_row_text）
image_base64: str             # 目标图片（ocr_click_same_row_image）
threshold: float = 0.8        # 图片匹配阈值
target_index: int = 0         # 目标索引（同行第几个匹配）

# 通用参数
offset: dict                  # 点击偏移 {"x": 10, "y": 5}
```

检查类Action：
- 参数与点击类相同，但无 `offset` 参数

#### 2.3 匹配策略（锚点和目标均适用）

采用自动降级策略：精确匹配 → 模糊匹配 → 报错

```python
def _find_text_with_fallback(platform, image_bytes, text, index=0):
    # 1. 先精确匹配
    position = platform._find_text_position(image_bytes, text, "exact", index)
    if position:
        return position, "exact"

    # 2. 再模糊匹配
    position = platform._find_text_position(image_bytes, text, "fuzzy", index)
    if position:
        return position, "fuzzy"

    # 3. 未找到
    return None, None
```

#### 2.4 执行流程

```
1. 获取完整截图
2. 在完整截图中用降级策略定位锚点文本 → 得到 (anchor_x, anchor_y)
3. 如果锚点未找到 → 返回失败："Anchor text not found: {anchor_text}"
4. 裁剪水平带状区域：
   - top = max(0, anchor_y - row_tolerance)
   - bottom = min(image_height, anchor_y + row_tolerance + 1)
5. 对裁剪后的图片执行目标查找（降级策略）
6. 如果目标未找到 → 返回失败："Target not found in row"
7. 计算目标在原图中的坐标：
   - target_x = crop_result.x
   - target_y = crop_result.y + top  # 加上裁剪偏移
8. 点击类：应用offset后执行点击
   检查类：返回坐标信息
```

#### 2.5 裁剪实现

使用Pillow裁剪：
```python
from PIL import Image
import io

def _crop_horizontal_band(image_bytes, anchor_y, tolerance):
    img = Image.open(io.BytesIO(image_bytes))
    width, height = img.size

    top = max(0, anchor_y - tolerance)
    bottom = min(height, anchor_y + tolerance + 1)

    cropped = img.crop((0, top, width, bottom))
    return cropped, top
```

#### 2.6 返回结果

**点击类ActionResult**：
```python
ActionResult(
    status=ActionStatus.SUCCESS,
    output="Clicked at ({x}, {y}) in row of \"{anchor_text}\""
)
```

**检查类ActionResult**：
```python
ActionResult(
    status=ActionStatus.SUCCESS,
    output="Found at ({x}, {y})"
)
# 失败时：
ActionResult(
    status=ActionStatus.FAILED,
    error="Target not found in row of \"{anchor_text}\""
)
```

---

## 文件修改清单

| 文件 | 修改内容 |
|------|---------|
| `worker/task/action.py` | 新增Action参数字段 |
| `worker/actions/coordinate.py` | 新增 `DoubleClickAction` |
| `worker/actions/ocr.py` | 新增 `OcrDoubleClickAction`, `OcrClickSameRowTextAction`, `OcrCheckSameRowTextAction` |
| `worker/actions/image.py` | 新增 `ImageDoubleClickAction`, `OcrClickSameRowImageAction`, `OcrCheckSameRowImageAction` |
| `worker/actions/__init__.py` | 注册新增的Action执行器 |
| `worker/platforms/base.py` | 新增 `double_click` 抽象方法，更新 `BASE_SUPPORTED_ACTIONS` |
| `worker/platforms/web.py` | 实现 `double_click` 方法 |
| `worker/platforms/windows.py` | 实现 `double_click` 方法 |
| `worker/platforms/mac.py` | 实现 `double_click` 方法 |
| `worker/platforms/android.py` | 实现 `double_click` 方法（模拟） |
| `worker/platforms/ios.py` | 实现 `double_click` 方法（模拟） |

## Action参数扩展

在 `Action` dataclass 中新增字段：

```python
# 同行定位参数
anchor_text: Optional[str] = None      # 锚点文本
anchor_index: Optional[int] = None     # 锚点索引
row_tolerance: Optional[int] = None    # 水平带范围（默认20）
target_index: Optional[int] = None     # 目标索引
```

## 测试策略

1. 单元测试：各Action执行器的定位逻辑
2. 平台测试：各平台的double_click实现
3. 集成测试：完整的同行定位流程
4. 边界测试：裁剪区域超出图片边界的处理

## 风险点

1. Android/iOS双击为模拟实现，可能存在时序问题
2. 模糊匹配可能导致误匹配，需要在日志中记录匹配模式
3. 裁剪区域过小可能导致目标部分被截断
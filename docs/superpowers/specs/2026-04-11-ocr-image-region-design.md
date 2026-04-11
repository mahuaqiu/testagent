# OCR/Image Action Region 参数支持设计

## 背景

在多画面会场场景中，同一截图里存在多个会场名称，无法精确判断整张图片里的画面变化。通过支持 `region` 参数，可以指定操作区域 `(x1, y1, x2, y2)`，让 OCR/图像识别只在指定区域内执行，从而实现精确定位。

## 方案选择

**方案 A：截图裁剪** — 在 action 执行时根据 region 参数裁剪截图，再传给 OCR/图像匹配。拿到结果后将相对坐标转换为全局坐标。

**选择原因**：改动最小，不依赖 OCR 服务端改造，兼容现有 OCR API。

## 涉及修改

### 1. Action 模型 (`worker/task/action.py`)

- 新增字段：`region: Optional[List[int]] = None`
- `from_dict()` 中解析 `region`
- `to_dict()` 中序列化 `region`

### 2. BaseActionExecutor (`worker/actions/base.py`)

新增两个辅助方法：

- `_crop_region(image_bytes, region) -> bytes`：按 `[x1, y1, x2, y2]` 裁剪图像
- `_offset_position(position, region) -> tuple`：将相对坐标 `(x, y)` 转为全局坐标 `(x+x1, y+y1)`

### 3. OCR Actions (`worker/actions/ocr.py`)

修改以下 10 个 action 的 `execute()` 方法：
- `OcrClickAction`, `OcrInputAction`, `OcrWaitAction`, `OcrAssertAction`, `OcrGetTextAction`, `OcrPasteAction`, `OcrMoveAction`, `OcrDoubleClickAction`, `OcrExistAction`, `OcrClickSameRowTextAction`, `OcrCheckSameRowTextAction`

统一模式：
```
screenshot = platform.take_screenshot(context)
if action.region:
    screenshot = self._crop_region(screenshot, action.region)
# 调用 OCR 查找
position = self._find_text_with_fallback(platform, screenshot, ...)
if position and action.region:
    position = self._offset_position(position, action.region)
# 后续操作使用转换后的全局坐标
```

### 4. Image Actions (`worker/actions/image.py`)

修改以下 8 个 action 的 `execute()` 方法：
- `ImageClickAction`, `ImageWaitAction`, `ImageAssertAction`, `ImageMoveAction`, `ImageDoubleClickAction`, `ImageExistAction`, `ImageClickNearTextAction`, `OcrClickSameRowImageAction`, `OcrCheckSameRowImageAction`

统一模式同 OCR Actions。

### 5. 不需要修改的部分

- `worker/platforms/base.py` — 不需要改，裁剪在 action 层完成
- `common/ocr_client.py` — 不需要改，OCR 服务端无感知

## 坐标处理示例

```
全屏截图: 1920x1080
region: [960, 540, 1920, 1080]  (右下区域)
OCR 在裁剪区域中找到文字，返回相对坐标 (100, 80)
全局坐标 = (960+100, 540+80) = (1060, 620)
点击 (1060, 620)
```

## 边界处理

- region 超出图像范围时，PIL.crop 会自动截断到图像边界
- region 无效时（x1>=x2 或 y1>=y2），返回错误
- region 为 None 或空时，行为不变（使用全屏截图）

## API 使用示例

```json
{
  "action_type": "ocr_click",
  "value": "会场名称",
  "region": [960, 0, 1920, 540]
}
```

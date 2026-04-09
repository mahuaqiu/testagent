# 文件上传与坐标获取功能设计

**日期**: 2026-04-09
**状态**: Draft

## 背景

1. **文件上传问题**：当前 Playwright 截图+点击的方式无法处理 Windows 系统文件上传弹窗（原生对话框），需要使用 Playwright 的文件选择器 API 来处理。
2. **坐标获取需求**：现有 action 只支持定位后直接操作，无法单独获取文字或图片的坐标列表，新增动作支持返回坐标供后续使用。

## 目标

新增三个 action：
1. `web_image_upload` - Web 平台文件上传（处理原生文件选择弹窗）
2. `ocr_get_position` - 获取文字坐标列表
3. `image_get_position` - 获取图片坐标列表

## 设计

### 1. web_image_upload 动作

#### 动作定义

| 参数 | 类型 | 说明 |
|------|------|------|
| `action_type` | string | `"web_image_upload"` |
| `x` | int | 触发上传弹窗的点击坐标 X |
| `y` | int | 触发上传弹窗的点击坐标 Y |
| `image_base64` | string | 要上传的图片 base64 编码 |

#### 行为流程

1. 验证参数（坐标、图片数据）
2. base64 解码，保存到临时文件
3. 使用 `page.expect_file_chooser()` 启动文件选择器监听
4. 在指定坐标 (x, y) 点击，触发系统文件选择弹窗
5. 通过 `file_chooser.set_files([temp_file_path])` 设置上传文件
6. 清理临时文件
7. 返回成功状态

#### 实现位置

- `worker/platforms/web.py` 中处理
- `SUPPORTED_ACTIONS` 添加 `"web_image_upload"`
- 新增 `_action_web_image_upload(action: Action) -> ActionResult`

#### 核心代码逻辑

```python
async def _async_upload(page, x, y, temp_file_path, timeout):
    async with page.expect_file_chooser(timeout=timeout) as fc_info:
        await page.mouse.click(x, y)
    file_chooser = await fc_info.value
    await file_chooser.set_files([temp_file_path])

def _action_web_image_upload(self, action: Action) -> ActionResult:
    # 1. 验证浏览器上下文和参数
    # 2. base64 解码，保存临时文件
    # 3. 调用 _run_async(_async_upload(...))
    # 4. 清理临时文件
    # 5. 返回结果
```

#### 错误处理

| 场景 | 错误信息 |
|------|----------|
| 无浏览器上下文 | `"Browser context not available"` |
| 缺少坐标 x/y | `"Click coordinates (x, y) are required"` |
| 缺少 image_base64 | `"image_base64 is required"` |
| base64 解码失败 | `"Invalid base64 image data"` |
| 未触发文件选择器（超时） | `"No file chooser dialog appeared within timeout"` |
| 文件设置失败 | `"Failed to set upload file: {error}"` |

#### 边界情况

- 临时文件清理：无论成功失败，都清理临时文件
- 文件选择器超时：默认 5000ms，可通过 action.timeout 配置
- 平台限制：仅 Web 平台支持，其他平台调用报错 `"Action only supported on web platform"`

### 2. ocr_get_position 动作

#### 动作定义

| 参数 | 类型 | 说明 |
|------|------|------|
| `action_type` | string | `"ocr_get_position"` |
| `value` | string | 要查找的文字内容（支持 `reg_` 前缀正则匹配） |

#### 返回格式

```json
{
  "positions": [[x1, y1], [x2, y2], ...]
}
```

坐标为文字区域的中心点，与现有 click 等动作使用的坐标一致。

#### 坐标顺序

1. 精确匹配的坐标（在前）
2. 模糊匹配的坐标（在后）
3. `reg_` 前缀：只使用正则匹配，按正则匹配结果顺序返回

#### 实现位置

- `worker/actions/position.py` 新建
- `OcrGetPositionExecutor` 类
- 注册到 `ActionRegistry`
- 调用 `platform._find_all_text_positions()` 获取坐标列表

#### 错误处理

| 场景 | 错误信息 |
|------|----------|
| OCR 客户端不可用 | `"OCR client not available"` |
| 缺少 value | `"Text value is required"` |
| 未找到匹配文字 | 返回空列表 `{"positions": []}`（SUCCESS 状态） |

### 3. image_get_position 动作

#### 动作定义

| 参数 | 类型 | 说明 |
|------|------|------|
| `action_type` | string | `"image_get_position"` |
| `image_base64` | string | 要查找的图片模板 base64 编码 |
| `threshold` | float | 匹配阈值（默认 0.8） |

#### 返回格式

```json
{
  "positions": [[x1, y1], [x2, y2], ...]
}
```

坐标为匹配图片区域的中心点。

#### 坐标顺序

按图像匹配结果顺序返回（有多少个就按顺序返回）。

#### 实现位置

- `worker/actions/position.py` 新建
- `ImageGetPositionExecutor` 类
- 注册到 `ActionRegistry`
- 调用 `platform._find_all_image_positions()` 获取坐标列表

#### 错误处理

| 场景 | 错误信息 |
|------|----------|
| 缺少 image_base64 | `"image_base64 is required"` |
| 未找到匹配图片 | 返回空列表 `{"positions": []}`（SUCCESS 状态） |

### 4. base.py 新增方法

在 `worker/platforms/base.py` 新增两个辅助方法：

#### _find_all_text_positions

```python
def _find_all_text_positions(self, image_bytes: bytes, text: str) -> List[tuple[int, int]]:
    """获取所有匹配文字的坐标列表。

    Args:
        image_bytes: 截图数据
        text: 目标文字（支持 reg_ 前缀正则匹配）

    Returns:
        坐标列表 [(x1, y1), (x2, y2), ...]
        顺序：精确匹配 → 模糊匹配
    """
```

#### _find_all_image_positions

```python
def _find_all_image_positions(
    self,
    source_bytes: bytes,
    template_base64: str,
    threshold: float = 0.8
) -> List[tuple[int, int]]:
    """获取所有匹配图片的坐标列表。

    Args:
        source_bytes: 源图像数据
        template_base64: 模板图像 base64
        threshold: 匹配阈值

    Returns:
        坐标列表 [(x1, y1), (x2, y2), ...]
    """
```

## 范围限制

- `web_image_upload` 仅 Web 平台支持
- `ocr_get_position` 和 `image_get_position` 所有平台可用

## 测试要点

### web_image_upload

1. 基础上传：点击上传按钮触发弹窗，成功上传图片
2. 无弹窗场景：点击不触发文件选择器，超时报错
3. 无效 base64：解码失败报错
4. 临时文件清理：验证临时文件被正确清理

### ocr_get_position

1. 单个文字：返回单个坐标
2. 多个相同文字：返回多个坐标
3. 精确+模糊匹配：精确匹配坐标在前
4. 正则匹配：使用 reg_ 前缀
5. 未找到：返回空列表

### image_get_position

1. 单个匹配：返回单个坐标
2. 多个匹配：返回多个坐标
3. 未找到：返回空列表
4. 阈值调整：不同阈值影响匹配结果
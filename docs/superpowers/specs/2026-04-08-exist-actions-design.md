# image_exist / ocr_exist Action 设计文档

## 背景

现有 `ocr_assert` 和 `image_assert` action 用于断言元素存在性，未找到时返回 FAILED 状态。某些场景需要"检查操作"——无论元素是否存在，action 本身都视为成功执行，只在结果中返回布尔值。

**重要变更**：本次实现同时统一所有 OCR action 的匹配策略，移除 `match_mode` 参数，采用自动降级匹配：
1. 优先精确匹配
2. 精确找不到才模糊匹配
3. `reg_` 开头的走正则匹配（不受降级约束）

## 设计目标

新增 `ocr_exist` 和 `image_exist` action：
- 无论元素是否存在，action 状态始终为 SUCCESS
- 通过 output 字段返回 JSON 格式的存在性判断
- 统一 OCR 匹配策略，移除 match_mode 参数

## Action 参数

### ocr_exist

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| value | string | ✓ | 要查找的文字（`reg_` 开头表示正则匹配） |
| index | int | | 选择第几个匹配结果，默认 0 |

**匹配策略**（所有 OCR action 统一）：
1. 优先精确匹配
2. 精确找不到才模糊匹配
3. `reg_` 开头的走正则匹配（不受降级约束）

**功能扩展**：`ocr_exist` 支持 `index` 参数选择第 N 个匹配，这是相对于 `ocr_assert` 的功能扩展（ocr_assert 不支持选择第 N 个匹配）。

### image_exist

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| image_base64 | string | ✓ | 图像模板 base64 编码 |
| threshold | float | | 匹配阈值，默认 0.8 |
| index | int | | 选择第几个匹配结果，默认 0 |

**功能扩展**：`image_exist` 支持 `index` 参数选择第 N 个匹配，这是相对于 `image_assert` 的功能扩展（image_assert 不支持选择第 N 个匹配）。

## 返回值设计

```json
{
  "status": "success",
  "output": "{\"exists\": true}"
}
```

未找到时：
```json
{
  "status": "success",
  "output": "{\"exists\": false}"
}
```

**说明**：
- `status` 始终为 `success`，表示检查操作成功执行
- `output` 返回 JSON 字符串，包含 `exists` 字段表示元素是否存在

## 实现位置

1. **执行器**：
   - `worker/actions/ocr.py` 添加 `OcrExistAction`
   - `worker/actions/image.py` 添加 `ImageExistAction`

2. **注册**：
   - `worker/platforms/base.py` 的 `BASE_SUPPORTED_ACTIONS` 添加 action 名称

3. **注册器**：
   - `worker/actions/__init__.py` 注册新 action 执行器

## 执行逻辑

### ocr_exist

```
1. 检查 OCR 客户端是否可用（不可用时返回 FAILED）
2. 检查 value 参数是否提供（缺失时返回 FAILED）
3. 获取当前截图
4. 使用统一匹配策略查找文字：
   - value 以 "reg_" 开头：正则匹配（去掉前缀）
   - 否则：精确匹配 → 模糊匹配（自动降级）
5. 返回 ActionResult:
   - status: SUCCESS
   - output: JSON {"exists": true/false}
```

**特殊场景**：
- 当 index 参数超出实际匹配数量时，返回 `{"exists": false}`（第 N 个元素不存在也是检查结果）

### image_exist

```
1. 检查 OCR 客户端是否可用（不可用时返回 FAILED）
2. 检查 image_base64 参数是否提供（缺失时返回 FAILED）
3. 获取当前截图
4. 使用 _find_image_position 查找图像（支持 index 和 threshold）
5. 返回 ActionResult:
   - status: SUCCESS
   - output: JSON {"exists": true/false}
```

**特殊场景**：
- 当 index 参数超出实际匹配数量时，返回 `{"exists": false}`（第 N 个元素不存在也是检查结果）

## 错误处理

以下情况返回 FAILED 状态（非正常执行失败）：
- OCR 客户端不可用
- 必填参数缺失（ocr_exist 的 value，image_exist 的 image_base64）
- 截图获取失败（底层异常向上抛出）

这些情况下 action 本身无法执行，属于操作失败，而非元素不存在。

**参数说明**：
- 不支持 `offset` 参数（与 ocr_assert/image_assert 保持一致，只判断存在性不进行点击操作）

## 测试要点

1. 正常场景：元素存在时返回 SUCCESS + {"exists": true}
2. 正常场景：元素不存在时返回 SUCCESS + {"exists": false}
3. 参数缺失：缺少必填参数时返回 FAILED
4. OCR 客户端不可用：返回 FAILED
5. index 参数：正确返回第 N 个匹配结果的存在性
6. index 超范围：当 index > 实际匹配数量时返回 {"exists": false}
7. threshold 参数：正确应用匹配阈值（仅 image_exist）
8. reg_ 前缀：正确处理正则快捷模式（仅 ocr_exist）
9. 输出格式：output 可通过 json.loads() 正确解析
10. 行为对比：元素不存在时，ocr_exist 返回 SUCCESS 而 ocr_assert 返回 FAILED
11. 行为对比：图像不存在时，image_exist 返回 SUCCESS 而 image_assert 返回 FAILED
12. 匹配策略：精确匹配成功时不尝试模糊匹配
13. 匹配策略：精确匹配失败时自动尝试模糊匹配
14. 匹配策略：reg_ 前缀直接使用正则匹配，不尝试精确/模糊

## 影响范围

- 新增文件：无
- 修改文件：
  - `worker/actions/base.py`（新增 `_find_text_with_fallback` 统一匹配方法）
  - `worker/actions/ocr.py`（新增 OcrExistAction，更新所有 OCR action 使用统一匹配策略，移除 match_mode 参数使用，更新文件头部注释）
  - `worker/actions/image.py`（新增 ImageExistAction，更新文件头部注释）
  - `worker/platforms/base.py`（BASE_SUPPORTED_ACTIONS）
  - `worker/actions/__init__.py`（注册新执行器，更新 __all__ 导出列表）
  - `worker/task/action.py`（ActionType 枚举：OCR_EXIST 添加到 OCR 操作组，IMAGE_EXIST 添加到图像操作组）

**影响的 OCR action**（统一匹配策略）：
- `ocr_click`、`ocr_input`、`ocr_wait`、`ocr_assert`、`ocr_paste`、`ocr_move`、`ocr_double_click`
- `ocr_click_same_row_text`、`ocr_check_same_row_text`
- 新增的 `ocr_exist`
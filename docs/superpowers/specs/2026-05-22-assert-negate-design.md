# ocr_assert/image_assert 支持 negate 参数

## 背景

现有 `ocr_assert` 和 `image_assert` action 用于断言元素**存在性**，未找到时返回 FAILED 状态。某些场景需要断言元素**不存在**（如验证错误提示消失、验证某个功能未出现）。

## 设计方案

新增 `negate` 参数，值为 `true` 时反转断言逻辑。

### 参数定义

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `negate` | bool | `false` | 是否断言不存在 |

### 执行逻辑

**ocr_assert**：
| negate | 文字存在 | 文字不存在 |
|--------|----------|------------|
| `false`（默认） | SUCCESS, "Text found: {value}" | FAILED, "Text not found: {value}" |
| `true` | FAILED, "Text found but expected not exist: {value}" | SUCCESS, "Text not found as expected: {value}" |

**image_assert**：
| negate | 图像存在 | 图像不存在 |
|--------|----------|------------|
| `false`（默认） | SUCCESS, "Image found" | FAILED, "Image not found" |
| `true` | FAILED, "Image found but expected not exist" | SUCCESS, "Image not found as expected" |

### 使用示例

```json
// 断言文字存在（默认行为）
{"action_type": "ocr_assert", "value": "登录成功"}

// 断言文字不存在
{"action_type": "ocr_assert", "value": "错误提示", "negate": true}

// 断言图像不存在
{"action_type": "image_assert", "image_base64": "...", "negate": true}
```

## 改动范围

| 文件 | 改动内容 |
|------|----------|
| `worker/task/action.py` | Action dataclass 新增 `negate: bool = False` 字段，`from_dict()` 解析该字段，`to_dict()` 序列化该字段 |
| `worker/actions/ocr.py` | `OcrAssertAction.execute()` 根据 `action.negate` 返回对应结果 |
| `worker/actions/image.py` | `ImageAssertAction.execute()` 根据 `action.negate` 返回对应结果 |

## 实现要点

- 最小改动：仅修改三个文件，不新增 action 类型
- 向后兼容：默认 `negate: false`，现有调用无需修改
- 语义清晰：`negate: true` 显式表达"断言不存在"
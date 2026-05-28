# ocr_assert 和 ocr_exist 支持 list 传参设计

## 需求概述

`ocr_assert` 和 `ocr_exist` 动作支持 `value` 参数传入字符串数组，一次 OCR 识别后验证所有文字都存在才算通过。

**核心约束**：只识别一次 OCR，不要多次调用 OCR 服务。

## 需求确认

| 项目 | 决定 |
|------|------|
| list 格式 | `["文字1", "文字2", "文字3"]` - 直接字符串数组 |
| 失败信息 | 返回所有不存在的文字列表：`Texts not found: ["文字2", "文字3"]` |
| ocr_exist 输出 | 保持兼容：`{"exists": true}`（list 全部存在才为 true） |
| negate=true | 要求所有文字都不存在才算通过（严格否定） |
| index 参数 | 两个动作都不支持，list 模式忽略 |

## 设计方案

### 1. Action 数据模型调整

**文件**：`worker/task/action.py`

修改 `value` 字段类型定义：

```python
# 原来
value: str | None = None

# 改为
value: str | list[str] | None = None
```

同时调整 `from_dict` 和 `to_dict` 方法处理 list 类型。

### 2. 新增 `_check_texts_in_ocr_result` 方法

**文件**：`worker/actions/base.py`

在 `BaseActionExecutor` 中新增：

```python
def _check_texts_in_ocr_result(
    self,
    platform: "PlatformManager",
    texts: list[str],
    match_mode: str = "exact"
) -> tuple[list[str], list[str]]:
    """
    在 OCR 缓存结果中批量检查多个文字是否存在。

    关键：不调用 OCR 服务，直接查询 platform 的 OCR 结果缓存。

    Args:
        platform: 平台管理器
        texts: 待检查的文字列表
        match_mode: 匹配模式

    Returns:
        (found_texts, not_found_texts): 找到的和未找到的文字列表
    """
    found = []
    not_found = []

    for text in texts:
        # 处理正则模式
        actual_text = text
        if match_mode == "regex" and not text.startswith("reg_"):
            actual_text = f"reg_{text}"

        # 在 OCR 缓存结果中查找（不重新调用 OCR）
        position = platform._find_text_position_cached(actual_text, "exact", 0)

        if position:
            found.append(text)
        else:
            not_found.append(text)

    return (found, not_found)
```

### 3. Platform 层新增缓存查询方法

**文件**：`worker/platforms/base.py`

在 `PlatformManager` 中新增：

```python
def _find_text_position_cached(
    self,
    text: str,
    match_mode: str = "exact",
    index: int = 0
) -> tuple[int, int] | None:
    """
    在 OCR 缓存结果中查找文字位置（不重新调用 OCR）。

    Args:
        text: 目标文字
        match_mode: 匹配模式
        index: 选择第几个匹配

    Returns:
        文字中心坐标，未找到返回 None
    """
    if not self.ocr_client:
        return None

    # 从 OCR 客户端获取缓存的识别结果
    ocr_results = self.ocr_client.get_last_ocr_results()
    if not ocr_results:
        return None

    # 在结果中查找文字（匹配策略由 OCR 客户端处理）
    return self.ocr_client.find_text_in_results(ocr_results, text, match_mode, index)
```

### 4. OCRClient 新增方法

**文件**：`common/ocr_client.py`

新增两个方法：

```python
def get_last_ocr_results(self) -> list | None:
    """
    获取最后一次 OCR 识别的原始结果列表。

    Returns:
        OCR 结果列表，每个元素包含 text, position 等信息
    """
    # 返回缓存的原始 OCR 结果
    pass

def find_text_in_results(
    self,
    ocr_results: list,
    text: str,
    match_mode: str = "exact",
    index: int = 0
) -> tuple[int, int] | None:
    """
    在 OCR 结果列表中查找指定文字的位置。

    Args:
        ocr_results: OCR 识别结果列表
        text: 目标文字
        match_mode: 匹配模式
        index: 选择第几个匹配

    Returns:
        文字中心坐标，未找到返回 None
    """
    # 匹配策略：精确匹配 → 模糊匹配
    # reg_ 前缀使用正则匹配
    pass
```

### 5. OcrAssertAction 改造

**文件**：`worker/actions/ocr.py`

核心逻辑：
- 获取截图，调用一次 OCR（结果缓存）
- 解析 `value` 为 texts 列表（支持单字符串和 list）
- 调用 `_check_texts_in_ocr_result` 批量检查
- 根据 `negate` 参数返回结果

```python
class OcrAssertAction(BaseActionExecutor):
    """OCR 文字断言。"""

    name = "ocr_assert"
    requires_ocr = True

    def execute(self, platform: "PlatformManager", action: Action, context: object | None = None) -> ActionResult:
        # 设置执行层级（Web 平台专用）
        self._set_level(platform, action)

        # 检查 OCR 客户端
        error = self._check_ocr_client(platform)
        if error:
            return error

        # 获取截图并 OCR 识别（只识别一次）
        screenshot = platform.take_screenshot(context)
        if action.region:
            screenshot = self._crop_region(screenshot, action.region)

        # 调用一次 OCR，结果会缓存在 ocr_client 中
        platform.ocr_client.recognize(screenshot)

        # 解析 texts 列表（支持单字符串和 list）
        texts = action.value if isinstance(action.value, list) else [action.value]
        if not texts or texts == [None]:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error="value is required",
            )

        # 在缓存结果中批量检查
        found, not_found = self._check_texts_in_ocr_result(platform, texts, action.match_mode)

        # 根据 negate 参数返回结果
        if action.negate:
            # negate=true: 要求所有文字都不存在
            if found:
                return ActionResult(
                    number=0,
                    action_type=self.name,
                    status=ActionStatus.FAILED,
                    error=f"Texts found but expected not exist: {found}",
                    ocr_info=self._get_last_ocr_info(platform),
                )
            else:
                return ActionResult(
                    number=0,
                    action_type=self.name,
                    status=ActionStatus.SUCCESS,
                    output=f"All texts not found as expected: {texts}",
                    ocr_info=self._get_last_ocr_info(platform),
                )
        else:
            # negate=false: 要求所有文字都存在
            if not_found:
                return ActionResult(
                    number=0,
                    action_type=self.name,
                    status=ActionStatus.FAILED,
                    error=f"Texts not found: {not_found}",
                    ocr_info=self._get_last_ocr_info(platform),
                )
            else:
                return ActionResult(
                    number=0,
                    action_type=self.name,
                    status=ActionStatus.SUCCESS,
                    output=f"All texts found: {texts}",
                    ocr_info=self._get_last_ocr_info(platform),
                )
```

### 6. OcrExistAction 改造

**文件**：`worker/actions/ocr.py`

核心逻辑：
- 去掉 `index` 支持（和 `ocr_assert` 保持一致）
- 获取截图，调用一次 OCR
- 解析 `value` 为 texts 列表
- 调用 `_check_texts_in_ocr_result` 批量检查
- 返回兼容格式 `{"exists": true/false}`

```python
class OcrExistAction(BaseActionExecutor):
    """检查文字是否存在。"""

    name = "ocr_exist"
    requires_ocr = True

    def execute(self, platform: "PlatformManager", action: Action, context: object | None = None) -> ActionResult:
        # 设置执行层级（Web 平台专用）
        self._set_level(platform, action)

        # 检查 OCR 客户端
        error = self._check_ocr_client(platform)
        if error:
            return error

        # 检查必填参数
        texts = action.value if isinstance(action.value, list) else [action.value]
        if not texts or texts == [None]:
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error="value is required",
            )

        # 获取截图并 OCR 识别（只识别一次）
        screenshot = platform.take_screenshot(context)
        if action.region:
            screenshot = self._crop_region(screenshot, action.region)

        # 谓用一次 OCR，结果缓存在 ocr_client 中
        platform.ocr_client.recognize(screenshot)

        # 在缓存结果中批量检查
        found, not_found = self._check_texts_in_ocr_result(platform, texts, action.match_mode)

        # 根据 negate 参数返回结果（保持兼容格式）
        if action.negate:
            # negate=true: 要求所有文字都不存在
            exists = len(found) == 0
        else:
            # negate=false: 要求所有文字都存在
            exists = len(not_found) == 0

        return ActionResult(
            number=0,
            action_type=self.name,
            status=ActionStatus.SUCCESS,
            output=json.dumps({"exists": exists}),
            ocr_info=self._get_last_ocr_info(platform),
        )
```

## 向后兼容

- `value` 传入单个字符串时，行为和之前完全一样
- `ocr_exist` 的输出格式保持不变（`{"exists": true}`）

## 使用示例

### 单文字（兼容原格式）

```json
{
  "action_type": "ocr_assert",
  "value": "登录"
}
```

### 多文字 list

```json
{
  "action_type": "ocr_assert",
  "value": ["用户名", "密码", "登录"]
}
```

### negate 场景

```json
{
  "action_type": "ocr_assert",
  "value": ["错误提示", "警告"],
  "negate": true
}
```

### ocr_exist list

```json
{
  "action_type": "ocr_exist",
  "value": ["首页", "设置", "帮助"]
}
```
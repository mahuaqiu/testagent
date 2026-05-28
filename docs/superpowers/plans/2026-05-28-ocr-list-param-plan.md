# OCR List 传参实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ocr_assert 和 ocr_exist 支持 value 传入字符串数组，一次 OCR 识别后批量验证多个文字。

**Architecture:** 在 OCRClient 缓存 recognize 结果，新增本地查找方法；在 BaseActionExecutor 新增批量检查方法；改造两个 Action 使用批量检查。

**Tech Stack:** Python dataclasses, httpx OCR client, pytest（本项目暂无测试目录，手动验证）

**Spec:** `docs/superpowers/specs/2026-05-28-ocr-list-param-design.md`

---

## 文件结构

| 文件 | 改动类型 | 职责 |
|------|----------|------|
| `worker/task/action.py` | 修改 | value 字段类型改为 `str | list[str] | None` |
| `common/ocr_client.py` | 修改 | 新增缓存和本地查找方法 |
| `worker/platforms/base.py` | 修改 | 新增 `_find_text_position_cached` 方法 |
| `worker/actions/base.py` | 修改 | 新增 `_check_texts_in_ocr_result` 方法 |
| `worker/actions/ocr.py` | 修改 | 改造 OcrAssertAction 和 OcrExistAction |

---

## Task 1: Action 数据模型调整

**Files:**
- Modify: `worker/task/action.py:74`

- [ ] **Step 1: 修改 value 字段类型**

```python
# 原来是
value: str | None = None

# 改为
value: str | list[str] | None = None
```

- [ ] **Step 2: 修改 from_dict 方法**

`from_dict` 方法中 `value` 的获取逻辑不需要修改，因为 `data.get("value")` 已经可以返回 list。

- [ ] **Step 3: 修改 to_dict 方法**

`to_dict` 方法中 `value` 的输出逻辑不需要修改，因为 `result["value"] = self.value` 已经可以处理 list。

- [ ] **Step 4: 验证类型变更**

启动 Worker，确认无类型错误。

---

## Task 2: OCRClient 缓存和本地查找方法

**Files:**
- Modify: `common/ocr_client.py:86-107, 109-161, 479-491`

- [ ] **Step 1: 添加缓存属性**

在 `OCRClient.__init__` 中添加 `_last_ocr_results` 缓存：

```python
def __init__(self, ...):
    ...
    self._last_response: dict = {}  # 已有
    self._last_ocr_results: list[TextBlock] = []  # 新增：缓存最后识别结果
```

- [ ] **Step 2: 修改 recognize 方法缓存结果**

```python
def recognize(self, ...):
    ...
    results = [
        TextBlock(...)
        for t in texts
    ]

    # 缓存识别结果（新增）
    self._last_ocr_results = results

    # 打印识别结果
    ...
    return results
```

- [ ] **Step 3: 新增 get_last_ocr_results 方法**

在 `get_last_ocr_info` 方法后添加：

```python
def get_last_ocr_results(self) -> list[TextBlock]:
    """
    获取最后一次 OCR 识别的结果列表（recognize 方法的返回值）。

    Returns:
        list[TextBlock]: OCR 识别结果列表
    """
    return self._last_ocr_results
```

- [ ] **Step 4: 新增 find_text_in_results 方法**

```python
def find_text_in_results(
    self,
    ocr_results: list[TextBlock],
    target_text: str,
    match_mode: str = "exact",
    index: int = 0
) -> tuple[int, int] | None:
    """
    在 OCR 结果列表中查找指定文字的位置（本地查找，不调用 OCR 服务）。

    匹配策略（与 OCR 服务端一致）：
    - 正则匹配：reg_ 前缀使用正则表达式
    - 精确匹配优先：target_text == text
    - 包含匹配降级：target_text in text

    Args:
        ocr_results: OCR 识别结果列表（TextBlock）
        target_text: 目标文字（以 reg_ 开头表示正则表达式）
        match_mode: 匹配模式（exact/fuzzy/contains，当前实现统一策略）
        index: 选择第几个匹配结果

    Returns:
        tuple[int, int] | None: 文字中心坐标
    """
    import re

    matches = []

    # 正则匹配（reg_ 前缀）
    if target_text.startswith("reg_"):
        pattern = target_text[4:]  # 去掉 reg_ 前缀
        for block in ocr_results:
            if re.search(pattern, block.text):
                matches.append(block)
    else:
        # 精确匹配优先，然后降级为包含匹配
        # 分两轮匹配：先收集精确匹配，再收集包含匹配
        exact_matches = []
        fuzzy_matches = []

        for block in ocr_results:
            if block.text == target_text:
                exact_matches.append(block)
            elif target_text in block.text:
                fuzzy_matches.append(block)

        # 精确匹配有结果时使用精确匹配，否则降级为包含匹配
        matches = exact_matches if exact_matches else fuzzy_matches

    if index < len(matches):
        return matches[index].center
    return None
```

- [ ] **Step 5: 验证 OCRClient 改动**

启动 Worker，确认 OCRClient 初始化无错误。

---

## Task 3: Platform 层新增缓存查询方法

**Files:**
- Modify: `worker/platforms/base.py:340-377`

- [ ] **Step 1: 新增 _find_text_position_cached 方法**

在 `_find_text_position` 方法后添加：

```python
def _find_text_position_cached(
    self,
    text: str,
    match_mode: str = "exact",
    index: int = 0
) -> tuple[int, int] | None:
    """
    在 OCR 缓存结果中查找文字位置（不重新调用 OCR 服务）。

    用于批量验证场景：一次 OCR 识别后，多次查询缓存结果。

    Args:
        text: 目标文字
        match_mode: 匹配模式
        index: 选择第几个匹配结果

    Returns:
        tuple[int, int] | None: 文字中心坐标
    """
    if not self.ocr_client:
        logger.error("OCR client not available")
        return None

    # 获取缓存的 OCR 结果
    ocr_results = self.ocr_client.get_last_ocr_results()
    if not ocr_results:
        logger.warning("No cached OCR results available")
        return None

    # 在本地结果中查找（不调用 OCR 服务）
    return self.ocr_client.find_text_in_results(ocr_results, text, match_mode, index)
```

- [ ] **Step 2: 验证 Platform 改动**

启动 Worker，确认无语法错误。

---

## Task 4: BaseActionExecutor 新增批量检查方法

**Files:**
- Modify: `worker/actions/base.py:158-323`

- [ ] **Step 1: 新增 _check_texts_in_ocr_result 方法**

在 `_smart_wait_before_loop` 方法后添加：

```python
def _check_texts_in_ocr_result(
    self,
    platform: "PlatformManager",
    texts: list[str],
    match_mode: str = "exact"
) -> tuple[list[str], list[str]]:
    """
    在 OCR 缓存结果中批量检查多个文字是否存在（不重新调用 OCR）。

    Args:
        platform: 平台管理器
        texts: 待检查的文字列表
        match_mode: 匹配模式

    Returns:
        tuple[list[str], list[str]]: (found_texts, not_found_texts)
    """
    found = []
    not_found = []

    for text in texts:
        # 处理正则模式
        actual_text = text
        if match_mode == "regex" and not text.startswith("reg_"):
            actual_text = f"reg_{text}"

        # 在 OCR 缓存结果中查找（不调用 OCR 服务）
        position = platform._find_text_position_cached(actual_text, match_mode, 0)

        if position:
            found.append(text)
        else:
            not_found.append(text)

    return (found, not_found)
```

- [ ] **Step 2: 验证 BaseActionExecutor 改动**

启动 Worker，确认无语法错误。

---

## Task 5: 改造 OcrAssertAction

**Files:**
- Modify: `worker/actions/ocr.py:217-276`

- [ ] **Step 1: 修改 OcrAssertAction.execute 方法**

完整替换：

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

        # 解析 texts 列表（支持单字符串和 list）
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

        # 调用一次 OCR，结果缓存在 ocr_client 中
        platform.ocr_client.recognize(screenshot)

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

- [ ] **Step 2: 验证 OcrAssertAction 改动**

启动 Worker，发送单文字和 list 请求测试。

---

## Task 6: 改造 OcrExistAction

**Files:**
- Modify: `worker/actions/ocr.py:715-760`

- [ ] **Step 1: 修改 OcrExistAction.execute 方法**

完整替换：

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

        # 调用一次 OCR，结果缓存在 ocr_client 中
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

- [ ] **Step 2: 验证 OcrExistAction 改动**

启动 Worker，发送单文字和 list 请求测试。

---

## Task 7: 手动验证

**无自动化测试目录，需手动验证**

- [ ] **Step 1: 启动 Worker**

```bash
python -m worker.main
```

- [ ] **Step 2: 发送单文字 ocr_assert 请求**

```bash
curl -X POST http://localhost:8080/task/execute -H "Content-Type: application/json" -d '{"platform": "web", "actions": [{"action_type": "start_app", "value": "https://example.com"}, {"action_type": "ocr_assert", "value": "Example"}]}'
```

预期：成功

- [ ] **Step 3: 发送 list ocr_assert 请求**

```bash
curl -X POST http://localhost:8080/task/execute -H "Content-Type: application/json" -d '{"platform": "web", "actions": [{"action_type": "start_app", "value": "https://example.com"}, {"action_type": "ocr_assert", "value": ["Example", "Domain"]}]}'
```

预期：成功，返回 `All texts found: ['Example', 'Domain']`

- [ ] **Step 4: 发送部分存在的 list 请求**

```bash
curl -X POST http://localhost:8080/task/execute -H "Content-Type: application/json" -d '{"platform": "web", "actions": [{"action_type": "start_app", "value": "https://example.com"}, {"action_type": "ocr_assert", "value": ["Example", "NotExist"]}]}'
```

预期：失败，返回 `Texts not found: ['NotExist']`

- [ ] **Step 5: 发送 negate list 请求**

```bash
curl -X POST http://localhost:8080/task/execute -H "Content-Type: application/json" -d '{"platform": "web", "actions": [{"action_type": "start_app", "value": "https://example.com"}, {"action_type": "ocr_assert", "value": ["NotExist1", "NotExist2"], "negate": true}]}'
```

预期：成功

- [ ] **Step 6: 发送 ocr_exist list 请求**

```bash
curl -X POST http://localhost:8080/task/execute -H "Content-Type: application/json" -d '{"platform": "web", "actions": [{"action_type": "start_app", "value": "https://example.com"}, {"action_type": "ocr_exist", "value": ["Example", "Domain"]}]}'
```

预期：返回 `{"exists": true}`

- [ ] **Step 7: 发送空 list 请求（边界情况）**

```bash
curl -X POST http://localhost:8080/task/execute -H "Content-Type: application/json" -d '{"platform": "web", "actions": [{"action_type": "start_app", "value": "https://example.com"}, {"action_type": "ocr_assert", "value": []}]}'
```

预期：失败，返回 `value is required`

- [ ] **Step 8: 发送正则模式 list 请求**

```bash
curl -X POST http://localhost:8080/task/execute -H "Content-Type: application/json" -d '{"platform": "web", "actions": [{"action_type": "start_app", "value": "https://example.com"}, {"action_type": "ocr_assert", "value": ["reg_Example", "Domain"], "match_mode": "regex"}]}'
```

预期：成功，正则模式生效

---

## Task 8: 提交代码

- [ ] **Step 1: 暂存改动**

```bash
git add worker/task/action.py common/ocr_client.py worker/platforms/base.py worker/actions/base.py worker/actions/ocr.py docs/superpowers/specs/2026-05-28-ocr-list-param-design.md docs/superpowers/plans/2026-05-28-ocr-list-param-plan.md
```

- [ ] **Step 2: 提交**

```bash
git commit -m "$(cat <<'EOF'
feat: ocr_assert 和 ocr_exist 支持 list 传参批量验证

- value 参数支持 str | list[str] 类型
- OCRClient 新增缓存和本地查找方法
- Platform 新增 _find_text_position_cached 方法
- BaseActionExecutor 新增 _check_texts_in_ocr_result 方法
- OcrAssertAction/OcrExistAction 改造支持批量验证
- 一次 OCR 识别后批量查询缓存结果
EOF
)"
```
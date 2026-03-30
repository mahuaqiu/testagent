# Web 平台 get_token Action 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 Web 平台专用 `get_token` action，捕获浏览器响应头中的 token 并返回。

**Architecture:** 在 WebPlatformManager 启动时监听 HTTP response 事件，将配置的 header 值存入 dict；创建 GetTokenAction 执行器返回捕获结果。

**Tech Stack:** Python, Playwright (async_api), dataclass

---

## 文件结构

| 文件 | 变更类型 | 责责 |
|------|----------|------|
| `worker/config.py` | 修改 | 新增 `token_headers` 字段到 PlatformConfig |
| `worker/platforms/web.py` | 修改 | 新增 token 捕获逻辑和 `get_captured_tokens()` 方法 |
| `worker/actions/web_token.py` | 创建 | 实现 `GetTokenAction` 执行器 |
| `worker/actions/__init__.py` | 修改 | 导入并注册 `GetTokenAction` |
| `config/worker.yaml` | 修改 | 新增 `platforms.web.token_headers` 配置项 |

---

### Task 1: 新增 PlatformConfig token_headers 字段

**Files:**
- Modify: `worker/config.py:79-128`

- [ ] **Step 1: 在 PlatformConfig 类中添加 token_headers 字段**

在 `worker/config.py` 的 `PlatformConfig` 类中，找到 `request_blacklist` 字段后添加：

```python
# Web 专用 - Token 捕获
token_headers: List[str] = field(default_factory=list)  # 要监听的 token header 名称列表
```

- [ ] **Step 2: 在 from_dict 方法中添加 token_headers 解析**

在 `PlatformConfig.from_dict()` 方法中，找到 `request_blacklist` 行后添加：

```python
token_headers=data.get("token_headers", []),
```

---

### Task 2: WebPlatformManager 新增 token 捕获逻辑

**Files:**
- Modify: `worker/platforms/web.py`

- [ ] **Step 1: 在 __init__ 中添加 token 相关属性**

在 `WebPlatformManager.__init__()` 方法中（约 line 70-86），在 `self.request_blacklist` 行后添加：

```python
# Token 捕获
self._token_headers: List[str] = config.token_headers or []
self._captured_tokens: Dict[str, str] = {}  # 存储捕获的 token
```

同时需要导入 `Dict` 类型（检查 typing 导入是否已包含 Dict）。

- [ ] **Step 2: 在 _async_start 中设置 response 监听**

在 `WebPlatformManager._async_start()` 方法末尾（约 line 196 之后），在 logger.info 行之前添加：

```python
# 设置 Token 捕获监听
if self._token_headers:
    await self._setup_token_capture()
```

- [ ] **Step 3: 添加 _setup_token_capture 方法**

在 `_async_start()` 方法之后（约 line 198），添加新方法：

```python
async def _setup_token_capture(self) -> None:
    """设置响应头 Token 捕获监听。"""
    async def on_response(response):
        headers = response.headers
        for header_name in self._token_headers:
            # HTTP headers 在 Playwright 中是小写的
            value = headers.get(header_name.lower())
            if value:
                self._captured_tokens[header_name] = value
                logger.debug(f"Captured token: {header_name}={value}")

    self._browser_context.on("response", on_response)
    logger.info(f"Token capture enabled for headers: {self._token_headers}")
```

- [ ] **Step 4: 添加 get_captured_tokens 方法**

在 `_async_start()` 方法区域之后添加：

```python
def get_captured_tokens(self) -> Dict[str, str]:
    """返回捕获的 tokens dict 副本。"""
    return dict(self._captured_tokens)
```

- [ ] **Step 5: 更新 SUPPORTED_ACTIONS**

在 `WebPlatformManager` 类定义开头（约 line 68），修改 `SUPPORTED_ACTIONS`：

```python
SUPPORTED_ACTIONS: Set[str] = {"navigate", "start_app", "stop_app", "get_token"}
```

- [ ] **Step 6: 提交变更**

```bash
git add worker/config.py worker/platforms/web.py
git commit -m "feat(web): 添加 token 捕获基础设施到 WebPlatformManager"
```

---

### Task 3: 创建 GetTokenAction 执行器

**Files:**
- Create: `worker/actions/web_token.py`

- [ ] **Step 1: 创建 web_token.py 文件**

创建 `worker/actions/web_token.py`：

```python
"""
Web Token 捕获 Action 执行器。

获取 Web 平台捕获的响应头 token。
"""

import json
from typing import Optional, TYPE_CHECKING

from worker.task import Action, ActionResult, ActionStatus
from worker.actions.base import BaseActionExecutor

if TYPE_CHECKING:
    from worker.platforms.base import PlatformManager


class GetTokenAction(BaseActionExecutor):
    """获取 Web 平台捕获的 token。"""

    name = "get_token"
    requires_context = False  # 不需要活跃的 page
    requires_ocr = False

    def execute(
        self,
        platform: "PlatformManager",
        action: Action,
        context: Optional[object] = None
    ) -> ActionResult:
        """
        执行 get_token action。

        Args:
            platform: 平台管理器
            action: 动作参数（无需参数）
            context: 执行上下文

        Returns:
            ActionResult: 包含捕获的 tokens dict
        """
        # 检查是否是 Web 平台
        if platform.platform != "web":
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error="get_token only supported on web platform",
            )

        # 获取捕获的 tokens
        tokens = platform.get_captured_tokens()

        return ActionResult(
            number=0,
            action_type=self.name,
            status=ActionStatus.SUCCESS,
            output=json.dumps(tokens),
        )
```

- [ ] **Step 2: 提交变更**

```bash
git add worker/actions/web_token.py
git commit -m "feat(web): 创建 GetTokenAction 执行器"
```

---

### Task 4: 注册 GetTokenAction

**Files:**
- Modify: `worker/actions/__init__.py`

- [ ] **Step 1: 导入 GetTokenAction**

在 `worker/actions/__init__.py` 的导入区域（约 line 34），在 `from worker.actions.cmd_exec import CmdExecAction` 后添加：

```python
from worker.actions.web_token import GetTokenAction
```

- [ ] **Step 2: 注册 GetTokenAction**

在 `_register_all_actions()` 函数中（约 line 62），在 `ActionRegistry.register(CmdExecAction())` 后添加：

```python
# Web Token Action
ActionRegistry.register(GetTokenAction())
```

- [ ] **Step 3: 添加到 __all__ 导出列表**

在 `__all__` 列表中（约 line 94），在 `"CmdExecAction",` 后添加：

```python
# Web Token Action
"GetTokenAction",
```

- [ ] **Step 4: 提交变更**

```bash
git add worker/actions/__init__.py
git commit -m "feat: 注册 GetTokenAction 到 ActionRegistry"
```

---

### Task 5: 更新配置文件

**Files:**
- Modify: `config/worker.yaml`

- [ ] **Step 1: 添加 token_headers 配置项**

在 `config/worker.yaml` 的 `platforms.web` 配置块中（约 line 22-42），在 `request_blacklist` 后添加：

```yaml
# Token 捕获：监听响应头中的 token header
token_headers:
  - "X-Auth-Token"
  # - "Authorization"  # 可根据需要添加更多
```

- [ ] **Step 2: 提交变更**

```bash
git add config/worker.yaml
git commit -m "config: 添加 web.token_headers 配置项"
```

---

### Task 6: 验证实现

**Files:**
- 无文件变更，手动验证

- [ ] **Step 1: 检查代码语法**

```bash
ruff check worker/config.py worker/platforms/web.py worker/actions/web_token.py worker/actions/__init__.py
```

Expected: 无语法错误

- [ ] **Step 2: 检查导入是否正确**

```bash
python -c "from worker.actions import GetTokenAction; print(GetTokenAction.name)"
```

Expected: 输出 `get_token`

- [ ] **Step 3: 检查 ActionRegistry 注册**

```bash
python -c "from worker.actions import ActionRegistry; print('get_token' in ActionRegistry.list_all())"
```

Expected: 输出 `True`

- [ ] **Step 4: 检查 WebPlatformManager 方法**

```bash
python -c "from worker.platforms.web import WebPlatformManager; print('get_token' in WebPlatformManager.SUPPORTED_ACTIONS)"
```

Expected: 输出 `True`

- [ ] **Step 5: 最终提交（如有遗漏的修复）**

如有语法检查发现的问题，修复后提交：

```bash
git add -A
git commit -m "fix: 修复 get_token 实现中的语法问题"
```

---

## 完整实现后的调用示例

```json
// HTTP POST /task/execute
{
  "platform": "web",
  "actions": [
    { "action_type": "start_app" },
    { "action_type": "navigate", "value": "https://example.com/login" },
    // ... 执行登录等操作，浏览器响应返回 X-Auth-Token ...
    { "action_type": "get_token" }
  ]
}

// 返回
{
  "number": 0,
  "action_type": "get_token",
  "status": "SUCCESS",
  "output": "{\"X-Auth-Token\": \"abc123xyz\"}"
}
```
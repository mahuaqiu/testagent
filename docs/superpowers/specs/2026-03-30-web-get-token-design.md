---
name: web-get-token-action
description: Web 平台 get_token action 设计文档
type: project
---

# Web 平台 get_token Action 设计文档

## 背景

多端自动化测试执行基建需要支持从 Web 浏览器响应头中捕获 token（如 `X-Auth-Token`），供后续 API 调用使用。

## 需求

1. 从浏览器启动时开始监听所有 HTTP 响应
2. 捕获响应头中指定的 token header（header name 可配置）
3. 调用 `get_token` action 时返回所有捕获到的 tokens

## 设计

### 架构

```
WebPlatformManager
├── _async_start()           # 启动浏览器时设置 response 监听
├── _token_headers           # List[str] 要监听的 header names
├── _captured_tokens         # Dict[str, str] 存储捕获的 token
├── get_captured_tokens()    # 返回 tokens dict 副本
└── SUPPORTED_ACTIONS        # 新增 "get_token"

GetTokenAction (新增)
├── name = "get_token"
├── execute()                # 调用 platform.get_captured_tokens()
└── requires_context = False # 不需要活跃的 page context

worker.yaml
└── platforms.web.token_headers  # token header 名称列表
```

### 配置

**worker.yaml**:
```yaml
platforms:
  web:
    token_headers:           # 要监听的 token header 名称列表
      - "X-Auth-Token"
      - "Authorization"      # 可配置多个
```

### Token 捕获逻辑

**WebPlatformManager**:
```python
# __init__ 中读取配置
self._token_headers = config.token_headers or []
self._captured_tokens: Dict[str, str] = {}

# _async_start() 中监听
async def _on_response(response):
    headers = response.headers
    for header_name in self._token_headers:
        value = headers.get(header_name.lower())  # HTTP headers 小写
        if value:
            self._captured_tokens[header_name] = value
            logger.debug(f"Captured token: {header_name}={value}")

self._browser_context.on("response", _on_response)
```

**关键点**:
- HTTP response headers 在 Playwright 中是小写的，需用 `header_name.lower()` 匹配
- 只捕获配置中指定的 header names
- 每次响应更新，保留最新值

### GetTokenAction 实现

**新文件**: `worker/actions/web_token.py`

```python
class GetTokenAction(BaseActionExecutor):
    """获取 Web 平台捕获的 token。"""

    name = "get_token"
    requires_context = False
    requires_ocr = False

    def execute(self, platform, action, context=None):
        # 检查是否是 Web 平台
        if platform.platform != "web":
            return ActionResult(
                number=0,
                action_type=self.name,
                status=ActionStatus.FAILED,
                error="get_token only supported on web platform",
            )

        tokens = platform.get_captured_tokens()

        return ActionResult(
            number=0,
            action_type=self.name,
            status=ActionStatus.SUCCESS,
            output=json.dumps(tokens),
        )
```

**WebPlatformManager 新增方法**:
```python
def get_captured_tokens(self) -> Dict[str, str]:
    """返回捕获的 tokens dict 副本。"""
    return dict(self._captured_tokens)
```

### 配置加载

**PlatformConfig 新增字段** (`worker/config.py`):
```python
class PlatformConfig:
    token_headers: Optional[List[str]] = None
```

**配置解析**:
从 `worker.yaml` 的 `platforms.web.token_headers` 加载到 `PlatformConfig`。

### 注册

**worker/actions/__init__.py**:
```python
from worker.actions.web_token import GetTokenAction

# 在 _register_all_actions() 中添加
ActionRegistry.register(GetTokenAction())
```

**WebPlatformManager**:
```python
SUPPORTED_ACTIONS: Set[str] = {"navigate", "start_app", "stop_app", "get_token"}
```

### 调用示例

**请求**:
```json
{
  "action_type": "get_token"
}
```

**响应**:
```json
{
  "number": 0,
  "action_type": "get_token",
  "status": "SUCCESS",
  "output": "{\"X-Auth-Token\": \"abc123\", \"Authorization\": \"Bearer xyz\"}"
}
```

## 文件变更清单

| 文件 | 变更 |
|------|------|
| `worker/platforms/web.py` | 新增 `_token_headers`, `_captured_tokens`, response 监听, `get_captured_tokens()`, 更新 `SUPPORTED_ACTIONS` |
| `worker/actions/web_token.py` | 新建，实现 `GetTokenAction` |
| `worker/actions/__init__.py` | 导入并注册 `GetTokenAction` |
| `worker/config.py` | 新增 `token_headers` 字段 |
| `config/worker.yaml` | 新增 `platforms.web.token_headers` 配置项 |
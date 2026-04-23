---
title: Request-ID 日志追踪设计
date: 2026-04-24
status: draft
---

# Request-ID 日志追踪设计

## 问题背景

当前日志查询困难，同一 action 的请求、返回日志分散在大量日志中，难以快速定位问题。需要引入 request-id 机制，让用户可以通过 `grep request-id` 快速检索同一 action 的完整执行日志链。

## 设计目标

1. 每个 action 执行开始时生成 UUID 作为 request-id
2. 所有日志输出自动携带 request-id 标记
3. OCR 服务 HTTP 请求 header 中传递 request-id
4. action 返回结果包含 request-id 字段
5. OCR/Image action 失败时，打印 OCR 服务返回的原始结果

## 核心设计

### 1. request-id 生命周期

**生成时机**：HTTP 请求入口处

| 入口 | 生成时机 | 传递方式 |
|------|----------|----------|
| `/task/execute` | 请求接收时 | 线程局部存储 |
| `/task/execute_async` | 请求接收时 | 线程局部存储 |

**传递路径**：
```
server.py (生成) → worker.execute_task() → _execute_actions()
    → PlatformManager → OCRClient (header)
    → ActionResult/TaskResult (返回字段)
```

**清理时机**：HTTP 响应返回后（通过 try-finally 确保清理）

### 2. 线程局部存储模块

**新增文件**：`common/request_context.py`

```python
import threading
import uuid

_request_context = threading.local()

def generate_request_id() -> str:
    """生成 request-id（UUID 格式）。"""
    return str(uuid.uuid4())

def set_request_id(request_id: str) -> None:
    """设置当前线程的 request-id。"""
    _request_context.request_id = request_id

def get_request_id() -> str | None:
    """获取当前线程的 request-id。"""
    return getattr(_request_context, 'request_id', None)

def clear_request_id() -> None:
    """清除当前线程的 request-id。"""
    if hasattr(_request_context, 'request_id'):
        del _request_context.request_id
```

**Why**: Python logging Filter 无法直接获取调用上下文，线程局部存储是标准的解决方案。

**How to apply**: 所有入口点调用 `set_request_id()`，退出点调用 `clear_request_id()`。

### 3. logging Filter

**改造文件**：`worker/logger.py`

```python
from common.request_context import get_request_id

class RequestIdFilter(logging.Filter):
    """自动注入 request-id 到日志记录。"""

    def filter(self, record):
        record.request_id = get_request_id() or '-'
        return True
```

**日志格式更新**：
```
%(asctime)s [%(request_id)s] %(levelname)s %(name)s: %(message)s
```

**示例输出**：
```
2026-04-24 10:30:15 [a1b2c3d4-e5f6-7890-abcd-ef1234567890] INFO worker.worker: Task started: platform=web
2026-04-24 10:30:16 [a1b2c3d4-e5f6-7890-abcd-ef1234567890] INFO common.ocr_client: OCR请求: /ocr/get_coord_by_text
2026-04-24 10:30:17 [a1b2c3d4-e5f6-7890-abcd-ef1234567890] INFO common.ocr_client: OCR响应: status=success
2026-04-24 10:30:18 [a1b2c3d4-e5f6-7890-abcd-ef1234567890] INFO worker.worker: Task completed
```

**Why**: Filter 方案侵入性最小，不需要修改每个 `logger.info()` 调用点。

### 4. HTTP 入口改造

**改造文件**：`worker/server.py`

```python
from common.request_context import generate_request_id, set_request_id, clear_request_id

@app.post("/task/execute")
async def execute_task(request: TaskRequest):
    request_id = generate_request_id()
    set_request_id(request_id)

    try:
        # 执行任务
        result = worker.execute_sync(...)
        result['request_id'] = request_id  # 添加到返回结果
        return result
    finally:
        clear_request_id()

@app.post("/task/execute_async")
async def execute_task_async(request: TaskRequest):
    request_id = generate_request_id()
    set_request_id(request_id)

    try:
        task_id, status = worker.execute_async(...)
        return {"task_id": task_id, "status": status, "request_id": request_id}
    finally:
        clear_request_id()
```

**异步任务传递**：后台线程需要继承 request-id

```python
# worker.py - execute_async()
def execute_async(...):
    request_id = get_request_id()  # 获取当前 request-id
    # TaskEntry 存储 request_id
    entry = TaskEntry(task_id=..., request_id=request_id, ...)

# _run_async_task()
def _run_async_task(entry):
    set_request_id(entry.request_id)  # 后台线程设置
    try:
        # 执行任务
        ...
    finally:
        clear_request_id()
```

**Why**: 异步任务在后台线程执行，需要显式传递 request-id。

### 5. OCRClient 改造

**改造文件**：`common/ocr_client.py`

```python
from common.request_context import get_request_id

class OCRClient:
    def _post(self, path: str, data: dict) -> dict:
        headers = {"X-Request-Id": get_request_id() or ""}
        response = self._client.post(url, json=data, headers=headers)
        ...
```

**Why**: OCR 服务需要 request-id 进行日志关联和问题排查。

### 6. OCR/Image action 失败日志增强

**改造文件**：`common/ocr_client.py`

```python
class OCRClient:
    # 缓存最后一次调用结果（用于失败诊断）
    _last_response: dict = {}

    def find_text(self, image_bytes, target_text, ...):
        response = self._post("/ocr/get_coord_by_text", {...})
        self._last_response = response  # 缓存原始响应
        # 解析响应返回 TextBlock 或 None
        ...

    def match_image(self, source_bytes, template_bytes, ...):
        response = self._post("/image/match", {...})
        self._last_response = response
        ...

    def get_last_response(self) -> dict:
        """获取最后一次 OCR/Image 调用的原始响应。"""
        return self._last_response
```

**Why**: OCR 调用在 OCRClient 中完成，缓存应该在 OCRClient 中管理。

**改造文件**：`worker/actions/base.py`

```python
class BaseActionExecutor:
    def _find_text_with_fallback(self, platform, image_bytes, text, ...):
        position = platform._find_text_position(image_bytes, text, ...)
        if position is None:
            # 获取 OCR 原始结果
            last_response = platform.ocr_client.get_last_response() if platform.ocr_client else {}
            logger.warning(
                f"OCR find text failed, target=\"{text}\", "
                f"ocr_response={last_response}"
            )
        return position

    def _find_image_position(self, platform, source_bytes, template_base64, ...):
        position = platform._find_image_position(source_bytes, template_base64, ...)
        if position is None:
            last_response = platform.ocr_client.get_last_response() if platform.ocr_client else {}
            logger.warning(
                f"Image match failed, threshold={threshold}, "
                f"ocr_response={last_response}"
            )
        return position
```

**Why**: OCR 失败时，原始响应可以帮助诊断是识别问题还是匹配问题。

### 7. TaskStore 改造

**改造文件**：`worker/task/store.py`

```python
@dataclass
class TaskEntry:
    task_id: str
    task: Task
    status: TaskStatus
    result: TaskResult | None = None
    thread: threading.Thread | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    request_id: str | None = None  # 新增字段
```

### 8. ActionResult/TaskResult 改造

**改造文件**：`worker/task/result.py`

```python
@dataclass
class ActionResult:
    number: int
    action_type: str
    status: ActionStatus
    request_id: str | None = None  # 新增字段
    duration_ms: int = 0
    ...

    def to_dict(self) -> Dict[str, Any]:
        result = {...}
        if self.request_id is not None:
            result["request_id"] = self.request_id
        return result

@dataclass
class TaskResult:
    task_id: Optional[str] = None
    request_id: str | None = None  # 新增字段
    status: TaskStatus = TaskStatus.PENDING
    ...

    def to_dict(self, include_task_id: bool = True) -> Dict[str, Any]:
        result = {...}
        if self.request_id is not None:
            result["request_id"] = self.request_id
        return result
```

## 文件变更清单

| 文件 | 改动类型 | 说明 |
|------|----------|------|
| `common/request_context.py` | 新增 | request-id 线程局部存储模块 |
| `worker/logger.py` | 修改 | 添加 RequestIdFilter，更新日志格式 |
| `worker/server.py` | 修改 | 入口生成 request-id，返回结果添加字段 |
| `worker/worker.py` | 修改 | 异步任务传递 request-id，ActionResult 填充 request_id |
| `worker/task/store.py` | 修改 | TaskEntry 增加 request_id 字段 |
| `worker/task/result.py` | 修改 | ActionResult/TaskResult 增加 request_id 字段 |
| `common/ocr_client.py` | 修改 | HTTP header 传递 request-id，缓存最后响应用于失败诊断 |
| `worker/actions/base.py` | 修改 | OCR/Image 失败时打印原始响应 |

## 使用示例

**日志查询**：
```bash
grep "a1b2c3d4-e5f6-7890-abcd-ef1234567890" worker.log
```

输出同一 action 的完整执行链：
```
2026-04-24 10:30:15 [a1b2c3d4...] INFO worker.server: Sync task raw request: {...}
2026-04-24 10:30:16 [a1b2c3d4...] INFO worker.worker: Task started: platform=web
2026-04-24 10:30:17 [a1b2c3d4...] INFO common.ocr_client: OCR请求: /ocr/get_coord_by_text
2026-04-24 10:30:18 [a1b2c3d4...] WARNING common.ocr_client: OCR find text failed, target="登录", ocr_result={"texts": []}
2026-04-24 10:30:19 [a1b2c3d4...] INFO worker.server: Sync task response: {"status": "failed", "request_id": "a1b2c3d4..."}
```

## 测试要点

1. 同步任务 request-id 正确生成和传递
2. 异步任务 request-id 正确传递到后台线程
3. 日志格式正确显示 request-id
4. OCR HTTP header 包含 X-Request-Id
5. 返回结果包含 request_id 字段
6. OCR 失败时日志包含原始 OCR 结果
7. 多线程并发时 request-id 不会混淆（线程安全）
# Request-ID 日志追踪实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 request-id 日志追踪机制，让用户可以通过 grep request-id 快速检索同一 action 的完整执行日志链。

**Architecture:** 使用线程局部存储传递 request-id，logging Filter 自动注入到日志，OCR HTTP header 传递，ActionResult/TaskResult 返回 request-id。

**Tech Stack:** threading.local, logging.Filter, FastAPI, httpx

---

## 文件结构

| 文件 | 责任 |
|------|------|
| `common/request_context.py` | request-id 线程局部存储，生成/设置/获取/清除 |
| `worker/logger.py` | RequestIdFilter，日志格式更新 |
| `worker/task/result.py` | ActionResult/TaskResult 增加 request_id 字段 |
| `worker/task/store.py` | TaskEntry 增加 request_id 字段 |
| `worker/server.py` | HTTP 入口生成 request-id，返回结果添加字段 |
| `worker/worker.py` | 异步任务传递 request-id，ActionResult 填充 |
| `common/ocr_client.py` | HTTP header 传递 request-id，缓存最后响应 |
| `worker/actions/base.py` | OCR/Image 失败时打印原始响应 |

---

### Task 1: 创建 request_context 模块

**Files:**
- Create: `common/request_context.py`

- [ ] **Step 1: 创建 request_context.py**

```python
"""
Request-ID 线程局部存储模块。

用于在多线程环境下传递 request-id，实现日志追踪。
"""

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

- [ ] **Step 2: 验证模块导入正常**

Run: `python -c "from common.request_context import generate_request_id, set_request_id, get_request_id, clear_request_id; print(generate_request_id())"`
Expected: 输出一个 UUID 字符串

- [ ] **Step 3: Commit**

```bash
git add common/request_context.py
git commit -m "feat: add request_context module for thread-local request-id storage"
```

---

### Task 2: 改造 logger 模块

**Files:**
- Modify: `worker/logger.py`

- [ ] **Step 1: 添加导入**

在文件顶部（约 line 12，在现有导入之后）添加：

```python
from common.request_context import get_request_id
```

- [ ] **Step 2: 添加 RequestIdFilter 类**

在导入语句之后、`get_default_log_path` 函数之前插入：

```python
class RequestIdFilter(logging.Filter):
    """自动注入 request-id 到日志记录。"""

    def filter(self, record):
        record.request_id = get_request_id() or '-'
        return True
```

- [ ] **Step 3: 在 setup_logging 中添加 Filter**

在 `root_logger.setLevel(log_level)` 之后（约 line 75）添加：

```python
    # 添加 RequestIdFilter
    request_id_filter = RequestIdFilter()
    root_logger.addFilter(request_id_filter)
```

- [ ] **Step 4: 更新日志格式**

将 `log_format` 行改为：

```python
    # 日志格式（包含 request_id）
    log_format = "%(asctime)s [%(request_id)s] %(levelname)s %(name)s: %(message)s"
```

- [ ] **Step 5: 验证日志格式**

Run: `python -c "
from common.request_context import set_request_id, clear_request_id, generate_request_id
from worker.logger import setup_logging
import logging

setup_logging('INFO')
logger = logging.getLogger('test')

# 无 request-id 时
logger.info('No request-id')

# 有 request-id 时
rid = generate_request_id()
set_request_id(rid)
logger.info('With request-id')
clear_request_id()
logger.info('After clear')
"`
Expected: 第一条日志 `[ - ]`，第二条 `[uuid]`，第三条 `[ - ]`

- [ ] **Step 6: Commit**

```bash
git add worker/logger.py
git commit -m "feat: add RequestIdFilter to auto-inject request-id in logs"
```

---

### Task 3: 改造 ActionResult/TaskResult

**Files:**
- Modify: `worker/task/result.py`

- [ ] **Step 1: 在 ActionResult 增加 request_id 字段**

在 `ActionResult` dataclass 中添加字段（在 `status` 字段之后）：

```python
    request_id: str | None = None  # 新增：request-id
```

修改 `from_dict` 方法添加读取：

```python
            request_id=data.get("request_id"),  # 新增
```

修改 `to_dict` 方法添加输出：

```python
        if self.request_id is not None:  # 新增
            result["request_id"] = self.request_id
```

- [ ] **Step 2: 在 TaskResult 增加 request_id 字段**

在 `TaskResult` dataclass 中添加字段（在 `task_id` 字段之后）：

```python
    request_id: str | None = None  # 新增：request-id
```

修改 `from_dict` 方法添加读取：

```python
            request_id=data.get("request_id"),  # 新增
```

修改 `to_dict` 方法添加输出：

```python
        # request_id 可选输出（新增）
        if self.request_id is not None:
            result["request_id"] = self.request_id
```

- [ ] **Step 3: 验证序列化**

Run: `python -c "
from worker.task.result import ActionResult, TaskResult, ActionStatus, TaskStatus

# 测试 ActionResult
ar = ActionResult(number=0, action_type='ocr_click', status=ActionStatus.SUCCESS, request_id='test-123')
print('ActionResult:', ar.to_dict())

# 测试 TaskResult
tr = TaskResult(task_id='task_1', request_id='test-123', status=TaskStatus.SUCCESS, platform='web')
print('TaskResult:', tr.to_dict())
"`
Expected: 输出包含 `request_id` 字段

- [ ] **Step 4: Commit**

```bash
git add worker/task/result.py
git commit -m "feat: add request_id field to ActionResult and TaskResult"
```

---

### Task 4: 改造 TaskStore

**Files:**
- Modify: `worker/task/store.py`

- [ ] **Step 1: 在 TaskEntry 增加 request_id 字段**

在 `TaskEntry` dataclass 中添加字段（在 `cancel_event` 字段之后）：

```python
    request_id: str | None = None  # 新增：用于异步任务传递 request-id
```

- [ ] **Step 2: Commit**

```bash
git add worker/task/store.py
git commit -m "feat: add request_id field to TaskEntry for async task propagation"
```

---

### Task 5: 改造 OCRClient

**Files:**
- Modify: `common/ocr_client.py`

- [ ] **Step 1: 添加导入**

在文件顶部添加：

```python
from common.request_context import get_request_id
```

- [ ] **Step 2: 在 __init__ 中添加 _last_response 属性**

在 `OCRClient.__init__` 方法中（`self._client` 初始化之后，约 line 105）添加：

```python
        self._last_response: dict = {}  # 缓存最后一次调用结果
```

- [ ] **Step 3: 添加 get_last_response 方法**

在类中添加新方法：

```python
    def get_last_response(self) -> dict:
        """获取最后一次 OCR/Image 调用的原始响应。"""
        return self._last_response
```

- [ ] **Step 4: 修改 _post 方法添加 header 和缓存**

修改 `_post` 方法（约 line 429），在方法开头添加 header：

```python
    def _post(self, path: str, data: dict) -> dict:
        """
        发送 POST 请求（带重试）。
        """
        last_error = None
        url = f"{self.base_url}{path}"

        # 添加 request-id header
        request_id = get_request_id()
        headers = {"X-Request-Id": request_id or ""}

        for attempt in range(self.retry + 1):
            try:
                logger.debug(f"OCR请求: {url}")
                response = self._client.post(url, json=data, headers=headers)
                response.raise_for_status()
                result = response.json()
                self._last_response = result  # 缓存响应
                logger.debug(f"OCR响应: status={result.get('status')}")
                return result
            except Exception as e:
                ...
```

并在最终失败时也缓存：

```python
        logger.error(f"OCR请求最终失败: {url}, 错误: {last_error}")
        error_result = {"status": "error", "error": str(last_error)}
        self._last_response = error_result  # 缓存错误结果
        return error_result
```

- [ ] **Step 5: Commit**

```bash
git add common/ocr_client.py
git commit -m "feat: add X-Request-Id header and cache last response in OCRClient"
```

---

### Task 6: 改造 actions/base.py

**Files:**
- Modify: `worker/actions/base.py`

- [ ] **Step 1: 在 _find_text_with_fallback 添加失败日志**

在方法末尾（返回前）添加：

```python
    # OCR 失败时打印原始响应
    if position is None and platform.ocr_client:
        last_response = platform.ocr_client.get_last_response()
        logger.warning(
            f"OCR find text failed, target=\"{text}\", "
            f"ocr_response={last_response}"
        )

    return position
```

- [ ] **Step 2: 在 _find_image_position 添加失败日志**

在方法末尾（返回前）添加：

```python
    # 图像匹配失败时打印原始响应
    if position is None and platform.ocr_client:
        last_response = platform.ocr_client.get_last_response()
        logger.warning(
            f"Image match failed, threshold={threshold}, "
            f"ocr_response={last_response}"
        )

    return position
```

- [ ] **Step 3: Commit**

```bash
git add worker/actions/base.py
git commit -m "feat: log OCR raw response on OCR/Image action failure"
```

---

### Task 7: 改造 server.py

**Files:**
- Modify: `worker/server.py`

- [ ] **Step 1: 添加导入**

在文件顶部添加：

```python
from common.request_context import generate_request_id, set_request_id, clear_request_id
```

- [ ] **Step 2: 修改 execute_task 函数**

在函数开头生成 request-id，在 try-finally 中管理：

```python
@app.post("/task/execute")
async def execute_task(request: TaskRequest):
    """同步执行任务。"""
    if not worker:
        raise HTTPException(status_code=503, detail="Worker not initialized")

    # 生成 request-id
    request_id = generate_request_id()
    set_request_id(request_id)

    try:
        # 记录原始请求数据（过滤 base64）
        logger.info(f"Sync task raw request: {_format_request_for_log(request)}")

        result = worker.execute_sync(...)

        # 添加 request_id 到返回结果
        result['request_id'] = request_id

        logger.info(f"Sync task response: {_format_result_for_log(result)}")
        return result

    except Exception as e:
        logger.error(f"execute_sync failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal error: {e}")

    finally:
        clear_request_id()
```

- [ ] **Step 3: 修改 execute_task_async 函数**

同样模式：

```python
@app.post("/task/execute_async")
async def execute_task_async(request: TaskRequest):
    """异步执行任务。"""
    if not worker:
        raise HTTPException(status_code=503, detail="Worker not initialized")

    # 生成 request-id
    request_id = generate_request_id()
    set_request_id(request_id)

    try:
        logger.info(f"Async task raw request: {_format_request_for_log(request)}")

        task_id, status = worker.execute_async(...)
        logger.info(f"Async task submitted: task_id={task_id}, status={status}")

        return {"task_id": task_id, "status": status, "request_id": request_id}

    except TaskConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))

    finally:
        clear_request_id()
```

- [ ] **Step 4: Commit**

```bash
git add worker/server.py
git commit -m "feat: generate request-id at HTTP entry and return in response"
```

---

### Task 8: 改造 worker.py

**Files:**
- Modify: `worker/worker.py`

- [ ] **Step 1: 添加导入**

在文件顶部添加：

```python
from common.request_context import get_request_id
```

- [ ] **Step 2: 修改 execute_async 传递 request_id**

在创建 `TaskEntry` 时获取并传递 request_id：

```python
def execute_async(...):
    ...
    # 获取当前 request-id（传递给后台线程）
    request_id = get_request_id()

    entry = TaskEntry(
        task_id=task.task_id,
        task=task,
        status=TaskStatus.RUNNING,
        request_id=request_id,  # 新增：传递 request-id
    )
    ...
```

- [ ] **Step 3: 修改 _run_async_task 设置 request_id**

在方法开头设置 request_id，在最外层 finally 清理：

```python
def _run_async_task(self, entry: TaskEntry) -> None:
    """后台线程执行异步任务。"""
    from common.request_context import set_request_id, clear_request_id

    task = entry.task
    platform = task.platform
    request_id = entry.request_id

    # 【重要】后台线程设置 request-id（在线程入口处）
    if request_id:
        set_request_id(request_id)

    context = None
    try:
        # 获取平台管理器
        manager = self.platform_managers.get(platform)
        ...
        # 执行动作列表
        result = self._execute_actions(...)

        # 确保 result 包含 request_id
        result.request_id = request_id

        entry.result = result
        entry.status = result.status

    except Exception as e:
        ...

    finally:
        # 【重要】在最外层 finally 清理 request-id（确保清理）
        if request_id:
            clear_request_id()

        # 清理执行上下文
        if context is not None:
            try:
                manager.close_context(context, close_session=False)
            except Exception as e:
                logger.warning(f"Failed to close context: {e}\n{traceback.format_exc()}")

        self.scheduler.release(platform, task.device_id)
        ...
```

- [ ] **Step 4: 修改 _execute_actions 填充 request_id**

在方法开头获取 request_id，填充到 ActionResult 和 TaskResult：

```python
def _execute_actions(...):
    """执行动作列表。"""
    started_at = datetime.now()
    actions_results = []
    request_id = get_request_id()  # 获取 request_id

    for i, action in enumerate(task.actions):
        ...
        result = manager.execute_action(context, action)
        result.number = i
        result.request_id = request_id  # 新增：填充 request_id
        actions_results.append(result)
        ...

    result = TaskResult(
        task_id=task.task_id,
        request_id=request_id,  # 新增：填充 request_id
        status=TaskStatus.SUCCESS,
        platform=task.platform,
        started_at=started_at,
        finished_at=datetime.now(),
        actions=actions_results,
    )
    ...
```

- [ ] **Step 5: Commit**

```bash
git add worker/worker.py
git commit -m "feat: propagate request-id to async task and ActionResult"
```

---

### Task 9: 集成验证

- [ ] **Step 1: 启动 Worker**

Run: `python -m worker.main`
Expected: Worker 正常启动，日志格式包含 `[request_id]`

- [ ] **Step 2: 发送测试请求**

Run: `curl -X POST http://localhost:8080/task/execute -H "Content-Type: application/json" -d '{"platform": "web", "actions": [{"action_type": "wait", "time": 1}]}'`
Expected: 返回 JSON 包含 `request_id` 字段

- [ ] **Step 3: 检查日志**

Run: `grep "<request_id值>" worker.log`
Expected: 看到同一 request-id 的完整执行日志链

- [ ] **Step 4: 验证线程隔离**

发送两个并发异步请求（不同平台），检查日志中各自的 request-id 不交叉污染。

- [ ] **Step 5: Final Commit**

```bash
git add -A
git commit -m "feat: complete request-id log tracing implementation"
```
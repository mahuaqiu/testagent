# Worker 执行内核与 Action 统一整改实施计划

> 日期：2026-07-12  
> 状态：评审稿  
> 涉及工程：`D:\code\autotest`、`D:\code\zq-platform`（仅平台后端）

## 一、目标与范围

本次改造一步到位替换 Worker 现有任务内核，不保留新旧两套任务实现，也不新增 API 版本前缀。现有 API 路径保持不变，平台后端同步适配新的状态和查询语义。

目标职责如下：

- 测试平台：负责全局任务生命周期、Worker/设备选择、跨 Worker 调度、重试决策和长期结果归档。
- Worker：负责本机资源编排、设备接入、任务可靠执行、短期结果和附件保存。
- `TaskService`：负责 Worker 本地任务生命周期、幂等、超时、取消和执行编排。
- `ResourceScheduler`：负责本机平台/设备资源的原子占用和释放，是资源忙碌状态的唯一事实源。
- `DeviceRegistry`：负责本机设备连接、服务和健康状态，是设备事实状态的唯一来源。
- `PlatformManager`：负责单平台会话和原子能力，不再参与任务存储、调度和上报。
- `ArtifactService`：负责截图、录屏、日志片段等附件的保存、查询和过期清理。

### 1.1 本次修改范围

- 替换 `Worker` 中现有同步/异步任务执行、`TaskStore` 和 `TaskScheduler`。
- 引入 SQLite，持久化 Worker 最近任务、动作结果、资源租约和附件元数据。
- 引入 `TaskService`、`ResourceScheduler`、`DeviceRegistry`、`ArtifactService`。
- 直接替换现有任务 API 的内部实现和部分语义。
- 调整平台后端的任务下发和轮询逻辑，不修改平台前端。
- 审查并统一整改所有 Action 的注册、参数校验、错误码、超时/取消、异常转换和结果结构。
- 整理 Worker 设备发现、监控、上报、日志和性能采集与新模块的边界。
- 增加任务内核、设备状态和全部 Action 的自动化测试。

### 1.2 明确不修改

- 各平台底层动作具体操作方式，例如 Playwright、uiautomator2、WDA、HDC、pyautogui 调用细节。
- OCR 服务及其识别算法。
- 测试用例工程。
- 推流协议及 H.264/JPEG 数据格式。
- `win-control`。
- Worker 安装和升级流程。
- 平台前端页面。

说明：Action 的公共执行框架和校验会修改，但不会主动改变已有动作的成功语义、坐标行为、匹配算法和平台底层调用方式。

## 二、现状问题清单

### 2.1 任务和资源调度

1. `TaskStore.is_busy()` 与 `TaskStore.store()` 分开执行，检查和占用不是原子操作，并发请求可能同时被接受。
2. `TaskStore._busy` 与 `TaskScheduler` 同时维护忙碌状态，存在两套事实源和释放时间窗口。
3. 同步任务只经过 `TaskScheduler`，异步任务同时经过 `TaskStore` 与 `TaskScheduler`，行为不一致。
4. 每个异步任务创建一个 daemon 线程，没有有界执行器、统一关闭和等待机制。
5. Worker 使用全局 `_status = busy/online`，多移动设备并行时，一个任务完成会错误地把仍在执行任务的 Worker 标记为空闲。
6. `GET /task/{task_id}` 查询终态后删除任务，网络重试会得到 404，结果查询不幂等。
7. 取消通过 `threading.Event` 实现，但大量 Action 使用 `time.sleep()` 或阻塞外部调用，无法及时响应。
8. 任务总超时仅在 Action 前后检查，单个 Action 阻塞时无法按总超时退出。
9. Worker 重启后内存任务全部丢失，平台无法区分不存在、丢失和执行中断。

### 2.2 设备状态

1. `Worker`、`DeviceMonitor`、Discoverer 和各 `PlatformManager` 分别持有设备列表或服务状态。
2. 在线、连接、服务可用、健康、忙碌等概念存在混用。
3. 设备监控回调直接更新 Worker 列表并触发上报，缺少统一版本和快照。
4. 资源忙碌状态不应写入设备事实记录，但当前平台管理器可反向查询 Scheduler，形成循环依赖。
5. Android/iOS/Harmony 的设备标识规则不完全统一，同名 `device_id` 可能在不同平台发生冲突。

### 2.3 Action 模型和注册

1. `ActionType`、`PlatformManager.BASE_SUPPORTED_ACTIONS`、`SUPPORTED_ACTIONS` 和 `ActionRegistry` 是多份能力清单，已存在不一致。
2. `Action` 是包含全部动作字段的宽 dataclass，缺少按动作类型的强校验。
3. 缺少统一的动作元数据：适用平台、是否需要上下文、是否需要设备服务、是否可取消、默认超时、参数模型。
4. Registry 允许同名 Action 静默覆盖，模块导入时自动注册，测试和启动行为依赖导入副作用。
5. 平台特有动作使用大量 `if action_type == ...`，通用动作走 Registry，能力声明和实际分发容易漂移。
6. Action 错误主要是英文字符串，平台只能解析文案，缺少稳定错误码和 `retryable` 属性。
7. 部分 Action 的结构化结果通过 `json.dumps()` 写入字符串，调用方需要二次解析。

### 2.4 Action 执行共性

1. OCR/Image 等待使用 `time.time()` 和 `time.sleep()`，不适合精确超时，也不能响应取消。
2. `_smart_wait_with_check()` 的预等待会消耗较大比例超时，且执行控制无法感知任务剩余时间。
3. OCR/Image Action 重复实现截图、区域裁剪、坐标回算、等待循环和结果构造，行为容易不一致。
4. 截图 Action 直接把 JPEG Base64 放进 `ActionResult`，增加内存和响应体积。
5. 失败截图也直接放进任务结果，缺少统一附件大小、保留期和清理策略。
6. `wait`、解锁、窗口激活等动作直接休眠，取消延迟明显。
7. `cmd_exec background=true` 创建脱离任务生命周期的 daemon 线程，任务取消和 Worker 退出无法管理后台命令。
8. 多处宽泛捕获 `Exception` 后只返回文本，错误类型、调用阶段和可重试性丢失。
9. 参数范围缺少统一限制，例如负超时、非法阈值、非法区域、过大录屏帧率和空命令。

### 2.5 测试覆盖

现有测试主要覆盖 Windows 截图、推流、Sidecar 和 Harmony 新增能力。以下核心区域缺少系统测试：

- 同步/异步任务状态机。
- 同一资源并发抢占。
- 任务取消和超时。
- 重复任务查询和幂等提交。
- Worker 重启恢复。
- SQLite 事务和过期清理。
- DeviceRegistry 并发更新和快照。
- OCR/Image/Coordinate/Cmd/Recording/Window/Unlock 等 Action 的参数矩阵和异常转换。

## 三、目标目录结构

在不移动各平台底层实现的前提下，新增并调整以下结构：

```text
worker/
├── runtime.py                         # WorkerRuntime，负责组装和生命周期
├── worker.py                          # 保留对外门面和兼容方法，逐步瘦身
├── server.py                          # 保留现有 API 路径，调用应用服务
├── errors.py                          # WorkerError、错误码和 HTTP 映射
│
├── task/
│   ├── models.py                      # WorkerTask、TaskAttempt、状态枚举
│   ├── service.py                     # TaskService
│   ├── executor.py                    # TaskExecutor，动作执行循环
│   ├── repository.py                  # TaskRepository 协议
│   ├── sqlite_repository.py           # SQLite 实现
│   └── recovery.py                    # 启动恢复和过期任务处理
│
├── scheduling/
│   ├── models.py                      # ResourceKey、ResourceLease
│   └── scheduler.py                   # ResourceScheduler
│
├── devices/
│   ├── models.py                      # DeviceRecord、状态枚举、快照
│   └── registry.py                    # DeviceRegistry
│
├── artifacts/
│   ├── models.py                      # ArtifactRef
│   └── service.py                     # ArtifactService
│
├── actions/
│   ├── spec.py                        # ActionSpec、ExecutionControl
│   ├── validation.py                  # 动作参数校验
│   ├── registry.py                    # 显式、不可重复注册
│   └── ...                            # 保留现有分组执行器
│
└── storage/
    ├── database.py                    # SQLite 连接、事务、迁移
    └── migrations/                    # schema_version 顺序迁移
```

测试目录对应新增：

```text
tests/
├── task/
├── scheduling/
├── devices/
├── artifacts/
├── actions/
└── api/
```

## 四、核心模型设计

### 4.1 WorkerTask 状态机

```text
accepted -> queued -> acquiring -> running -> releasing -> success
                                            -> releasing -> failed
                                            -> cancelling -> releasing -> cancelled
                                            -> releasing -> timeout

accepted/queued/acquiring -> cancelling -> releasing -> cancelled
非终态任务在 Worker 启动恢复时 -> interrupted
```

终态包括：

```text
success / failed / cancelled / timeout / interrupted
```

约束：

- 只有 `ResourceScheduler` 已确认释放资源后，任务才能写入终态。
- 状态转换使用 repository 事务和条件更新，禁止任意覆盖状态。
- `cancelled` 表示取消完成，不表示仅收到取消请求。
- 同步和异步任务使用同一个状态机和执行入口；同步 API 只是等待结果。

### 4.2 ResourceKey 与 ResourceLease

资源键统一包含平台：

```text
platform:web
platform:windows
platform:mac
device:android:{device_id}
device:ios:{device_id}
device:harmony_mobile:{device_id}
device:harmony_pc:{device_id}
```

`ResourceScheduler.acquire()` 在一个事务内完成：

1. 检查有效租约。
2. 创建租约。
3. 更新任务到 `acquiring/running`。
4. 返回 `ResourceLease`。

租约至少包含：

```text
resource_key
task_id
state
acquired_at
released_at
release_reason
```

首期继续采用“忙则拒绝”，不引入本地排队。API 冲突返回 HTTP 409 和 `DEVICE_BUSY`/`PLATFORM_BUSY`。

### 4.3 DeviceRecord

```text
device_id
platform
physical_id
name
model
os_version
connection_status: connected/disconnected
service_status: unknown/starting/ready/faulty
health_status: healthy/degraded/unhealthy
capabilities
metadata_json
last_seen_at
revision
```

规则：

- `DeviceRegistry` 不存储 busy；查询 `/worker_devices` 时合并 Scheduler 的资源快照。
- Discoverer 只提交发现事实，DeviceMonitor 只提交服务检测事实。
- 更新必须携带 revision 或 observed_at，旧检测结果不得覆盖新状态。
- `PlatformManager` 不再持有 Scheduler 引用。

### 4.4 ArtifactRef

```text
artifact_id
task_id
action_number
artifact_type
mime_type
relative_path
size
sha256
created_at
expires_at
```

文件布局：

```text
data/artifacts/{task_id}/{artifact_id}.{ext}
```

SQLite 只保存元数据，不保存截图和录屏二进制。

## 五、SQLite 设计

数据库位置：

```text
data/worker.db
```

启用：

- WAL 模式。
- `foreign_keys=ON`。
- `busy_timeout`。
- 每线程独立连接或受控连接工厂，禁止跨线程复用同一连接。
- 所有 schema 变更使用 `schema_version` 和顺序迁移。

### 5.1 表结构

`worker_tasks`：

```text
task_id TEXT PRIMARY KEY
platform_task_id TEXT NULL
idempotency_key TEXT NULL
request_id TEXT NULL
platform TEXT NOT NULL
device_id TEXT NULL
status TEXT NOT NULL
request_json TEXT NOT NULL
result_json TEXT NULL
error_code TEXT NULL
error_message TEXT NULL
retryable INTEGER NOT NULL DEFAULT 0
cancel_requested INTEGER NOT NULL DEFAULT 0
created_at TEXT NOT NULL
started_at TEXT NULL
finished_at TEXT NULL
expires_at TEXT NOT NULL
```

索引及约束：

- `UNIQUE(idempotency_key)`，空值除外。
- `INDEX(status)`。
- `INDEX(expires_at)`。
- `INDEX(platform_task_id)`。

`task_actions`：

```text
task_id TEXT NOT NULL
action_number INTEGER NOT NULL
action_type TEXT NOT NULL
status TEXT NOT NULL
started_at TEXT NULL
finished_at TEXT NULL
duration_ms INTEGER NULL
output_json TEXT NULL
error_code TEXT NULL
error_message TEXT NULL
PRIMARY KEY(task_id, action_number)
```

`resource_leases`：

```text
resource_key TEXT PRIMARY KEY
task_id TEXT NOT NULL
state TEXT NOT NULL
acquired_at TEXT NOT NULL
released_at TEXT NULL
release_reason TEXT NULL
```

只有活动租约保留唯一资源键；释放后归档或删除活动记录并写审计字段。

`artifacts`：保存 4.4 中的附件元数据。

### 5.2 启动恢复

Worker 启动时执行：

1. 将遗留的 `accepted/queued/acquiring/running/cancelling/releasing` 改为 `interrupted`。
2. 将对应活动租约标记为已释放，原因为 `worker_restart`。
3. 调用各 PlatformManager 的既有安全清理入口，清理残留 context；不改变平台底层实现。
4. 扫描附件目录，补记可识别的孤儿文件或删除超过宽限期的孤儿文件。
5. 启动定期清理任务，默认保留任务和附件 24 小时。

SQLite 是 Worker 的短期执行事实，不直接暴露数据库内容给平台用户。平台通过现有任务 API 获取状态和结果，平台数据库仍负责用户可见的长期记录。

## 六、现有 API 直接替换方案

不新增 `/api/v1`，沿用现有路径。

### 6.1 `POST /task/execute_async`

调整：

- 调用 `TaskService.submit()`。
- 支持请求头 `Idempotency-Key`；平台后端必须生成并传入。
- 参数校验、设备快照检查和资源冲突在接受前完成。
- 成功返回原有字段，并允许增加 `request_id`：

```json
{
  "task_id": "task_xxx",
  "status": "accepted",
  "request_id": "req_xxx"
}
```

- 资源冲突返回 HTTP 409，不创建失败任务。
- 相同幂等键、相同请求返回已有任务；相同键不同请求返回 HTTP 409 `IDEMPOTENCY_CONFLICT`。

### 6.2 `POST /task/execute`

调整：

- 同样通过 `TaskService.submit()` 执行，不再走独立同步逻辑。
- API 等待任务终态后返回结果，保持不返回 `task_id` 的现有外部契约。
- HTTP 客户端断开不等价于取消任务，任务继续可靠执行；由总超时或显式取消结束。
- 同步等待必须使用受控 future，不阻塞 FastAPI 事件循环。

### 6.3 `GET /task/{task_id}`

调整：

- 改为幂等查询，任何查询都不删除记录。
- 返回 SQLite 中的当前状态和结果。
- 终态在保留期内可重复查询。
- 不存在或已过期返回 404 `TASK_NOT_FOUND`。
- 动作 `output` 返回结构化 JSON，不再返回 JSON 字符串。

### 6.4 `DELETE /task/{task_id}`

调整：

- 改为幂等取消请求。
- 非终态任务设置 `cancel_requested=1` 并触发 `ExecutionControl.cancel_event`。
- 接收取消后返回 `cancelling`；资源释放后查询结果为 `cancelled`。
- 已终态任务返回当前终态，不删除任务。
- 不再通过 DELETE 删除查询结果；过期由后台统一清理。

### 6.5 `GET /worker_devices`

调整：

- 设备事实来自 `DeviceRegistry`。
- busy/task_id 来自 `ResourceScheduler` 快照。
- Worker 状态由生命周期和运行任务计数计算，不再手工赋值。
- 尽量保持现有响应字段，新增字段只做向后兼容扩展。

### 6.6 `POST /devices/refresh`

调整：

- 触发 Discoverer 刷新并写入 `DeviceRegistry`。
- 返回本次刷新 revision 和设备快照。
- 不直接改写 Worker 私有设备列表。

### 6.7 平台后端同步修改

`D:\code\zq-platform\backend-fastapi` 修改范围：

- 下发异步任务时生成稳定 `Idempotency-Key`。
- 轮询终态后不再依赖 Worker 的“一次性查询”语义。
- 识别 `accepted/acquiring/running/cancelling/releasing/interrupted` 状态。
- 根据 `error.code` 和 `retryable` 判断是否重试，不解析错误文案。
- Worker 返回 `interrupted` 时，由平台决定失败或创建新执行尝试。
- 保持现有前端响应模型，必要的新字段在后端转换，不修改前端。

## 七、统一错误模型

新增 `WorkerError`：

```python
class WorkerError(Exception):
    code: str
    message: str
    retryable: bool
    details: dict[str, Any]
```

第一批错误码：

```text
INVALID_REQUEST
IDEMPOTENCY_CONFLICT
UNSUPPORTED_PLATFORM
UNSUPPORTED_ACTION
PLATFORM_UNAVAILABLE
PLATFORM_BUSY
DEVICE_NOT_FOUND
DEVICE_DISCONNECTED
DEVICE_SERVICE_UNAVAILABLE
DEVICE_BUSY
ACTION_FAILED
ACTION_TIMEOUT
TASK_TIMEOUT
TASK_CANCELLED
TASK_INTERRUPTED
OCR_SERVICE_UNAVAILABLE
COMMAND_TIMEOUT
ARTIFACT_TOO_LARGE
ARTIFACT_NOT_FOUND
INTERNAL_ERROR
```

HTTP 映射：

- 400：参数和动作校验失败。
- 404：任务、设备或附件不存在。
- 409：资源冲突、幂等冲突、非法状态转换。
- 422：保留 FastAPI 请求模型错误。
- 503：平台或设备服务不可用。
- 500：未分类内部错误。

任务已经被接受后的执行错误仍通过任务结果返回，不把动作失败转换为 HTTP 500。

## 八、Action 统一整改设计

### 8.1 ActionSpec 单一能力来源

每个 Action 注册一个 `ActionSpec`：

```text
name
executor
supported_platforms
requires_context
requires_device_service
default_timeout_ms
interruptible
validation_schema
security_level
```

规则：

- `ActionRegistry.register()` 遇到重名立即失败，不允许覆盖。
- 启动时显式调用 `register_builtin_actions()`，移除依赖导入副作用的自动注册。
- `ActionType` 不再单独维护不完整枚举；由 Registry 导出动作清单供校验和上报。
- `PlatformManager.get_supported_actions()` 根据平台特有动作和 Registry 的平台声明计算。
- 启动自检确保“声明支持的动作一定有分发器”。

### 8.2 参数校验

保留 API 接收字典，但在任务接受阶段按动作类型校验。可采用 Pydantic discriminated union，或使用独立校验器避免一次迁移改动过大。

通用限制：

- `action_type` 非空且已注册。
- `timeout` 范围 `1..task_remaining_ms`，不允许负数。
- `threshold` 范围 `0..1`。
- `index/anchor_index/target_index` 非负。
- `region` 必须为四个整数且 `x2>x1, y2>y1`。
- 坐标必须是整数；是否允许负坐标由动作明确声明。
- `duration/wait/time/recording_timeout` 必须非负并受配置上限约束。
- `fps`、录屏时长、命令输出和输入文本长度设置上限。
- 未知字段是否拒绝需与平台现有请求核对；建议首期记录警告并忽略，下一版本再严格拒绝。

### 8.3 ExecutionControl

所有执行器接收：

```python
@dataclass
class ExecutionControl:
    deadline_monotonic: float | None
    cancel_event: threading.Event

    def remaining_ms(self) -> int | None: ...
    def checkpoint(self) -> None: ...
    def wait(self, seconds: float) -> None: ...
```

规则：

- 使用 `time.monotonic()`，禁止用系统时间计算超时。
- `control.wait()` 使用 `cancel_event.wait(timeout)`，替代 Action 中的 `time.sleep()`。
- OCR/Image 每次截图、识别和循环休眠前后执行 checkpoint。
- 外部 HTTP/子进程超时不得超过任务剩余时间。
- 当前无法中断的第三方调用在返回后立即检查取消/超时；首期不引入子进程隔离。

### 8.4 ActionResult

统一字段：

```text
number
action_type
status
started_at
finished_at
duration_ms
output: dict/list/string/number/bool/null
error: {code, message, retryable, details} | null
artifacts: list[ArtifactRef]
context_update: 仅 TaskExecutor 内部使用，不序列化
```

禁止执行器返回已经 JSON 编码的字符串。历史字段可在 API 序列化层做兼容，但内部只保存结构化值。

## 九、各 Action 分组整改任务

### 9.1 坐标和基础动作 `coordinate.py`

涉及：`click/right_click/double_click/move/input/paste/swipe/drag/press/screenshot/wait`。

整改：

1. 将坐标、终点、文本和按键校验前移到任务接受阶段。
2. 明确 `input` 与 `paste` 的 `text/value` 兼容规则，内部统一为一个字段。
3. `wait` 改为 `ExecutionControl.wait()`，支持立即取消和任务剩余超时。
4. `screenshot` 不再生成 Base64，调用 `ArtifactService.save_bytes()` 返回附件引用。
5. `paste` 的剪贴板保存和恢复使用严格 `try/finally`，恢复失败作为警告详情，不覆盖主动作结果。
6. 平台不支持右键、鼠标移动等能力时返回 `UNSUPPORTED_ACTION`，不依赖底层异常文本。
7. 统一 duration、steps、click_duration 的默认值和范围。

验收测试：每个动作覆盖正常、缺参、边界值、平台不支持、底层异常和取消。

### 9.2 OCR 动作 `ocr.py`

涉及全部 `ocr_*` 动作。

整改：

1. 抽取统一 `capture_and_locate_text()`，集中处理截图、region 裁剪、坐标回算、match_mode 和 index。
2. 抽取统一 `poll_until()`，替换重复等待循环和 `_smart_wait` 双阶段实现。
3. OCR 客户端不可用统一返回 `OCR_SERVICE_UNAVAILABLE`。
4. `ocr_wait` 的 `time` 前置等待改为可取消等待，并计入总超时。
5. `ocr_assert`、`ocr_exist` 和同行检查统一 negate/exists 语义和结构化输出。
6. `ocr_get_text`、`ocr_get_position` 返回结构化数组，不再 JSON 字符串。
7. 同行定位共享同一套 anchor/target/row_tolerance 校验和坐标回算。
8. region 越界由统一裁剪器校验，不允许 Pillow 异常直接冒泡。

验收测试：精确/正则、index、多结果、region、negate、同行、OCR 不可用、超时和取消。

### 9.3 图像动作 `image.py`、`position.py`

整改：

1. 抽取 `capture_and_locate_image()`，统一 Base64 解码、阈值、index、region 和坐标回算。
2. Base64 非法、空模板和超大模板在接受阶段拒绝。
3. `image_wait` 使用统一可取消轮询。
4. `image_assert/image_exist` 统一 negate/exists 输出。
5. 近文本和同行图像动作复用 OCR anchor 定位组件，避免两套同行算法漂移。
6. `image_get_position` 返回结构化坐标数组。
7. 图像解码和模板匹配异常映射稳定错误码。

验收测试：阈值边界、非法 Base64、多目标、region、同行、超时、取消和底层匹配异常。

### 9.4 命令动作 `cmd_exec.py`

整改：

1. 保持现有 `shell=True` 业务语义，但增加配置开关、命令长度和超时上限，并在计划实施时确认 Worker API 鉴权已生效。
2. 前台命令超时取 `min(action.timeout, task.remaining)`，继续使用进程树终止能力。
3. stdout/stderr 设置最大采集字节数，标记 `truncated`，避免结果撑爆内存和 SQLite。
4. `background=true` 不再创建匿名 daemon 线程；由受控 BackgroundProcessRegistry 管理进程、状态和 Worker 关闭清理。
5. 后台命令返回 `process_id/background_command_id`，明确它不随任务成功表示命令成功。
6. 命令超时、启动失败、非零退出码使用不同错误码和结构化输出。

验收测试：成功、非零退出、超时、输出截断、后台启动、关闭清理和鉴权/配置禁用。

### 9.5 录屏动作 `recording.py`

整改：

1. 保持 ScreenManager/推流协议不变，仅调整任务和附件边界。
2. `start_recording` 校验文件名，禁止绝对路径和目录穿越。
3. 输出路径只能由 ArtifactService 分配，Action 不直接拼接任意路径。
4. `stop_recording` 将生成文件登记为 ArtifactRef，校验文件存在、大小和格式元数据。
5. 明确重复 start、无 start 的 stop、任务取消、Worker 关闭时的行为。
6. 录屏会话按资源键管理，避免不同设备或平台名称冲突。
7. 任务结束时若仍有该任务启动的录屏，按配置自动停止并登记附件。

验收测试：正常开始/停止、重复调用、路径穿越、取消、超时、空文件和跨任务隔离。

### 9.6 窗口动作 `window.py`

整改：

1. 统一 `value/name/match_by` 参数规则，接受阶段校验。
2. 轮询等待改为 ExecutionControl，子进程 timeout 受任务剩余时间限制。
3. 区分窗口未找到、进程未找到、激活失败和不支持平台。
4. 保留 Windows/Mac 当前实现，不改底层激活算法。
5. 宽泛异常补充阶段信息和错误码，避免吞掉关键诊断。

### 9.7 解锁动作 `unlock.py`

整改：

1. 将多处固定 sleep 改为可取消等待。
2. 屏幕状态探测失败不能简单等同于“屏幕关闭”，返回 degraded 诊断并按既有兼容策略继续或失败。
3. 解锁密码、点击序列和设备类型参数增加严格校验，日志中禁止打印敏感内容。
4. 分辨率探测、截图解析和设备命令异常使用稳定错误码。
5. 保持 Android/iOS/Harmony 当前解锁步骤不变。

### 9.8 手势、Token 与其他动作

- `pinch`：校验 direction、scale、duration 和平台能力；统一异常映射。
- `get_token`：保持 Web 会话缓存逻辑，明确 token 不存在与 Web 会话不存在的错误区别；日志中不得打印 token。
- 平台特有 `start_app/stop_app/navigate/new_page/switched_page/close_page/set_resolution/set_volume/audio_device`：不改底层实现，但纳入 ActionSpec、统一校验、ExecutionControl 和错误模型。
- `activate_window/start_recording/stop_recording/cmd_exec` 等无 context Action 仍可能需要平台或资源信息，ActionSpec 单独声明，不再只用 `requires_context` 推断是否启动平台。

## 十、Worker 其他业务整改

### 10.1 WorkerRuntime 与生命周期

启动顺序：

```text
配置和日志
-> SQLite 迁移
-> Task 恢复
-> PlatformRegistry
-> DeviceRegistry/DeviceMonitor
-> ResourceScheduler
-> Artifact 清理器
-> Reporter
-> 接受 HTTP 任务
```

停止顺序：

```text
停止接受新任务
-> 请求取消并等待运行任务宽限期
-> 停止任务关联录屏/后台命令
-> 释放 context 和资源租约
-> 停止设备监控和上报
-> 停止平台管理器
-> 关闭 SQLite
```

### 10.2 DeviceMonitor

- 保留现有发现和服务检测逻辑。
- 移除内部作为最终设备列表的职责，所有结果写入 DeviceRegistry。
- 循环等待统一使用 stop event，避免固定 sleep。
- 对连续失败增加退避和状态降级，单次失败不立即判定物理设备离线。
- 上报读取不可变快照，避免遍历时设备列表变化。

### 10.3 Reporter

- 设备数据来自 DeviceRegistry 快照。
- Worker busy 来自 ResourceScheduler 活动租约计数。
- HTTP 失败分类为状态码、连接、超时，增加有界退避；不在本次引入外部消息队列。
- 上报失败不能影响任务终态写入。
- 保持现有平台注册地址和载荷兼容，必要新增字段由平台后端兼容接收。

### 10.4 日志与可观测性

- 任务、Action、资源租约、设备状态变化统一携带 `request_id/task_id/device_id/resource_key`。
- 错误日志记录 `error_code` 和阶段，用户响应保留简洁 message。
- 禁止日志记录 token、密码、完整 Base64、超长命令输出。
- 对任务耗时拆分：等待资源、创建 context、Action、释放资源。
- 现有日志查询接口保持不变。

### 10.5 性能采集

- 性能采集不是 TaskService 的任务，不强制迁入任务表。
- 它对设备的占用语义需显式定义：只读采集可与测试并行，可能干扰设备的操作必须申请独立资源或与设备资源互斥。
- 本次保留现有采集 API 和上报格式，只统一设备查询来源和生命周期关闭。

### 10.6 Web 缓存清理

- 不再依据全局 `_status == online` 判断空闲。
- 查询 `ResourceScheduler` 的 `platform:web` 是否空闲后再清理。
- 缓存清理状态可继续使用现有 JSON，后续可迁入 SQLite；本次不扩大范围。

## 十一、实施步骤与提交拆分

### 步骤 1：建立基线和契约测试

文件：

- 新增 `tests/api/test_task_api_contract.py`
- 新增 `tests/actions/test_action_registry_contract.py`
- 新增 `tests/task/test_current_task_behavior.py`

工作：

1. 固化现有 API 请求字段和成功结果中平台依赖字段。
2. 记录本次明确改变的语义：查询不删除、DELETE 不删除、异步初始状态、结构化错误。
3. 建立所有平台注册动作与实际分发一致性的契约测试。

验收：测试可以区分“必须兼容字段”和“本次有意修改语义”。

### 步骤 2：错误模型、ActionSpec 和校验框架

文件：

- 新增 `worker/errors.py`
- 新增 `worker/actions/spec.py`
- 新增 `worker/actions/validation.py`
- 修改 `worker/actions/registry.py`
- 修改 `worker/actions/__init__.py`
- 修改 `worker/task/action.py`
- 修改 `worker/platforms/base.py`

工作：实现单一动作能力来源、显式注册、重复注册失败、请求预校验和结构化错误。

验收：Registry 动作、平台支持动作和分发实现完全一致；非法动作在创建 context 前失败。

### 步骤 3：SQLite 基础设施和 Repository

文件：

- 新增 `worker/storage/database.py`
- 新增 `worker/storage/migrations/001_initial.sql` 或等价 Python 迁移
- 新增 `worker/task/repository.py`
- 新增 `worker/task/sqlite_repository.py`
- 新增 `worker/task/recovery.py`

工作：实现事务、状态条件更新、幂等键、动作结果、租约和附件元数据。

验收：多线程并发测试无重复占用；重启后非终态任务变为 interrupted；重复查询不删除。

### 步骤 4：ResourceScheduler

文件：

- 新增 `worker/scheduling/models.py`
- 新增 `worker/scheduling/scheduler.py`
- 新增 `tests/scheduling/test_resource_scheduler.py`

工作：统一同步/异步资源键、原子申请、释放、活动快照和启动恢复。

验收：同一资源仅一个任务成功申请；不同移动设备可并行；所有平台键隔离；释放后才允许下一任务。

### 步骤 5：DeviceRegistry

文件：

- 新增 `worker/devices/models.py`
- 新增 `worker/devices/registry.py`
- 修改 `worker/device_monitor.py`
- 修改 `worker/worker.py` 的设备读取和上报组装
- 新增 `tests/devices/test_device_registry.py`

工作：统一设备事实、快照、revision 和状态合并。

验收：旧检测结果不能覆盖新结果；busy 不进入设备事实；Harmony 移动/PC 和相同 ID 正确隔离。

### 步骤 6：ArtifactService

文件：

- 新增 `worker/artifacts/models.py`
- 新增 `worker/artifacts/service.py`
- 修改截图和录屏 Action
- 修改任务失败截图逻辑
- 新增 `tests/artifacts/test_artifact_service.py`

工作：安全路径、原子写入、大小限制、校验和、下载、过期和孤儿清理。

验收：任务结果不再持有新生成的 Base64；路径穿越被拒绝；数据库与文件清理一致。

### 步骤 7：TaskService 和 TaskExecutor

文件：

- 新增 `worker/task/models.py`
- 新增 `worker/task/service.py`
- 新增 `worker/task/executor.py`
- 修改 `worker/worker.py`
- 删除或停止使用 `worker/task/store.py` 和旧 `TaskScheduler`

工作：统一同步/异步执行、状态机、取消、超时、资源释放和 future 管理。使用有界 `ThreadPoolExecutor`，线程数按可执行资源上限配置。

验收：同步/异步走同一执行路径；任一异常路径都释放 context 和租约；Worker 停止可等待并中断任务。

### 步骤 8：逐组迁移全部 Action

顺序：

1. coordinate/gesture。
2. OCR/position。
3. image。
4. window/web_token。
5. cmd_exec。
6. recording。
7. unlock。
8. 平台特有 Action 适配。

每组必须同时完成：参数校验、ExecutionControl、结构化结果、错误码和单元测试。不得只改公共签名而保留不可取消的内部等待。

### 步骤 9：替换现有 API

文件：

- 修改 `worker/server.py`
- 修改 `api.yaml`
- 新增/修改 `tests/api/test_task_api.py`

工作：按第六章直接替换现有接口实现。移除终态查询 pop、取消删除和独立同步执行路径。

验收：路径不变；平台旧请求字段可接受；新查询和取消语义生效。

### 步骤 10：平台后端适配

工程：`D:\code\zq-platform\backend-fastapi`。

工作：幂等键、状态映射、可靠轮询、错误码重试和 interrupted 处理。由于该工程不在当前 Worker 可写根目录，实施时需单独取得写入许可并遵循其 AGENTS.md。

验收：现有前端无需修改；命令模板等现有异步调用不再依赖一次性查询；平台可重复轮询同一结果。

### 步骤 11：生命周期、上报和清理整合

文件：

- 新增 `worker/runtime.py`
- 修改 `worker/main.py`、`worker/gui_main.py`、`worker/worker.py`
- 修改 `worker/reporter/client.py`

工作：启动恢复、停止顺序、Worker 状态计算、设备快照和后台清理。

验收：正常停止无活动线程/租约泄漏；重启状态可解释；上报不影响任务执行。

### 步骤 12：删除旧实现并全量回归

工作：

- 删除旧 `TaskStore`/`TaskScheduler` 使用点和重复设备列表。
- 删除 Action 自动注册副作用和重复能力集合。
- 清理 Base64 新写入路径；只保留必要的历史响应兼容读取。
- 更新项目文档、API 文档和架构图。

验收：`rg` 不再发现旧任务存储、一次性 pop 查询和平台管理器 Scheduler 引用。

## 十二、测试矩阵

### 12.1 任务和资源

- 两请求同时抢 Web/Windows，只接受一个。
- 两请求同时抢同一 Android/iOS/Harmony 设备，只接受一个。
- 不同移动设备可以并行。
- Harmony Mobile/PC 相同 ID 不冲突。
- context 创建失败、Action 异常、失败截图异常、context 关闭异常均释放租约。
- 任务完成后立即提交下一任务成功。
- 重复 GET 返回相同终态结果。
- 重复 DELETE 幂等。
- 相同幂等键不重复执行。
- Worker 重启后 running 变为 interrupted。
- SQLite 锁竞争和事务回滚不产生幽灵租约。

### 12.2 超时和取消

- accepted/acquiring/running 各阶段取消。
- wait/OCR wait/image wait/窗口等待/解锁等待可及时取消。
- Action timeout 小于任务 timeout。
- 任务剩余时间小于 Action timeout 时使用剩余时间。
- cmd_exec 超时终止进程树。
- 取消后必须完成资源释放才返回 cancelled。

### 12.3 DeviceRegistry

- 设备连接、断开、服务 starting/ready/faulty 状态转换。
- 旧 revision 更新被忽略。
- 监控异常不会清空全部设备。
- `/worker_devices` 正确合并 busy 和 task_id。
- 多任务并行时 Worker running_tasks 和 status 正确。

### 12.4 Artifact

- 截图、失败截图、录屏登记。
- 附件大小限制、checksum 和 MIME。
- 路径穿越、非法文件名和任务隔离。
- 过期任务和附件一致清理。
- 文件存在但元数据不存在、元数据存在但文件不存在的修复策略。

### 12.5 Action

每个注册 Action 至少具备：

- 一个成功测试。
- 一个缺少必填参数测试。
- 一个非法边界测试。
- 一个底层能力异常转换测试。
- 等待型 Action 的取消和超时测试。
- 平台能力声明测试。

### 12.6 回归

- 现有 Harmony 测试。
- Windows 截图回退测试。
- Sidecar 和推流测试，确认本次未改变协议。
- 现有截图/录屏端到端脚本。
- 平台后端命令模板异步执行流程。

## 十三、配置项

建议新增到 `config/worker.yaml`：

```yaml
task:
  result_retention_hours: 24
  cleanup_interval_seconds: 600
  max_running_threads: 16
  shutdown_grace_seconds: 30
  max_actions_per_task: 500
  max_request_bytes: 10485760
  max_action_output_bytes: 1048576

artifacts:
  root_dir: data/artifacts
  max_file_bytes: 5368709120
  max_task_bytes: 10737418240

security:
  api_token: ""
  cmd_exec_enabled: true
  max_command_length: 8192
```

API Token 的具体生成和平台下发方式需在实施前确认。若当前部署暂时无法同步配置 Token，可以先完成鉴权中间件和配置能力，在受控内网以兼容开关上线，但生产目标必须启用。

## 十四、数据兼容与上线

### 14.1 首次启动

- 自动创建 `data/worker.db` 和附件目录。
- 当前内存任务无法迁移，切换前应停止接收新任务并等待旧任务完成。
- 旧 Base64 结果不导入 SQLite。

### 14.2 上线顺序

这是接口语义替换，不运行双任务内核，部署必须协调：

1. 先发布支持新旧 Worker 响应的平台注册后端代码，但仍兼容旧 Worker。
2. 停止目标 Worker 接收任务并等待存量任务完成。
3. 发布新 Worker，一次性切换本机执行内核。
4. 验证注册、设备、同步任务、异步任务、取消、重复查询和附件。
5. 全部 Worker 完成升级后，平台后端移除一次性查询兼容分支。

这里的兼容仅发生在平台后端对不同 Worker 版本的响应处理，不在单个 Worker 内保留两套执行实现。

### 14.3 回滚

- 上线前备份配置和新建的 `data/worker.db`。
- 回滚旧 Worker 时，旧版本不会读取 SQLite；平台将新 Worker 未完成任务标记为 interrupted/lost 后决定重试。
- 附件目录保留，不由旧版本处理，避免回滚过程误删诊断文件。
- 数据库迁移首期只新增表，不设计破坏性 downgrade。

## 十五、完成标准

满足以下条件才算完成：

1. 现有任务 API 路径全部由新 TaskService 提供，不存在旧执行分支。
2. 资源忙碌只有 ResourceScheduler 一个事实源。
3. 设备事实只有 DeviceRegistry 一个来源，busy 通过查询时合并。
4. SQLite 支持重启恢复、幂等查询、幂等提交和 24 小时清理。
5. GET 任务结果可重复查询，DELETE 只负责取消。
6. 新生成截图和录屏通过 ArtifactRef 返回，不进入任务结果 Base64。
7. 所有 Action 由 ActionSpec 注册，能力清单无重复维护。
8. 所有等待型 Action 支持 ExecutionControl 取消和任务剩余超时。
9. 所有 Action 有参数校验、稳定错误码和最低测试覆盖。
10. 平台后端适配完成，平台前端无需修改即可正常展示现有内容。
11. 现有截图、录屏、推流、Harmony 和平台任务链路回归通过。
12. Worker 正常停止后无任务线程、活动租约、录屏会话和后台命令泄漏。

## 十六、实施前需确认的决策

1. Worker API Token 的生成、保存和平台分发方式。
2. `cmd_exec background=true` 是否允许命令在提交任务结束后继续运行；本计划默认允许，但必须受 BackgroundProcessRegistry 管理。
3. 同步 HTTP 客户端断开后是否继续执行；本计划默认继续执行。
4. 附件单文件和单任务上限是否适合现有最长录屏；文中数值为初始建议。
5. `interrupted` 在各平台业务中的默认处理；本计划建议由平台按动作幂等性决定是否重试，不自动重放。
6. 是否允许未配置 API Token 的兼容启动；建议仅开发环境允许，生产拒绝启动或拒绝高风险接口。


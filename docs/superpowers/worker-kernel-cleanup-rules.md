# Worker 执行内核清理规则

## 目标

Worker 切换到 `TaskService`、`ResourceScheduler`、`DeviceRegistry` 和
`ArtifactService` 后，不保留旧任务内核的生产引用。这样本机任务状态、
资源占用、设备事实和附件元数据都只有一个事实来源。

## 必须清理的旧实现

- `worker/task/store.py` 及其生产引用。
- `worker/worker.py` 中的旧 `TaskScheduler`。
- `worker/worker.py` 中独立的旧同步/异步执行分支。
- 终态查询时的 `TaskStore.pop()`。
- 取消任务时的删除逻辑。
- 依赖 `_status = "busy"` 的资源状态判断。

## 清理顺序

1. 先让同步和异步接口都调用同一个 `TaskService`。
2. 确认资源占用只由 `ResourceScheduler` 维护。
3. 确认任务结果查询不删除数据库记录，取消只设置取消请求。
4. 接入 `DeviceRegistry` 和 `ArtifactService`。
5. 运行架构静态检查和任务、调度、设备、附件、Action、平台回归测试。
6. 删除旧文件和旧方法，再运行一次全量检查。

## 不得清理的范围

首期不修改各平台动作底层实现、OCR 服务、用例工程、推流协议、
`win-control` 和 Worker 安装升级流程；平台前端仅在 API 契约变化时调整，本轮不需要修改。


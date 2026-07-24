# 鸿蒙性能跨仓身份与命令契约初稿

本文档记录不依赖真机即可冻结的跨仓边界。设备端命令的真实输出、字段语义、权限和
单位仍须在 `perfharmony/tests/fixtures/real/harmony_pc` 与
`perfharmony/tests/fixtures/real/harmony_mobile` 归档后评审。

## 身份边界

| 字段 | 所有者 | 用途 |
| --- | --- | --- |
| `device_id` | ZQ `EnvMachine.id` | 调度、采集记录、spool、上报和终态关联 |
| `device_type` | ZQ/Worker | 选择 `PerfwinBackend` 或 `PerfharmonyBackend` |
| `device_sn` | `EnvMachine.device_sn` | 鸿蒙为 HDC target UDID，传给 `Monitor(udid=...)` |

Worker 对鸿蒙请求只允许使用 `(device_type, device_sn)` 查询 `DeviceRegistry`；未知、离线、
不健康或缺少 UDID 必须返回 4xx。数据库 `device_id` 不得推断 HDC UDID，也不得回退到
Windows perfwin。旧 Windows 请求可以在 API 边界补齐 `device_type=windows`。

## 已覆盖的契约用例

| 用例 | 断言 | 测试位置 |
| --- | --- | --- |
| 正常鸿蒙请求且 `device_id != device_sn` | 使用 HDC UDID 启动 | `tests/test_perf_harmony_server_contract.py` |
| 鸿蒙缺 `device_sn` | 创建 Collector 前返回 400 | 同上 |
| 未知 UDID | 返回 404，不创建采集 | 同上 |
| UDID 离线/不健康 | 返回 409 | 同上及 Worker 注册表测试 |
| 不支持 `device_type` | 返回 400，不回退 Windows | 同上 |
| 旧 Windows 请求缺类型 | 显式兼容为 Windows | 同上 |
| 空目标进程列表 | 不创建空 `ProcessFilter`，只采系统指标 | `tests/test_perf_backend_windows.py` |
| 鸿蒙不加载 perfwin | 只延迟导入 `perfharmony` | `tests/test_perf_backend_windows.py` |

## 设备命令门禁

以下内容必须等 PC 和 Mobile 真机输出冻结后才能从“待验证”改为“已确认”：

- `hidumper --cpuusage` 的字段名称、user/system/idle 语义和整机归一化规则。
- `hidumper --mem` 及 `hidumper --mem <pid>` 的字段和 RSS/PSS/VSS 映射。
- `ps -ef`、`top -n 1 -b` 的真实列布局和批量 CPU 快照能力。
- GPU、温度、功耗、网络节点、权限和单位。
- Worker-HDC `start/stop/status` 真机链路、断线三轮失败和停止期间 shell 回收。

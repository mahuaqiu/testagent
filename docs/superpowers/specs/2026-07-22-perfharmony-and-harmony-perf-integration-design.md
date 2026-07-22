# perfharmony 与鸿蒙性能采集全链路详细设计

> 日期：2026-07-22  
> 状态：draft（待评审）  
> 关联：  
> - 可行性报告 `2026-07-22-harmony-pc-performance-collection-feasibility.md`  
> - Windows 采集库 `D:\code\perfwin`  
> - Worker `D:\code\autotest`  
> - 平台 `D:\code\zq-platform`  
> - 鸿蒙 SDK `D:\code\commandline-tools-windows-x64-6.1.0.850\command-line-tools`

---

## 0. 范围澄清（重要）

### 0.1 要做

| 层 | 内容 |
|----|------|
| **1. 独立库 `perfharmony`** | Rust + PyO3，放在 `D:\code\perfharmony`；可 `pip install`；第三方可不依赖 Worker 直接调用 |
| **2. Worker 集成** | `autotest` 的 `performance_monitor` 按设备类型分支：Windows→perfwin，鸿蒙→perfharmony |
| **3. 平台打通** | `zq-platform` 启停/进程列表/上报/曲线/对比支持 `harmony_pc` / `harmony_mobile` |

### 0.2 不做

| 项 | 说明 |
|----|------|
| 远程桌面 / 投屏 / 实时操控大屏 | 与性能采集无关 |
| 改写 / 移植 perfwin 到鸿蒙 | perfwin 保持 Windows 专精 |
| 平台直连 HDC（仿 Linux SSH 直采） | 鸿蒙统一走 Worker，避免第三套协议 |
| 应用内埋点（hidebug/PerfTest）一期强制依赖 | 可选增强，非主路径 |
| 新建平台「性能任务」业务实体 | 继续用现有 `PerformanceCollect` |

### 0.3 成功标准

1. 任意开发者可在 Windows 主机 `pip install perfharmony` 后，仅凭 hdc + 设备 UDID 采到与 perfwin 同构的 Sample。  
2. 平台对 `harmony_pc` / `harmony_mobile` 发起采集，Worker 用 perfharmony 采样并上报，前端能看曲线与历史。  
3. 样本协议兼容现有 `WorkerReportRequestV3`，版本对比/标签/导出可复用。  
4. 指标设计一次写全；实现允许按优先级逐步落地，但接口与字段预留完整。
5. Worker/ZQ 全链路合入前，必须冻结鸿蒙 PC 与鸿蒙移动端各一份 P0 真机命令输出；合成 fixture 仅用于骨架、失败路径和回归测试，不能证明 P0 解析可用。

---

## 1. 总体架构

```
┌─────────────────────────────────────────────────────────────────┐
│ zq-platform（调度 / 存储 / 展示）                                  │
│  performance_monitor API + DB + 前端图表                          │
│  device_type: windows | linux | harmony_pc | harmony_mobile | …  │
└────────────────────────────┬────────────────────────────────────┘
                             │ HTTP
                             │ start/stop/processes
                             │ report / worker-event
┌────────────────────────────▼────────────────────────────────────┐
│ autotest Worker                                                  │
│  performance_monitor.PerformanceCollector                        │
│    ├─ backend: PerfwinBackend        (device_type=windows 宿主机) │
│    └─ backend: PerfharmonyBackend    (harmony_pc / harmony_mobile)│
│         └─ import perfharmony                                    │
└───────────────┬─────────────────────────────┬───────────────────┘
                │                             │
                ▼                             ▼
        ┌───────────────┐            ┌────────────────────┐
        │   perfwin     │            │   perfharmony      │
        │ (Windows only)│            │ (本设计新建独立仓)   │
        └───────────────┘            │  Rust + PyO3       │
                                     │  HDC transport     │
                                     └─────────┬──────────┘
                                               │ hdc -t <udid> shell …
                                               ▼
                                     ┌────────────────────┐
                                     │ 鸿蒙设备端工具        │
                                     │ hidumper/top/ps/…  │
                                     └────────────────────┘
```

**原则：**

1. **采集内核下沉为库**（perfwin / perfharmony），无 HTTP。  
2. **调度与上报在 Worker**。  
3. **任务与展示在平台**。  
4. **第三方可只装 perfharmony**，脱离 Worker 使用。

---

## 2. 独立库 `perfharmony` 设计

### 2.1 仓库与包

| 项 | 值 |
|----|-----|
| 路径 | `D:\code\perfharmony` |
| Python 包名 | `perfharmony` |
| 构建 | maturin（同 perfwin） |
| 最低 Python | 3.10+ |
| 主机平台 | **Windows x64 优先**（Worker 宿主机为 Windows）；后续可扩 macOS 主机 |
| 目标设备 | 经 HDC 连接的鸿蒙 PC / 手机 / 平板 / 2in1（设备侧） |

### 2.2 目录结构

```
D:\code\perfharmony\
├── Cargo.toml
├── pyproject.toml
├── README.md
├── CLAUDE.md
├── src\
│   ├── lib.rs                 # PyO3 入口
│   ├── data.rs                # Sample 等结构（字段对齐 perfwin）
│   ├── monitor.rs             # 后台采集线程 + RingBuffer
│   ├── ring_buffer.rs
│   ├── error.rs
│   ├── hdc\
│   │   ├── mod.rs
│   │   ├── client.rs          # 调 hdc.exe：list/shell/file
│   │   └── path.rs            # hdc 路径搜索
│   └── collector\
│       ├── mod.rs             # Collector trait
│       ├── system_cpu.rs
│       ├── system_mem.rs
│       ├── process.rs
│       ├── gpu.rs             # 可降级 empty
│       ├── network.rs
│       ├── thermal_power.rs
│       └── topn.rs
├── python\perfharmony\
│   └── __init__.py            # 工厂、路径默认、参数校验
├── tests\
│   ├── test_api.py
│   ├── test_parse_*.py        # 解析器单测（固定样例文本）
│   └── fixtures\              # 真机命令输出样例
├── examples\
│   └── basic_usage.py
└── docs\
    └── command-contract.md    # HDC 命令与字段映射契约
```

### 2.3 对外 Python API（对齐 perfwin）

```python
import perfharmony

# 设备列表（包装 hdc list targets -v）
targets = perfharmony.list_targets()
# [{"udid": "...", "status": "Ready", "connection_type": "USB", ...}, ...]

# 进程列表
procs = perfharmony.list_processes(udid="XXXX")
# [(pid, name), ...]

with perfharmony.Monitor(
    udid="XXXX",                 # 必填：鸿蒙设备 connect key
    interval=2.0,                # >= 1.0
    duration=3600,               # None=无限
    hdc_path=None,               # None 则自动搜索
    process_filter=perfharmony.ProcessFilter(names=["com.xxx.app"]),
    # 或 ProcessFilter(pids=[1234]) / name= / name_regex=
    top_n_cpu=10,
    top_n_gpu=10,                # 无 GPU 数据时 samples 中 top_n_gpu 为空列表或 None
    enable_aggregation=True,
    # 指标开关（设计完整，默认全开能采的）
    enable_system_cpu=True,
    enable_system_mem=True,
    enable_process=True,
    enable_gpu=True,             # 采不到则 system.gpu_percent=None
    enable_network=True,
    enable_thermal_power=True,
) as monitor:
    # 也可 monitor.start() / stop()
    result = monitor.get_result()   # 增量，调用后清空缓冲

for sample in result.samples:
    print(sample.sequence, sample.elapsed_ms, sample.system.cpu_percent)
```

辅助 API：

| API | 说明 |
|-----|------|
| `list_targets()` | 枚举 HDC 设备 |
| `list_processes(udid, hdc_path=None)` | 进程 (pid, name) |
| `list_sensors(udid)` | 调试用：当前周期能读到的 raw 键 |
| `ProbeResult probe(udid)` | 探测设备能力：哪些 collector 可用 |
| `__version__` | 版本号 |

### 2.4 与 perfwin API 对照

| 能力 | perfwin | perfharmony |
|------|---------|-------------|
| `Monitor(...)` | 有 | 有（多 `udid`/`hdc_path`） |
| `start/stop/get_result/is_running` | 有 | 有 |
| `ProcessFilter` | pids/name/names/regex | 同左 |
| `list_processes` | 本机 | 远程设备 |
| `list_targets` | 无 | **有** |
| HWiNFO 侧车 | 有 | **无**（用设备 dump） |
| 平台依赖 | 仅 Windows | 主机 Windows + 设备鸿蒙 |

### 2.5 数据结构（与 perfwin / Worker 上报同构）

Rust `data.rs` 字段与 `D:\code\perfwin\src\data.rs` 对齐：

```text
Sample
  sequence: u64
  elapsed_ms: u64
  timestamp: DateTime<Utc>
  system: SystemMetrics
    cpu_percent: Option<f64>
    gpu_percent: Option<f64>
    gpu_adapters: Vec<{luid, name, utilization_percent}>
    gpu_source: String   # "hdc_dump" | "unavailable" | …
  hwinfo_raw: HashMap<String, {value: f64, unit: String}>
  processes: Option<Vec<ProcessInfo>>
  aggregated: Option<Vec<AggregatedProcessInfo>>
  top_n_cpu: Option<Vec<AggregatedProcessInfo>>
  top_n_gpu: Option<Vec<AggregatedProcessInfo>>

ProcessInfo
  pid, name, cpu_percent, working_set_mb, committed_memory_mb,
  gpu_percent, gpu_memory_mb, handle_count

AggregatedProcessInfo
  name, pids, cpu_percent_total, working_set_mb_total,
  committed_memory_mb_total, gpu_percent_total, handle_count_total, process_count
```

**语义映射（鸿蒙 → 字段）：**

| 字段 | 鸿蒙语义 | 说明 |
|------|----------|------|
| `working_set_mb` | RSS（优先）或 PSS | 文档标明；禁止与 Windows 绝对值横比 |
| `committed_memory_mb` | VSS 或 PSS（次选） | 无则 0 |
| `handle_count` | fd 数（若可得）否则 0 | 前端可隐藏 |
| `gpu_percent` | dump 可用则填，否则 0/进程级；系统级用 Option None | `gpu_source=unavailable` |
| `hwinfo_raw` | 所有可解析传感器/扩展指标 | 键名稳定化，见 §3 |

`get_result().to_dicts()` / PyO3 属性导出，保证 Worker 现有 `_convert_sample_to_report` 逻辑可几乎零改或仅分支。

### 2.6 perfwin 兼容契约与数值语义

“同构”同时指 Rust 数据字段、Python 可观察 API 和 WorkerReport v0.3.1 序列化行为，不是仅复制 `data.rs`。`perfharmony` 必须通过与 `perfwin` 共用的契约测试，至少保证：

1. `Monitor`、`ProcessFilter`、`MonitorResult`、`Sample` 的构造和关键方法可用；`Monitor` 支持上下文管理器。
2. `get_result()` 返回带 `.samples` 的结果并排空增量缓冲；`buffer_len()`、`is_running()`、`stop()` 的行为与 perfwin 一致。
3. `sample.system` 和 `sample.hwinfo_raw` 可转换为 `dict`；时间戳为 ISO 8601；进程与聚合对象拥有 Worker 所读取的所有属性；`to_dicts()` 是稳定公共 API。
4. `sequence` 从 1 单调递增，`elapsed_ms` 基于单调时钟；Worker 始终以 `{collect_id}:{sequence}` 生成 `sample_key`。

CPU 统一按“整机总计算容量”归一化到 `[0, 100]`。单进程、同名聚合和 TopN 的 `cpu_percent` / `cpu_percent_total` 均不得超过 100，以满足现有 WorkerReport 校验；实现需以逻辑 CPU 数或设备端工具的总量语义做归一化。P0 解析必须记录采用的 CPU 语义及核心数来源。

对于无法采集的指标，系统指标使用 `null` 或不写扩展键；不得用 `0` 伪装采集失败。现有上报协议中进程字段不能为 `null`，因此 P0 目标进程 CPU/RSS 无法可靠取得时，该进程不进入该轮 `processes`，并在内部日志/采样质量信息中记录原因。是否将进程字段升级为 nullable，必须作为独立协议变更评审，不能在本项目中隐式改变。

### 2.7 设备身份与 Worker 调用契约

ZQ 的 `device_id` 是 `EnvMachine.id`，仅用于调度、采集记录和上报关联；鸿蒙物理设备标识必须使用 `device_sn`，其值等于 HDC target UDID。Worker 不得把数据库 `device_id` 当作 HDC UDID。

平台调用 Worker 的进程列表、开始采集、停止采集和状态接口均使用 URL 中的 `device_id`，并在请求参数或 JSON 中携带：

```json
{
  "device_type": "harmony_pc",
  "device_sn": "<HDC target UDID>"
}
```

Windows 可省略 `device_sn`，但必须显式或由兼容逻辑推导 `device_type=windows`。Worker 收到鸿蒙请求后必须在 `DeviceRegistry` 中以 `(device_type, device_sn)` 查询，且要求设备在线；不匹配、离线或平台类型不支持均返回 4xx，不得回退到本机 perfwin。

### 2.8 HDC 传输层

```text
HdcClient
  path: PathBuf
  default_timeout: Duration
  shell(udid, cmd) -> String
  list_targets() -> Vec<Target>
  file_recv / file_send（二期剖析文件用，一期可桩）
```

行为约定：

1. `hdc_path` 搜索顺序：显式参数 > `PERFHARMONY_HDC` > `HDC_PATH` > Worker 包内 `tools/hdc` > SDK 根目录环境变量 > PATH。开发机绝对 SDK 路径仅可作为本地开发候选，不能成为库发布契约。  
2. 命令失败：区分「设备离线 / 超时 / 命令不存在 / 解析失败」。  
3. 瞬时错误可重试 1～2 次；连续 N 次失败不杀 Monitor，该次 Sample 字段降级。  
4. **禁止**在库内做平台 HTTP。

### 2.9 采集线程模型与采样预算

对齐 perfwin：

```
start()
  → 校验 udid 在线（list targets 或 probe）
  → 启动后台线程
  → loop:
       t0 = now
       sample = collect_all()   # 顺序/并行调 collectors
       sequence += 1
       elapsed_ms = t0 - start
       buffer.push(sample)
       sleep(max(0, interval - cost))
  duration 到期 → 自停 is_running=false

get_result() → drain buffer（增量）
stop() → 停线程，不 kill 设备侧长期服务（一期无常驻 daemon）
```

`interval` 最小值 **1.0s**（与 perfwin 一致）；推荐默认 **2.0s**（降低 shell 侵入）。

每个 UDID 的 HDC shell 调用必须串行，避免性能采集与自动化动作争用同一 HDC target。P0 每轮命令预算为 6 次以内，正常路径目标不超过 4 次；超出预算的 collector 不得启动。采样优先使用批量快照：一次取得系统 CPU/内存/网络，另一次取得全量进程 CPU 快照；仅对配置目标进程及已从批量快照选出的 TopN 获取详细内存。禁止为所有 PID 逐个执行 `hidumper --cpuusage <pid>` 和 `hidumper --mem <pid>`。

采集器需记录实际 `collection_duration_ms`。一轮耗时超过 interval 时，直接进入下一轮，不补跑或并发补样。RingBuffer 必须有固定上限；满时丢弃最旧样本并记录日志。HDC 子进程超时必须被终止和回收。

连续错误策略：单 collector 失败只使该 collector 字段缺失；同一 UDID 连续 3 轮无法取得任何 P0 数据，Monitor 停止并向 Worker 返回可辨识的设备断连/不可用错误，Worker 上报终态 `failed`。`stop()` 必须可中断等待中的采样，不得因 shell 超时无限阻塞。

### 2.10 Collector 插件与指标全集（一次设计）

每个 Collector 实现：

```rust
trait Collector {
    fn name(&self) -> &str;
    fn probe(&self, hdc: &HdcClient, udid: &str) -> Capability; // Available / Degraded / Unavailable
    fn collect(&self, ctx: &mut CollectContext) -> Result<(), CollectError>;
}
```

#### 指标总表

| ID | 指标 | 填充位置 | 采集命令策略（按优先级） | 优先级 | 采不到时 |
|----|------|----------|-------------------------|--------|----------|
| S1 | 系统 CPU% | `system.cpu_percent` | `hidumper --cpuusage` → 解析 `/proc/stat` 差分 → `top -n 1` | P0 | null |
| S2 | 系统内存 used/total | `hwinfo_raw` + 可选扩展 system | `hidumper --mem` → `/proc/meminfo` | P0 | null 键 |
| S3 | 系统内存 available | `hwinfo_raw` | 同上 | P0 | — |
| S4 | 系统 GPU% | `system.gpu_percent` | 厂商/hidumper GPU 服务（真机冻结） | P1 | null, source=unavailable |
| S5 | 网络 up/down B/s | `hwinfo_raw` | `/proc/net/dev` 两次差分 | P1 | 不写键 |
| S6 | 温度 | `hwinfo_raw` | hidumper 热相关 / 节点 | P2 | 不写键 |
| S7 | 功耗 | `hwinfo_raw` | 电量/电流 dump | P2 | 不写键 |
| P1 | 进程列表 | list_processes + filter | `ps -ef` / hidumper 进程 | P0 | 空列表 |
| P2 | 进程 CPU% | `processes[].cpu_percent` | 批量 top/hidumper 快照，目标 PID 可补查 | P0 | 本轮不写该进程 |
| P3 | 进程 RSS | `working_set_mb` | `hidumper --mem <pid>` / smaps，仅目标或 TopN | P0 | 本轮不写该进程 |
| P4 | 进程 VSS/PSS | `committed_memory_mb` | 同上 | P1 | 本轮不写该进程 |
| P5 | 进程 GPU% | `gpu_percent` | 真机确认 | P2 | 进程结构受 v0.3.1 限制暂填 0，并由能力状态标识不可用 |
| P6 | 进程 fd | `handle_count` | `/proc/<pid>/fd` 计数（权限允许） | P2 | 进程结构受 v0.3.1 限制暂填 0，并由能力状态标识不可用 |
| A1 | 同名聚合 | `aggregated` | 库内计算 | P0 | — |
| T1 | TopN CPU | `top_n_cpu` | 已冻结的批量全量 CPU 快照聚合排序 | P1 | None 若未开或快照不可用 |
| T2 | TopN GPU | `top_n_gpu` | 有 GPU 进程数据时 | P2 | 空 |

#### `hwinfo_raw` 稳定键名（鸿蒙）

避免直接暴露易变英文 dump 标题，**库内映射到稳定 key**（平台 metric_mapping 用这些 key）：

| 稳定 key | 单位 | 含义 |
|----------|------|------|
| `Harmony CPU Usage` | % | 系统 CPU |
| `Harmony CPU User` | % | user |
| `Harmony CPU System` | % | system |
| `Harmony CPU Idle` | % | idle |
| `Harmony Mem Total` | MB | 总量 |
| `Harmony Mem Used` | MB | 已用 |
| `Harmony Mem Available` | MB | 可用 |
| `Harmony Mem Free` | MB | 空闲 |
| `Harmony Swap Total/Used` | MB | 若有 |
| `Harmony Net Upload` | KB/s | 上传 |
| `Harmony Net Download` | KB/s | 下载 |
| `Harmony GPU Usage` | % | 系统 GPU |
| `Harmony CPU Temp` | °C | 温度 |
| `Harmony Power` | mW/W | 功耗（单位以真机为准写入 unit） |

原始 dump 中未映射字段可另存 `Harmony Raw: <原名>`，便于调试，默认可不进主展示。

### 2.11 命令契约与真机冻结

实现前必须在目标设备执行并归档到 `tests/fixtures/`。鸿蒙 PC 和鸿蒙移动端至少各一套 P0 输出；未完成冻结时，只允许实现 mock、解析器框架和失败路径，不允许宣称 P0/Worker/ZQ 全链路可用：

```text
hdc -t <udid> shell "param get const.product.type"
hdc -t <udid> shell "param get const.product.device_type"
hdc -t <udid> shell "hidumper -h"
hdc -t <udid> shell "hidumper --cpuusage"
hdc -t <udid> shell "hidumper --mem"
hdc -t <udid> shell "ps -ef"
hdc -t <udid> shell "cat /proc/stat"
hdc -t <udid> shell "cat /proc/meminfo"
hdc -t <udid> shell "cat /proc/net/dev"
hdc -t <udid> shell "which hiperf; which top"
```

文档 `docs/command-contract.md` 记录：**命令 → 样例输出 → 正则/解析 → 字段**。  
PC 与 Mobile 各至少一份 fixture；解析器按 product type 选策略或通用多策略。

### 2.12 错误模型

| 错误 | Python 表现 | Monitor 行为 |
|------|-------------|--------------|
| hdc 不存在 | 构造/start 抛 `FileNotFoundError` | 不启动 |
| 设备离线 | start 抛；运行中记录错误 | 单轮字段缺失；连续 3 轮无 P0 数据后停止并返回设备不可用 |
| 命令不存在 | probe 标记 Unavailable | 跳过该 collector |
| 解析失败 | 日志 + 该字段 null | 不中断整轮 |
| udid 空 | ValueError | — |

### 2.13 测试策略

| 类型 | 内容 |
|------|------|
| 单元 | 解析器吃 fixtures，不连真机 |
| API | mock HdcClient |
| 真机 | mark `harmony_device`，可选 CI 跳过 |
| 契约 | Sample dict 键集合与 perfwin 一致的快照测试 |
| 可靠性 | 断线、shell 超时、stop 期间 shell、采样超时、RingBuffer 满、部分 collector 失败 |

### 2.14 版本与发布

- `pyproject.toml` / `lib.rs` 版本同步（同 perfwin 规范）。  
- 产出 wheel，供 autotest 打包依赖与 pip 安装。  
- 初始版本建议 `0.1.0`：P0 指标 + API 稳定。
- 产物必须在干净的目标 CPython 虚拟环境中安装并完成 `import perfharmony`、`list_targets` mock 和 Monitor 契约测试。Worker Windows 打包脚本须显式安装对应 ABI 的 perfharmony wheel，并让 Nuitka 包含 `perfharmony` 的扩展模块和包数据。

---

## 3. Worker 集成设计（autotest）

### 3.1 目标

`PerformanceCollector` 不再写死 `import perfwin`，按设备类型选择后端。

### 3.2 设备类型 → 后端

| device_type / 上下文 | 后端 | 库 |
|----------------------|------|-----|
| Windows 宿主机性能（现有 env 设备 windows） | PerfwinBackend | perfwin |
| `harmony_pc` | PerfharmonyBackend | perfharmony |
| `harmony_mobile` | PerfharmonyBackend | perfharmony |
| `linux` | 不经 Worker（平台 SSH） | — |

Worker 通过平台调用契约收到 `device_type + device_sn`，并使用 `DeviceRegistry.get(device_type, device_sn)` 验证本地事实状态；URL 中的 `device_id` 仅保留给 Collector、spool、WorkerReport 和终态事件的关联。`PerfharmonyBackend` 构造时注入经过验证的 `udid=device_sn` 与 `hdc_path`。

### 3.3 模块改动

| 文件 | 改动 |
|------|------|
| `worker/performance_monitor.py` | 抽取 `CollectBackend` 协议；`_create_monitor` 分支；`get_processes` 分支；转换逻辑复用 |
| `worker/perf_backends/base.py`（新） | Protocol: start/stop/get_result/is_running/list_processes |
| `worker/perf_backends/perfwin_backend.py`（新） | 现有逻辑迁入 |
| `worker/perf_backends/perfharmony_backend.py`（新） | 包装 perfharmony.Monitor |
| `worker/server.py` | 路由保持数据库 `device_id`，请求模型增加 `device_type/device_sn` 并验证注册表 |
| `worker/config.py` / `config/worker.yaml` | `performance.hdc_path` 或复用 harmony.hdc_path |
| 打包脚本 | 依赖 perfharmony wheel；捆绑或声明 hdc（hdc 已有 tools/hdc） |
| 测试 | mock 两后端；身份契约、离线拒绝、鸿蒙分支、Windows 回归 |

### 3.4 Backend 协议

```python
class CollectBackend(Protocol):
    def start(self, interval: float, duration: float | None, process_filter, top_n_cpu, top_n_gpu) -> None: ...
    def stop(self) -> None: ...
    def is_running(self) -> bool: ...
    def buffer_len(self) -> int: ...
    def get_result(self) -> Any: ...  # 具 .samples 且 sample 字段同构

def list_processes_for_device(device_id) -> list[tuple[int,str]]: ...
```

Worker 上报仍用现有：

- `POST {platform}/api/core/performance-monitor/report`
- `POST .../collect/worker-event`
- spool 文件逻辑 **不改**

`_convert_sample_to_report`：若 sample 已是同构对象，Windows/鸿蒙共用；GPU HWiNFO 回退仅 Windows 后端需要。

### 3.5 进程筛选

- 鸿蒙进程名多为 **bundleName** 或可执行名，支持 `names` / `pids` / `name_regex`。  
- 平台下发的 `target_processes` 结构不变：`{name, pids?}`。  
- 不允许 PID 与 name 混用（保持现有校验）。
- `target_processes=[]` 明确定义为仅采系统指标，不构造空的 `ProcessFilter`。目标进程数量必须受每轮 HDC 命令预算约束；若设备端无法批量取得目标进程内存，Worker 在 start 阶段拒绝超出能力上限的请求，而不是启动后静默漏采。

### 3.6 并发与设备锁

- 同一 `device_id` 同时仅一个采集任务（现有逻辑）。  
- 鸿蒙多设备：各 device_id 独立 Collector / 独立 perfharmony.Monitor / 独立 udid。

---

## 4. 平台集成设计（zq-platform）

### 4.1 后端

| 点 | 行为 |
|----|------|
| `start_collect` | `linux` → SSH 直采；`windows` / `harmony_pc` / `harmony_mobile` → **通知 Worker** |
| `stop_collect` | 同上分支 |
| `get_processes` | linux 返回空；windows/harmony_* 代理 Worker，并将 `device_type/device_sn` 传给 Worker |
| `report` | **不改协议**；继续 v0.3.1 |
| metric_mapping | 新增鸿蒙稳定键初始化脚本 `init_harmony_metric_mapping.py` |
| 状态机 / 对账 | 复用；鸿蒙同样心跳+sequence |

显式设备类型集合建议：

```python
WORKER_PERF_TYPES = {"windows", "harmony_pc", "harmony_mobile"}  # 可扩 mac 等
LINUX_PERF_TYPES = {"linux"}
```

避免「非 linux 一律 Worker」误伤未实现类型：对未支持类型返回 400「该设备类型暂不支持性能采集」。

### 4.2 数据与展示

- 表结构 **不改**。  
- `cpu_usage` / `memory_usage` 等主字段继续由 `report_data` 从 `system` + aggregated 提取。  
- 鸿蒙 `system.cpu_percent` 优先；`hwinfo_raw` 补充。  
- GPU 全 null 时前端隐藏 GPU 曲线（与 Linux 类似）。  
- 句柄：鸿蒙多为 0，前端 `harmony_*` 可隐藏句柄卡片。

### 4.3 前端

| 改动 | 说明 |
|------|------|
| 设备列表 | 性能页可选 `harmony_pc` / `harmony_mobile`（在线、非虚拟、具 device_sn） |
| CollectDialog | 保留进程选择（与 Windows 同）；预设进程可按平台切换（鸿蒙包名示例） |
| ChartPanel | 使用 `deviceKind=windows/linux/harmony`，并根据样本可用性隐藏 GPU/句柄；内存轴标题显示「RSS/PSS」语义提示 |
| 指标搜索 | 最近搜索按真实 `device_type` 隔离；Harmony raw 指标按独立 source/分组显示 |
| 版本对比 | 仅默认允许相同 `device_type` 对比；跨 Windows/鸿蒙绝对值比较必须显式确认并显示语义警告 |
| metric 搜索 | 加载鸿蒙 mapping |

### 4.4 设备注册前提（已有能力复用）

- `env_machine.device_type` 已支持 `harmony_pc` / `harmony_mobile`。  
- `device_sn` = HDC udid。  
- Worker 已发现并上报在线；平台 `ip:port` 指向宿主机 Worker。

---

## 5. 端到端调用链

### 5.1 开始采集

```
用户在性能页选鸿蒙设备 → 选进程 → 开始
  POST /api/core/performance-monitor/collect/start
    {device_id, device_type, device_sn, interval, timeout, target_processes}
  → 落库 status=starting
  → Background: POST http://{worker}/api/worker/{device_id}/collect/start
       {device_type, device_sn, interval, timeout, target_processes}
  → Worker: PerfharmonyBackend
       perfharmony.Monitor(udid=device_sn, ...).start()
  → 采集线程 shell 采样
  → POST /api/core/performance-monitor/report  (samples v0.3.1)
  → status: starting → running
  → 前端轮询 latest/range 画图
```

### 5.2 停止

```
POST /collect/stop → stopping
  → Worker collect/stop → monitor.stop()
  → worker-event status=stopped
  → 平台终态 stopped
```

### 5.3 第三方独立使用（不经平台）

```python
# 仅安装 perfharmony + 本机 hdc
import perfharmony, time
with perfharmony.Monitor(udid="...", interval=2.0) as m:
    time.sleep(10)
    print(m.get_result().to_dicts())
```

---

## 6. 配置

### 6.1 Worker `config/worker.yaml`

```yaml
platforms:
  harmony:
    hdc_path: ""   # 空则自动搜索 / tools/hdc
performance:
  default_interval_harmony: 2
  # 可选：能力开关默认值
```

### 6.2 环境变量

| 变量 | 用途 |
|------|------|
| `PERFHARMONY_HDC` | hdc 可执行路径 |
| `HDC_PATH` | 兼容别名 |

---

## 7. 实现顺序（可渐进，设计一步到位）

设计指标与接口一次定稿；编码建议顺序（非产品分期阉割）：

| 步骤 | 交付 | 仓库 |
|------|------|------|
| 1 | 建仓、API 骨架、RingBuffer、兼容契约测试、fixtures 目录 | perfharmony |
| 0 | 在骨架仓中冻结 PC/Mobile P0 命令输出、设备身份契约、CPU 归一化语义 | 三仓 |
| 2 | HdcClient + list_targets + 批量快照命令接口 | perfharmony |
| 3 | P0 系统/目标进程 CPU、内存；不承诺 TopN | perfharmony |
| 4 | 单元/可靠性测试 + wheel + 干净环境安装验证 | perfharmony |
| 5 | Worker Backend 抽象 + Windows 完整回归 | autotest |
| 6 | ZQ→Worker 身份契约 + perfharmony 接入 + 打包验证 | autotest / zq-platform |
| 7 | 平台类型分支 + metric mapping | zq-platform |
| 8 | 前端设备、图表、指标搜索和跨类型对比限制 | zq-platform |
| 9 | 真机 E2E、断线/停止/超时测试 | 三仓 |
| 10 | 网络 / TopN / GPU / 热功耗按 probe 启用 | perfharmony |

步骤 9 不阻塞 1～8 上线；字段与开关已在 API 中预留。

---

## 8. 风险与对策

| 风险 | 对策 |
|------|------|
| 真机输出与假设不符 | fixtures 驱动；多策略解析；probe 降级 |
| shell 性能侵入 | 默认 interval≥2s；合并命令减少往返 |
| 无 root 丢指标 | 字段 null，不 fail 任务 |
| PC/Mobile 差异 | product type 分支 + 双 fixtures |
| 与 Windows 指标误比 | 文档 + 前端提示 + 对比默认同 device_type |
| hdc 路径混乱 | 统一搜索顺序 + 配置项 |
| 库与 Worker 双份逻辑 | 转换逻辑只留 Worker 一处；库只出同构 Sample |

---

## 9. 非目标再确认

- 不做远程桌面、不做平台直连 HDC 采集。  
- 不把 perfharmony 做成 HTTP 服务。  
- 不删除或修改 perfwin 的 Windows 职责。  
- 不强制应用签名/埋点。

---

## 10. 验收清单

### 库

- [ ] `pip install` 后 `import perfharmony` 成功  
- [ ] 无设备时 `list_targets` 不崩溃  
- [ ] mock/真机：`Monitor` 产出含 sequence/elapsed_ms/system/hwinfo_raw 的样本  
- [ ] 解析单测全部通过  

### Worker

- [ ] 鸿蒙设备 start/stop/status 正常  
- [ ] report 入库无协议错误  
- [ ] Windows 路径回归通过  

### 平台

- [ ] 鸿蒙设备可选、可开采、可看曲线  
- [ ] 停止与超时终态正确  
- [ ] 版本对比/导出仍可用  

---

## 11. 开放决策（已定）

| 决策点 | 结论 |
|--------|------|
| 库位置 | `D:\code\perfharmony` 独立仓 |
| 是否含 Worker/平台 | **含**，全链路打通 |
| 是否含远程桌面 | **否** |
| 数据格式 | 与 perfwin / WorkerReport v0.3.1 同构 |
| 设备范围 | `harmony_pc` + `harmony_mobile`（同一库） |
| 实现策略 | 指标与 API 一次设计；编码可按 §7 顺序渐进 |

---

## 12. 下一步

1. 评审本设计，修订异议点。  
2. 通过后：`writing-plans` 拆实施 plan（perfharmony → Worker → 平台）。  
3. 先完成 PC/Mobile P0 真机 fixture 与命令契约门禁，再实现 P0 解析器和 Worker/ZQ 全链路。

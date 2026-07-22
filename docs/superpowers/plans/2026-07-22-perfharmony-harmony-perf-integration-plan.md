# perfharmony 与鸿蒙性能采集全链路 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新建独立库 `D:\code\perfharmony`（Rust + PyO3），经 HDC 采集鸿蒙 PC/移动性能并产出与 perfwin 同构 Sample；接入 `autotest` Worker 与 `zq-platform` 性能监控，全链路打通启停、上报、曲线与对比（不做远程桌面）。

**Architecture:** 采集内核下沉为 `perfharmony`（无 HTTP）；Worker 按显式 `device_type + device_sn` 选择并校验 `PerfwinBackend`（Windows）或 `PerfharmonyBackend`（harmony_pc/harmony_mobile）；平台复用现有 `performance_monitor` 任务/上报/展示，并扩展身份契约、设备类型分支与 metric mapping。指标算法参考 SmartPerf device_command 与 hidumper 官方 CLI，解析用 PC/Mobile 真实 P0 fixture 驱动；可选指标再用合成 fixture 补失败路径。

**Tech Stack:** Rust 2021 + PyO3 0.22 + maturin；Python 3.10+；HDC shell；FastAPI Worker/平台（既有）；Vue 3 性能页（既有）。

## Global Constraints

- 库路径：`D:\code\perfharmony` 独立仓，可 `pip install`，第三方可不依赖 Worker。
- 样本字段、Python 可观察 API 和 WorkerReport v0.3.1 与 perfwin **同构**（sequence、elapsed_ms、system、hwinfo_raw、processes、aggregated、top_n_*）；必须以共享契约测试验证，不能只复制 `data.rs`。
- 设备类型：`harmony_pc` + `harmony_mobile` 共用库；Windows 继续用 perfwin。
- 不做远程桌面/投屏；平台不直连 HDC；perfharmony 库内无 HTTP。
- 所有指标按**可实现**规划；系统字段采不到时为 null/缺键，不 fail 整次采样；禁止用 0 代表采集失败。进程字段受现有上报 schema 限制不能为 null，采不到时不写该进程。
- 解析器 **必须** 有单元测试 + `tests/fixtures/`；真机调试阶段只更新 fixture 与正则，不改 API。
- P0（系统/目标进程 CPU、内存）进入 Worker/ZQ 前，鸿蒙 PC 与鸿蒙移动端各必须冻结一份真实命令输出；合成 fixture 只允许用于骨架和失败路径。
- 每个 UDID 的 HDC shell 调用串行；P0 每轮正常路径不超过 4 次、上限 6 次，禁止对全量 PID 逐个执行 CPU/内存命令。
- CPU 统一归一化到整机 `[0,100]`，使进程、聚合与 TopN 满足当前 ZQ schema；TopN 在批量 CPU 快照经真机冻结前不是 P0 交付。
- 参考源码（只参考算法/命令，**不拷进运行时依赖**）：
  - `D:\code\developtools\developtools_smartperf_host-master\smartperf_device\device_command\collector\`
  - `D:\code\developtools\hiviewdfx_hidumper-master\README_zh.md`
  - 现有 Worker：`D:\code\autotest\worker\platforms\harmony_hdc.py`、`performance_monitor.py`
  - 设计：`docs/superpowers/specs/2026-07-22-perfharmony-and-harmony-perf-integration-design.md`

### 开源参考 → 本库取数映射（实现时贴代码注释）

| 指标 | 主路径（HDC shell） | 参考实现 |
|------|---------------------|----------|
| 系统 CPU | `hidumper --cpuusage`；回退 `cat /proc/stat` 差分 | SmartPerf `CPU.cpp`（usage 分项）；hidumper README §14 |
| 系统内存 | `hidumper --mem`；回退 `cat /proc/meminfo` | SmartPerf `RAM::GetSysRamInfo`（memTotal/Free/Available）；hidumper §16 |
| 进程内存 | `hidumper --mem <pid>` | SmartPerf `RAM::CollectRam` + `DUMPER_MEM` |
| 进程 CPU | `hidumper --cpuusage <pid>`；回退 `/proc/<pid>/stat` | SmartPerf 进程 CPU；hidumper §14 |
| 进程列表 | `hidumper -p` 或 `ps -ef` | hidumper §12–13 |
| 网络 up/down | 两次 `cat /proc/net/dev`，累加 wlan0/eth0/rmnet0，算 Δ | SmartPerf `Network.cpp`（**直接读 /proc/net/dev**） |
| 功耗 | 读 `current_now`×`voltage_now` 估 W | SmartPerf `Power.h` 路径 `/sys/class/power_supply/Battery/current_now`、`voltage_now` |
| 温度 | 枚举 `/sys/class/thermal/*/type`+`temp` | SmartPerf `Temperature.cpp`（/sys/class/thermal，毫摄氏度 /1e3） |
| GPU load | 优先 hidumper/厂商；回退 devfreq load 节点 | SmartPerf `GPU.cpp`（collector 或 `.../devfreq/.../load`） |
| 网络备选 | `hidumper --net [pid]` | hidumper README §10 |

### 仓库与文件地图

```
D:\code\perfharmony\                    # 新建独立仓
  Cargo.toml / pyproject.toml / README.md / CLAUDE.md
  src/{lib,data,monitor,ring_buffer,error}.rs
  src/hdc/{mod,client,path}.rs
  src/collector/{mod,system_cpu,system_mem,process,network,power,thermal,gpu,topn}.rs
  src/parse/{cpuusage,meminfo,netdev,hidumper_mem,ps}.rs
  python/perfharmony/__init__.py
  tests/fixtures/*.txt
  tests/test_*.py
  examples/basic_usage.py

D:\code\autotest\
  worker/perf_backends/{base,perfwin_backend,perfharmony_backend}.py
  worker/performance_monitor.py         # 改：按设备选 backend
  worker/server.py                      # 请求模型增加 device_type/device_sn，并校验 DeviceRegistry
  config/worker.yaml                    # hdc_path 等
  pyproject.toml / 打包脚本             # 依赖 perfharmony

D:\code\zq-platform\
  backend-fastapi/core/performance_monitor/api.py
  backend-fastapi/scripts/init_harmony_metric_mapping.py
  web/.../performance-monitor/*         # 设备类型与图表裁剪
```

---

### Task 0: 冻结跨仓库契约与 P0 真机证据（Worker/ZQ 合入门禁）

**目的：** 先消除设备身份、CPU 语义和设备端输出的不确定性；未完成本任务，不得声称 P0 采集已可用，也不得合入 Task 8–10 的业务链路。

**Files:**
- Create: `D:\code\autotest\docs\superpowers\specs\harmony-perf-command-samples\harmony_pc\*`
- Create: `D:\code\autotest\docs\superpowers\specs\harmony-perf-command-samples\harmony_mobile\*`
- Create: `D:\code\autotest\docs\superpowers\specs\harmony-perf-command-contract.md`（跨仓初稿）
- Modify: 本设计中的 Worker/ZQ 请求模型，明确 `device_id`、`device_type`、`device_sn` 职责

**Contract:**
- ZQ URL 的 `{device_id}` 始终为 `EnvMachine.id`，只用于调度、spool、上报和终态关联。
- ZQ 调 Worker 的进程列表、开始、停止、状态请求携带 `device_type` 与 `device_sn`；鸿蒙 `device_sn` 等于 HDC UDID。
- Worker 以 `DeviceRegistry.get(device_type, device_sn)` 验证在线状态；不匹配、离线或不支持时返回 4xx，绝不回退为 perfwin。
- CPU 全部按整机容量归一化为 `[0,100]`，记录逻辑 CPU 数/来源。

- [ ] **Step 1: 获取 PC 和 Mobile 的 P0 真机输出并归档**

每个设备至少归档：`hdc list targets -v`、`param get const.product.type`、`hidumper -h`、`hidumper --cpuusage`、`hidumper --mem`、`ps -ef`、`cat /proc/stat`、`cat /proc/meminfo`，以及目标应用运行前后两次 CPU 快照。

- [ ] **Step 2: 在 command-contract.md 固化命令、样例、解析器、字段、权限和 CPU 归一化规则**

- [ ] **Step 3: 决定批量全量 CPU 快照命令**

必须能在有限 HDC 往返内获得全量进程 CPU；不能做到则首期不提供 TopN，只采目标进程。

- [ ] **Step 4: 写跨仓请求/响应契约测试用例清单**

覆盖缺 `device_sn`、UDID 不存在、UDID 离线、`device_type` 不支持、Windows 兼容请求，以及 device_id 与 device_sn 不相等的正常鸿蒙请求。

- [ ] **Gate: PC/Mobile P0 原始证据、身份契约和 CPU 语义均经评审确认后，才开始 Task 3/4/8–10。Task 1/2 的骨架与通用 HDC 工作可先行。**

---

### Task 1: 初始化 perfharmony 仓库与可 import 骨架

**Files:**
- Create: `D:\code\perfharmony\Cargo.toml`
- Create: `D:\code\perfharmony\pyproject.toml`
- Create: `D:\code\perfharmony\src\lib.rs`
- Create: `D:\code\perfharmony\src\data.rs`
- Create: `D:\code\perfharmony\src\error.rs`
- Create: `D:\code\perfharmony\src\ring_buffer.rs`
- Create: `D:\code\perfharmony\python\perfharmony\__init__.py`
- Create: `D:\code\perfharmony\README.md`
- Create: `D:\code\perfharmony\CLAUDE.md`
- Create: `D:\code\perfharmony\tests\test_import.py`
- Create: `D:\code\perfharmony\tests\test_perfwin_contract.py`
- Import: Task 0 的 PC/Mobile 原始证据到 `D:\code\perfharmony\tests\fixtures\real\`
- Create: `D:\code\perfharmony\.gitignore`

**Interfaces:**
- Produces: Python `import perfharmony`；`perfharmony.__version__ == "0.1.0"`；Rust 导出空模块可加载；预置与 perfwin 共享的 API/序列化契约测试骨架

- [ ] **Step 1: 创建仓库目录与 git**

```powershell
New-Item -ItemType Directory -Force -Path D:\code\perfharmony | Out-Null
Set-Location D:\code\perfharmony
git init
```

- [ ] **Step 1.1: 将 Task 0 已评审的命令样例复制到新仓 fixtures，并保留来源说明与设备版本指纹**

- [ ] **Step 2: 写 `Cargo.toml`**

```toml
[package]
name = "perfharmony"
version = "0.1.0"
edition = "2021"

[lib]
name = "perfharmony"
crate-type = ["cdylib"]

[dependencies]
pyo3 = { version = "0.22", features = ["extension-module"] }
chrono = { version = "0.4", features = ["serde"] }
serde = { version = "1", features = ["derive"] }
parking_lot = "0.12"
regex = "1"
log = "0.4"
```

- [ ] **Step 3: 写 `pyproject.toml`**

```toml
[build-system]
requires = ["maturin>=1.5,<2.0"]
build-backend = "maturin"

[project]
name = "perfharmony"
version = "0.1.0"
description = "HarmonyOS performance collector via HDC (perfwin-compatible samples)"
requires-python = ">=3.10"
license = { text = "MIT" }

[tool.maturin]
python-source = "python"
module-name = "perfharmony._native"
features = ["pyo3/extension-module"]
```

- [ ] **Step 4: 最小 `data.rs` 与绑定清单（字段及 Python 行为对齐 perfwin）**

从 `D:\code\perfwin\src\data.rs` 复制 `SensorValue`、`ProcessInfo`、`AggregatedProcessInfo`、`GpuAdapterMetrics`、`SystemMetrics`、`Sample`、`ProcessFilter` 结构定义到 `src/data.rs`（保持字段名一致）。`MonitorConfig` 增加：

```rust
pub struct MonitorConfig {
    pub udid: String,
    pub hdc_path: Option<String>,
    pub interval: f64,
    pub duration: Option<f64>,
    pub process_filter: Option<ProcessFilter>,
    pub top_n_cpu: Option<usize>,
    pub top_n_gpu: Option<usize>,
    pub enable_aggregation: bool,
    pub enable_system_cpu: bool,
    pub enable_system_mem: bool,
    pub enable_process: bool,
    pub enable_gpu: bool,
    pub enable_network: bool,
    pub enable_thermal_power: bool,
}
```

默认：除 gpu 外 enable_*=true；`enable_gpu=true` 但采不到时 null。

同时建立 `tests/test_perfwin_contract.py`，定义后续两库共用的断言：`ProcessFilter` 参数互斥、Monitor 上下文管理器、`get_result().samples`、`to_dicts()`、Sample dict 属性、ISO 时间戳、增量 drain 和 `buffer_len/is_running`。Task 1 可以只标记为 xfail/skip，Task 4 前必须转为通过。

- [ ] **Step 5: 最小 `lib.rs` 暴露版本**

```rust
use pyo3::prelude::*;
mod data;
mod error;
mod ring_buffer;

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", "0.1.0")?;
    Ok(())
}
```

- [ ] **Step 6: Python 包入口**

```python
# python/perfharmony/__init__.py
from perfharmony._native import __version__

__all__ = ["__version__"]
```

- [ ] **Step 7: 使用仓库虚拟环境构建并验证 import**

```powershell
Set-Location D:\code\perfharmony
# 仅首次创建；后续所有 Python/pip/maturin/pytest 命令必须激活该环境
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -c "import sys; assert sys.prefix != sys.base_prefix"
python -m pip install maturin pytest
python -m maturin develop
python -c "import perfharmony; print(perfharmony.__version__)"
```

Expected: 打印 `0.1.0`

- [ ] **Step 8: Commit**

```powershell
git add Cargo.toml pyproject.toml src python tests README.md CLAUDE.md .gitignore
git commit -m "chore: init perfharmony rust/pyo3 package skeleton"
```

---

### Task 2: HdcClient + list_targets + shell（可 mock）

**Files:**
- Create: `D:\code\perfharmony\src\hdc\mod.rs`
- Create: `D:\code\perfharmony\src\hdc\client.rs`
- Create: `D:\code\perfharmony\src\hdc\path.rs`
- Create: `D:\code\perfharmony\tests\fixtures\list_targets_v.txt`
- Create: `D:\code\perfharmony\tests\test_hdc_parse.py` 或 Rust `#[cfg(test)]`
- Modify: `src/lib.rs` 导出 `list_targets`、内部可测 parse

**Interfaces:**
- Produces:
  - `HdcClient::new(path: PathBuf) -> Self`
  - `fn shell(&self, udid: &str, cmd: &str, timeout_secs: u64) -> Result<String, Error>`
  - `fn list_targets_raw(&self) -> Result<String, Error>`
  - `fn parse_list_targets(output: &str) -> Vec<TargetInfo { udid, status, connection_type }>`
  - Python: `list_targets(hdc_path: Optional[str]=None) -> list[dict]`

- [ ] **Step 1: 写 fixture `list_targets_v.txt`（合成样例，真机后替换）**

```text
127.0.0.1:5555	TCP	Ready	harmony-pc-demo	hdc
ABCDEF123456	USB	Ready	phone-demo	hdc
COM1	UART	Ready	unknown	hdc
```

- [ ] **Step 2: 写真实格式与异常格式的解析测试**

```python
# tests/test_list_targets_parse.py
from pathlib import Path
# 若 parse 仅在 Rust，用 maturin 导出 parse_list_targets_for_test
# 或纯 Python 测试：先在 Python 层镜像解析逻辑不推荐——优先 Rust unit test

def test_fixture_exists():
    p = Path(__file__).parent / "fixtures" / "list_targets_v.txt"
    assert p.exists()
    assert "Ready" in p.read_text(encoding="utf-8")
```

Rust 侧至少覆盖：表头/空输出、Ready/Offline、USB/TCP/UART、字段数量变化和无法识别行。UART 是否纳入结果由 `list_targets(include_uart=False)` 的显式参数控制，不能靠隐式过滤；Worker 发现策略保持现有行为。

Rust 侧：

```rust
#[test]
fn parse_list_targets_skips_uart_optional() {
    let out = include_str!("../tests/fixtures/list_targets_v.txt");
    let t = parse_list_targets(out);
    assert!(t.iter().any(|x| x.udid.contains("ABCDEF")));
}
```

- [ ] **Step 3: 实现路径搜索与错误分类**

`传入 path` > `PERFHARMONY_HDC` > `HDC_PATH` > Worker 包内 tools 路径（仅 Worker 注入时）> SDK 根目录环境变量 > PATH。开发机绝对路径不得作为库发布时的固定候选；可仅在本地开发测试中通过显式参数使用。

`shell` 必须区分：HDC 不存在、target 离线、超时、远端命令失败、输出解析失败；超时后终止并回收子进程。

- [ ] **Step 4: 实现 client**

调用：`hdc -t <udid> shell <cmd>`（Windows 下 cmd 整体作一参，对齐 `harmony_hdc.py` 双引号转义策略）。

超时：默认 15s；list targets 默认 10s。

- [ ] **Step 5: 导出 Python `list_targets`**

```python
def list_targets(hdc_path: str | None = None) -> list[dict]:
    ...
```

- [ ] **Step 6: 无设备时 list_targets 返回 []；HDC 不存在和命令失败返回可辨识异常**

- [ ] **Step 7: Commit**

```powershell
git add src/hdc src/lib.rs tests
git commit -m "feat: add HdcClient and list targets"
```

---

### Task 3: 解析器 — CPU / meminfo / netdev / hidumper mem（fixture 驱动）

**Files:**
- Create: `src/parse/mod.rs` + `cpu_stat.rs` `meminfo.rs` `netdev.rs` `hidumper_cpu.rs` `hidumper_mem.rs` `ps.rs`
- Create: `tests/fixtures/proc_stat.txt`、`proc_meminfo.txt`、`proc_net_dev.txt`、`hidumper_cpuusage.txt`、`hidumper_mem.txt`、`hidumper_mem_pid.txt`、`ps_ef.txt`
- Create: `tests` 或 `#[cfg(test)]` 覆盖每个 parse

**Interfaces:**
- Produces:
  - `parse_proc_stat_total(prev, curr) -> CpuSnapshot { user,nice,system,idle,... total_percent }`
  - `parse_meminfo(text) -> MemInfo { total_kb, free_kb, available_kb }`
  - `parse_netdev(text) -> NetBytes { rx, tx }`  # 累加 wlan0+eth0+rmnet0，跳过 lo
  - `parse_hidumper_cpuusage(text) -> ...`（仅在 Task 0 真机 fixture 冻结后实现）
  - `parse_hidumper_mem_system(text) / parse_hidumper_mem_pid(text) -> ProcessMem { pss_kb, ... }`
  - `parse_ps_ef(text) -> Vec<(u32,String)>`

**参考算法：**

- 网络：完整照搬 SmartPerf `Network.cpp` 接口选择与差分思想（输出改为 KB/s：`diff / interval_sec / 1024`）。
- 内存系统：`MemTotal/MemFree/MemAvailable` 同 hidebug 注释。
- 进程内存：`hidumper --mem <pid>` 解析（参考 SmartPerf `GetPssRamInfo` 字段名 pss 等；fixture 用合成文本）。

- [ ] **Step 1: 接入 Task 0 的 PC/Mobile 真实 P0 fixtures，并补合成边界 fixtures**

`proc_stat.txt` 两段（t0/t1）写在 `proc_stat_t0.txt` / `proc_stat_t1.txt`。

`proc_net_dev.txt` 含 wlan0/eth0/lo。

- [ ] **Step 2: 为每个 parse 写测试（先失败）**

```rust
#[test]
fn netdev_sums_wlan_eth_skips_lo() {
    let t = include_str!("../../tests/fixtures/proc_net_dev.txt");
    let n = parse_netdev(t).unwrap();
    assert!(n.rx > 0);
}
```

- [ ] **Step 3: 实现解析直至全绿，并记录语义/单位与 CPU 归一化来源**

- [ ] **Step 4: Commit**

```powershell
git add src/parse tests/fixtures tests
git commit -m "feat: add fixture-driven parsers for cpu mem net and ps"
```

---

### Task 4: Collectors + Monitor 线程（P0 指标）

**Files:**
- Create: `src/collector/mod.rs` trait + 各 collector
- Create: `src/monitor.rs`
- Modify: `src/lib.rs` 绑定 `Monitor`、`ProcessFilter`、`list_processes`、`Sample` 等
- Modify: `python/perfharmony/__init__.py` 工厂包装
- Create: `examples/basic_usage.py`
- Create: `tests/test_monitor_mock.py`（若可注入 FakeHdc）

**Interfaces:**
- Produces Python API:

```python
class ProcessFilter:  # pids | name | names | name_regex 四选一
class Monitor:
    def __init__(self, udid: str, interval: float = 2.0, duration: float | None = None,
                 hdc_path: str | None = None, process_filter=None,
                 top_n_cpu: int | None = 10, top_n_gpu: int | None = None,
                 enable_aggregation: bool = True, **enable_flags): ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def get_result(self) -> MonitorResult: ...
    def is_running(self) -> bool: ...
    def buffer_len(self) -> int: ...
    def __enter__/__exit__
def list_processes(udid: str, hdc_path: str | None = None) -> list[tuple[int,str]]: ...
```

- Collect 一轮至少填充：
  - `system.cpu_percent`
  - `hwinfo_raw`: `Harmony CPU Usage/User/System/Idle`, `Harmony Mem Total/Used/Available/Free`
  - `processes` / `aggregated`（有 filter 时）；`top_n_cpu` 仅在 Task 0 已冻结批量全量 CPU 快照时启用
  - `sequence` 从 1 递增，`elapsed_ms`

- [ ] **Step 1: Collector trait**

```rust
trait Collector: Send {
    fn name(&self) -> &'static str;
    fn collect(&mut self, ctx: &mut CollectContext) -> Result<(), Error>;
}
```

`CollectContext` 持有：`&HdcClient`、`udid`、可变 `Sample` 草稿、上一拍 CPU/Net 快照、本轮命令预算、`collection_duration_ms` 与 collector 状态。每个 UDID 共用串行命令锁。

- [ ] **Step 2: 实现 SystemCpuCollector / SystemMemCollector / ProcessCollector / TopN**

命令优先级：

1. CPU: `hidumper --cpuusage` → fail 则 `/proc/stat` 需**两拍**（第一拍只存快照，cpu_percent 可 None 或跳过写）。
2. Mem: `hidumper --mem` → `/proc/meminfo`。
3. Process CPU: 优先单次批量快照；只允许对用户明确选择的目标 PID 进行补查。
4. Process mem: 只对目标 PID 和批量 CPU 快照已选出的 TopN 读取，不得扫描所有 PID。
5. 单轮 P0 正常路径不超过 4 次 HDC shell、硬上限 6 次；任何 collector 不得突破预算。
6. 采集失败不写 0：系统字段 null/缺键；进程 CPU/RSS 不完整时本轮不写该进程，并记录状态。

- [ ] **Step 3: Monitor 循环、固定容量 RingBuffer 与故障状态机**

要求：`interval>=1.0`、默认 2.0；超时子进程必须回收；一轮超时不补跑；连续 3 轮无任何 P0 数据则停止并暴露设备不可用错误；`stop()` 可在 shell 等待期间有界退出。

- [ ] **Step 4: 完整 PyO3 绑定与稳定 `to_dicts()` API**

Worker 侧需要能读：`sample.sequence`、`elapsed_ms`、`timestamp`、`system`（dict 或属性）、`hwinfo_raw`、`processes`、`aggregated`、`top_n_cpu`、`top_n_gpu`。

- [ ] **Step 5: 共享兼容契约与 FakeHdc 可靠性测试**

注入返回 fixture 文本的 HdcClient trait 对象；覆盖部分 collector 失败、连续断线、shell 超时、stop during shell、duration 自动停止、RingBuffer 满、单轮命令预算和 CPU `[0,100]`。

- [ ] **Step 6: Commit**

```powershell
git add src/collector src/monitor.rs src/lib.rs python tests examples
git commit -m "feat: monitor loop with bounded P0 collectors"
```

---

### Task 5: P1/P2 Collectors — Network / Power / Thermal / GPU（按可实现写全）

**Files:**
- Create/Modify: `src/collector/network.rs` `power.rs` `thermal.rs` `gpu.rs`
- Create: fixtures `battery_current_now.txt` 等（模拟 cat 输出）
- Tests for each

**Interfaces:**
- Network → `hwinfo_raw["Harmony Net Upload"]` / `["Harmony Net Download"]` unit `KB/s`  
  算法：同 SmartPerf，Δbytes/interval；首样本写 0。
- Power → 读  
  `/sys/class/power_supply/Battery/current_now`  
  `/sys/class/power_supply/Battery/voltage_now`  
  （可再试 `battery` 小写路径）  
  `power_w = |I| * V`（注意单位：常见 µA/µV 或 mA/µV，**用常量并在真机校准**；先按 SmartPerf 原始字符串进 raw，再算 `Harmony Power`）  
  失败：不写键。
- Thermal → 列 `/sys/class/thermal`，匹配 SmartPerf `collectNodes`，temp/1000 → `Harmony CPU Temp` 等。
- GPU → 尝试 `hidumper`/已知 devfreq load；失败 `system.gpu_percent=None`, `gpu_source="unavailable"`。

- [ ] **Step 1: 网络 collector + 测试（两拍差分）**

- [ ] **Step 2: Power/Thermal/GPU + 测试（失败路径也要测）**

- [ ] **Step 3: `probe(udid)` API 返回各能力 Available/Degraded/Unavailable（必做）**

- [ ] **Step 4: Commit**

```powershell
git add src/collector src/lib.rs tests/fixtures tests
git commit -m "feat: add probed optional collectors"
```

---

### Task 6: 文档、命令契约、wheel 构建

**Files:**
- Create: `D:\code\perfharmony\docs\command-contract.md`
- Create: `D:\code\perfharmony\docs\metric-mapping.md`
- Modify: `README.md` 用法
- Create: `build_wheel` 说明或脚本

- [ ] **Step 1: command-contract.md 列出每条 shell、fixture 文件名、字段**

含真机验收命令清单（从设计 § 拷贝）。

- [ ] **Step 2: 在已激活的仓库 `.venv` 中执行 `python -m maturin build --release`**

在全新、匹配 Worker CPython ABI 的虚拟环境中安装 wheel，运行 import、list_targets mock、Monitor 共享契约测试；记录 wheel 文件名和 SHA256。

- [ ] **Step 3: Commit tag `v0.1.0`（可选）**

---

### Task 7: Worker — CollectBackend 抽象 + perfwin 迁入

**Files:**
- Create: `D:\code\autotest\worker\perf_backends\__init__.py`
- Create: `D:\code\autotest\worker\perf_backends\base.py`
- Create: `D:\code\autotest\worker\perf_backends\perfwin_backend.py`
- Modify: `D:\code\autotest\worker\performance_monitor.py`
- Test: `D:\code\autotest\tests\test_perf_backend_windows.py`（mock perfwin）

**Interfaces:**
- Produces:

```python
class CollectBackend(Protocol):
    def start(self, *, interval: float, duration: float | None, process_filter, top_n_cpu, top_n_gpu, enable_aggregation: bool) -> None: ...
    def stop(self) -> None: ...
    def is_running(self) -> bool: ...
    def buffer_len(self) -> int: ...
    def get_result(self) -> Any: ...  # .samples 同构

def list_processes_backend(...) -> list[tuple[int, str]]: ...
```

- [ ] **Step 1: 把现有 `import perfwin` 创建 Monitor 逻辑移到 `PerfwinBackend`，`PerformanceCollector` 只持有 `self._backend`**

- [ ] **Step 2: Windows 回归：现有 performance 相关测试全绿**

```powershell
Set-Location D:\code\autotest
# 激活项目 venv
pytest tests -k "performance or perf" -q
```

- [ ] **Step 3: Commit**

```powershell
git add worker/perf_backends worker/performance_monitor.py tests/test_perf_backend_windows.py
git commit -m "refactor(worker): extract PerfwinBackend for performance monitor"
```

---

### Task 8: Worker — PerfharmonyBackend + 设备类型分支

**Files:**
- Create: `worker/perf_backends/perfharmony_backend.py`
- Modify: `worker/performance_monitor.py` — `start_collect`/`get_processes` 按类型选 backend
- Modify: `worker/server.py` — 请求模型接收并校验 `device_type/device_sn`
- Modify: `worker/config.py` / `config/worker.yaml` — `platforms.harmony.hdc_path`
- Modify: 打包依赖（`pyproject.toml` 或 requirements）声明 perfharmony 路径/版本
- Test: mock `perfharmony.Monitor`

**Interfaces:**
- URL `{device_id}` 是 ZQ `EnvMachine.id`，继续作为 Collector、spool、report 和终态关联键。
- 请求显式携带 `device_type` 与 `device_sn`；鸿蒙 `device_sn` 是 HDC UDID。
- Worker 使用 `worker.device_registry.get(device_type, device_sn)` 验证在线设备，不允许通过数据库 `device_id` 猜 UDID，也不允许未知类型回退到 Windows。

**分支规则：**

```python
if device_type in ("harmony_pc", "harmony_mobile"):
    backend = PerfharmonyBackend(udid=device_sn, hdc_path=cfg.hdc_path)
elif device_type == "windows":
    backend = PerfwinBackend()
else:
    raise ValueError(f"不支持的性能采集设备类型: {device_type}")
```

仅为兼容当前已上线的 Windows 调用，可在 API 边界将缺失 `device_type` 显式补为 `windows` 并记录弃用日志；Backend 选择函数本身不得接受 `None`。

- [ ] **Step 1: 实现 PerfharmonyBackend 包装**

```python
import perfharmony
# Monitor(udid=..., interval=..., duration=..., process_filter=..., top_n_cpu=10, ...)
```

ProcessFilter 映射：names/pids 与现逻辑一致，禁止混合。

`target_processes=[]` 表示仅采系统指标，不创建空 `ProcessFilter`。若目标进程详细内存需要逐 PID 命令，Backend 必须依据单轮命令预算限制目标数量，并在 start 阶段返回 400/422，不能启动后静默漏采。

- [ ] **Step 2: `_convert_sample_to_report` 共用**；Windows 的 GPU HWiNFO 回退仅当 backend 为 perfwin 或 sample.system.gpu_source 需要时执行。

- [ ] **Step 3: `get_processes`：鸿蒙走 `perfharmony.list_processes(udid)`**

- [ ] **Step 4: 单测 mock**

覆盖：`device_id != device_sn` 正常工作、缺少 device_sn、类型/UDID 不匹配、设备离线、不支持类型、Windows 老请求兼容、鸿蒙请求绝不 import perfwin。

- [ ] **Step 5: Worker Windows 发布物集成**

修改 `scripts/build_windows.ps1`：增加 `PerfharmonyWheel` 参数、安装对应 CPython ABI wheel、Nuitka 显式包含 `perfharmony`，最终打包目录执行 `import perfharmony` smoke test。`perfharmony` 延迟导入，缺失时不得影响 Windows perfwin 路径。

- [ ] **Step 6: Commit**

```powershell
git add worker/perf_backends worker/performance_monitor.py worker/server.py worker/config.py config/worker.yaml scripts/build_windows.ps1 tests
git commit -m "feat(worker): integrate perfharmony backend for harmony devices"
```

---

### Task 9: 平台后端 — 类型分支 + metric mapping

**Files:**
- Modify: `D:\code\zq-platform\backend-fastapi\core\performance_monitor\api.py`
  - `WORKER_PERF_TYPES = {"windows", "harmony_pc", "harmony_mobile"}`
  - `start_collect` / `stop_collect` / `get_processes`：linux 直采；worker 类型通知 Worker；其它 400
- Create: `backend-fastapi/scripts/init_harmony_metric_mapping.py`
- Test: 若有 api 测试则补 harmony 分支

**调用契约：** ZQ 从 `EnvMachine` 读取 `device_type/device_sn`，调用 Worker 的 processes/start/stop/status 时一并传递。鸿蒙缺少 `device_sn` 在创建采集记录前返回 400；未支持类型在创建记录前返回 400，避免先落库再异步失败。

**metric mapping 初始键（与库稳定 key 一致）：**

```python
HARMONY_METRIC_MAPPINGS = [
  {"hwinfo_key": "Harmony CPU Usage", "display_name": "系统CPU", "unit": "%", "category": "cpu", "is_primary": True, "sort": 1},
  {"hwinfo_key": "Harmony CPU User", "display_name": "CPU用户态", "unit": "%", "category": "cpu", "sort": 2},
  {"hwinfo_key": "Harmony CPU System", "display_name": "CPU系统态", "unit": "%", "category": "cpu", "sort": 3},
  {"hwinfo_key": "Harmony Mem Total", "display_name": "内存总量", "unit": "MB", "category": "memory", "is_primary": True, "sort": 10},
  {"hwinfo_key": "Harmony Mem Used", "display_name": "内存使用", "unit": "MB", "category": "memory", "is_primary": True, "sort": 11},
  {"hwinfo_key": "Harmony Mem Available", "display_name": "内存可用", "unit": "MB", "category": "memory", "is_primary": True, "sort": 12},
  {"hwinfo_key": "Harmony Net Upload", "display_name": "上行速率", "unit": "KB/s", "category": "network", "sort": 20},
  {"hwinfo_key": "Harmony Net Download", "display_name": "下行速率", "unit": "KB/s", "category": "network", "sort": 21},
  {"hwinfo_key": "Harmony Power", "display_name": "估算功耗", "unit": "W", "category": "power", "sort": 30},
  {"hwinfo_key": "Harmony CPU Temp", "display_name": "CPU温度", "unit": "°C", "category": "thermal", "sort": 40},
  {"hwinfo_key": "Harmony GPU Usage", "display_name": "GPU使用率", "unit": "%", "category": "gpu", "sort": 50},
]
```

- [ ] **Step 1: 改 api 分支、身份参数和前置校验**

- [ ] **Step 2: 加 mapping 脚本**

- [ ] **Step 3: Commit（zq-platform 仓）**

```powershell
git add backend-fastapi/core/performance_monitor/api.py backend-fastapi/scripts/init_harmony_metric_mapping.py backend-fastapi/tests
git commit -m "feat(performance): support harmony devices via worker"
```

---

### Task 10: 平台前端 — 设备可选与图表裁剪

**Files:**
- Modify: `web/apps/web-ele/src/views/performance-monitor/index.vue`（设备过滤）
- Modify: `components/CollectDialog.vue` / `config.ts`（鸿蒙预设进程示例可配置）
- Modify: `ChartPanel.vue` 等 — `isHarmonyDevice` 时：GPU/句柄无数据则隐藏；可选内存副标题 RSS
- Modify: `components/MetricSearchPopup.vue` — 最近搜索和分组按真实设备类型隔离
- Modify: `compare.vue` — 默认仅允许相同 `device_type` 对比，跨 Windows/鸿蒙需显式确认并提示指标语义差异

- [ ] **Step 1: 设备列表包含在线 harmony_pc / harmony_mobile**

- [ ] **Step 2: 以 `deviceKind=windows|linux|harmony` 统一 UI 分支**

GPU/句柄按样本能力与数据可用性隐藏；鸿蒙内存明确显示 RSS/PSS 语义；Harmony raw 指标独立分组，不再把所有非 Linux 指标当作 Windows/HWiNFO。

- [ ] **Step 3: 前端测试与响应式视觉检查**

至少覆盖 Windows、Linux、harmony_pc、harmony_mobile 四种设备，验证进程选择、无 GPU/句柄、指标搜索隔离和跨类型对比提示。

- [ ] **Step 4: Commit**

```powershell
git add web/apps/web-ele/src/views/performance-monitor
git commit -m "feat(web): support harmony performance views"
```

---

### Task 11: 真机 E2E、可选指标校准与发布门禁

**Files:**
- Update: `perfharmony/tests/fixtures/*` 补充可选指标真实输出；P0 真实输出已在 Task 0 冻结
- Update: `docs/command-contract.md` 解析备注
- Fix: 解析正则/单位（尤其 current_now/voltage_now）

- [ ] **Step 1: 在 PC/Mobile 真机补齐可选指标输出并归档**

```bat
hdc -t <udid> shell "hidumper -h"
hdc -t <udid> shell "hidumper --cpuusage"
hdc -t <udid> shell "hidumper --mem"
hdc -t <udid> shell "hidumper --mem <pid>"
hdc -t <udid> shell "hidumper --cpuusage <pid>"
hdc -t <udid> shell "hidumper -p"
hdc -t <udid> shell "hidumper --net"
hdc -t <udid> shell "cat /proc/stat"
hdc -t <udid> shell "cat /proc/meminfo"
hdc -t <udid> shell "cat /proc/net/dev"
hdc -t <udid> shell "ls /sys/class/power_supply"
hdc -t <udid> shell "cat /sys/class/power_supply/Battery/current_now"
hdc -t <udid> shell "cat /sys/class/power_supply/Battery/voltage_now"
hdc -t <udid> shell "ls /sys/class/thermal"
hdc -t <udid> shell "param get const.product.type"
```

- [ ] **Step 2: 替换 fixtures，跑 `pytest` / `cargo test` 全绿**

- [ ] **Step 3: PC/Mobile 各跑 E2E**

平台选鸿蒙设备 → 选进程 → 采集 2 分钟 → 曲线有 CPU/内存 → 停止终态 stopped；另测设备断开连续 3 轮后 failed、duration 到期 timed_out、停止期间 shell 有界退出、device_id 与 device_sn 不同。

- [ ] **Step 4: Windows 路径回归一次**

- [ ] **Step 5: Commit**

```powershell
git add tests/fixtures docs src
git commit -m "fix(perfharmony): calibrate optional collectors with devices"
```

---

## 实现顺序与依赖

```
Task0 身份/CPU 语义 + PC/Mobile P0 真机证据（业务链路门禁）
  ├─→ Task1 骨架、导入真实 fixtures、共享契约测试
  └─→ Task2 HDC（可与 Task1 并行）
        → Task3 P0 解析器（需通过 Task0 Gate）
        → Task4 有界 Monitor P0
        → Task6 文档/wheel/干净环境验证
        → Task7 Worker 抽象与 Windows 回归
        → Task8 Worker 鸿蒙接入与 Nuitka 验证
        → Task9 平台后端身份契约
        → Task10 前端三态与对比限制
        → Task11 PC/Mobile E2E 与发布门禁

Task5 可选指标在 Task4 后实施，可与 Task7–10 并行，但不阻塞 P0；未经真机 probe 的指标不得默认展示。
```

## Spec 覆盖自检

| 设计要求 | Task |
|----------|------|
| 独立库 D:\code\perfharmony | 1–6 |
| API 对齐 perfwin | 1,4,6 |
| 同构 Sample | 1,4,6 |
| HDC 采集 | 2–5 |
| Net/Power 按可实现 | 5（参考 SmartPerf） |
| Worker 集成 | 7–8 |
| 平台打通 | 9–10 |
| 非远程桌面 | 全局约束 |
| P0 真机冻结 | 0 |
| 真机 E2E 与可选指标 | 11 |
| 参考 developtools | 全局映射表 + Task3/5 |

## Placeholder 扫描

无 TBD 实现步骤。P0 命令与 CPU 语义必须在 Task 0 冻结；Task 11 仅校准可选指标和完成 E2E，不允许把 P0 不确定性推迟到最后。

---

## 执行方式（完成后你选）

Plan 已保存到：

`D:\code\autotest\docs\superpowers\plans\2026-07-22-perfharmony-harmony-perf-integration-plan.md`

**两种执行方式：**

1. **Subagent-Driven（推荐）** — 每任务新开子代理，任务间审查  
2. **Inline Execution** — 本会话按任务连续实现并设检查点  

你要选哪种？或者先只从 **Task 1 建仓** 开始。

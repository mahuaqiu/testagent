# 鸿蒙性能采集问题修复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复鸿蒙性能采集的全部已确认问题：内存口径改为常驻内存（pss−swapPss）、-PKG 前缀误抓兜底过滤、新增精准匹配（-PID）模式、多包名并发采集、平台 UI 的 SN 展示/历史设备信息/固定 5s 刷新/分进程折线图。

**Architecture:** 三仓分层改动。perfharmony（Rust/PyO3，升版 0.3.0）负责采集内核：内存口径、精确过滤、-PID 参数；autotest worker 负责 PID 自动发现/30s 跟随/多后端并发与样本合并；zq-platform 负责协议透传（match_mode）、采集记录冗余设备信息、前端展示与刷新策略。真机已验证：SP_daemon 支持 `-PID`；`-PKG` 为前缀匹配（会误抓同前缀包）；双参/逗号分隔不支持；两个 SP_daemon 实例可并发。

**Tech Stack:** Rust + PyO3 + maturin（perfharmony）；Python 3.12 + FastAPI + pytest（autotest）；FastAPI + SQLAlchemy async + Alembic + Vue3 + Element Plus + ECharts（zq-platform）。

## Global Constraints

- perfharmony CLAUDE.md：跨仓协议以 `docs/command-contract.md` 为准；`device_id` 是平台数据库 ID，`device_sn` 是 HDC UDID，不能混用；代码注释用中文；P0 解析器必须真机 fixture 驱动；Rust/PyO3 API 与 perfwin 可观察行为保持一致。
- SP_daemon 设备端命令**禁止管道**（stdout 非终端时静默无输出），过滤解析全部在宿主机侧。
- SP_daemon 一次采集只能带一个 `-PKG` **或**一个 `-PID`（真机实证，双参/逗号均无效）。
- 采集间隔 >= 1 秒（SP_daemon 固定 1s 一拍，间隔靠降采样）。
- 前端改动集中在 `web/apps/web-ele/src/views/performance-monitor/`，遵循现有代码风格（中文注释）。
- **前端改动不得改变 Windows 现有功能与展示**；刷新页面（D4）、历史记录（D3）、tooltip 明细（D5）为全平台共享逻辑，Windows 与鸿蒙行为一致。
- 折线图保持「多进程合一条」展示，明细通过 tooltip 悬浮查看（用户决策，不做多线拆分）。
- 每个任务结束必须通过该仓的测试/检查命令并 git commit（conventional commits）。

## 背景速查（问题 → 任务映射）

| # | 问题 | 根因 | 任务 |
|---|------|------|------|
| 1 | 内存与任务管理器有差距 | `working_set_mb` 用含 swapPss 的 pss | A1 |
| 2 | -pkg 不支持多包名 | SP_daemon 单 -PKG；worker 只取 target_processes[0] | B3 + D6 |
| 3 | 前缀包名误抓统计成一个 | 设备端 -PKG 前缀匹配（真机实证），宿主 0.2.0 删了过滤 | A2（兜底）+ A3/B2（精准根治） |
| 4 | 需要精准匹配 + APP 未拉起 30s 探测 | SP_daemon 支持 -PID（真机实证） | A3 + B1/B2 + D6 |
| 5 | 设备选择/弹窗看不出 SN | 数据链路全通，UI 只显示 type+ip | D1 + D2 |
| 6 | 历史记录看不出设备 | performance_collect 表只存 device_id | C1 + D3 |
| 7 | 页面刷新太快 | 轮询间隔 = 采集间隔 | D4 |
| 8 | 折线图多进程合一条 | 前端 reduce 求和聚合 | D5 |

---

# Phase A — perfharmony（Rust 采集内核）

### Task A1: 内存口径改为常驻内存 pss−swapPss + 主进程内存细分白名单

**Files:**
- Modify: `D:\code\perfharmony\src\parse\sp_daemon.rs`
- Test: 同文件 `#[cfg(test)] mod tests`（fixture 驱动）

**Interfaces:**
- Produces: `ProcessInfo.working_set_mb` 语义变为「常驻内存（pss−swapPss，swapPss 缺失回退 pss）」；`hwinfo_raw` 新增键 `Harmony Native Heap Pss` / `Harmony Graphic Pss` / `Harmony ArkTS Heap Pss` / `Harmony Swap Pss`（单位 MB，主进程口径）。下游 autotest/zq-platform 无需改字段名。

真机 fixture 实测值（`tests/fixtures/real_device2/sp_daemon_n2_full.txt`，两块相同）：主进程 `pss=258695`、`swapPss=92360`；子进程 `childPss`/`childSwapPss`：`44831:172507/2964`、`44974:25226/2048`、`44977:169559/23696`、`45200:77699/11088`。

- [ ] **Step 1: 写失败测试**

在 `sp_daemon.rs` 的 tests 模块中修改 `processes_expand_main_and_children`，并在 `system_sensors_use_expected_units_and_whitelist` 中追加细分键断言：

```rust
    #[test]
    fn processes_expand_main_and_children() {
        let blocks = parse_fixture_blocks();
        let processes = build_processes(&blocks[1]).unwrap();

        assert_eq!(processes.len(), 5);
        assert_eq!(processes[0].pid, 43950);
        assert_eq!(processes[0].name, "com.huawei.it.works");
        // 常驻内存 = pss − swapPss：258695 − 92360。
        assert!((processes[0].working_set_mb - (258695.0 - 92360.0) / 1024.0).abs() < 1e-9);

        let child = processes.iter().find(|item| item.pid == 44977).unwrap();
        assert_eq!(child.name, "com.huawei.it.works:44977");
        assert!((child.cpu_percent - 0.029499).abs() < 1e-9);
        // 子进程同样扣除 childSwapPss：169559 − 23696。
        assert!((child.working_set_mb - (169559.0 - 23696.0) / 1024.0).abs() < 1e-9);

        let gpu_child = processes.iter().find(|item| item.pid == 44831).unwrap();
        assert!((gpu_child.gpu_memory_mb - 111212.0 / 1024.0).abs() < 1e-9);
    }

    #[test]
    fn resident_memory_falls_back_to_pss_without_swappss() {
        // 旧设备/NA 场景：swapPss 缺失时回退 pss 原值。
        let mut block = HashMap::new();
        block.insert("ProcAppName".to_string(), "com.app".to_string());
        block.insert("ProcId".to_string(), "100".to_string());
        block.insert("pss".to_string(), "20480".to_string());
        let processes = build_processes(&block).unwrap();
        assert!((processes[0].working_set_mb - 20.0).abs() < 1e-9);
    }
```

在 `system_sensors_use_expected_units_and_whitelist` 末尾追加（用 fixture 自身值做自洽断言，避免硬编码跨块差异）：

```rust
        // 主进程内存细分（MB），用于与任务管理器对数。
        assert_eq!(sensors["Harmony Native Heap Pss"].unit, "MB");
        let native_kb = blocks[1]["nativeHeapPss"].trim().parse::<f64>().unwrap();
        assert!((sensors["Harmony Native Heap Pss"].value - native_kb / 1024.0).abs() < 1e-9);
        let swap_kb = blocks[1]["swapPss"].trim().parse::<f64>().unwrap();
        assert!((sensors["Harmony Swap Pss"].value - swap_kb / 1024.0).abs() < 1e-9);
        assert!(sensors.contains_key("Harmony Graphic Pss"));
        assert!(sensors.contains_key("Harmony ArkTS Heap Pss"));
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd D:/code/perfharmony && cargo test`
Expected: FAIL（working_set_mb 仍是 pss 原值；细分键不存在）

- [ ] **Step 3: 实现**

`sp_daemon.rs`，在 `build_processes` 前加辅助函数：

```rust
/// 常驻内存（KB）= pss − swapPss；swapPss 缺失（NA/旧设备）时回退 pss。
/// 口径对齐：任务管理器展示的是真驻留在物理内存里、归属该进程的部分。
fn resident_kb(block: &HashMap<String, String>, pss_key: &str, swap_key: &str) -> Option<f64> {
    let pss = get_f64(block, pss_key)?;
    let swap = get_f64(block, swap_key).unwrap_or(0.0);
    Some((pss - swap).max(0.0))
}
```

`build_processes` 中主进程一行（原 `working_set_mb: get_f64(block, "pss").map(|kb| kb / KB_PER_MB).unwrap_or(0.0),`）改为：

```rust
        working_set_mb: resident_kb(block, "pss", "swapPss")
            .map(|kb| kb / KB_PER_MB)
            .unwrap_or(0.0),
```

子进程部分：在 `let child_pss = ...` 之后增加一行解析，并替换 `working_set_mb` 取值：

```rust
    let child_swap_pss = parse_child_map(
        block.get("childSwapPss").map(String::as_str).unwrap_or(""),
    );
```

```rust
            working_set_mb: child_pss
                .get(pid)
                .map(|kb| {
                    (kb - child_swap_pss.get(pid).copied().unwrap_or(0.0)).max(0.0) / KB_PER_MB
                })
                .unwrap_or(0.0),
```

`build_system_sensors` 中「系统内存」块之后增加主进程内存细分白名单：

```rust
    // 主进程内存细分（KB→MB），诊断用：与任务管理器/手机管家对数时逐项比对。
    const PROCESS_MEM_KEYS: [(&str, &str); 4] = [
        ("nativeHeapPss", "Harmony Native Heap Pss"),
        ("graphicPss", "Harmony Graphic Pss"),
        ("arktsHeapPss", "Harmony ArkTS Heap Pss"),
        ("swapPss", "Harmony Swap Pss"),
    ];
    for (sp_key, sensor_key) in PROCESS_MEM_KEYS {
        if let Some(kb) = get_f64(block, sp_key) {
            add_sensor(&mut raw, sensor_key, kb / KB_PER_MB, "MB");
        }
    }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd D:/code/perfharmony && cargo test`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
cd D:/code/perfharmony && git add src/parse/sp_daemon.rs && git commit -m "feat: 进程内存口径改为常驻内存 pss-swapPss，补充主进程内存细分白名单"
```

### Task A2: -PKG 模糊模式宿主侧精确过滤（防同前缀包误抓）

**Files:**
- Modify: `D:\code\perfharmony\src\monitor.rs`
- Test: 同文件 tests 模块

**Interfaces:**
- Consumes: `parse_ps_ef(text) -> Vec<(u32, String)>`（`src/parse/ps.rs:16`，pid 在前）
- Produces: `filter_processes_by_package(processes, package, pid_names) -> Vec<ProcessInfo>`；`MonitorClient::shell` 在 -PKG 模式下每 30s 多一次 `ps -ef` 短命令（流不持 target 锁，可并发，契约文档同步）。

过滤规则：主进程（build_processes 产物 index 0）按真实进程名取 `bundle_base_name` 与目标包名**全等**比较；子进程按 `ps -ef` 的 pid→进程名映射同样比较，映射缺失（采集期间新出现的进程）保留，避免误杀。

- [ ] **Step 1: 写失败测试**

monitor.rs tests 模块新增（复用现有 `make` 进程构造方式，见 `aggregation_groups_children_under_bundle_name`）：

```rust
    #[test]
    fn package_filter_drops_same_prefix_bundle_children() {
        let make = |pid: u32, name: &str| ProcessInfo {
            pid,
            name: name.to_string(),
            cpu_percent: 0.0,
            working_set_mb: 1.0,
            committed_memory_mb: 0.0,
            gpu_percent: 0.0,
            gpu_memory_mb: 0.0,
            handle_count: 0,
        };
        let mut pid_names = std::collections::HashMap::new();
        pid_names.insert(200u32, "com.a.test:render".to_string());
        pid_names.insert(300u32, "com.a.test.abc".to_string());
        // pid 400 不在映射里（新进程），应保留。
        let filtered = super::filter_processes_by_package(
            vec![
                make(100, "com.a.test"),
                make(200, "com.a.test:200"),
                make(300, "com.a.test:300"),
                make(400, "com.a.test:400"),
            ],
            "com.a.test",
            &pid_names,
        );
        let pids: Vec<u32> = filtered.iter().map(|item| item.pid).collect();
        assert_eq!(pids, vec![100, 200, 400]);
    }

    #[test]
    fn package_filter_drops_foreign_main_process() {
        // SP_daemon 前缀匹配可能把 com.a.test.abc 当主进程上报，必须整条丢弃。
        let make = |pid: u32, name: &str| ProcessInfo {
            pid,
            name: name.to_string(),
            cpu_percent: 0.0,
            working_set_mb: 1.0,
            committed_memory_mb: 0.0,
            gpu_percent: 0.0,
            gpu_memory_mb: 0.0,
            handle_count: 0,
        };
        let pid_names = std::collections::HashMap::new();
        let filtered = super::filter_processes_by_package(
            vec![make(1, "com.a.test.abc"), make(2, "com.a.test.abc:2")],
            "com.a.test",
            &pid_names,
        );
        assert!(filtered.is_empty());
    }
```

同时修改 `FakeClient`：加 `shell_output` 字段（`new` 保持原行为，新增 `with_shell_output` 构造），`shell()` 返回该字段：

```rust
    struct FakeClient {
        lines: Vec<String>,
        shell_output: String,
        shell_calls: Mutex<Vec<String>>,
        stream_commands: Mutex<Vec<String>>,
    }

    impl FakeClient {
        fn new(text: &str) -> Self {
            Self::with_shell_output(text, "")
        }

        fn with_shell_output(text: &str, shell_output: &str) -> Self {
            Self {
                lines: text.lines().map(str::to_string).collect(),
                shell_output: shell_output.to_string(),
                shell_calls: Mutex::new(Vec::new()),
                stream_commands: Mutex::new(Vec::new()),
            }
        }
    }
```

`impl MonitorClient for FakeClient` 的 `shell` 返回值改为 `Ok(self.shell_output.clone())`（记录调用不变）。

修改 `issues_stop_cleanup_before_and_after_and_builds_full_command` 的期望（-PKG 模式会在开流前多一次 ps -ef）：

```rust
        assert_eq!(
            fake.shell_calls.lock().unwrap().as_slice(),
            ["SP_daemon -stop", "ps -ef", "SP_daemon -stop"]
        );
```

新增端到端过滤测试（合成流 + 合成 ps -ef）：

```rust
    #[test]
    fn fuzzy_mode_filters_contaminated_children() {
        // -PKG com.a.test：设备端前缀匹配混入 com.a.test.abc（pid 300），
        // ps -ef 映射后必须过滤，只留主进程 + 子进程 200。
        let ps_ef = "UID PID PPID C STIME TTY TIME CMD\n\
                     u200 200 1 0 10:00 ? 0:00 com.a.test:render\n\
                     u200 300 1 0 10:00 ? 0:00 com.a.test.abc\n";
        let mut lines = Vec::new();
        for shot in 0..3 {
            lines.push("order:0 TotalcpuUsage=1.000000".to_string());
            lines.push("order:1 memTotal=1048576".to_string());
            lines.push("order:2 memAvailable=524288".to_string());
            lines.push(format!("order:3 timestamp={}", 1000 + shot));
            lines.push("order:4 ProcAppName=com.a.test".to_string());
            lines.push("order:5 ProcId=100".to_string());
            lines.push("order:6 ChildProcId=200|300|".to_string());
            lines.push("order:7 childPss=200:50|300:70|".to_string());
            lines.push(String::new());
        }
        lines.push(super::FINISH_MARKER.to_string());
        let client = FakeClient::with_shell_output(&lines.join("\n"), ps_ef);
        let inner = Arc::new(MonitorInner::new());
        run_monitor(super::config(1.0, Some(10.0), Some("com.a.test")), client, inner.clone());

        let sample = &inner.drain().samples[0];
        let processes = sample.processes.as_ref().unwrap();
        let pids: Vec<u32> = processes.iter().map(|item| item.pid).collect();
        assert_eq!(pids, vec![100, 200]);
        let aggregated = sample.aggregated.as_ref().unwrap();
        assert_eq!(aggregated.len(), 1);
        assert_eq!(aggregated[0].pids, vec![100, 200]);
    }
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd D:/code/perfharmony && cargo test`
Expected: FAIL（`filter_processes_by_package` 不存在；shell_calls 期望不匹配）

- [ ] **Step 3: 实现**

monitor.rs 顶部常量区追加：

```rust
/// ps -ef 进程名映射刷新周期（模糊模式防同前缀包误抓）。
const PID_NAMES_REFRESH: Duration = Duration::from_secs(30);
/// ps -ef 短命令超时；流式采集不持 target 锁，短命令可并发执行。
const PS_EF_TIMEOUT_SECS: u64 = 15;
```

`filter_processes_by_package` + `fetch_pid_names`（放在 `aggregate_processes` 附近）：

```rust
/// 模糊模式（-PKG）宿主侧精确过滤兜底：设备端按前缀匹配，同前缀包
/// （com.a.test / com.a.test.abc）会混入子进程列表。规则：
/// 主进程按真实进程名取包名基名全等比较；子进程以 ps -ef 映射为准，
/// 映射缺失（新进程）保留，避免误杀。
pub(crate) fn filter_processes_by_package(
    processes: Vec<ProcessInfo>,
    package: &str,
    pid_names: &std::collections::HashMap<u32, String>,
) -> Vec<ProcessInfo> {
    let target = package.to_string();
    let mut kept = Vec::with_capacity(processes.len());
    for (index, process) in processes.into_iter().enumerate() {
        let real_name = if index == 0 {
            process.name.clone()
        } else {
            pid_names.get(&process.pid).cloned().unwrap_or_default()
        };
        if real_name.is_empty() || bundle_base_name(&real_name) == target {
            kept.push(process);
        }
    }
    kept
}

fn fetch_pid_names<C: MonitorClient>(
    config: &MonitorConfigOwned,
    client: &C,
    cancel: &AtomicBool,
) -> std::collections::HashMap<u32, String> {
    match client.shell(&config.udid, "ps -ef", PS_EF_TIMEOUT_SECS, cancel) {
        Ok(text) => crate::parse::ps::parse_ps_ef(&text).into_iter().collect(),
        Err(_) => std::collections::HashMap::new(),
    }
}
```

`run_monitor`：开流前取一次映射，并传给流循环（`config.package.is_some()` 时才取）：

```rust
    let mut pid_names = if config.package.is_some() {
        fetch_pid_names(&config, &client, &inner.stop)
    } else {
        std::collections::HashMap::new()
    };

    if !inner.is_stopped() {
        match client.open_stream(&config.udid, &command) {
            Ok(mut stream) => {
                run_stream_loop(&config, &client, &inner, &mut stream, started, &mut pid_names);
                stream.stop();
            }
            Err(error) => inner.set_error(format!("启动 SP_daemon 采集流失败: {error}")),
        }
    }
```

`run_stream_loop` 改签名并在循环头部按 30s 刷新（其余逻辑不变）：

```rust
fn run_stream_loop<C: MonitorClient>(
    config: &MonitorConfigOwned,
    client: &C,
    inner: &MonitorInner,
    stream: &mut C::Stream,
    started: Instant,
    pid_names: &mut std::collections::HashMap<u32, String>,
) {
    let step = (config.interval.round() as u64).max(1);
    let mut parser = SpStreamParser::new();
    let mut block_index = 0u64;
    let mut saw_order = false;
    let mut last_line_at = Instant::now();
    let mut last_pid_check = Instant::now();

    while !inner.is_stopped() {
        if config.package.is_some() && last_pid_check.elapsed() >= PID_NAMES_REFRESH {
            *pid_names = fetch_pid_names(config, client, &inner.stop);
            last_pid_check = Instant::now();
        }
        match stream.next_line(POLL_STEP) {
            // ……原有逻辑不变，仅 handle_block 调用改为：
            if let Some(block) = parser.push_line(&line) {
                handle_block(
                    inner,
                    &block,
                    started,
                    step,
                    &mut block_index,
                    config.package.as_deref(),
                    pid_names,
                );
            }
```

`handle_block` / `sample_from_block` 透传两个新参数，`sample_from_block` 中过滤：

```rust
fn handle_block(
    inner: &MonitorInner,
    block: &std::collections::HashMap<String, String>,
    started: Instant,
    step: u64,
    block_index: &mut u64,
    package: Option<&str>,
    pid_names: &std::collections::HashMap<u32, String>,
) {
```

```rust
fn sample_from_block(
    inner: &MonitorInner,
    block: &std::collections::HashMap<String, String>,
    elapsed: Duration,
    package: Option<&str>,
    pid_names: &std::collections::HashMap<u32, String>,
) -> Option<Sample> {
    let raw = build_system_sensors(block);
    let mut processes = build_processes(block);
    if let (Some(package), Some(list)) = (package, processes.as_mut()) {
        *list = filter_processes_by_package(std::mem::take(list), package, pid_names);
        if list.is_empty() {
            processes = None; // 全部被过滤时不产出进程数据，仅保留系统指标
        }
    }
    // ……其余不变
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd D:/code/perfharmony && cargo test && cargo fmt`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
cd D:/code/perfharmony && git add src/monitor.rs && git commit -m "fix: -PKG 模糊模式宿主侧按包名精确过滤，兜底同前缀包误抓"
```

### Task A3: Monitor 新增 pid 参数（SP_daemon -PID 精准模式）

**Files:**
- Modify: `D:\code\perfharmony\src\parse\sp_daemon.rs:53`（sp_daemon_command）
- Modify: `D:\code\perfharmony\src\monitor.rs`（MonitorConfigOwned）
- Modify: `D:\code\perfharmony\src\lib.rs:428-467`（PyMonitor::new）

**Interfaces:**
- Produces: Python API `Monitor(udid, interval=1.0, duration=None, hdc_path=None, package=None, pid=None)`；`package` 与 `pid` 互斥（同时传抛 `ValueError`）。命令形态 `SP_daemon -N n -c -g -f -t -p -r -net -d -PID <pid>`。

- [ ] **Step 1: 写失败测试**

sp_daemon.rs tests 的 `command_covers_full_metric_set` 改为三参并补 -PID 用例：

```rust
    #[test]
    fn command_covers_full_metric_set() {
        assert_eq!(
            sp_daemon_command(600, Some("com.huawei.it.works"), None),
            "SP_daemon -N 600 -c -g -f -t -p -r -net -d -PKG com.huawei.it.works"
        );
        assert_eq!(
            sp_daemon_command(60, None, None),
            "SP_daemon -N 60 -c -g -f -t -p -r -net -d"
        );
        assert_eq!(
            sp_daemon_command(60, None, Some(28645)),
            "SP_daemon -N 60 -c -g -f -t -p -r -net -d -PID 28645"
        );
    }
```

monitor.rs tests 的 `config` helper 增加 `pid: None` 字段；`issues_stop_cleanup...` / `default_duration_uses_24h_shot_budget` 中 stream_commands 期望不变（命令串不变）。lib.rs 侧校验用例在 `tests/test_perfwin_contract.py`（Python 契约测试）补一条：

```python
def test_pid_and_package_mutually_exclusive():
    import pytest, perfharmony
    with pytest.raises(ValueError):
        perfharmony.Monitor("FAKE-UDID", package="com.app", pid=123)
```

（文件：`D:\code\perfharmony\tests\test_perfwin_contract.py`，追加在文件末尾。）

- [ ] **Step 2: 跑测试确认失败**

Run: `cd D:/code/perfharmony && cargo test`
Expected: FAIL（函数签名/字段不存在）

- [ ] **Step 3: 实现**

`sp_daemon.rs`：

```rust
/// 构造 SP_daemon 长跑采集命令；`total_samples` 为总拍数（1 拍 = 1 秒）。
/// package（-PKG）与 pid（-PID）互斥，由上层校验；真机实证一次只能带其一。
pub fn sp_daemon_command(total_samples: u32, package: Option<&str>, pid: Option<u32>) -> String {
    let mut command = format!("SP_daemon -N {total_samples} -c -g -f -t -p -r -net -d");
    if let Some(package) = package {
        command.push_str(" -PKG ");
        command.push_str(package);
    }
    if let Some(pid) = pid {
        command.push_str(" -PID ");
        command.push_str(&pid.to_string());
    }
    command
}
```

`monitor.rs` `MonitorConfigOwned` 增加字段（`pub package` 之后）：

```rust
    /// 单进程精准采集：SP_daemon -PID，与 package 互斥。
    pub pid: Option<u32>,
```

`run_monitor` 中命令拼装改为 `let command = sp_daemon_command(total, config.package.as_deref(), config.pid);`。tests 的 `config` helper 增加 `pid: None,`。

`lib.rs` `PyMonitor::new`：

```rust
    #[new]
    // 0.3.0：-PKG 单应用（含子进程）与 -PID 单进程精准模式二选一。
    #[pyo3(signature = (udid, interval=1.0, duration=None, hdc_path=None, package=None, pid=None))]
    fn new(
        udid: String,
        interval: f64,
        duration: Option<f64>,
        hdc_path: Option<String>,
        package: Option<String>,
        pid: Option<u32>,
    ) -> PyResult<Self> {
        // ……原有 udid/interval/duration/package 校验保持不变，追加：
        if package.is_some() && pid.is_some() {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "package 与 pid 只能二选一",
            ));
        }
        if pid.is_some_and(|value| value == 0) {
            return Err(pyo3::exceptions::PyValueError::new_err("pid 必须为正整数"));
        }
        // ……config 构造增加 pid 字段
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd D:/code/perfharmony && cargo test && cd D:/code/perfharmony && D:/code/autotest/venv/Scripts/python -m pytest tests/test_perfwin_contract.py -v`
Expected: PASS（Python 侧跑的是旧 wheel 时互斥校验用例会 FAIL，属预期——该用例在 Task A4 重新构建安装后复跑转绿；先标注 `@pytest.mark.skip(reason="等待 0.3.0 wheel")`，A4 完成后移除）

- [ ] **Step 5: 提交**

```bash
cd D:/code/perfharmony && git add -A && git commit -m "feat: Monitor 支持 pid 参数，SP_daemon -PID 精准模式（与 -PKG 互斥）"
```

### Task A4: 过时示例修正、版本 0.3.0、契约文档、构建 wheel

**Files:**
- Modify: `D:\code\perfharmony\examples\basic_usage.py`（删除已废弃的 `ProcessFilter/process_filter` 用法，改 `package=`/`pid=`）
- Modify: `D:\code\perfharmony\pyproject.toml:7`（version = "0.3.0"）
- Modify: `D:\code\perfharmony\docs\command-contract.md`（-PID 模式、ps -ef 30s 短命令预算、内存口径 pss−swapPss）
- Modify: `D:\code\autotest\pyproject.toml:47`（"perfharmony==0.2.2" → "perfharmony==0.3.0"）

- [ ] **Step 1: 修正示例与文档**（basic_usage.py 中所有 `process_filter=` 改为 `package="com.xxx"`；command-contract.md 追加「-PID 精准模式」「-PKG 前缀匹配与宿主侧精确过滤」「working_set_mb = pss − swapPss（常驻内存口径）」三节）
- [ ] **Step 2: 版本号 0.3.0 + 移除 A3 里 skip 标记**
- [ ] **Step 3: 构建并安装到 autotest venv**

```bash
cd D:/code/perfharmony && D:/code/autotest/venv/Scripts/python -m pip install maturin -q
D:/code/autotest/venv/Scripts/python -m maturin build --release
D:/code/autotest/venv/Scripts/python -m pip install --force-reinstall target/wheels/perfharmony-0.3.0-*.whl
```

Expected: 生成并安装 `perfharmony-0.3.0-cp312-win_amd64.whl`

- [ ] **Step 4: 复跑契约测试**

Run: `cd D:/code/perfharmony && D:/code/autotest/venv/Scripts/python -m pytest tests/test_perfwin_contract.py -v`
Expected: PASS（含互斥校验用例）

- [ ] **Step 5: 提交两仓**

```bash
cd D:/code/perfharmony && git add -A && git commit -m "chore: release 0.3.0，同步契约文档"
cd D:/code/autotest && git add pyproject.toml && git commit -m "chore: perfharmony 依赖升级 0.3.0"
```

---

# Phase B — autotest worker

### Task B1: 协议新增 match_mode 字段

**Files:**
- Modify: `D:\code\autotest\worker\performance_monitor.py:39-51`（CollectStartRequest）、`:205-215` 附近（start_collect 存储处）、`_is_same_task`
- Test: 新建 `D:\code\autotest\tests\test_perfharmony_match_mode.py`

**Interfaces:**
- Produces: `CollectStartRequest.match_mode: Literal["fuzzy", "exact"] = "fuzzy"`；`PerformanceCollector` 实例属性 `self._match_mode`；`_is_same_task` 将 match_mode 纳入比较。

- [ ] **Step 1: 写失败测试**

```python
"""match_mode 协议字段与任务比对测试。"""
import pytest
from worker.performance_monitor import CollectStartRequest, PerformanceCollector


def test_match_mode_defaults_to_fuzzy():
    request = CollectStartRequest(collect_id="c1", device_type="harmony_pc", device_sn="SN1")
    assert request.match_mode == "fuzzy"


def test_match_mode_rejects_unknown_value():
    with pytest.raises(Exception):
        CollectStartRequest(collect_id="c1", match_mode="regex")


def test_same_task_requires_same_match_mode():
    collector = PerformanceCollector("dev1")
    base = dict(collect_id="c1", interval=5, device_type="harmony_pc", device_sn="SN1")
    from worker.performance_monitor import TargetProcess
    request_a = CollectStartRequest(**base, target_processes=[TargetProcess(name="com.app")], match_mode="fuzzy")
    request_b = CollectStartRequest(**base, target_processes=[TargetProcess(name="com.app")], match_mode="exact")
    collector._collect_id = "c1"
    collector._interval = 5
    collector._device_type = "harmony_pc"
    collector._device_sn = "SN1"
    collector._match_mode = "fuzzy"
    collector._target_processes = [TargetProcess(name="com.app")]
    assert collector._is_same_task(request_a) is True
    assert collector._is_same_task(request_b) is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd D:/code/autotest && venv/Scripts/python -m pytest tests/test_perfharmony_match_mode.py -v`
Expected: FAIL（无 match_mode 字段）

- [ ] **Step 3: 实现**

`CollectStartRequest` 增加字段：

```python
    match_mode: Literal["fuzzy", "exact"] = Field(
        "fuzzy",
        description="鸿蒙匹配模式：fuzzy=设备端 -PKG 包名匹配；exact=ps -ef 定位 PID 后 -PID 精准采集",
    )
```

文件顶部 import 行加 `Literal`（`from typing import ... Literal`，与现有 typing 导入合并）。

`start_collect` 中存储处（`self._target_processes = list(request.target_processes)` 同块）加：

```python
            self._match_mode = request.match_mode or "fuzzy"
```

`__init__` 中加 `self._match_mode: str = "fuzzy"`；`_stop_collect_internal` 清理处（`self._target_processes = []` 附近）加 `self._match_mode = "fuzzy"`。

`_is_same_task` 在 device_sn 比较之后加：

```python
        if (self._match_mode or "fuzzy") != (request.match_mode or "fuzzy"):
            return False
```

- [ ] **Step 4: 跑测试确认通过**（同 Step 2 命令，Expected: PASS）
- [ ] **Step 5: 提交**

```bash
cd D:/code/autotest && git add worker/performance_monitor.py tests/test_perfharmony_match_mode.py && git commit -m "feat: 采集协议新增 match_mode 字段（fuzzy/exact）"
```

### Task B2: PerfharmonyBackend 精准模式（PID 发现 + 30s 跟随重启）

**Files:**
- Modify: `D:\code\autotest\worker\perf_backends\perfharmony_backend.py`
- Modify: `D:\code\autotest\worker\performance_monitor.py`（`_collect_loop` heartbeat 钩子；`_create_backend` 鸿蒙分支重写——与 B3 共用，本任务先做单目标）
- Test: `D:\code\autotest\tests\test_perfharmony_match_mode.py` 追加

**Interfaces:**
- Consumes: Task A3 的 `perfharmony.Monitor(..., pid=)`、`perfharmony.list_processes(udid, hdc_path) -> list[tuple[int, str]]`
- Produces: `PerfharmonyBackend.start(*, interval, duration, package=None, match_mode="fuzzy")`；`heartbeat()`（fuzzy 模式 no-op）；`get_result()` 返回对象含 `.samples`（重启前旧样本先暂存合并，不丢数据）。

行为规格：
- exact 模式启动时用 `list_processes` 精确解析 PID（进程名 == 包名优先，其次 `包名:子进程`），未找到则先起无参 Monitor（仅系统指标），`heartbeat()` 每 ≥30s 重解析；PID 出现/变化时：暂存旧 Monitor 缓冲 → stop 旧 → 起新（携带 -PID）。设备端 SP_daemon 单例，必须先 stop 再 start（中断 1-2s 属预期）。
- fuzzy 模式行为与现状完全一致。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_perfharmony_match_mode.py`：

```python
"""PerfharmonyBackend 精准模式测试：全部用注入的假 perfharmony 模块，不碰真机。"""
import sys
import types
from datetime import datetime, timezone

import pytest

from worker.perf_backends.perfharmony_backend import PerfharmonyBackend


class _FakeResult:
    def __init__(self, samples):
        self.samples = samples


class FakeMonitor:
    """记录构造参数与启停；samples 由测试用例通过类变量注入。"""

    created = []
    next_samples = []

    def __init__(self, *, udid, hdc_path=None, interval=1.0, duration=None, package=None, pid=None):
        self.kwargs = dict(udid=udid, hdc_path=hdc_path, interval=interval,
                           duration=duration, package=package, pid=pid)
        self.started = False
        self.stopped = False
        FakeMonitor.created.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def is_running(self):
        return self.started and not self.stopped

    def buffer_len(self):
        return len(FakeMonitor.next_samples)

    def get_result(self):
        samples, FakeMonitor.next_samples = FakeMonitor.next_samples, []
        return _FakeResult(samples)

    def last_error(self):
        return None


@pytest.fixture()
def fake_module(monkeypatch):
    module = types.ModuleType("perfharmony")
    module.Monitor = FakeMonitor
    module.list_processes = staticmethod(lambda udid, hdc_path=None: list(FakeMonitor.ps_list))
    monkeypatch.setitem(sys.modules, "perfharmony", module)
    FakeMonitor.created = []
    FakeMonitor.next_samples = []
    FakeMonitor.ps_list = []
    return module


def _sample(seq=1):
    return types.SimpleNamespace(sequence=seq, elapsed_ms=seq * 1000,
                                 timestamp=datetime(2026, 9, 8, tzinfo=timezone.utc),
                                 system={}, hwinfo_raw={}, processes=[],
                                 aggregated=[], top_n_cpu=None, top_n_gpu=None)


def test_exact_mode_resolves_pid_at_start(fake_module):
    FakeMonitor.ps_list = [(100, "com.app"), (200, "com.app:render")]
    backend = PerfharmonyBackend(udid="SN1")
    backend.start(interval=5, duration=3600, package="com.app", match_mode="exact")
    assert FakeMonitor.created[0].kwargs["pid"] == 100
    assert FakeMonitor.created[0].kwargs["package"] is None


def test_exact_mode_system_only_when_app_absent(fake_module):
    FakeMonitor.ps_list = []
    backend = PerfharmonyBackend(udid="SN1")
    backend.start(interval=5, duration=3600, package="com.app", match_mode="exact")
    assert FakeMonitor.created[0].kwargs["pid"] is None
    assert FakeMonitor.created[0].kwargs["package"] is None


def test_exact_mode_follows_pid_change_within_30s_window(fake_module):
    FakeMonitor.ps_list = []
    backend = PerfharmonyBackend(udid="SN1")
    backend.start(interval=5, duration=3600, package="com.app", match_mode="exact")
    # 强制到期，模拟 30s 后复核
    backend._last_pid_check = 0.0
    FakeMonitor.ps_list = [(100, "com.app")]
    FakeMonitor.next_samples = [_sample(1)]
    backend.heartbeat()
    assert FakeMonitor.created[0].stopped is True          # 旧 Monitor 已停
    assert FakeMonitor.created[1].kwargs["pid"] == 100     # 新 Monitor 带新 PID
    # 旧样本不丢：暂存后随 get_result 一并返回
    result = backend.get_result()
    assert [s.sequence for s in result.samples] == [1]
    # PID 未变化时 heartbeat 不重启
    FakeMonitor.created.clear()
    backend._last_pid_check = 0.0
    backend.heartbeat()
    assert FakeMonitor.created == []


def test_fuzzy_mode_heartbeat_is_noop(fake_module):
    backend = PerfharmonyBackend(udid="SN1")
    backend.start(interval=5, duration=3600, package="com.app", match_mode="fuzzy")
    assert FakeMonitor.created[0].kwargs["package"] == "com.app"
    backend._last_pid_check = 0.0
    backend.heartbeat()
    assert len(FakeMonitor.created) == 1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd D:/code/autotest && venv/Scripts/python -m pytest tests/test_perfharmony_match_mode.py -v`
Expected: 新增 4 个用例 FAIL（start 不接受 match_mode / heartbeat 不存在）

- [ ] **Step 3: 实现**

`perfharmony_backend.py` 重写（完整文件内容）：

```python
"""鸿蒙性能采集后端，延迟加载独立 perfharmony 库。"""

from __future__ import annotations

import logging
import time
from types import SimpleNamespace
from typing import Any

logger = logging.getLogger(__name__)

PID_CHECK_INTERVAL_SECS = 30.0


class _EmptyResult:
    """后端尚未启动时的空结果。"""

    samples: list = []

    def to_dicts(self) -> list:
        """返回空字典列表。"""
        return []


class PerfharmonyBackend:
    """通过 HDC UDID 采集 HarmonyOS 设备性能。

    match_mode：
    - fuzzy：设备端 SP_daemon -PKG 按包名采集（自动含主进程+子进程）；
    - exact：ps -ef 精确解析 PID 后 SP_daemon -PID 单进程采集；
      应用未启动时先采系统指标，heartbeat 每 30s 复核，PID 出现/变化自动重启跟随。
    """

    def __init__(self, *, udid: str, hdc_path: str | None = None) -> None:
        if not udid or not udid.strip():
            raise ValueError("鸿蒙性能采集必须提供 device_sn/HDC UDID")
        self.udid = udid.strip()
        self.hdc_path = hdc_path or None
        self._monitor: Any | None = None
        self._package: str | None = None
        self._pid: int | None = None
        self._match_mode: str = "fuzzy"
        self._interval: float = 1.0
        self._duration: float | None = None
        self._last_pid_check: float = 0.0
        self._restarting: bool = False
        self._pending: list = []

    @staticmethod
    def _module():
        """按需导入，确保 Windows perfwin 路径不依赖 perfharmony。"""
        try:
            import perfharmony
        except ImportError as error:
            raise RuntimeError("未安装 perfharmony，请先安装对应的 wheel") from error
        return perfharmony

    def start(
        self,
        *,
        interval: float,
        duration: float | None,
        package: str | None = None,
        match_mode: str = "fuzzy",
    ) -> None:
        """创建并启动 Harmony Monitor。"""
        self._interval = interval
        self._duration = duration
        self._package = package
        self._match_mode = match_mode if match_mode in ("fuzzy", "exact") else "fuzzy"
        self._pid = None
        if self._match_mode == "exact" and package:
            self._pid = self._resolve_pid(package)
        self._last_pid_check = time.monotonic()
        self._start_monitor()

    def _start_monitor(self) -> None:
        """按当前 package/pid 组合启动 Monitor；exact 未命中 PID 时仅采系统指标。"""
        perfharmony = self._module()
        self._monitor = perfharmony.Monitor(
            udid=self.udid,
            hdc_path=self.hdc_path,
            interval=self._interval,
            duration=self._duration,
            package=self._package if self._match_mode == "fuzzy" else None,
            pid=self._pid,
        )
        self._monitor.start()

    def heartbeat(self) -> None:
        """精准模式下按 30s 节奏复核 PID；由采集循环在每轮排空前调用。"""
        if self._match_mode != "exact" or not self._package:
            return
        if time.monotonic() - self._last_pid_check < PID_CHECK_INTERVAL_SECS:
            return
        self._last_pid_check = time.monotonic()
        try:
            pid = self._resolve_pid(self._package)
        except Exception as error:
            logger.warning("鸿蒙精准采集 PID 复核失败: %s", error)
            return
        if pid == self._pid:
            return
        logger.info("鸿蒙精准采集 PID 变更: %s -> %s，重启采集流", self._pid, pid)
        old = self._monitor
        self._restarting = True
        try:
            if old is not None:
                self._stash_pending(old)
                old.stop()
        finally:
            self._pid = pid
            self._last_pid_check = time.monotonic()
            try:
                self._start_monitor()
            finally:
                self._restarting = False

    def _resolve_pid(self, package: str) -> int | None:
        """按包名精确解析主进程 PID：进程名恰为包名优先，其次首个「包名:子进程」。"""
        values = self.list_processes(None)
        candidates = [
            (pid, name) for pid, name in values if self._bundle_base(name) == package
        ]
        if not candidates:
            return None
        for pid, name in candidates:
            if name == package:
                return pid
        return candidates[0][0]

    @staticmethod
    def _bundle_base(name: str) -> str:
        """与 Rust bundle_base_name/worker _harmony_bundle_base 语义一致。"""
        base, sep, _ = name.partition(":")
        if sep and "." in base and "/" not in base:
            return base
        return name

    def _stash_pending(self, monitor: Any) -> None:
        """重启前暂存旧 Monitor 未上报样本，避免换 PID 时丢数据。"""
        try:
            self._pending.extend(monitor.get_result().samples)
        except Exception as error:
            logger.warning("暂存旧 Monitor 样本失败: %s", error)

    def stop(self) -> None:
        """停止 Harmony Monitor。"""
        if self._monitor:
            self._monitor.stop()

    def is_running(self) -> bool:
        """返回采集线程是否运行（重启窗口期内视为运行，避免外层误判终态）。"""
        return self._restarting or bool(self._monitor and self._monitor.is_running())

    def buffer_len(self) -> int:
        """返回待上报样本数。"""
        return self._monitor.buffer_len() if self._monitor else 0

    def get_result(self) -> Any:
        """读取并排空增量采样结果；含换 PID 前暂存的旧样本。"""
        if self._pending:
            pending, self._pending = self._pending, []
            current = self._monitor.get_result().samples if self._monitor else []
            return SimpleNamespace(samples=pending + current)
        return self._monitor.get_result() if self._monitor else _EmptyResult()

    def last_error(self) -> str | None:
        """读取 Monitor 最近一次设备/采集错误。"""
        if not self._monitor:
            return None
        error = getattr(self._monitor, "last_error", None)
        if callable(error):
            error = error()
        if error is None:
            return None
        text = str(error).strip()
        return text or None

    def list_processes(self, search: str | None = None) -> list[tuple[int, str]]:
        """读取 Harmony 设备进程列表。"""
        perfharmony = self._module()
        values = perfharmony.list_processes(self.udid, self.hdc_path)
        if not search:
            return values
        keyword = search.lower()
        return [(pid, name) for pid, name in values if keyword in name.lower()]
```

`performance_monitor.py` `_collect_loop`：在 `self._flush_spool()` 之前插入 heartbeat 钩子：

```python
            try:
                # 精准模式后端按 30s 节奏复核 PID（fuzzy 后端为 no-op）。
                backend = self._backend
                if backend is not None and hasattr(backend, "heartbeat"):
                    backend.heartbeat()
                # 先发送历史 spool，再读取本轮增量数据。
                self._flush_spool()
                self._drain_backend_buffer()
```

`_create_backend` 鸿蒙分支（先做单目标，B3 再扩多目标）：删掉「不支持按 PID 筛选」的 raise（`:295-296`），`backend.start(...)` 增加 `match_mode=getattr(request, "match_mode", "fuzzy") or "fuzzy"`。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd D:/code/autotest && venv/Scripts/python -m pytest tests/test_perfharmony_match_mode.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
cd D:/code/autotest && git add worker/perf_backends/perfharmony_backend.py worker/performance_monitor.py tests/test_perfharmony_match_mode.py && git commit -m "feat: 鸿蒙精准采集模式，PID 自动发现与 30s 跟随重启"
```

### Task B3: 多目标并发采集（多包名 / 多 PID）

**Files:**
- Modify: `D:\code\autotest\worker\perf_backends\perfharmony_backend.py`（新增 HarmonyMultiBackend）
- Modify: `D:\code\autotest\worker\performance_monitor.py`（_create_backend 鸿蒙分支支持 N 目标）
- Test: `D:\code\autotest\tests\test_perfharmony_match_mode.py` 追加

**Interfaces:**
- Produces: `HarmonyMultiBackend(backends, interval)`，实现与 PerfharmonyBackend 相同的方法面（start/stop/is_running/buffer_len/get_result/last_error/heartbeat）。样本按 `floor(timestamp) / interval` 时间桶合并：system/hwinfo_raw 取首个子后端，processes/aggregated 按子后端顺序拼接，sequence 由 MultiBackend 重新编号。
- 设备端并发依据：真机已验证两个 SP_daemon 实例可同时出数。

- [ ] **Step 1: 写失败测试**

```python
def test_multi_backend_merges_samples_by_time_bucket(fake_module):
    from worker.perf_backends.perfharmony_backend import HarmonyMultiBackend

    FakeMonitor.ps_list = []
    backend_a = PerfharmonyBackend(udid="SN1")
    backend_a.start(interval=5, duration=3600, package="com.app.a", match_mode="fuzzy")
    backend_b = PerfharmonyBackend(udid="SN1")
    backend_b.start(interval=5, duration=3600, package="com.app.b", match_mode="fuzzy")
    assert FakeMonitor.created[0].kwargs["package"] == "com.app.a"
    assert FakeMonitor.created[1].kwargs["package"] == "com.app.b"

    ts = datetime(2026, 9, 8, 0, 0, 2, tzinfo=timezone.utc)
    sample_a = types.SimpleNamespace(sequence=1, elapsed_ms=2000, timestamp=ts,
                                     system={"cpu_percent": 1.0}, hwinfo_raw={"k": 1},
                                     processes=[("p-a",)], aggregated=[{"name": "com.app.a"}],
                                     top_n_cpu=None, top_n_gpu=None)
    FakeMonitor.next_samples = [sample_a]
    backend_a._monitor.next_samples = []
    FakeMonitor.next_samples = []
    # 给 backend_b 的 monitor 注入同桶样本：直接替换其 _monitor 为带样本的实例
    sample_b = types.SimpleNamespace(sequence=1, elapsed_ms=2300, timestamp=ts,
                                     system={"cpu_percent": 1.0}, hwinfo_raw={"k": 1},
                                     processes=[("p-b",)], aggregated=[{"name": "com.app.b"}],
                                     top_n_cpu=None, top_n_gpu=None)
    backend_b._monitor.get_result = lambda: _FakeResult([sample_b])

    multi = HarmonyMultiBackend([backend_a, backend_b], interval=5)
    merged = multi.get_result().samples
    assert len(merged) == 1
    assert merged[0]["sequence"] == 1
    assert merged[0]["system"] == {"cpu_percent": 1.0}
    assert merged[0]["processes"] == [("p-a",), ("p-b",)]
    assert [agg["name"] for agg in merged[0]["aggregated"]] == ["com.app.a", "com.app.b"]
    assert multi.buffer_len() == 0
```

（注意 `FakeMonitor.buffer_len` 消费 next_samples，测试里对 `backend_a` 走 `get_result` 前先排空，避免断言歧义；上面写法已按此排布，执行时若 buffer_len 断言失败，按「先 get_result 后断言」的顺序调整。）

- [ ] **Step 2: 跑测试确认失败**

Run: `cd D:/code/autotest && venv/Scripts/python -m pytest tests/test_perfharmony_match_mode.py -v`
Expected: FAIL（HarmonyMultiBackend 不存在）

- [ ] **Step 3: 实现**

`perfharmony_backend.py` 末尾追加：

```python
class HarmonyMultiBackend:
    """多目标鸿蒙采集：每个包名/PID 一个 SP_daemon 实例（真机验证可并发），
    按时间桶合并成单路样本流，对上层保持与单后端一致的方法面。"""

    def __init__(self, backends: list[PerfharmonyBackend], interval: int) -> None:
        if not backends:
            raise ValueError("HarmonyMultiBackend 至少需要一个子后端")
        self._backends = backends
        self._interval = max(1, int(interval))
        self._sequence = 0

    def start(self, *, interval: float, duration: float | None, packages: list[str | None],
              match_mode: str = "fuzzy") -> None:
        """按 packages 顺序启动各子后端（一包一实例）。"""
        if len(packages) != len(self._backends):
            raise ValueError("packages 数量必须与子后端数量一致")
        for backend, package in zip(self._backends, packages):
            backend.start(interval=interval, duration=duration,
                          package=package, match_mode=match_mode)

    def heartbeat(self) -> None:
        """扇出精准模式 PID 复核。"""
        for backend in self._backends:
            backend.heartbeat()

    def stop(self) -> None:
        for backend in self._backends:
            backend.stop()

    def is_running(self) -> bool:
        return any(backend.is_running() for backend in self._backends)

    def buffer_len(self) -> int:
        return sum(backend.buffer_len() for backend in self._backends)

    def get_result(self) -> Any:
        """按 floor(timestamp)/interval 桶合并各子后端样本。"""
        merged: dict[int, dict] = {}
        order: list[int] = []
        for backend in self._backends:
            for sample in backend.get_result().samples:
                ts = getattr(sample, "timestamp", None)
                if ts is None:
                    continue
                bucket = int(ts.timestamp()) // self._interval
                elapsed_ms = int(getattr(sample, "elapsed_ms", 0) or 0)
                if bucket not in merged:
                    order.append(bucket)
                    merged[bucket] = {
                        "sequence": 0,
                        "elapsed_ms": elapsed_ms,
                        "timestamp": ts,
                        "system": getattr(sample, "system", None),
                        "hwinfo_raw": getattr(sample, "hwinfo_raw", None),
                        "processes": list(getattr(sample, "processes", None) or []),
                        "aggregated": list(getattr(sample, "aggregated", None) or []),
                        "top_n_cpu": None,
                        "top_n_gpu": None,
                    }
                else:
                    entry = merged[bucket]
                    entry["elapsed_ms"] = min(entry["elapsed_ms"], elapsed_ms)
                    entry["processes"].extend(getattr(sample, "processes", None) or [])
                    entry["aggregated"].extend(getattr(sample, "aggregated", None) or [])
        samples = []
        for bucket in order:
            self._sequence += 1
            entry = merged[bucket]
            entry["sequence"] = self._sequence
            samples.append(entry)
        return SimpleNamespace(samples=samples)

    def last_error(self) -> str | None:
        """任一子后端仍在运行时不返回错误；全部停止后返回首个非空错误。"""
        if self.is_running():
            return None
        for backend in self._backends:
            error = backend.last_error()
            if error:
                return error
        return None
```

`_create_backend` 鸿蒙分支整体替换为：

```python
        if device_type in ("harmony_pc", "harmony_mobile"):
            if not request.device_sn:
                raise ValueError("鸿蒙性能采集必须提供 device_sn（HDC UDID）")
            match_mode = getattr(request, "match_mode", "fuzzy") or "fuzzy"
            # 每个目标一个 SP_daemon 实例（真机验证可并发），包名归一取冒号前。
            children: list[tuple[PerfharmonyBackend, str | None]] = []
            for tp in request.target_processes:
                package = self._harmony_bundle_base(tp.name.strip()) if tp.name.strip() else None
                if package:
                    children.append(
                        (PerfharmonyBackend(udid=request.device_sn, hdc_path=self._hdc_path), package)
                    )
            if not children:
                # 未选应用：仅系统指标。
                backend = PerfharmonyBackend(udid=request.device_sn, hdc_path=self._hdc_path)
                backend.start(interval=float(request.interval), duration=float(request.timeout))
                self._backend = backend
                return
            if len(children) == 1:
                backend, package = children[0]
                backend.start(
                    interval=float(request.interval),
                    duration=float(request.timeout),
                    package=package,
                    match_mode=match_mode,
                )
                self._backend = backend
                return
            multi = HarmonyMultiBackend(
                [backend for backend, _ in children], interval=request.interval
            )
            multi.start(
                interval=float(request.interval),
                duration=float(request.timeout),
                packages=[package for _, package in children],
                match_mode=match_mode,
            )
            self._backend = multi
            return
```

同时删除原「鸿蒙采集一次仅支持一个应用」「不支持按 PID 筛选」两处 raise（已含在上面的整体替换中）。

- [ ] **Step 4: 跑全量测试**

Run: `cd D:/code/autotest && venv/Scripts/python -m pytest tests/test_perfharmony_match_mode.py -v && ruff check worker/perf_backends/perfharmony_backend.py worker/performance_monitor.py`
Expected: PASS / 无 lint 错误

- [ ] **Step 5: 提交**

```bash
cd D:/code/autotest && git add -A && git commit -m "feat: 鸿蒙多目标并发采集，按时间桶合并多实例样本"
```

---

# Phase C — zq-platform 后端

### Task C1: 采集记录冗余设备信息 + match_mode 落库

**Files:**
- Modify: `D:\code\zq-platform\backend-fastapi\core\performance_monitor\model.py:28`（PerformanceCollect 加列）
- Create: `D:\code\zq-platform\backend-fastapi\alembic\versions\<rev>_add_device_info_to_performance_collect.py`
- Modify: `D:\code\zq-platform\backend-fastapi\core\performance_monitor\schema.py`（CollectStartRequest:15-23 加 match_mode；CollectResponse:253-287 加 4 个响应字段）
- Modify: `D:\code\zq-platform\backend-fastapi\core\performance_monitor\service.py:118-167`（start_collect 落库）
- Modify: `D:\code\zq-platform\backend-fastapi\core\performance_monitor\api.py:140-143`（传 device）

**Interfaces:**
- Produces: `performance_collect` 表新增列 `device_type`/`device_ip`/`device_sn`/`match_mode`；`CollectResponse` 返回这 4 个字段（`CollectResponse` 为 `from_attributes=True`，model_validate 自动带出，无需改构造代码）。

- [ ] **Step 1: 模型加列**（`device_id` 列定义之后）

```python
    # 设备信息快照（开始采集时冗余落库，历史记录与版本对比展示用）
    device_type = Column(String(30), nullable=True, comment="设备类型快照")
    device_ip = Column(String(64), nullable=True, comment="设备IP快照")
    device_sn = Column(String(64), nullable=True, comment="设备SN快照（鸿蒙为HDC UDID）")
    match_mode = Column(String(10), nullable=True, comment="鸿蒙匹配模式：fuzzy/exact")
```

- [ ] **Step 2: 生成迁移文件**

```bash
cd D:/code/zq-platform/backend-fastapi && python -m alembic heads
```

记下当前 head revision，然后手写迁移（`down_revision` 填上面命令的输出）：

```python
"""add device info to performance_collect

Revision ID: b1c2d3e4f5a6
Revises: <alembic heads 输出值>
Create Date: 2026-09-08
"""
from alembic import op
import sqlalchemy as sa

revision = "b1c2d3e4f5a6"
down_revision = "<alembic heads 输出值>"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("performance_collect", sa.Column("device_type", sa.String(30), nullable=True, comment="设备类型快照"))
    op.add_column("performance_collect", sa.Column("device_ip", sa.String(64), nullable=True, comment="设备IP快照"))
    op.add_column("performance_collect", sa.Column("device_sn", sa.String(64), nullable=True, comment="设备SN快照（鸿蒙为HDC UDID）"))
    op.add_column("performance_collect", sa.Column("match_mode", sa.String(10), nullable=True, comment="鸿蒙匹配模式：fuzzy/exact"))


def downgrade():
    op.drop_column("performance_collect", "match_mode")
    op.drop_column("performance_collect", "device_sn")
    op.drop_column("performance_collect", "device_ip")
    op.drop_column("performance_collect", "device_type")
```

- [ ] **Step 3: schema 加字段**

`CollectStartRequest` 追加：

```python
    match_mode: Optional[str] = Field(
        None, description="鸿蒙匹配模式：fuzzy=设备端-PKG包名匹配；exact=定位PID后-PID精准采集"
    )
```

`CollectResponse` 追加：

```python
    device_type: Optional[str] = Field(None, description="设备类型快照")
    device_ip: Optional[str] = Field(None, description="设备IP快照")
    device_sn: Optional[str] = Field(None, description="设备SN快照（鸿蒙为HDC UDID）")
    match_mode: Optional[str] = Field(None, description="鸿蒙匹配模式")
```

- [ ] **Step 4: service 落库**

`start_collect` 签名与构造：

```python
    async def start_collect(
        cls, db: AsyncSession, request: CollectStartRequest, device: Optional[EnvMachine] = None
    ) -> str:
```

（顶部确认已 import `EnvMachine`，无则从 `core.env_machine.model` 导入。）

```python
        collect = PerformanceCollect(
            device_id=request.device_id,
            start_time=start_time_utc.replace(tzinfo=None),  # 存储为 naive datetime（UTC）
            interval=request.interval,
            target_processes=request.target_processes,
            match_mode=request.match_mode or "fuzzy",
            device_type=device.device_type if device else None,
            device_ip=device.ip if device else None,
            device_sn=(device.device_sn or None) if device else None,
            status="starting"
        )
```

`api.py` `start_collect` 调用处改为：

```python
        collect_id = await PerformanceCollectService.start_collect(db, request, device=device)
```

- [ ] **Step 5: 验证**

```bash
cd D:/code/zq-platform/backend-fastapi && python -m compileall core/performance_monitor -q && python -m alembic upgrade head
```

Expected: 编译无错；迁移执行成功（需要本地 Postgres 已启动，见 docker-compose.yml）

- [ ] **Step 6: 提交**

```bash
cd D:/code/zq-platform && git add backend-fastapi/core/performance_monitor backend-fastapi/alembic/versions && git commit -m "feat: 性能采集记录冗余设备信息与匹配模式"
```

### Task C2: match_mode 平台 → worker 透传

**Files:**
- Modify: `D:\code\zq-platform\backend-fastapi\core\performance_monitor\worker_client.py:55-77`（notify_worker_start）
- Modify: `D:\code\zq-platform\backend-fastapi\core\performance_monitor\api.py:172-184`（background_tasks.add_task 实参）

- [ ] **Step 1: worker_client.py** — `notify_worker_start` 签名加 `match_mode: str | None,`（`device_sn` 之后），worker_request 构造改为：

```python
    worker_request = {
        "collect_id": collect_id,
        "interval": interval,
        "timeout": timeout,
        "target_processes": target_processes or [],
        "device_type": device_type,
        "match_mode": match_mode or "fuzzy",
    }
```

- [ ] **Step 2: api.py** — `background_tasks.add_task(notify_worker_start, ..., device_sn=device_sn, match_mode=request.match_mode,)`
- [ ] **Step 3: 验证 + 提交**

```bash
cd D:/code/zq-platform/backend-fastapi && python -m compileall core/performance_monitor -q
cd D:/code/zq-platform && git add backend-fastapi/core/performance_monitor && git commit -m "feat: match_mode 透传到 worker 采集接口"
```

---

# Phase D — zq-platform 前端

### Task D1: 设备下拉与状态卡片显示 SN

**Files:**
- Modify: `D:\code\zq-platform\web\apps\web-ele\src\views\performance-monitor\index.vue:1283-1310`（下拉与卡片）、script 区加两个 helper

**Interfaces:** Consumes `EnvMachine.device_sn`（接口已返回）。 Produces: `shortSn()` / `deviceLabel()` helper，D3 复用。

- [ ] **Step 1: script 区（`currentDeviceInfo` 之后）加 helper**

```ts
// SN 展示缩略：超长时保留尾部 8 位（SN 尾部是唯一性最高的部分）
function shortSn(sn?: string | null): string {
  if (!sn) return '';
  return sn.length > 10 ? `…${sn.slice(-8)}` : sn;
}

// 设备下拉 label：鸿蒙/安卓等带 SN 设备必须可区分（同 IP 多台只靠 SN 区分）
function deviceLabel(device: EnvMachine): string {
  const sn = device.device_sn ? ` (${shortSn(device.device_sn)})` : '';
  return `${device.device_type} - ${device.ip}${sn}`;
}
```

- [ ] **Step 2: 下拉模板改造（:1283-1301）**

```html
          <el-option
            v-for="device in onlineDevices"
            :key="device.id"
            :label="deviceLabel(device)"
            :value="device.id"
          >
            <span>{{ device.device_type }}</span>
            <span style="margin-left: 10px; color: #409eff">{{ device.ip }}</span>
            <span v-if="device.device_sn" style="margin-left: 10px; color: #909399">
              SN {{ device.device_sn }}
            </span>
          </el-option>
```

- [ ] **Step 3: 设备状态卡片（:1305-1308）加 SN**

```html
        <div v-if="!collectStatus.is_collecting && currentDeviceInfo" class="device-status-card">
          <span class="device-ip">{{ currentDeviceInfo.ip }}</span>
          <span v-if="currentDeviceInfo.device_sn" class="device-sn" style="margin-left: 8px; color: #909399">
            SN {{ currentDeviceInfo.device_sn }}
          </span>
          <span class="online-badge" v-if="currentDeviceInfo.status === 'online' || currentDeviceInfo.status === 'using'">● 在线</span>
        </div>
```

- [ ] **Step 4: 检查 + 提交**

```bash
cd D:/code/zq-platform/web && pnpm check:type
cd D:/code/zq-platform && git add web/apps/web-ele/src/views/performance-monitor/index.vue && git commit -m "feat: 性能监控设备下拉与状态卡片展示设备SN"
```

### Task D2: 开始采集弹窗显示 SN

**Files:**
- Modify: `D:\code\zq-platform\web\apps\web-ele\src\views\performance-monitor\components\CollectDialog.vue:36-42`

- [ ] **Step 1: deviceDisplay 追加 SN**

```ts
// 设备显示信息（带 SN 设备必须可区分，鸿蒙同 IP 多台仅靠 SN）
const deviceDisplay = computed(() => {
  if (props.deviceInfo) {
    const deviceType = props.deviceInfo.device_type || 'windows';
    const sn = props.deviceInfo.device_sn ? ` (SN ${props.deviceInfo.device_sn})` : '';
    return `${deviceType}-${props.deviceInfo.ip}${sn}`;
  }
  return '未选择设备';
});
```

- [ ] **Step 2: 检查 + 提交**（`pnpm check:type`；commit `-m "feat: 采集弹窗目标设备展示SN"`）

### Task D3: 历史采集记录展示设备信息

**Files:**
- Modify: `D:\code\zq-platform\web\apps\web-ele\src\api\core\performance-monitor.ts`（采集记录响应 interface 追加字段）
- Modify: `D:\code\zq-platform\web\apps\web-ele\src\views\performance-monitor\index.vue`（历史卡片 :1557-1570 加一行；版本选择器 :1435-1440 label 追加设备后缀）

**Interfaces:** Consumes Task C1 的 `CollectResponse` 新字段 `device_type/device_ip/device_sn/match_mode`。

- [ ] **Step 1: TS 类型追加**（采集记录响应 interface，文件内搜 `collect_id` 所在 interface）

```ts
  device_type?: string;
  device_ip?: string;
  device_sn?: string;
  match_mode?: string;
```

- [ ] **Step 2: 历史卡片加「采集设备」行**（card-info-row 内、采集频率之后）

```html
              <div class="info-item" v-if="c.device_type || c.device_ip || c.device_sn">
                <span class="info-label">采集设备</span>
                <span class="info-value">
                  {{ [c.device_type, c.device_ip, c.device_sn ? `SN ${shortSn(c.device_sn)}` : ''].filter(Boolean).join(' · ') }}
                </span>
              </div>
```

- [ ] **Step 3: 版本选择器 label 加设备后缀**（script 加 helper，模板改 label）

```ts
// 采集记录的设备标识后缀，用于版本对比时区分来源设备
function collectDeviceSuffix(c: { device_type?: string; device_ip?: string; device_sn?: string }): string {
  const parts = [c.device_type, c.device_ip, c.device_sn ? `SN ${shortSn(c.device_sn)}` : '']
    .filter(Boolean);
  return parts.length ? ` · ${parts.join('·')}` : '';
}
```

```html
              :label="(c.name || `${new Date(c.start_time).toLocaleString('zh-CN')} (${c.interval}s)`) + collectDeviceSuffix(c)"
```

- [ ] **Step 4: 检查 + 提交**（`pnpm check:type`；commit `-m "feat: 历史采集记录与版本选择器展示设备标识"`）

### Task D4: 页面刷新固定 5 秒

**Files:**
- Modify: `D:\code\zq-platform\web\apps\web-ele\src\views\performance-monitor\index.vue:822-832`

- [ ] **Step 1: 替换 startPolling**

```ts
// 页面固定 5 秒刷新一次：采集间隔只决定数据入库节奏，页面刷新不跟随，
// 避免 1 秒采集时前端请求与图表重绘过于频繁。
const PAGE_REFRESH_INTERVAL_MS = 5000;

function startPolling(collectId: string) {
  if (pollingTimer) clearInterval(pollingTimer);

  // 立即获取最新数据显示
  loadLatestData(collectId);

  // 定时轮询获取最新数据（固定 5s，与采集间隔解耦）
  pollingTimer = window.setInterval(async () => {
    await loadLatestData(collectId);
  }, PAGE_REFRESH_INTERVAL_MS);
}
```

- [ ] **Step 2: 检查 + 提交**（`pnpm check:type`；commit `-m "fix: 性能监控页面轮询固定5秒，与采集间隔解耦"`）

### Task D5: 折线图 tooltip 进程明细增强（保持合线展示）

**Files:**
- Modify: `D:\code\zq-platform\web\apps\web-ele\src\views\performance-monitor\components\MiniTooltip.vue:64-77`（processSummary）、`:140-153`（容器样式）

**Interfaces:** Consumes `PerformanceData.target_processes[]`（含 `instances` 明细）。折线图保持「多进程合一条」展示不变（Windows 与鸿蒙一致），悬浮 tooltip 的进程摘要区显示**全部**目标进程各自数值：去掉「只取前 3 个」截断与「instances 非空」过滤，容器限高放宽为可滚动。Windows 与鸿蒙共用同一逻辑，无平台分支。

- [ ] **Step 1: processSummary 显示全部进程**（:65-77 整体替换）

```ts
// 显示全部目标进程的当前指标数值（多进程合一条线时，悬浮即可看出每个进程的明细）
const processSummary = computed(() => {
  if (props.data?.target_processes && props.chartType !== 'hwinfo') {
    return props.data.target_processes.map(p => ({
      name: p.name,
      instanceCount: p.instances?.length || 0,
      valueText: processValueText(p)
    }));
  }
  return [];
});
```

- [ ] **Step 2: 容器样式放宽为可滚动**（`.mini-tooltip` 样式，:150-151）

```css
  max-height: 320px;
  overflow-y: auto;
```

- [ ] **Step 3: 检查 + 提交**

```bash
cd D:/code/zq-platform/web && pnpm check:type
cd D:/code/zq-platform && git add web/apps/web-ele/src/views/performance-monitor/components/MiniTooltip.vue && git commit -m "feat: 图表tooltip进程明细展示全部目标进程并支持滚动"
```

### Task D6: 采集弹窗匹配模式选择 + 鸿蒙多应用

**Files:**
- Modify: `D:\code\zq-platform\web\apps\web-ele\src\views\performance-monitor\components\CollectDialog.vue`
- Modify: `D:\code\zq-platform\web\apps\web-ele\src\api\core\performance-monitor.ts:209+`（startCollect 参数类型）

**Interfaces:** Produces `startCollect({ ..., match_mode })`；worker 端协议见 Task B1。

- [ ] **Step 1: script 增加模式状态与联动**

```ts
// 鸿蒙匹配模式：fuzzy=设备端 -PKG 包名匹配（含子进程）；exact=精准 PID，支持应用后启动自动跟随
const harmonyMatchMode = ref<'fuzzy' | 'exact'>('fuzzy');

// 鸿蒙强制包名模式（PID 模式对鸿蒙无意义：精准匹配由 worker 自动定位 PID）
watch(
  isHarmonyDevice,
  (value) => {
    if (value) collectMode.value = 'name';
  },
  { immediate: true },
);
```

- [ ] **Step 2: 鸿蒙解除单应用限制** — 三处改动：
  1. `toggleProcessName`（:172-184）删除鸿蒙替换逻辑：

```ts
// 进程名模式：选中/取消选中进程名
function toggleProcessName(name: string) {
  const idx = selectedProcessNames.value.indexOf(name);
  if (idx >= 0) {
    selectedProcessNames.value.splice(idx, 1);
    return;
  }
  selectedProcessNames.value.push(name);
}
```

  2. `handleManualAdd`（:236-240）删除鸿蒙「只取第一个包名」分支（多包名逗号分隔直接全部加入）。
  3. `handleStart`（:287-291）删除「鸿蒙设备一次仅支持采集一个应用」的拦截。

- [ ] **Step 3: 模板加匹配模式单选**（鸿蒙进程选择区之前；ElRadioGroup/ElRadioButton 已在 :3 导入。`label` 兼容写法跨 element-plus 版本）

```html
      <el-form-item v-if="isHarmonyDevice" label="匹配模式">
        <el-radio-group v-model="harmonyMatchMode">
          <el-radio-button value="fuzzy">PKG 模糊匹配</el-radio-button>
          <el-radio-button value="exact">PID 精准匹配</el-radio-button>
        </el-radio-group>
        <!-- element-plus 2.11.7：单选按钮用 value prop（label 仅作显示文本） -->
        <div class="mode-tip" style="width: 100%; margin-top: 4px; color: #909399; font-size: 12px; line-height: 1.5">
          {{ harmonyMatchMode === 'exact'
            ? '通过 ps -ef 按包名精准定位 PID 采集，不受同前缀包名干扰；应用未启动时每 30 秒自动探测，应用重启后自动跟随新 PID。'
            : '按包名交给设备端 SP_daemon 采集，自动包含主进程与全部子进程；存在同前缀包名时可能误匹配。' }}
        </div>
      </el-form-item>
```

- [ ] **Step 4: handleStart 携带 match_mode**（常规分支 :295-302 的 startCollect 入参追加）

```ts
      match_mode: isHarmonyDevice.value ? harmonyMatchMode.value : undefined,
```

- [ ] **Step 5: TS API 类型** — `startCollect` 参数类型（performance-monitor.ts:209+）追加 `match_mode?: string;`
- [ ] **Step 6: 检查 + 提交**

```bash
cd D:/code/zq-platform/web && pnpm check:type
cd D:/code/zq-platform && git add web/apps/web-ele/src && git commit -m "feat: 鸿蒙采集弹窗支持模糊/精准匹配模式选择与多应用采集"
```

---

# Phase E — 真机联调验收（手工）

前置：Phase A-D 全部完成；worker 已重启（`cd D:/code/autotest && venv/Scripts/python -m worker.main`，配置 `config/worker.yaml` 中 hdc_path=tools/hdc/hdc.exe）；平台后端与前端 dev 服务已启动；真机 UDID `2LQ0224125000197` 已连接（`tools/hdc/hdc.exe list targets`）。

- [ ] **E1 内存口径**：hidumper 基线 `hdc shell hidumper --mem <pid>` 记录 Pss Total/SwapPss → 平台对该应用采集 1 分钟 → 曲线「进程内存」≈ (Pss Total − SwapPss)/1024 MB（允许秒级抖动）；`hwinfo_raw` 里能看到 Harmony Swap Pss / Native Heap Pss。
- [ ] **E2 前缀过滤**：只启动 `com.huawei.hmos.vassistant.launcher`（`hdc shell aa start -b com.huawei.hmos.vassistant.launcher -a VoiceAbility`）→ 平台模糊模式采集 `-PKG com.huawei.hmos.vassistant`（主应用未运行）→ 过滤后应**无该包进程数据**（修复前会误抓 launcher）；再启动真实目标应用验证正常采集。
- [ ] **E3 精准模式**：应用未启动时开始精准采集 → 系统指标正常 → 30s 内启动应用 → PID 出现、应用曲线出现 → `hdc shell kill <pid>` 重启应用 → 曲线短暂中断后自动跟随新 PID（worker 日志出现「PID 变更」）。
- [ ] **E4 多包名**：选择两个应用（如 com.huawei.hmos.health + 另一应用）开始采集 → 两应用数据同时入库，曲线两条线。
- [ ] **E5 平台 UI**：设备下拉/弹窗/设备卡片显示 SN；历史卡片显示「采集设备」行；版本选择器 label 带设备后缀；采集间隔选 1 秒时页面仍 5 秒刷新一次；多应用采集时折线图分线显示。
- [ ] **E6 回归**：Windows 设备性能采集一轮（name + pid 模式各一次），确认 `_create_backend` Windows 分支与图表未受影响。

## Self-Review 记录

- 覆盖检查：8 个问题全部映射到任务（见背景速查表）；match_mode 全链路（前端 D6 → 平台 C2 → worker B1/B2 → perfharmony A3）闭环；设备 SN 展示三处（D1/D2/D3）闭环。
- 占位符检查：迁移文件 `down_revision` 需执行 `alembic heads` 取真实值（已写明命令与步骤，属动态值非内容占位）。
- 类型一致性：`filter_processes_by_package`/`heartbeat()`/`match_mode`/`shortSn`/`perProcessSeries` 在定义任务与消费任务的签名一致；`working_set_mb` 字段名不变（语义变更已在接口块声明）。
- 已知风险：①SP_daemon `-stop` 对并发实例的停止范围未验证，B3 联调时若一个实例停止会连坐其它实例，则把子后端停止改为「先排空再统一 -stop」；②element-plus 单选按钮 `label` 写法跨版本兼容已按旧语法处理；③`dict(sample.system)` 对 Rust 对象的行为沿用现有单后端路径，未做改动。

# 鸿蒙 PC 性能采集集成可行性报告

> 日期：2026-07-22  
> 调研范围：  
> - Windows 采集库：`D:\code\perfwin`  
> - 平台：`D:\code\zq-platform`  
> - Worker：`D:\code\autotest`  
> - 鸿蒙 SDK：`D:\code\commandline-tools-windows-x64-6.1.0.850\command-line-tools`（6.1.0.850 / API 23）  
> 结论摘要：**可行，但不可复用 perfwin 内核；推荐在 Worker 侧新增「HDC 采集后端」，复用现有 performance_monitor 任务/上报/展示协议。**

---

## 1. 背景与目标

当前 Windows 性能监控链路已打通：

```
平台前端 → zq-platform performance_monitor API
         → 通知 Windows Worker
         → Worker 调 perfwin (Rust/PyO3)
         → 样本 POST /report 入库
         → 前端曲线 / 版本对比 / 导出
```

业务希望把同样能力扩展到**鸿蒙 PC**（`harmony_pc`），回答三个问题：

1. `perfwin` 能否直接或改造后用于鸿蒙？
2. 鸿蒙 SDK / HDC 能提供哪些对标指标？
3. 与 `zq-platform` 现有性能模块如何集成，工作量与风险如何？

---

## 2. 现状架构拆解

### 2.1 perfwin（Windows 专用采集内核）

| 项 | 结论 |
|----|------|
| 定位 | Rust + PyO3 的 Python 扩展，强化版 psutil |
| 平台 | **仅 Windows**（PDH / HWiNFO / WinAPI 强绑定） |
| 进程级 | CPU、Working Set、Commit、句柄、GPU%（PDH） |
| 系统级 | 温度、功耗、网络等（HWiNFO 共享内存） |
| 接口 | `perfwin.Monitor(interval, duration, process_filter, top_n_*)` + `get_result()` |
| 依赖 | sysinfo crate、Windows PDH、捆绑 HWiNFO64.EXE |
| 与平台关系 | **平台不直接 import**；由 `autotest/worker/performance_monitor.py` 调用 |

核心模块：

```
src/lib.rs              # PyO3 绑定
src/monitor.rs          # 后台采集线程 + 汇总
src/collector/sysinfo.rs
src/collector/pdh.rs
src/collector/hwinfo.rs
src/hwinfo_manager.rs
```

**关键结论：perfwin 源码与 Windows API/HWiNFO 深度耦合，不能移植到鸿蒙 PC，也不应硬改成多平台库。**

### 2.2 Worker 性能模块（autotest）

文件：`worker/performance_monitor.py` + `worker/server.py`

| 能力 | 实现 |
|------|------|
| 启停采集 | `POST /api/worker/{device_id}/collect/start\|stop` |
| 状态查询 | `GET .../collect/status` |
| 进程列表 | `get_processes()` → **硬编码 `import perfwin`** |
| 采集循环 | 线程轮询 `monitor.get_result()` → 组装 v0.3.1 样本 → 上报平台 |
| 容错 | JSONL spool 本地缓存 + 终态 `worker-event` |

当前 **PerformanceCollector 无平台分支**，默认就是 Windows + perfwin。  
鸿蒙侧已有：

- `worker/platforms/harmony.py` / `harmony_hdc.py`：设备操控、截图、shell
- `classify_harmony_device()`：可区分 `mobile` / `pc`
- 设备类型计划：`harmony_mobile` / `harmony_pc`（设备接入已落地，**性能采集为零**）

### 2.3 zq-platform 性能监控

模块：`backend-fastapi/core/performance_monitor/`

| 能力 | 状态 |
|------|------|
| 采集任务表 `performance_collect` | 已有完整状态机 |
| 样本表 `performance_data` | sample_key / sequence / elapsed_ms 幂等协议 |
| Worker 上报 v0.3.1 | `POST /report` |
| Windows 路径 | 通知 Worker 启停 |
| Linux 路径 | **平台内 SSH 直采**（vmstat/free），不经 Worker |
| 版本对比 / 标签 / 导出 | 可复用 |
| 鸿蒙 PC | **未接入**；`start_collect` 非 linux 一律当 Worker 路径 |

设备类型：schema 侧已出现 `harmony_mobile` / `harmony_pc` 等枚举扩展趋势；model 注释仍偏旧。  
前端性能页按「Windows 进程采集」与「Linux 系统级」二分，尚未有鸿蒙 UI 分支。

### 2.4 鸿蒙 SDK（command-line-tools 6.1.0.850）

| 组件 | 版本/位置 |
|------|-----------|
| 包版本 | 6.1.0.850，HarmonyOS 6.1.0 Release，API 23 |
| hdc | `sdk/default/openharmony/toolchains/hdc.exe` |
| 其他 | hvigor / ohpm / hstack / emulator / codelinter（开发构建为主） |

本包 **toolchains 里没有 hiperf / SmartPerf 独立主机侧可执行文件**；性能数据依赖：

1. `hdc shell` 调用设备端工具（hidumper / top / cat /proc 等）
2. 或额外安装/推送 hiperf、Profiler 相关组件（部分版本随系统镜像提供）

本机实测：`hdc list targets` 仅见 UART 假设备 `COM1`，**无可用鸿蒙 PC 真机**，命令级指标验证需后续真机补齐。

---

## 3. 指标对标分析

### 3.1 Windows（perfwin）已采指标

| 类别 | 指标 | 来源 |
|------|------|------|
| 系统 CPU | percent | sysinfo / 计算 |
| 系统 GPU | percent + adapters | PDH，HWiNFO 回退 |
| 进程 CPU / 内存 / 句柄 | % / MB / count | sysinfo |
| 进程 GPU / 显存 | % / MB | PDH |
| 温度 / 功耗 / 网速等 | 多传感器 | HWiNFO raw |
| TopN | CPU/GPU Top10 | 汇总 |
| 时序 | sequence + elapsed_ms | Rust 单调时钟 |

### 3.2 鸿蒙侧可对标能力（基于 HDC + 设备端工具）

| 指标 | 可行性 | 典型采集方式 | 备注 |
|------|--------|--------------|------|
| 系统 CPU | **高** | `hidumper --cpuusage` / 解析 `/proc/stat` 类接口 / `top` | 与 Linux 路径类似 |
| 进程 CPU | **高** | hidumper / `ps` / `top -n 1` 解析 | 需稳定解析格式 |
| 系统内存 | **高** | `hidumper --mem` / `/proc/meminfo` | 需确认 PC 镜像字段 |
| 进程内存 | **高** | hidumper mem / pss | PSS/RSS 语义与 Windows Working Set 不同 |
| 进程列表 | **高** | `ps -ef` / hidumper | Worker 进程选择 UI 依赖 |
| GPU 使用率 | **中** | 部分机型 hidumper GPU 服务 / 厂商节点 | **PC 桌面 GPU 兼容性未知** |
| FPS | **中~低（首期可不做）** | 显示服务 / gfx 相关 dump / 专项 trace | Windows 现状也无业务 FPS 主路径 |
| 温度 / 功耗 | **中** | 热/电量相关 dump，权限可能受限 | 对标 HWiNFO 完整度差距大 |
| 网络速率 | **中** | `/proc/net/dev` 差分 | 需自建采样 |
| 句柄数 | **低/不同语义** | 无 Windows handle 等价 | 可空或映射 fd 数 |

参考公开资料：OpenHarmony/HarmonyOS 的 [hiperf](https://developer.huawei.com/consumer/cn/doc/harmonyos-guides/hiperf)、[hidumper](https://developer.huawei.com/consumer/cn/doc/harmonyos-guides/hidumper) 与社区实践表明，CPU/内存/调用栈类能力较成熟；GPU/FPS/功耗在桌面形态上需真机验证，不能默认与 Windows HWiNFO 对齐。

### 3.3 指标语义差异（必须在产品侧接受）

| 概念 | Windows | 鸿蒙 PC | 影响 |
|------|---------|---------|------|
| 进程内存 | Working Set / Commit | RSS / PSS / VSS | 版本对比不能直接横比绝对值 |
| GPU | PDH 引擎使用率 | 可能缺省或厂商私有 | 曲线可能 N/A |
| 句柄 | Win32 Handle | 无等价 | 前端需隐藏 |
| HWiNFO raw | 数百传感器 | 无 | 用 `system_metrics` / 自定义 raw 键替代 |
| 进程名 | `xxx.exe` | `bundleName` / 可执行名 | 筛选器模型要适配 |

---

## 4. 集成方案对比

### 方案 A：扩展 perfwin 支持鸿蒙（不推荐）

- 优点：单一库  
- 缺点：PDH/HWiNFO/WinAPI 与 HDC 模型完全不兼容；Rust 交叉目标与设备侧 shell 解析混杂；维护成本爆炸  
- **结论：否决**

### 方案 B：平台侧直采（对齐 Linux SSH）（备选）

```
平台 api.start_collect(device_type=harmony_pc)
  → 读设备 HDC 连接信息
  → 后台线程循环 hdc -t <udid> shell ...
  → 直接写 performance_data
```

| 优点 | 缺点 |
|------|------|
| 不改 Worker 采集循环 | 平台需持有 HDC/设备线缆拓扑，部署复杂 |
| 与 Linux 模式类似 | Linux 当前 **缺 sequence/sample_key**，协议分裂会加重 |
| | 进程级筛选、spool、终态对账已在 Worker 更成熟 |

适合：仅系统级粗指标、且采集机与设备同机部署不可行时。

### 方案 C：Worker 侧 HDC 采集后端（推荐）

```
平台 start_collect（harmony_pc 走 Worker 分支，与 Windows 同）
  → Worker PerformanceCollector 按 device_type 分支
       Windows → perfwin.Monitor
       Harmony PC → HarmonyPerfCollector (hdc shell 周期采样)
  → 统一转换成 WorkerReportRequestV3 上报
  → 平台入库 / 前端展示（指标映射按平台裁剪）
```

| 优点 | 缺点 |
|------|------|
| **复用** 状态机、上报幂等、spool、终态、对比导出 | 需新写解析器与稳定性逻辑 |
| 与现有鸿蒙操控同进程，HDC 已封装 | GPU/传感器覆盖弱于 Windows |
| 平台改动最小（分支 + 指标映射 + 前端隐藏项） | 依赖真机验证解析稳定性 |

**推荐方案 C。**

---

## 5. 推荐架构设计（方案 C 细化）

### 5.1 分层

```
┌──────────────── zq-platform ─────────────────┐
│ performance_monitor API / DB / 图表 / 对比   │
│ device_type: windows | linux | harmony_pc …  │
└───────────────┬──────────────────────────────┘
                │ HTTP 启停 + report
┌───────────────▼──────────────────────────────┐
│              autotest Worker                  │
│  PerformanceCollector                         │
│   ├─ backend: PerfwinBackend (现有)           │
│   └─ backend: HarmonyHdcBackend (新增)        │
│        └─ HarmonyHdcClient (已有封装)         │
└───────────────┬──────────────────────────────┘
                │ hdc shell / file
┌───────────────▼──────────────────────────────┐
│           鸿蒙 PC 设备端工具                  │
│  hidumper / top / ps /proc / (hiperf 可选)   │
└──────────────────────────────────────────────┘
```

### 5.2 样本协议兼容策略

继续使用 `WorkerReportRequestV3`：

```json
{
  "collect_id": "...",
  "device_id": "...",
  "samples": [{
    "sample_key": "{collect_id}:{sequence}",
    "sequence": 0,
    "elapsed_ms": 1000,
    "timestamp": "...",
    "system": {
      "cpu_percent": 12.3,
      "gpu_percent": null,
      "gpu_source": "unavailable",
      "memory_used_mb": 8192
    },
    "hwinfo_raw": {
      "Harmony CPU User": {"value": 10.1, "unit": "%"},
      "Harmony Mem Available": {"value": 4096, "unit": "MB"}
    },
    "processes": [...],
    "aggregated": [...],
    "top_n_cpu": [...],
    "top_n_gpu": null
  }]
}
```

约定：

1. **主字段**尽量填：`system.cpu_percent`、进程 `cpu_percent` / `working_set_mb`（语义上映射 RSS/PSS，文档标明）。  
2. 鸿蒙特有指标进 `hwinfo_raw` / `system_metrics`，平台 `metric_mapping` 增加鸿蒙键。  
3. 无 GPU 时 `gpu_percent=null`，前端隐藏 GPU 曲线（参考 Linux）。  
4. **必须写** `sample_key/sequence/elapsed_ms`，避免再走 Linux 协议分叉。

### 5.3 代码改动面

| 仓库 | 文件/模块 | 改动 |
|------|-----------|------|
| autotest | `worker/performance_monitor.py` | 抽象 Backend；Windows/Harmony 分支 |
| autotest | `worker/platforms/harmony_perf.py`（新） | HDC 周期采样 + 解析 |
| autotest | `worker/platforms/harmony_hdc.py` | 补 list_processes / 性能 shell 封装 |
| autotest | `worker/server.py` | 基本可复用路由；进程列表走设备类型 |
| zq-platform | `performance_monitor/api.py` | `harmony_pc` 明确走 Worker；非 linux 勿误伤 |
| zq-platform | metric mapping 脚本 | 鸿蒙指标中文名 |
| zq-platform | 前端 performance-monitor | `isHarmonyPc`：进程选择保留，GPU/句柄可隐藏 |
| perfwin | — | **不改** |

### 5.4 设备连接前提

- 鸿蒙 PC 已通过 HDC 被 Worker 发现（USB/TCP），`device_category=pc` / `device_type=harmony_pc`。  
- 设备开启开发者模式、授权调试。  
- 部分 dump 可能需要 `hdc smode`（root）——需在真机确认；**设计上应优先非 root 可读接口**。

---

## 6. 分期落地建议

### 一期：MVP（系统 + 目标进程 CPU/内存）— **强烈建议先做**

- [ ] Worker：`HarmonyHdcBackend` 采样循环（默认 interval≥1s）  
- [ ] 解析：系统 CPU、系统内存、进程列表、目标进程 CPU/内存  
- [ ] 上报 v0.3.1 协议 + spool/终态复用  
- [ ] 平台：`harmony_pc` 启停走 Worker；metric mapping 基础集  
- [ ] 前端：设备可选 + 隐藏 GPU/句柄（若无数据）  
- [ ] 真机验收：启停、断线、超时、版本对比最小路径  

**预估：约 1.5～2.5 人周**（有真机、解析格式稳定前提下）

### 二期：增强指标

- [ ] TopN CPU  
- [ ] 网络速率  
- [ ] 可选 GPU（真机验证后）  
- [ ] 温度/功耗（权限允许时）  
- [ ] 更稳的进程名/包名筛选  

**预估：约 1～2 人周**

### 三期：专项（可选）

- [ ] FPS / 帧时间（若产品强需求）  
- [ ] hiperf 短时剖析文件上传（偏诊断，非长时监控）  
- [ ] 与自动化任务联动（跑用例同时挂采集）  

**预估：视需求 2+ 人周**

---

## 7. 风险与缓解

| 风险 | 等级 | 缓解 |
|------|------|------|
| 无真机，解析格式未验证 | **高** | 一期以接口契约 + mock 输出推进；真机到后优先冻结「命令→字段」表 |
| 不同鸿蒙 PC 镜像 hidumper 输出差异 | **高** | 多策略解析；版本指纹；失败降级仅系统级 |
| GPU/FPS 不可用导致预期落差 | **中** | 产品文档明确一期范围；UI 按可用性展示 |
| 高频 shell 采样影响被测性能 | **中** | interval 默认 2～5s；避免同时开多 dump |
| root/权限限制 | **中** | 优先非 root；记录失败码到 `failure_message` |
| 与 Windows 指标横比误用 | **中** | 版本对比按 device_type 隔离；报告注明语义 |
| Linux 协议分裂历史包袱 | **低** | 鸿蒙强制走完整 sample_key 协议，不复制 Linux 缺字段路径 |
| 误把 Windows Worker 调到鸿蒙设备 | **中** | start 时校验 device_type 与 backend 匹配 |

---

## 8. 可行性结论

| 问题 | 结论 |
|------|------|
| 能否做鸿蒙 PC 性能采集？ | **能** |
| 能否复用 perfwin？ | **不能**（内核层）；**可复用任务/上报/展示层** |
| 能否复用 zq-platform？ | **能，改动小**（分支 + 映射 + 前端裁剪） |
| 能否复用 autotest Worker？ | **能，核心改造点** |
| 鸿蒙 SDK 是否够用？ | **hdc + 设备端 dump 可支撑 MVP**；本包无独立 SmartPerf 主机工具 |
| 与 Windows 指标对齐度？ | **CPU/内存进程级可达 70%+ 业务可用**；GPU/传感器/句柄差距大 |
| 总评 | **技术可行，产品可行；一期 MVP 风险可控，关键路径依赖真机命令输出冻结** |

### 总体建议

1. **不要改 perfwin**，保持 Windows 专精。  
2. **在 Worker 增加 Harmony 采集后端**，统一上报协议。  
3. **平台只做薄适配**，避免第三套 Linux 式直采。  
4. **一期砍范围**：系统/进程 CPU+内存 + 进程选择 + 曲线与历史；GPU/FPS/功耗后置。  
5. **拿到鸿蒙 PC 真机后**先做 1 天「命令输出冻结」再写解析器，避免返工。

---

## 9. 真机验收清单（阶段 0）

在编码前建议在目标鸿蒙 PC 上执行并归档输出样例：

```text
hdc -t <udid> shell "hidumper -h"
hdc -t <udid> shell "hidumper --cpuusage"
hdc -t <udid> shell "hidumper --mem"
hdc -t <udid> shell "ps -ef"
hdc -t <udid> shell "cat /proc/meminfo"
hdc -t <udid> shell "cat /proc/stat"
hdc -t <udid> shell "which hiperf; hiperf --help"
hdc -t <udid> shell "param get const.product.type"
hdc -t <udid> shell "param get const.product.device_type"
```

输出归档路径建议：`docs/superpowers/specs/harmony-pc-perf-command-samples/`。

---

## 10. 关键路径索引

### perfwin
- `D:\code\perfwin\CLAUDE.md`
- `D:\code\perfwin\docs\2026-04-29-perfdog-design.md`
- `D:\code\perfwin\src\{lib,monitor,data}.rs`
- `D:\code\perfwin\src\collector\{sysinfo,pdh,hwinfo}.rs`

### Worker
- `D:\code\autotest\worker\performance_monitor.py`
- `D:\code\autotest\worker\server.py`
- `D:\code\autotest\worker\platforms\harmony_hdc.py`
- `D:\code\autotest\docs\superpowers\plans\2026-07-11-harmony-pc-mobile-platform-integration-plan.md`

### 平台
- `D:\code\zq-platform\backend-fastapi\core\performance_monitor\{api,service,model,schema,linux_collector,utils}.py`
- `D:\code\zq-platform\web\apps\web-ele\src\views\performance-monitor\`

### SDK
- `D:\code\commandline-tools-windows-x64-6.1.0.850\command-line-tools\version.txt`
- `...\sdk\default\openharmony\toolchains\hdc.exe`

---

## 11. 下一步（可选）

若本报告结论认可，建议顺序：

1. 确认一期指标清单（产品签字）  
2. 真机命令输出冻结  
3. 出详细设计 + 任务拆分（Worker Backend / 平台 / 前端）  
4. 按 TDD 实现 MVP → 联调 → 再开二期  

如需，可在本报告基础上直接输出《鸿蒙 PC 性能采集详细设计》与实施 plan。

# Windows 录制水印与 PTS 统一为抓帧时刻设计

**日期**: 2026-07-26  
**状态**: 待审  
**范围**: `windows-screen-sidecar` 录制链路（不含 H.264 推流、不含 Python API 变更）

## 1. 背景与问题

### 1.1 现状

Windows 桌面录制水印与视频 PTS 使用**逻辑时间**：

```text
recording_start_time = GetLocalTime()   // recorder.start() 时拍一次
watermark            = start + frame_index / fps
sample PTS           = frame_index / fps
```

抓屏路径其实已经产生了帧时间戳：

```text
capture → captured_at_ms
       → FrameHub.capture_pts_100ns
```

但 `RecordingWorker` 调用 `write_frame` 时只传 `bgra`，时间戳在消费端被丢弃。

### 1.2 问题

| 问题 | 影响 |
|------|------|
| 水印不是“这帧画面出现时刻” | 会议双端录屏、PPT 翻页时差测量偏大 |
| 启动空窗固化进起点 | 首帧水印相对墙钟固定偏移 |
| 水印 / 抓帧 / 逻辑 PTS 语义分裂 | 后续改一半极易“时间对不齐” |
| duplicate tick 时水印仍推进 | 画面不动、时间空走 |

### 1.3 使用约束（产品侧）

- 用途：会议软件双端录屏，OCR 水印计算 PPT 翻页时间
- 两端机器时钟可视为已同步
- **最高录制帧率预期约 30fps**
- **偏差“不太大即可”**：以约 1 个帧间隔为可接受量级（30fps ≈ 33ms，20fps ≈ 50ms），不追求个位数毫秒极端精度

## 2. 目标与非目标

### 2.1 目标

| ID | 目标 | 验收 |
|----|------|------|
| G1 | 水印贴近该帧抓取墙钟 | 水印 = `format_local(capture_pts)` |
| G2 | 水印与内容时间同源 | 同帧水印与 PTS 均由 `capture_pts` 派生 |
| G3 | 不引入额外内存问题 | 不新增帧队列；不拷贝 BGRA 只为带时间戳 |
| G4 | 不引入时间轴错乱 | 禁止双时钟；PTS 单调；duplicate 语义写死 |
| G5 | ≤30fps 行为可预期 | 文档写明采样下限与双端时差量级 |

### 2.2 非目标

- 不改 Python / HTTP `start_recording` 协议
- 不改 H.264 推流路径时间语义（本期隔离）
- 不引入 `chrono` 等新依赖
- 不做跨机器 NTP
- 不强制默认升到 60fps
- 不承诺双端时差稳定 <10ms

### 2.3 30fps 下的预期量级

| 指标 | 目标（“不太大即可”） |
|------|----------------------|
| 水印 vs 该帧抓取时刻 | 约 1–5ms（算法贴帧） |
| 单端事件定位 | ≤ 1 帧（30fps ≈ 33ms） |
| 两端翻页时差（时钟已同步） | 典型约 1 帧量级；最坏约 2 帧 |
| 录制内存 | 同分辨率同时长，Private Bytes 与改前同量级 |

## 3. 统一时间模型

### 3.1 唯一权威源

```text
capture_pts_100ns
  = 该帧 BitBlt 成功后立刻读取的系统墙钟
  = epoch 起的 100ns 计数（i64）
```

**之后任何环节不得用 `now()` 覆盖该帧时间。**

派生关系（只允许这一棵树）：

```text
capture_pts_100ns
  ├─ watermark_str = local_hms_ms(capture_pts_100ns)
  └─ sample_pts    = f(capture_pts_100ns, anchor, duplicated, fps)
```

### 3.2 录制锚点

- `anchor_pts_100ns` = **本段录制成功写入的第一帧** 的 `capture_pts_100ns`
- **不是** `recorder.start()` 时刻（避免启动空窗抬偏整段时间轴）
- 相对 PTS：`pts = capture_pts - anchor`（再经单调钳制）

### 3.3 与旧模型对比

| 项目 | 旧 | 新 |
|------|----|----|
| 水印 | `start_local + frame_index/fps` | `local(capture_pts)` |
| PTS | `frame_index/fps` | `capture 相对首帧锚点`（duplicate 见下） |
| 权威源 | 逻辑帧号 | `capture_pts_100ns` |

### 3.4 Duplicate 语义（写死）

RecordingWorker 固定 tick 上可能复用上一帧像素：

| 字段 | 真实新帧 | duplicate |
|------|----------|-----------|
| 像素 | 新 bgra | 复用上一帧 |
| 水印 / 内容时间 | 新 capture_pts | **冻结为原 capture_pts** |
| 容器 PTS | `max(capture-anchor, last+1)` | **`last_pts + 1/fps`** |
| duration | 固定 `1/fps` | 固定 `1/fps` |

说明：

- **内容时间**（给 OCR / 翻页测量）：跟画面，duplicate 不空走
- **容器时间**（给播放器 / 文件时长）：匀速前进，避免同 PTS 多帧或时长变短

这是有意的语义分裂，必须在注释与文档中写清；禁止实现成“两端都冻结”或“两端都用 now()”。

## 4. 架构与数据流

```text
CaptureProducer
  BitBlt OK → now_as_pts_100ns() → publish_raw(pts, w, h, bgra)

FrameHub
  latest-only；CapturedFrame { seq, capture_pts_100ns, width, height, bgra }

RecordingWorker
  tick → latest
  duplicated = (seq 未前进)
  sink.write_frame(WriteFrame { bgra: &[], capture_pts_100ns, duplicated })

WinRecorder
  watermark ← local(capture_pts)          // 永不在此 GetLocalTime 用于水印
  pts/dur  ← 状态机（§5）
  staging → GDI 水印 → MF sample → write_sample(pts, dur)
```

### 4.1 内存硬约束

| 约束 | 原因 |
|------|------|
| 时间戳只传 `i64`，`bgra` 只借用 | 避免每帧分辨率级拷贝 |
| FrameHub 保持 latest-only | 禁止为对齐时间建历史队列 |
| Worker 仅 `last_frame: Option<Arc<CapturedFrame>>` 单槽 | 与现状一致 |
| 水印 GDI 对象继续缓存 | 禁止每帧 CreateFont/DIB |
| 不缓存每帧水印文案位图 | 避免随时长线性涨内存 |
| 不在 finalize 路径新增常驻缓冲 | 保持现有 MF 生命周期 |

### 4.2 时间对齐硬约束

| 约束 | 原因 |
|------|------|
| 禁止 `write_frame` 内 `now()` 画水印 | 会变成编码时刻 |
| 禁止“只改水印、PTS 仍 frame_index” | 双时钟，历史多次踩坑 |
| PTS 必须单调非降 | 防 MF 写坏 / 播放异常 |
| 时钟回拨：`pts = last_pts + 1` | 钳制，不中断录制 |
| 推流路径本期不动 | 降低回归面 |

## 5. PTS 状态机

状态（均为标量，无堆分配）：

```text
anchor_pts_100ns: Option<i64>
last_pts_100ns: i64
frame_duration_100ns = 10_000_000 / fps
```

每一写入帧：

```text
if anchor is None:
    anchor = capture_pts
    pts = 0
else if not duplicated:
    raw = capture_pts - anchor
    pts = if raw <= last_pts { last_pts + 1 } else { raw }
else:
    pts = last_pts + frame_duration

duration = frame_duration
last_pts = pts
write_sample(sample, pts, duration)
watermark 始终用输入的 capture_pts（duplicate 时为旧值）
```

**duration 固定为 `1/fps`**：播放节奏平稳；抓帧抖动只体现在水印数字，不体现在播放忽快忽慢。

## 6. 接口变更

### 6.1 FrameSink

```rust
pub struct WriteFrame<'a> {
    pub bgra: &'a [u8],
    pub capture_pts_100ns: i64,
    pub duplicated: bool,
}

fn write_frame(&mut self, frame: WriteFrame<'_>) -> Result<(), String>;
```

### 6.2 WinRecorder / RecordingContext

```rust
pub fn write_frame(
    &mut self,
    bgra: &[u8],
    capture_pts_100ns: i64,
    duplicated: bool,
) -> Result<(), RecorderError>;
```

删除录制路径对 `recording_start_time + logical_time_for_frame_index` 的依赖。

### 6.3 MFSinkWriter

```rust
pub fn write_sample(
    &mut self,
    sample: &IMFSample,
    timestamp_100ns: i64,
    duration_100ns: i64,
) -> Result<(), RecorderError>;
```

`frame_count` 仅统计，不再参与时间计算。

### 6.4 时间工具

```rust
pub fn now_as_pts_100ns() -> i64;
pub fn local_hms_ms_from_pts_100ns(pts_100ns: i64) -> String; // "HH:MM:SS.mmm"
```

实现：Win32 文件时间 / 本地 `SYSTEMTIME`，与现有本地显示一致；不引入 chrono。

`logical_time_for_frame_index`：录制水印路径停用；可保留供测试或其它工具，文档标明非录制权威源。

## 7. 模块改动清单

| 模块 | 改动 |
|------|------|
| `capture.rs` | BitBlt 成功后取墙钟；统一为 pts_100ns 或保持 ms 再换算 |
| `capture_producer.rs` | 已 publish pts，基本不动 |
| `media/types.rs` | 已有 `capture_pts_100ns`，确认文档注释 |
| `recording_worker.rs` | 传 `WriteFrame` |
| `win_recorder/mod.rs` | FrameSink 适配 |
| `recorder.rs` | 锚点状态机 + 水印改源 |
| `mf_writer.rs` | 外部传入 pts/duration |
| `logical_time.rs`（或邻接模块） | 本地格式化 / now_as_pts |
| `watermark.rs` | 不改（仍收 `&str`） |
| `stream_worker.rs` | 本期不改 |
| Python / API | 不改 |

## 8. 边界情况

| 场景 | 行为 |
|------|------|
| 首帧 | anchor=capture，pts=0，水印=该帧本地时间 |
| 正常新帧 | pts 单调；水印=新 capture |
| duplicate | 水印冻结；pts += 1/fps |
| 抓帧 < 录制 fps | duplicate 增多；内容时间阶梯停留 |
| 抓帧 > 录制 fps | 只取 latest，中间帧丢弃；不建队列 |
| 时钟前跳 | pts 可跳；duration 仍 1/fps |
| 时钟回拨 | pts=last+1 钳制；水印显示回拨后本地时间 |
| watermark=false | 不绘制；PTS 仍走 capture 锚点 |
| stop | 清空 anchor/last_pts；不留帧缓冲 |

## 9. 可观测性与错误处理

- 时钟回拨钳制：非推流路径打一条 diag（避免刷屏，可限频）
- 保留 / 复用 `log_process_memory` 在 start/finalize 采样，便于对比改前改后
- 水印 GDI 失败：与现状一致，警告并跳过水印，**不中断录制**
- `write_sample` 失败：向上返回错误，由 worker 终止本段录制（与现状错误传播一致）

## 10. 测试计划

### 10.1 单元测试

| 用例 | 断言 |
|------|------|
| `local_hms_ms_from_pts` 已知输入 | 格式与进位正确 |
| 首帧 pts=0、anchor 固定 | 状态正确 |
| 新帧单调 | pts2 > pts1 |
| 回拨钳制 | pts = last+1 |
| duplicate | 水印用同一 capture；容器 pts += duration |
| worker → sink | 收到的 capture_pts 与 publish 一致 |
| 静态约束 | 录制热路径无对 bgra 的 `.to_vec()` 仅为传 pts |

### 10.2 实机冒烟（≤30fps）

1. 20 或 30fps 录 30–60s，桌面显示系统时钟
2. 抽帧对比水印与画面时钟：偏差约 ≤1 帧即可
3. 观察 sidecar Private Bytes：与改前同量级
4. 播放 MP4：可正常播，时长约 = 帧数/fps

## 11. 发布与回滚

- **发布**：随 sidecar 二进制更新；Python 无协议变更
- **行为变更**：所有 `watermark=true` 的录制，水印从逻辑时钟改为抓帧墙钟（预期内产品修正）
- **回滚**：回退 sidecar 二进制即可恢复旧逻辑时间行为
- **可选开关**（非必须）：内部 `watermark_time_mode=capture|logical` 仅作紧急回退；默认 capture。若实现成本低可加，否则以 git 回退为准

## 12. 工作量

约 **2 人日**（接口贯通 + PTS 状态机 + Win32 本地格式化 + 单测 + 实机内存/时间回归）。

## 13. 决策记录

1. 完整方案：水印 + PTS 同源，不做“只改水印”
2. 权威源：`capture_pts_100ns`
3. duplicate：内容时间冻结，容器 PTS 匀速
4. duration 固定 1/fps
5. 推流 / Python 本期不动
6. 产品预期：最高约 30fps，偏差约 1 帧量级可接受

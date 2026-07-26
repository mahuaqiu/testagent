# Windows 录制 capture 时刻水印/PTS Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Windows 录制水印与 sample PTS 统一为同源 `capture_pts_100ns`（抓帧墙钟），去掉逻辑时钟水印；无兼容开关。

**Architecture:** Capture 在 BitBlt 后写入 `capture_pts_100ns` → FrameHub 已携带 → RecordingWorker 经 `WriteFrame` 把 `bgra` 借用 + pts + duplicated 交给 sink → WinRecorder 用水印=local(pts)、PTS=相对首帧锚点状态机写入 MF。推流路径与 Python API 不动。

**Tech Stack:** Rust、windows-rs 0.58（Win32 文件时间/本地 SYSTEMTIME）、既有 D3D11 + Media Foundation SinkWriter、cargo test。

**Spec:** `docs/superpowers/specs/2026-07-26-windows-recording-capture-timestamp-design.md`

## Global Constraints

- 唯一权威源：`capture_pts_100ns`（BitBlt 成功后取墙钟，单位 100ns）
- 禁止在 `write_frame` 内用 `GetLocalTime`/`SystemTime::now()` 画水印
- 禁止只改水印、PTS 仍 `frame_index/fps`
- 禁止为传时间戳 `bgra.to_vec()` 或新建帧历史队列
- duration 固定 `10_000_000 / fps`；duplicate：水印冻结、容器 PTS `+= duration`
- 无兼容开关、无 logical 回退模式
- 不改 `stream_worker` / Python API
- 不引入 `chrono`
- 产品预期最高约 30fps，偏差约 1 帧可接受

## File map

| 文件 | 职责 |
|------|------|
| `rust/windows-screen-sidecar/src/win_recorder/logical_time.rs` | pts 工具：`now_as_pts_100ns`、`local_hms_ms_from_pts_100ns`、`frame_duration_100ns`、`next_sample_timing` 状态机纯函数 |
| `rust/windows-screen-sidecar/src/win_recorder/mf_writer.rs` | `write_sample(sample, timestamp, duration)` 外部传入时间 |
| `rust/windows-screen-sidecar/src/win_recorder/recorder.rs` | 锚点状态 + capture 水印；删除 `recording_start_time` |
| `rust/windows-screen-sidecar/src/win_recorder/mod.rs` | `RecordingContext` / `FrameSink` 适配 `WriteFrame` |
| `rust/windows-screen-sidecar/src/media/recording_worker.rs` | `WriteFrame` + 传 `capture_pts`/`duplicated` |
| `rust/windows-screen-sidecar/src/media/mod.rs` | 如需 re-export `WriteFrame` |
| `rust/windows-screen-sidecar/src/capture.rs` | 确保抓帧时间在 BitBlt 后取得（可保持 ms 再换算） |

---

### Task 1: 时间工具纯函数（TDD）

**Files:**
- Modify: `rust/windows-screen-sidecar/src/win_recorder/logical_time.rs`
- Modify: `rust/windows-screen-sidecar/src/win_recorder/mod.rs`（export 新 API）

**Interfaces:**
- Produces:
  - `pub fn frame_duration_100ns(fps: u32) -> i64`
  - `pub fn now_as_pts_100ns() -> i64`
  - `pub fn local_hms_ms_from_pts_100ns(pts_100ns: i64) -> String`
  - `pub struct SampleTimingState { pub anchor_pts_100ns: Option<i64>, pub last_pts_100ns: i64 }`
  - `pub fn next_sample_timing(state: &mut SampleTimingState, capture_pts_100ns: i64, duplicated: bool, fps: u32) -> (i64 /*pts*/, i64 /*duration*/)`
- 保留：`frame_index_to_pts_100ns`、`sample_timing_for_frame_index`（测试/工具可继续用，录制路径不再调用）
- 删除录制依赖：`logical_time_for_frame_index` 可保留但录制不再 import

- [ ] **Step 1: 写失败单测（状态机 + duration）**

在 `logical_time.rs` 的 `#[cfg(test)] mod tests` 追加：

```rust
#[test]
fn frame_duration_matches_fps() {
    assert_eq!(frame_duration_100ns(10), 1_000_000);
    assert_eq!(frame_duration_100ns(30), 333_333); // 10_000_000/30 截断
    assert_eq!(frame_duration_100ns(0), 10_000_000); // fps.max(1)
}

#[test]
fn next_sample_timing_first_frame_is_zero() {
    let mut state = SampleTimingState::default();
    let (pts, dur) = next_sample_timing(&mut state, 1_000_000_000, false, 30);
    assert_eq!(pts, 0);
    assert_eq!(dur, frame_duration_100ns(30));
    assert_eq!(state.anchor_pts_100ns, Some(1_000_000_000));
    assert_eq!(state.last_pts_100ns, 0);
}

#[test]
fn next_sample_timing_new_frame_uses_capture_delta() {
    let mut state = SampleTimingState::default();
    let _ = next_sample_timing(&mut state, 1_000_000_000, false, 10);
    let (pts, dur) = next_sample_timing(&mut state, 1_000_000_000 + 2_500_000, false, 10);
    assert_eq!(pts, 2_500_000);
    assert_eq!(dur, frame_duration_100ns(10));
    assert_eq!(state.last_pts_100ns, 2_500_000);
}

#[test]
fn next_sample_timing_duplicate_advances_by_frame_duration() {
    let mut state = SampleTimingState::default();
    let _ = next_sample_timing(&mut state, 5_000, false, 10);
    let (pts, _) = next_sample_timing(&mut state, 5_000, true, 10);
    assert_eq!(pts, frame_duration_100ns(10));
    let (pts2, _) = next_sample_timing(&mut state, 5_000, true, 10);
    assert_eq!(pts2, 2 * frame_duration_100ns(10));
}

#[test]
fn next_sample_timing_clamps_non_monotonic_capture() {
    let mut state = SampleTimingState::default();
    let _ = next_sample_timing(&mut state, 1_000_000_000, false, 10);
    let _ = next_sample_timing(&mut state, 1_000_000_000 + 5_000_000, false, 10);
    // 回拨
    let (pts, _) = next_sample_timing(&mut state, 1_000_000_000 + 1_000_000, false, 10);
    assert_eq!(pts, 5_000_000 + 1);
}
```

- [ ] **Step 2: 运行确认失败**

```bash
cd rust/windows-screen-sidecar
cargo test next_sample_timing -- --nocapture
```

Expected: 编译失败（符号不存在）或 FAIL。

- [ ] **Step 3: 实现纯函数**

在 `logical_time.rs` 增加（Windows 本地格式化可用 Win32；`now_as_pts` 用 `GetSystemTimeAsFileTime` 转 100ns epoch。FILETIME 是 1601 epoch，需减 `116444736000000000` 才是 Unix epoch 100ns，或统一全链路用 FILETIME 基准——**选定 Unix epoch 100ns，与现有 `captured_at_ms * 10_000` 一致**）：

```rust
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct SampleTimingState {
    pub anchor_pts_100ns: Option<i64>,
    pub last_pts_100ns: i64,
}

pub fn frame_duration_100ns(fps: u32) -> i64 {
    let fps = fps.max(1) as i64;
    10_000_000 / fps
}

pub fn next_sample_timing(
    state: &mut SampleTimingState,
    capture_pts_100ns: i64,
    duplicated: bool,
    fps: u32,
) -> (i64, i64) {
    let duration = frame_duration_100ns(fps);
    let pts = if state.anchor_pts_100ns.is_none() {
        state.anchor_pts_100ns = Some(capture_pts_100ns);
        0
    } else if duplicated {
        state.last_pts_100ns.saturating_add(duration)
    } else {
        let anchor = state.anchor_pts_100ns.unwrap();
        let raw = capture_pts_100ns.saturating_sub(anchor);
        if raw <= state.last_pts_100ns {
            state.last_pts_100ns.saturating_add(1)
        } else {
            raw
        }
    };
    state.last_pts_100ns = pts;
    (pts, duration)
}
```

`now_as_pts_100ns`：

```rust
pub fn now_as_pts_100ns() -> i64 {
    // 与 capture::current_timestamp_ms 一致：Unix epoch 毫秒 * 10_000
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| (d.as_millis() as i64).saturating_mul(10_000))
        .unwrap_or(0)
}
```

`local_hms_ms_from_pts_100ns`（Windows）：

```rust
#[cfg(windows)]
pub fn local_hms_ms_from_pts_100ns(pts_100ns: i64) -> String {
    use windows::Win32::Foundation::FILETIME;
    use windows::Win32::System::SystemInformation::FileTimeToSystemTime;
    use windows::Win32::System::Time::FileTimeToLocalFileTime;
    // Unix epoch 100ns → FILETIME（1601）
    // pts 为 Unix epoch 100ns；FILETIME 为 1601-01-01 起 100ns
    const UNIX_EPOCH_AS_FILETIME: i64 = 116444736000000000;
    let ft_val = pts_100ns.saturating_add(UNIX_EPOCH_AS_FILETIME);
    let ft = FILETIME {
        dwLowDateTime: ft_val as u32,
        dwHighDateTime: (ft_val >> 32) as u32,
    };
    unsafe {
        let mut local = FILETIME::default();
        if FileTimeToLocalFileTime(&ft, &mut local).is_err() {
            return "00:00:00.000".to_string();
        }
        let mut st = windows::Win32::Foundation::SYSTEMTIME::default();
        if FileTimeToSystemTime(&local, &mut st).is_err() {
            return "00:00:00.000".to_string();
        }
        format!(
            "{:02}:{:02}:{:02}.{:03}",
            st.wHour, st.wMinute, st.wSecond, st.wMilliseconds
        )
    }
}
```

实现时核对 `windows` crate 中 `FileTimeToLocalFileTime` 所在 feature/module（可能是 `Win32_System_SystemInformation` 或 `Win32_System_Time`）；若 API 路径不同，以能编译为准，逻辑不变。

单测 `local_hms_ms`：可用**固定 pts** 与本机时区相关，**不要写死时区**。改为：

```rust
#[test]
fn local_hms_ms_roundtrip_shape() {
    let pts = now_as_pts_100ns();
    let s = local_hms_ms_from_pts_100ns(pts);
    // HH:MM:SS.mmm
    assert_eq!(s.len(), 12);
    assert_eq!(&s[2..3], ":");
    assert_eq!(&s[5..6], ":");
    assert_eq!(&s[8..9], ".");
}
```

- [ ] **Step 4: 跑通单测**

```bash
cd rust/windows-screen-sidecar
cargo test --lib logical_time -- --nocapture
```

Expected: PASS（若模块路径是 bin 内，用 `cargo test logical_time`）。

- [ ] **Step 5: Commit**

```bash
git add rust/windows-screen-sidecar/src/win_recorder/logical_time.rs rust/windows-screen-sidecar/src/win_recorder/mod.rs rust/windows-screen-sidecar/Cargo.toml
git commit -m "feat(sidecar): capture pts timing helpers and sample state machine"
```

---

### Task 2: MFSinkWriter 接收外部 PTS

**Files:**
- Modify: `rust/windows-screen-sidecar/src/win_recorder/mf_writer.rs`
- Modify: 所有 `write_sample` 调用点（本 Task 后由 Task 3 接上；本 Task 改签名并临时让 recorder 编译）

**Interfaces:**
- Consumes: 无
- Produces: `MFSinkWriter::write_sample(&mut self, sample: &IMFSample, timestamp_100ns: i64, duration_100ns: i64) -> Result<(), RecorderError>`
- `frame_count` 仍自增，仅统计

- [ ] **Step 1: 改 `write_sample` 签名与实现**

替换：

```rust
pub fn write_sample(
    &mut self,
    sample: &IMFSample,
    timestamp_100ns: i64,
    duration_100ns: i64,
) -> Result<(), RecorderError> {
    let duration = duration_100ns.max(1);
    unsafe {
        sample
            .SetSampleTime(timestamp_100ns)
            .map_err(|e| RecorderError::MFError(format!("设置样本时间失败: {}", e)))?;
        sample
            .SetSampleDuration(duration)
            .map_err(|e| RecorderError::MFError(format!("设置样本持续时间失败: {}", e)))?;
        self.sink_writer
            .WriteSample(self.stream_index, sample)
            .map_err(|e| RecorderError::MFError(format!("写入样本失败: {}", e)))?;
        self.frame_count += 1;
    }
    Ok(())
}
```

删除对 `sample_timing_for_frame_index` 的 import（若不再使用）。

- [ ] **Step 2: 临时修复 `recorder.rs` 调用以通过编译**

在 Task 3 完成前，可先：

```rust
let (ts, dur) = crate::win_recorder::logical_time::sample_timing_for_frame_index(
    writer.frame_count(),
    self.fps,
);
writer.write_sample(&sample, ts, dur)?;
```

（下一 Task 会换成状态机；此步仅保证编译。）

- [ ] **Step 3: 编译**

```bash
cd rust/windows-screen-sidecar
cargo test --no-run
```

Expected: 成功编译。

- [ ] **Step 4: Commit**

```bash
git add rust/windows-screen-sidecar/src/win_recorder/mf_writer.rs rust/windows-screen-sidecar/src/win_recorder/recorder.rs
git commit -m "feat(sidecar): MFSinkWriter accepts external sample timing"
```

---

### Task 3: WinRecorder 改用 capture 水印 + 锚点 PTS

**Files:**
- Modify: `rust/windows-screen-sidecar/src/win_recorder/recorder.rs`
- Modify: `rust/windows-screen-sidecar/src/win_recorder/mod.rs`

**Interfaces:**
- Consumes: `next_sample_timing`, `local_hms_ms_from_pts_100ns`, `SampleTimingState`
- Produces:
  - `WinRecorder::write_frame(&mut self, frame_data: &[u8], capture_pts_100ns: i64, duplicated: bool) -> Result<(), RecorderError>`
  - `RecordingContext::write_frame(&mut self, bgra_data: &[u8], capture_pts_100ns: i64, duplicated: bool) -> Result<(), RecorderError>`

- [ ] **Step 1: 改结构体字段**

删除：

```rust
recording_start_time: Option<ClockTime>,
```

以及 `current_local_clock_time`、`GetLocalTime` import、`logical_time_for_frame_index` import。

增加：

```rust
timing: SampleTimingState,
```

`new`/`start`/`stop`：
- `start`：不再拍 `recording_start_time`；可 `self.timing = SampleTimingState::default();`
- `stop`：`self.timing = SampleTimingState::default();`

- [ ] **Step 2: 改 `write_frame`**

```rust
pub fn write_frame(
    &mut self,
    frame_data: &[u8],
    capture_pts_100ns: i64,
    duplicated: bool,
) -> Result<(), RecorderError> {
    if !self.recording {
        return Err(RecorderError::NotRecording);
    }
    let texture_manager = self.texture_manager.as_ref().ok_or(RecorderError::NotRecording)?;
    let sink_writer = self.sink_writer.as_ref().ok_or(RecorderError::NotRecording)?;

    texture_manager.upload_bgra_to_staging(frame_data)?;

    if self.watermark {
        if let Some(renderer) = &mut self.watermark_renderer {
            let time_str = local_hms_ms_from_pts_100ns(capture_pts_100ns);
            if let Err(e) = renderer.render(
                texture_manager.context(),
                texture_manager.staging_texture(),
                self.width,
                self.height,
                &time_str,
            ) {
                eprintln!("Warning: watermark render failed: {}", e);
            }
        }
    }

    let sample = texture_manager.create_sample_from_staging()?;
    let (timestamp, duration) =
        next_sample_timing(&mut self.timing, capture_pts_100ns, duplicated, self.fps);
    let mut writer = sink_writer.lock();
    writer.write_sample(&sample, timestamp, duration)?;
    Ok(())
}
```

- [ ] **Step 3: 更新 `RecordingContext`**

```rust
pub fn write_frame(
    &mut self,
    bgra_data: &[u8],
    capture_pts_100ns: i64,
    duplicated: bool,
) -> Result<(), RecorderError> {
    if let Some(ref mut recorder) = self.recorder {
        recorder.write_frame(bgra_data, capture_pts_100ns, duplicated)
    } else {
        Err(RecorderError::NotRecording)
    }
}
```

`FrameSink` impl 放到 Task 4 与 `WriteFrame` 一起改。

- [ ] **Step 4: 编译（允许 FrameSink 暂未对齐时报错——若报错则与 Task 4 合并提交）**

```bash
cd rust/windows-screen-sidecar
cargo test --no-run
```

- [ ] **Step 5: Commit**（若 Task 4 未合并）

```bash
git add rust/windows-screen-sidecar/src/win_recorder/recorder.rs rust/windows-screen-sidecar/src/win_recorder/mod.rs
git commit -m "feat(sidecar): recorder watermark and PTS from capture_pts"
```

---

### Task 4: RecordingWorker + FrameSink 贯通

**Files:**
- Modify: `rust/windows-screen-sidecar/src/media/recording_worker.rs`
- Modify: `rust/windows-screen-sidecar/src/media/mod.rs`（export `WriteFrame`）
- Modify: `rust/windows-screen-sidecar/src/win_recorder/mod.rs`（`impl FrameSink`）

**Interfaces:**
- Produces:

```rust
pub struct WriteFrame<'a> {
    pub bgra: &'a [u8],
    pub capture_pts_100ns: i64,
    pub duplicated: bool,
}

pub trait FrameSink: Send + 'static {
    fn write_frame(&mut self, frame: WriteFrame<'_>) -> Result<(), String>;
    fn stop(&mut self) -> Result<(), String>;
}
```

- [ ] **Step 1: 改 trait 与 worker 调用**

```rust
// recording_worker 热路径
sink.write_frame(WriteFrame {
    bgra: &frame.bgra,
    capture_pts_100ns: frame.capture_pts_100ns,
    duplicated,
})?;
```

**禁止** `frame.bgra.clone()` / `to_vec()`。

- [ ] **Step 2: 更新测试 `TestSink`**

```rust
struct TestSink {
    writes: Arc<Mutex<Vec<(Vec<u8>, i64, bool)>>>,
}

impl FrameSink for TestSink {
    fn write_frame(&mut self, frame: WriteFrame<'_>) -> Result<(), String> {
        self.writes.lock().unwrap().push((
            frame.bgra.to_vec(), // 仅测试收集允许
            frame.capture_pts_100ns,
            frame.duplicated,
        ));
        Ok(())
    }
    fn stop(&mut self) -> Result<(), String> { Ok(()) }
}
```

扩展现有测试或新增：

```rust
#[test]
fn worker_forwards_capture_pts_and_duplicated_flag() {
    let hub = Arc::new(FrameHub::new());
    let writes = Arc::new(Mutex::new(Vec::new()));
    let sink = TestSink { writes: writes.clone() };
    let worker = RecordingWorkerHandle::start(hub.clone(), 50, Box::new(sink)).unwrap();

    hub.publish(CapturedFrame {
        seq: 0,
        capture_pts_100ns: 42_0000,
        width: 1,
        height: 1,
        bgra: vec![1; 4],
    });
    std::thread::sleep(Duration::from_millis(50));
    let _ = worker.stop();

    let w = writes.lock().unwrap();
    assert!(!w.is_empty());
    assert!(w.iter().any(|(_, pts, _)| *pts == 42_0000));
    // 高 fps 下应出现 duplicated=true 的写入
    assert!(w.iter().any(|(_, _, dup)| *dup));
}
```

（`publish` 会重写 seq；pts 应原样保留——确认 `FrameHub::publish` 不改 `capture_pts_100ns`。）

- [ ] **Step 3: `impl FrameSink for RecordingContext`**

```rust
impl crate::media::FrameSink for RecordingContext {
    fn write_frame(&mut self, frame: crate::media::WriteFrame<'_>) -> Result<(), String> {
        RecordingContext::write_frame(
            self,
            frame.bgra,
            frame.capture_pts_100ns,
            frame.duplicated,
        )
        .map_err(|e| e.to_string())
    }
    fn stop(&mut self) -> Result<(), String> {
        RecordingContext::stop(self).map(|_| ()).map_err(|e| e.to_string())
    }
}
```

- [ ] **Step 4: 全量编译 + 相关测试**

```bash
cd rust/windows-screen-sidecar
cargo test recording_worker next_sample_timing logical_time -- --nocapture
cargo test --no-run
```

Expected: PASS / 编译成功。

- [ ] **Step 5: Commit**

```bash
git add rust/windows-screen-sidecar/src/media/recording_worker.rs rust/windows-screen-sidecar/src/media/mod.rs rust/windows-screen-sidecar/src/win_recorder/mod.rs
git commit -m "feat(sidecar): forward capture_pts through recording FrameSink"
```

---

### Task 5: Capture 取时位置确认 + 清理

**Files:**
- Modify: `rust/windows-screen-sidecar/src/capture.rs`（如需要）
- Modify: `rust/windows-screen-sidecar/src/media/capture_producer.rs`（确认）
- Modify: `rust/windows-screen-sidecar/src/win_recorder/recorder.rs` / `logical_time.rs` 清理无用 import

**Interfaces:**
- 保持：`CapturedFrame.captured_at_ms` → producer `publish_raw(captured_at_ms * 10_000, ...)`
- 或改为直接存/传 `capture_pts_100ns`；二选一，**全链路 Unix epoch 100ns 一致**

- [ ] **Step 1: 确认 BitBlt 成功后才取时间**

`capture_rect` 中 `captured_at_ms: current_timestamp_ms()` 必须在像素 copy 完成之后（现状已在 `Ok(CapturedFrame{...})` 处）。可选改为：

```rust
let captured_at_ms = current_timestamp_ms();
Ok(CapturedFrame { ..., captured_at_ms })
```

紧挨成功路径，避免错误路径取时。

- [ ] **Step 2: 全文搜索禁止项**

```bash
cd rust/windows-screen-sidecar
rg "logical_time_for_frame_index|recording_start_time|GetLocalTime" src
rg "write_frame\(" src
```

Expected：
- 录制路径无 `logical_time_for_frame_index` / `recording_start_time` / 水印用 `GetLocalTime`
- 所有 `write_frame` 签名已统一

- [ ] **Step 3: 全量测试**

```bash
cd rust/windows-screen-sidecar
cargo test
```

Expected: 全部 PASS。

- [ ] **Step 4: Commit**

```bash
git add rust/windows-screen-sidecar/src
git commit -m "chore(sidecar): finalize capture-timestamp recording path cleanup"
```

---

### Task 6: 构建产物与发布说明

**Files:**
- 按需：`tools/windows-screen-sidecar.exe`（若仓库跟踪 release 二进制）
- 可选文档一句：spec 状态改为已实现

- [ ] **Step 1: Release 构建**

```bash
cd rust/windows-screen-sidecar
cargo build --release
```

- [ ] **Step 2: 复制到 tools（与项目既有发布方式一致）**

```bash
# PowerShell
Copy-Item -Force rust\windows-screen-sidecar\target\release\windows-screen-sidecar.exe tools\windows-screen-sidecar.exe
```

若 `scripts/build_windows.ps1` 已包含 sidecar 构建，优先跑该脚本保证与生产一致。

- [ ] **Step 3: 冒烟清单（手工，写入 commit message 或 PR 描述）**

1. `start_recording` 20 或 30fps，`watermark=true`，录 20–30s  
2. 播放视频：左下角水印为本地时分秒，与桌面时钟约 ≤1 帧  
3. 暂停对比：翻页场景水印不“空走”（画面不变时时间不跳秒狂奔——duplicate 冻结）  
4. 任务管理器：sidecar Private Bytes 无异常持续爬升  

- [ ] **Step 4: Commit 二进制（若仓库惯例提交 tools exe）**

```bash
git add tools/windows-screen-sidecar.exe
git commit -m "build(sidecar): ship capture-timestamp recording watermark binary"
```

端侧全部更新该 sidecar 版本即可，无协议兼容层。

---

## Spec coverage checklist

| Spec 项 | Task |
|---------|------|
| G1 水印=local(capture) | 3 |
| G2 PTS 同源 capture | 1, 2, 3 |
| G3 无额外内存/队列 | 4 约束 + 5 检查 |
| G4 单调 PTS / duplicate 语义 | 1 状态机 |
| G5 ≤30fps 预期 | 文档/冒烟 |
| 无开关 | 全任务不引入 |
| 推流不动 | 不改 stream_worker |
| Python 不动 | 无 Python 任务 |
| 测试 | 1, 4, 5 |

## 执行说明

工作目录：`D:\code\autotest`，Rust 子项目：`rust/windows-screen-sidecar`。  
Windows 上跑 `cargo test` / `cargo build --release`。  
每 Task 结束必须测试通过再 commit。

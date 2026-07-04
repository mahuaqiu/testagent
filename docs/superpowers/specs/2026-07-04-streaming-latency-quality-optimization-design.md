# Windows H.264 推流延迟与画质优化设计文档（v2 — 实测驱动）

**日期**: 2026-07-04
**状态**: 初稿
**前置**: 本文档替代 `2026-07-02-streaming-latency-optimization-design.md`（那份将延迟归因为"拉模式轮询"，经实测推翻——真实瓶颈在编码器侧与配置链路，详见 §1.4 复盘）。
**目标**: 在 ≤4Mbps 平均带宽（VBR 瞬时突发可超）下，将 Windows H.264 推流的首帧延迟从 ~2.8s、操作延迟从 ~3s 降到 ≤1s，并解决 1080p 画质糊的问题。

## 1. 背景与问题

### 1.1 当前架构

Windows H.264 推流链路（实测梳理）：

```
worker.yaml (streaming_fps/bitrate/codec)
    │
    ▼
server.py: screen_stream()
    │  streaming_fps = worker.config.websocket_streaming_fps
    │  ├─→ WindowsSidecarManager.start_streaming(codec, fps=streaming_fps)
    │  │       └─→ WindowsSidecarStreamer → sidecar 请求 "stream_start" (fps/bitrate/profile)
    │  └─→ PushFrameReader.start_push(fps=streaming_fps)
    │           └─→ sidecar 请求 "stream_push_start" (fps)
    │
    ▼
Rust sidecar (windows-screen-sidecar.exe)
    │  stream_start  → EncodingContext::new → prime_encoder_with_black_frame
    │  stream_push_start → set_push_enabled(true) + push_sps_pps_once + capture_loop
    │  capture_loop: capture_monitor → encoder.encode_frames_detailed → push_frame_to_stderr
    │
    ▼
server.py 主循环: reader.get_frame() → websocket.send_bytes(prefix + NAL)
    │
    ▼
前端 (D:\code\zq-platform, 内网 HTTP 架构, jmuxer 解码) —— 客户端不可改解码逻辑
```

### 1.2 表现症状（实测）

| 症状 | 实测值 |
|------|--------|
| 首帧出现 | ~2.8 秒（你肉眼看） |
| 操作后看到变化 | ~3 秒（你肉眼看） |
| 画质 | 1080p 糊 |

### 1.3 实测证据（两轮诊断插桩 + 源码核实）

实测采用三个诊断测点：
- **测点 1**（Rust 侧）：`capture_loop` 加 push_enabled 跳变检测 + `encode_frames_detailed` 返回 `Ok(None)` 计数 + 首帧 `Ok(Some)` 耗时日志。
- **测点 2**（Python 侧）：`start_push` 记录启动时刻，`_handle_line` 收到 IDR 前缀打时间戳日志。
- **测点 3**（Python 侧）：bitrate 2M→8M、profile 66→100 临时改动跑画质对比。

实测关键数据（2026-07-04 08:54 完整日志）：

| 事件 | 距推流启动 | size |
|------|-----------|------|
| `stream_push_start` 请求发出 | 0ms | — |
| Rust `push_enabled set to true` | ~4ms | — |
| server 收到 SPS | ~102ms | 30 bytes |
| server 收到 PPS | ~103ms | 7 bytes |
| capture_loop 第一次 push_enabled=true | ~147ms | — |
| **IDR#1 到达** | **1593ms** | **6138（极小！伪 IDR）** |
| IDR#2 | 2812ms | 235274（真 IDR） |
| IDR#3 | 4078ms | 271803 |
| IDR#4 | 5312ms | 260293 |
| IDR 间隔 | 稳定 1.23-1.34s | — |

源码核实的三个根因：

1. **黑帧伪 IDR bug**：`h264_encoder.rs` 的 `prime_encoder_with_black_frame`（line 321-347）只调用一次 `process_encoder_output`。H.264 MFT 流水线有约 1 帧延迟——首次 `ProcessOutput` 通常返回 `MF_E_TRANSFORM_NEED_MORE_INPUT`（`process_encoder_output` line 833 分支直接 `break`），黑帧 IDR 未被真正取出，残留在 MFT 内部。`capture_loop` 第一次送真实屏幕帧时，这个延迟的黑帧 IDR 才被吐出，形成 6KB 伪 IDR#1 推给客户端。客户端 jmuxer 把伪 IDR 当真 IDR 锁定黑屏基线，直到真 IDR#2（~2.8s）才出画面——首帧 ~2.8s 延迟的直接根因。
2. **画质糊** = bitrate 2Mbps + profile Baseline(66)。实测 8Mbps + High(100) 清晰，坐实。
3. **配置链路三处电缆未接通**：
   - `config.py` 的 `websocket_streaming_bitrate` 配置项从 yaml 读取后，从未传给 `WindowsSidecarStreamer`（实例化时漏传参数，走默认值）。
   - `profile`（66/100）写死在 `stream_start` 请求，无配置项。
   - `set_gop_size`（`h264_encoder.rs:500-504`）是**空实现**，`idr_interval` 字段（line 103, 149）声明为 30 但全代码库无任何使用点——GOP 完全走 MFT 默认。实测 IDR 间隔 1.23-1.34s 是 MFT 默认产物。

### 1.4 上一份 spec 的复盘（为何重做）

上一份 `2026-07-02-streaming-latency-optimization-design.md` 把延迟归因为"拉模式轮询"，方案是"推替代拉"。实测推翻：

| 上一份假设 | 实测结论 |
|-----------|---------|
| 拉模式轮询是延迟主因 | 拉/推只差约几十毫秒，相对 1.5-3s 真实延迟聊胜于无 |
| 编码器预热 ~1.5s 是首帧延迟主因 | 预热在 `stream_start` 的 `EncodingContext::new` 阶段完成（`prime_encoder_with_black_frame`），与首帧延迟无关 |
| IDR 间隔 3s（最坏延迟来源） | 实测 1.23-1.34s（MFT 默认 GOP） |
| 巨型 P 帧源自 idr 间隔太长残差累积 | 8Mbps 下 IDR 真帧也 230-360KB，大帧非低码率独有 |

真实瓶颈是黑帧伪 IDR bug + 配置链路失效 + 低码率/profile，三者均未被上一份 spec 覆盖。

## 2. 设计约束（用户拍板）

| 维度 | 约束 | 来源 |
|------|------|------|
| 带宽 | 约 4Mbps 平均，VBR 瞬时突发可超 | 用户拍板 |
| 帧率 vs 画质 | 降帧率，保运动场景清晰 | 用户拍板 |
| 延迟目标 | 首帧 ≤1s、操作跟手 ≤0.5-1s | 用户拍板 |
| 优化范围 | 含 server.py 转发限速 | 用户拍板 |
| 录制/截图隔离 | 不得影响录制（mf_writer 5Mbps）与截图路径 | 用户拍板 |
| 前端 | 可改 `D:\code\zq-platform` 但维持内网 HTTP + jmuxer 架构（不引入 WebRTC） | 用户拍板 |
| 帧率来源 | Python 侧统一从 `streaming_fps` 传入，Rust/server 不自创帧率 | 用户拍板 |

## 3. 设计方案（方案 1：低帧率强控 + 配置化 + 修复黑帧）

三处必做改动 + 一处复测协议，全部限定在推流路径，不碰录制/截图。

### 3.1 修复黑帧伪 IDR bug（Rust 侧）

**位置**：`rust/windows-screen-sidecar/src/win_recorder/h264_encoder.rs` 的 `prime_encoder_with_black_frame`。

**改动**：在现有"送 1 帧黑帧 + 一次 process_encoder_output（取 SPS/PPS）"之后，追加冲刷步骤——额外送黑帧并循环 drain ProcessOutput，直到连续两次拿到空输出（流水线被彻底冲空），期间产出的所有帧（含延迟的黑帧 IDR）一律丢弃、不外送：

```rust
unsafe fn prime_encoder_with_black_frame(&mut self) -> Result<(), RecorderError> {
    // ... 现有：送 1 帧黑帧 + 一次 process_encoder_output（提取 SPS/PPS）...

    // ★ 新增：冲刷 MFT 流水线残留，丢弃延迟的黑帧 IDR
    let mut dry_count = 0;
    while dry_count < 2 {
        let black = self.make_black_nv12()?;          // 抽出黑帧生成为辅助方法
        let sample = self.create_nv12_sample(&black)?;
        let _ = self.h264_encoder.as_ref().unwrap()
                  .ProcessInput(self.encoder_input_id, &sample, 0);
        match self.process_encoder_output() {
            Ok(frames) => {
                let _ = frames;                       // 丢弃所有输出帧
                dry_count = if frames.is_empty() { dry_count + 1 } else { 0 };
            }
            Err(_) => { dry_count += 1; }
        }
    }
    Ok(())
}
```

**为什么是冲刷而非"跳过首帧 6KB IDR"**：跳过首帧是症状治理——若 MFT 行为变化，6KB 可能不在首帧；且跳首帧可能误丢真 IDR。冲刷流水线是根因治理——保证 prime 后 MFT 内部无残留，`capture_loop` 第一帧拿到的就是真屏帧的 IDR。

**实施时需验证**：`make_black_nv12` 抽取为辅助方法的逻辑与现有 line 322-331 一致（NV12 Y 平面置 0、UV 平面填 128）。冲刷轮数阈值 `dry_count < 2` 取经验值，MFT 实际流水线深度一般 1-2 帧，2 次 dry 足以确认冲空。

**不改**：RecordingContext、WinRecorder、mf_writer、snapshot 路径零改动。

### 3.2 推流编码参数配置化 + GOP 真正生效

**问题**：bitrate 走死默认、profile 写死、`set_gop_size` 空实现、`idr_interval` 死字段。

**改动 A — Python 侧配置接通**：

1. `worker/config.py`：
   - `websocket_streaming_bitrate` 默认 `2000000` → `4000000`
   - 新增 `websocket_streaming_profile: int = 100`（66=Baseline / 77=Main / 100=High）
   - `config.py:131` 的 `websocket_cfg` 解析增 `streaming_profile` 读取（line 162-164 同处）

2. `config/worker.yaml` 第 283-289 段：
   ```yaml
   websocket_streaming:
     max_connections_per_device: 3
     send_timeout_seconds: 30
     streaming_fps: 10
     streaming_codec: jpeg
     streaming_bitrate: 4000000     # H.264 平均码率 (4Mbps, VBR 瞬时突发可超)
     streaming_profile: 100         # H.264 profile: 66=Baseline, 77=Main, 100=High
   ```

3. `worker/server.py` `screen_stream` 函数：
   - 读出 `streaming_bitrate = worker.config.websocket_streaming_bitrate`
   - 读出 `streaming_profile = worker.config.websocket_streaming_profile`
   - 传给 `screen_manager.start_streaming(codec=codec, bitrate=streaming_bitrate, profile=streaming_profile)`

4. `worker/screen/windows_sidecar.py`：
   - `WindowsSidecarStreamer.__init__` 默认 `bitrate` 从 `8_000_000`（实测临时值）改回 `4_000_000`
   - `start(codec, profile=100)` 接收 profile 参数
   - `WindowsSidecarManager.start_streaming` 透传 bitrate/profile 给 `WindowsSidecarStreamer`
   - `stream_start` 请求的 `profile` 字段用传入值，不再写死 100

**改动 B — Rust 侧 GOP 真正生效**：

`h264_encoder.rs:500-504` 的 `set_gop_size` 从空实现改为真正调用 ICodecAPI：

```rust
unsafe fn set_gop_size(&self, encoder: &IMFTransform) -> Result<(), RecorderError> {
    let codec_api: ICodecAPI = encoder.cast()
        .map_err(|e| RecorderError::MFError(format!("ICodecAPI cast 失败: {}", e)))?;
    let gop: u32 = 10;                                  // 方案1: 10fps × 10帧 = IDR 间隔 1.0s
    let var = windows::core::VARIANT::new(gop);
    codec_api.SetValue(&CODECAPI_AVEncMPVGOPSize, &var)
        .map_err(|e| RecorderError::MFError(format!("设置 GOP 大小失败: {}", e)))?;
    Ok(())
}
```

GOP 值用常量 10（YAGNI——这次不做成配置；若后续需调再加 `websocket_streaming_gop` 配置项）。同步清理 `idr_interval` 死字段（line 103, 149）或保留待后续配置化——本 spec 倾向保留字段、标注 TODO 待配置化，降低本次改动面。

**改动 C — VBR 码率控制（Rust 侧，`configure_pipeline` 内新增）**：

为落实"4Mbps 平均、瞬时突发可超"，需显式设码控模式为 Peak VBR。在 `configure_pipeline`（line 468-496）末尾增：

```rust
// 码率控制模式 = Peak VBR (允许瞬时超码率)
let codec_api: ICodecAPI = h264_encoder.cast()?;
let rc_mode: u32 = 1;   // CODECAPI_AVEncCommonRateControlMode: 0=CBR, 1=Peak VBR, 2=Quality VBR
codec_api.SetValue(&CODECAPI_AVEncCommonRateControlMode, &VARIANT::new(rc_mode))?;
// 平均码率/峰值码率由 bitrate 字段经 CODECAPI_AVEncCommonMeanBitRate / AVEncCommonMaxBitRate 设置
```

⚠️ **实施时风险点**：Windows H.264 MFT 对 `CODECAPI_AVEncCommonRateControlMode` 的支持因 Windows 版本而异。某些版本只接受有限码控模式或属性名不同。**实施时必须先探测 MFT 支持的属性**（`ICodecAPI::IsSupported` 或尝试设置后看返回码），不支持则退回默认（MFT 默认码控，通常 CBR-ish）。这是本 spec 标注的"实施时需探测验证"项，不在设计阶段敲死具体属性值与回退码。

**不改**：`mf_writer.rs` 录制分支的 5Mbps、`RecordingContext`、`WinRecorder`、`snapshot` 路径全部零改动。已核实录制走 `state.recorder`（RecordingContext），推流走 `state.encoder`（EncodingContext），两条路径在 `capture_loop` 内各有独立分支处理（line 527-537 录制 vs line 540-588 推流），bitrate/profile 参数完全解耦。

### 3.3 server.py 转发限速 + Rust 帧率强控

**问题**：server.py H.264 推流分支主循环（line 1043-1061）没有限速，`get_frame` 队列有帧就立刻转发。实测 Rcapture_loop 真实捕获 ~8fps（非配置 10fps，因 capture 耗时吃掉部分节流），但若 Rust 突发产帧，会直接灌给前端 → frame_queue 积压 → 丢旧帧 + 带宽不可预测。

**帧率来源**（实测核实，单一来源）：worker.yaml `streaming_fps: 10` → `config.py` `websocket_streaming_fps` → `server.py:916` `streaming_fps` → 同时传给 Rust（`start_streaming(fps=...)` + `start_push(fps=...)`）和本次新增的 server 转发限速。**三层共用同一 `streaming_fps`**，改 fps 只改 worker.yaml 一处。

**改动 A — server.py 主循环加限速**（与本文件非推流分支 line 1069 已有 `asyncio.sleep(1/fps)` 对齐）：

```python
import time as _t
frame_interval = 1.0 / streaming_fps
last_send = _t.monotonic()
while reader.is_running():
    frame_type, frame_data = await reader.get_frame()
    if not frame_data:
        continue
    if frame_type in ('idr', 'p'):
        prefix = b'\x02' if frame_type == 'idr' else b'\x03'
        await asyncio.wait_for(
            websocket.send_bytes(prefix + frame_data),
            timeout=send_timeout
        )
        # ★ 限速兜底：用传进来的 streaming_fps 控制转发节奏
        elapsed = _t.monotonic() - last_send
        if elapsed < frame_interval:
            await asyncio.sleep(frame_interval - elapsed)
        last_send = _t.monotonic()
    # sps/pps 不限速，确保尽快送达
```

**改动 B — Rust 侧无需改帧率逻辑**：`capture_loop` 已有 `push_fps.max(active_fps)` 计算 `effective_fps` 并按 `interval` 节流（line 437-504）。`start_push` 与 `start_streaming` 传的 fps 都是同一个 `streaming_fps`，max 出来即此值，Rust 忠实接收 Python 传值，不引入额外帧率。

### 3.4 延迟复测协议（修复完成后由用户验收）

操作延迟 ~3s 的落差（实测 IDR 间隔 1.28s，落差 ~1.7s）有候选 a（黑帧伪 IDR 锁死客户端基线）与候选 b（不限速缓冲堆积）两个来源。两者已被 §3.1 与 §3.3 顺带覆盖，**本 spec 不单独新增额外修复**。

**复测设计**（诊断测点保留为可 flag 开启，默认关闭）：

| 指标 | 修复后目标 | 验证方法 |
|------|-----------|----------|
| 黑帧伪 IDR | IDR#1 不再是 6KB，应为 ~230KB+ 真屏 IDR | 开测点2 flag，grep `[测点2] IDR#1` 看 size |
| 首帧延迟 | ~2.8s → ≤1s | 测点2 日志 + 用户肉眼首次出画面 |
| 操作延迟 | ~3s → ≤1s | 用户实操移动文件看跟手 |
| 带宽 | 平均 ≤4Mbps，瞬时峰值允许突发到 ~6Mbps | WS 流量统计或 Rust 侧 P 帧平均 size 反推 |
| 画质 | 1080p 运动场景清晰 | 用户肉眼 |

**复测后分支决策**：
- 操作延迟降到 ≤1s → 候选 a/b 被覆盖，疑点闭合，spec 完成。
- 操作延迟仍 >1s 但 <2s → 剩余来源可能在客户端 jmuxer GOP 缓冲深度（前端 `D:\code\zq-platform` 可改），作为后续 follow-up，不在本 spec 范围。
- IDR#1 size 仍 ~6KB → §3.1 冲刷未生效，回 systematic-debugging Phase 1 重查 MFT 流水线行为。

## 4. 改动清单（受影响文件汇总）

| 文件 | 改动 | 是否新 |
|------|------|--------|
| `rust/windows-screen-sidecar/src/win_recorder/h264_encoder.rs` | `prime_encoder_with_black_frame` 追加冲刷；`set_gop_size` 实现真正逻辑；`configure_pipeline` 增 VBR 码控（探测 MFT 支持） | 是（修复+实现） |
| `worker/config.py` | `websocket_streaming_bitrate` 默认改 4M；新增 `websocket_streaming_profile` | 是 |
| `config/worker.yaml` | `streaming_bitrate` 改 4M；新增 `streaming_profile` | 是 |
| `worker/server.py` | `screen_stream` 读 bitrate/profile 传给 start_streaming；推流主循环加 `streaming_fps` 限速 | 是 |
| `worker/screen/windows_sidecar.py` | `WindowsSidecarStreamer` 默认 bitrate 收回 4M；profile 走参数透传；`stream_start` profile 不写死 | 是 |
| 前端 `D:\code\zq-platform` | 本 spec 范围内不动；仅复测不达标时作 follow-up | 否 |

## 5. 不改清单（守约束）

- `rust/windows-screen-sidecar/src/win_recorder/mf_writer.rs` — 录制分支 5Mbps，零改动
- `RecordingContext` / `WinRecorder` — 录制器，零改动
- `snapshot` 命令路径 — 截图，format/quality 独立参数，零改动
- `capture_loop` 内录制分支 `if has_recorder { recorder.write_frame }` — 不动
- 拉模式推流分支（`server.py:1067-1088` 非推流路径）— 已有限速保留
- 前端 jmuxer 解码逻辑 — 客户端不可改，不引入 WebRTC

## 6. 风险与未知

| 风险/未知 | 应对 |
|----------|------|
| MFT 对 VBR 码控属性支持因 Windows 版本异 | 实施时探测 `IsSupported`，不支持退回默认（§3.2 改动 C） |
| 冲刷轮数 `dry_count < 2` 是否冲空彻底 | 实施后复测 IDR#1 size 验证；不达标回 Phase 1 |
| 操作延迟落差剩余部分若来自客户端 jmuxer | 不在本 spec 范围，作 follow-up |
| GOP=10 在 4Mbps 下静态画面码率是否被 IDR 占用过多 | 复测关注静态画面清晰度与平均带宽；若不达标调 GOP 或 bitrate |
| `idr_interval` 死字段清理 | 本 spec 保留待配置化，不强行删除降低改动面 |

## 7. 验收标准

全部满足即 spec 完成：

1. IDR#1 size ≥ 200KB（不再是 6KB 黑帧伪 IDR）
2. 首帧延迟 ≤ 1s
3. 操作延迟 ≤ 1s（用户复测验收）
4. 平均带宽 ≤ 4Mbps，瞬时峰值允许 ~6Mbps
5. 1080p 运动场景清晰（用户复测）
6. 录制（5Mbps）与截图功能回归正常不受影响

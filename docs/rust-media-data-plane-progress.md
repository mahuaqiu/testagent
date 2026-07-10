# Rust 媒体数据面重构计划完成进展

> 报告日期：2026-07-10
> 开发方式：当前分支直接开发
> 项目路径：`D:\code\autotest`
> Rust sidecar：`rust/windows-screen-sidecar`

## 1. 总体结论

当前方案 C——**Rust 媒体数据面重构，Python 控制面，二进制媒体通道**——尚未整体完成。

目前已经完成第一轮主链路落地，并继续补齐了断线恢复和启动回滚，按完整方案 C 估算整体完成度约为 **78%**：

- Rust 的抓帧、录制、推流已经拆分为独立媒体组件，并接入 `session.rs`。
- 录制 PTS 和水印时间已经改为由逻辑帧时间轴驱动。
- Rust 推流 worker 已支持 RSM1 二进制媒体 packet。
- 独立 TCP 二进制输出已经接入 sidecar session 生命周期。
- Python 已能协商并读取 RSM1 packet，Windows H.264 WebSocket 路径已优先使用二进制通道。
- 新客户端连接后的首包已经增加关键帧恢复门控，避免旧 P 帧造成前端无解码上下文。
- Python 二进制 reader 已支持 RSM1 TCP 断线重连；重连后 Rust 端会重新执行首个关键帧门控。
- Rust `stream_start` 已增加事务性失败回滚，避免捕获线程启动失败后残留编码器、队列和 TCP 端点。
- 旧 stderr 文本/base64 推流仍保留，作为兼容和回退路径。

尚未完成的部分主要是：

1. 真实 Windows sidecar 的端到端 binary `stream_start` 验证。
2. 使用真实编码数据验证前端 WebSocket 解码、SPS/PPS、IDR 恢复和重连。
3. 长时间同时录制和推流的压力、背压、资源占用和稳定性回归。
4. 使用 `ffprobe` 对录制文件进行 PTS、duration、帧率和连续性验收。

因此，当前不能宣称方案 C 已完全完成，但已经从“基础骨架”进入“可运行主链路 + 待真实环境验收”阶段。

---

## 2. 分阶段进展

| 阶段 | 计划内容 | 状态 | 完成度 |
|---|---|---|---:|
| 阶段 0 | 问题定位与架构方案确认 | 已完成 | 100% |
| 阶段 1 | 录制逻辑时间轴和水印时间修复 | 已完成 | 100% |
| 阶段 2 | `FrameHub` 和 `CaptureProducer` 基础层 | 已完成 | 100% |
| 阶段 3 | 接入 `session.rs` 主媒体链路 | 已完成 | 100% |
| 阶段 4 | 独立 `RecordingWorker` 和固定 FPS tick | 已完成 | 100% |
| 阶段 5 | 独立 `StreamWorker` 和推流暖管 | 已完成 | 100% |
| 阶段 6 | RSM1 二进制媒体协议和 TCP 输出 | 已完成基础链路、首包恢复和重连配合 | 92% |
| 阶段 7 | Python 控制面接入和 WebSocket 转发 | 已完成基础链路、降级、映射和断线重连 | 90% |
| 阶段 8 | 真实端到端、长时间和性能回归 | 未完成 | 0% |

---

## 3. 已完成内容

### 3.1 阶段 0：问题定位与方案确认

已经确认当前故障的主要原因：

1. 原录制 PTS 使用 `MFGetSystemTime()`，录制时间轴受抓帧阻塞、编码耗时、推流压力和线程调度抖动影响。
2. 原水印每帧调用 `GetLocalTime()`，水印显示的是实时本地时间，不是视频逻辑时间，因此会随抓帧或写入抖动跳跃。
3. 抓帧、录制、编码和推流仍在同一个高耦合执行链路中，媒体链路之间会相互阻塞。

长期架构已经确定为：

```text
Python 控制面
    |
    | 控制命令 / 状态查询
    v
Rust 媒体数据面
    |
    +-- CaptureProducer
    |       |
    |       v
    |    FrameHub
    |      /   \\
    |     /     \\
    |  Recorder  Stream
    |   Worker   Worker
    |
    +-- 二进制媒体通道
```

### 3.2 阶段 1：录制逻辑时间轴修复

#### 新增逻辑时间模块

文件：

```text
rust/windows-screen-sidecar/src/win_recorder/logical_time.rs
```

已实现：

- `ClockTime`：表示水印起始本地时间。
- `frame_index_to_pts_100ns()`：按帧号和 FPS 计算 PTS。
- `sample_timing_for_frame_index()`：计算帧时间戳和持续时间。
- `logical_time_for_frame_index()`：根据录制开始时间和帧号生成水印时间。
- 跨午夜时间滚动处理。
- 非整数帧时长的累计误差处理，例如 30 FPS 下自动分配 `333333 / 333334` 个 100ns 单位。

#### `mf_writer.rs` 已切换到逻辑 PTS

文件：

```text
rust/windows-screen-sidecar/src/win_recorder/mf_writer.rs
```

已完成：

- 移除基于 `MFGetSystemTime()` 的首帧时间计算。
- 移除固定的 `frame_duration` 字段。
- 新增 `fps` 字段。
- 每次写帧时根据 `frame_count` 和 `fps` 计算逻辑 PTS 和 duration。
- 写入成功后递增帧计数。

当前时间轴计算方式为：

```text
PTS(frame_index) = frame_index * 10_000_000 / fps
```

该改动可以避免系统时间、抓帧间隔和推流压力直接污染录制文件时间轴。

#### `watermark.rs` 已切换到外部逻辑时间

文件：

```text
rust/windows-screen-sidecar/src/win_recorder/watermark.rs
```

已完成：

- 移除水印渲染器内部的 `GetLocalTime()` 调用。
- 删除内部实时生成时间字符串的逻辑。
- `render()` 改为接收调用方传入的逻辑时间字符串。

#### `recorder.rs` 已绑定录制起始时间和帧号

文件：

```text
rust/windows-screen-sidecar/src/win_recorder/recorder.rs
```

已完成：

- 新增 `recording_start_time` 字段。
- 录制开始时只读取一次本地时间。
- 每次写帧时使用 sink writer 当前帧号生成水印时间。
- 停止录制时清理起始时间状态。

这样水印时间会跟视频帧序列推进，而不是跟当前系统时钟推进。

### 3.3 阶段 2～5：媒体基础层和主链路接入

新增目录：

```text
rust/windows-screen-sidecar/src/media/
```

已完成文件：

```text
rust/windows-screen-sidecar/src/media/mod.rs
rust/windows-screen-sidecar/src/media/types.rs
rust/windows-screen-sidecar/src/media/frame_hub.rs
rust/windows-screen-sidecar/src/media/capture_producer.rs
```

当前基础能力包括：

- `CapturedFrame`：统一的 Rust 内部帧结构。
- `FrameHub`：发布和读取最新帧。
- `CaptureProducer`：独立线程按目标 FPS 抓帧并发布到 `FrameHub`。
- `FrameHubStats`、`RecorderStats`、`StreamStats`、`MediaSessionStats` 等统计结构。
- `FrameHub` 等基础组件的初步单元测试。

这些组件已经接入 `session.rs` 的生产运行链路，并按录制或推流生命周期按需启动和释放。

### 3.4 独立录制 worker

文件：

```text
rust/windows-screen-sidecar/src/media/recording_worker.rs
```

已完成：

- 录制由独立线程按照固定 FPS tick 驱动。
- 新帧可用时写入最新帧；没有新帧时复用上一帧。
- 停止时等待 worker 退出并 finalize sink。
- 输出写帧数、补帧数、迟到 tick 和最后一次写入耗时统计。

这使录制时间轴不再依赖抓帧线程是否准时返回。

### 3.5 独立推流 worker

文件：

```text
rust/windows-screen-sidecar/src/media/stream_worker.rs
```

已完成：

- 编码和推流从抓帧生产者中拆出独立线程。
- 推流按逻辑 tick 计算 PTS 和 duration。
- 旧 `stream_queue` 仍然保留，兼容 `stream_next` 和旧客户端。
- 推流 stderr/base64 路径仍保留。
- 推流暖管过滤已经移动到 worker，黑帧 IDR 和真正 IDR 前的 P 帧不会直接发送。
- 二进制 packet 的 `sequence` 只对实际生成的媒体 packet 递增，不会因编码器空 tick 产生无意义的序号跳跃。

### 3.6 Session 生命周期

`SessionState` 现在统一管理：

- `FrameHub`。
- `CaptureProducer`。
- `RecordingWorkerHandle`。
- `StreamWorkerHandle`。
- 可选的 `BinaryMediaOutput`。

录制或推流开始时启动抓帧生产者，两者都停止后释放抓帧资源；停止推流时先停止编码 worker，再释放 TCP 输出，避免后台线程继续写已关闭的媒体通道。

---

### 3.7 二进制输出首包恢复

文件：

```text
rust/windows-screen-sidecar/src/media/binary_output.rs
```

已完成：

- 每个新 TCP 客户端连接都会重新进入首包门控。
- 连接建立后直接丢弃无法独立解码的旧 P 帧。
- 在收到下一个关键帧之前，缓存最近的配置包。
- 收到不带配置的关键帧时，按“配置包 + 关键帧”顺序释放。
- 关键帧已经携带配置时只发送当前关键帧，避免重复配置。
- 客户端断开后下一次连接重新执行上述恢复流程。

### 3.8 二进制断线恢复和启动事务回滚

Python 文件：

```text
worker/screen/windows_sidecar.py
```

已完成：

- `MediaPacketReader` 保存 TCP endpoint，支持断线后清空半包缓存并建立新连接。
- `WindowsSidecarStreamer.get_media_packet_async()` 遇到 EOF、socket 错误或超时后尝试重连，不立即结束 WebSocket 推流。
- 重连失败时停止当前 binary streamer，避免在失效连接上忙循环。
- 重连后的新 TCP 客户端由 Rust `InitialKeyframeGate` 重新等待可解码关键帧。

Rust 文件：

```text
rust/windows-screen-sidecar/src/session.rs
```

已完成：

- 抽取统一的 stream 状态清理逻辑。
- `stream_start` 在捕获线程启动失败时调用停止和清理流程。
- 清理编码器信息、SPS/PPS、兼容队列和二进制 TCP 输出，保证下一次启动不被残留状态阻塞。

## 4. 当前代码状态

### 已修改文件

```text
rust/windows-screen-sidecar/src/main.rs
rust/windows-screen-sidecar/src/session.rs
rust/windows-screen-sidecar/src/media/mod.rs
rust/windows-screen-sidecar/src/media/packet.rs
rust/windows-screen-sidecar/src/media/binary_output.rs
rust/windows-screen-sidecar/src/media/stream_worker.rs
rust/windows-screen-sidecar/src/media/recording_worker.rs
rust/windows-screen-sidecar/src/media/capture_producer.rs
rust/windows-screen-sidecar/src/media/frame_hub.rs
rust/windows-screen-sidecar/src/win_recorder/mf_writer.rs
rust/windows-screen-sidecar/src/win_recorder/mod.rs
rust/windows-screen-sidecar/src/win_recorder/recorder.rs
rust/windows-screen-sidecar/src/win_recorder/watermark.rs
worker/screen/windows_sidecar.py
worker/server.py
tests/screen/test_push_streaming.py
```

`media/` 和 `logical_time.rs` 是本轮新增模块；当前尚未提交 commit，是否提交由后续集成流程决定。

### 当前仍存在的 warning

当前 warning 主要来自：

- 媒体模块中为后续状态查询预留的统计结构和解码辅助 API 尚未全部暴露到控制面。
- 部分旧代码已有的未使用方法、字段和编码器兼容 API。
- `frame_index_to_pts_100ns` 等同时供测试和后续查询使用的导出暂时没有生产调用。

这些 warning 不代表当前编译失败，但说明媒体骨架还没有完成实际接线。

这些 warning 不代表编译失败，但在最终清理阶段可以通过收窄模块导出或补充状态查询接口减少。

---

## 5. 当前验证结果

### 5.1 Rust 编译和单元测试

执行：

```powershell
cd D:\code\autotest\rust\windows-screen-sidecar
cargo check
```

结果：

- cargo 输出 `Finished`。
- 未发现 Rust 编译错误。
- 当前有未使用代码相关 warning。

最新全量测试结果：

```text
32 passed; 0 failed
```

### 5.2 逻辑时间、媒体组件和协议测试

执行：

```powershell
cd D:\code\autotest\rust\windows-screen-sidecar
cargo test logical_time -- --nocapture
```

结果：

```text
6 passed; 0 failed
```

覆盖内容：

- PTS 按帧号递增。
- 25 FPS 时间计算。
- 30 FPS 非整数帧时长计算。
- 跨午夜时间处理。
- 逻辑水印时间计算。
- sample 时间戳和 duration 连续性。

### 5.3 Python 二进制通道和旧推流兼容测试

执行前已激活项目虚拟环境：

- 空 hub 状态。
- 最新帧发布和读取。
- 等待新帧。
- `VIRTUAL_ENV=D:\code\autotest\venv`

执行：

```powershell
python -m pytest --capture=no tests/screen/test_push_streaming.py -q
```

结果：

```text
18 passed; 1 skipped
```

覆盖 RSM1 半包、粘包、magic/version、payload 超限、时序元数据保留、旧 stderr 推流兼容，以及 binary 启动参数、失败降级、WebSocket 帧前缀映射和断线重连。

当前环境仍会输出 pytest-asyncio 配置提示；使用 `-p no:cacheprovider` 后没有依赖 pytest cache 的失败。

本轮还通过了：

```text
rustfmt --check --edition 2021 src/session.rs src/media/binary_output.rs
cargo check
```

Release 构建尚未完成，当前机器对 Rust `target` 工作区目录返回 Windows `os error 5`（拒绝访问）；这属于环境权限阻塞，不代表 debug 编译或测试失败。

---

## 6. 尚未完成的核心工作

### 6.1 真实端到端 binary 推流验证

媒体组件已经接入 `session.rs`，当前缺少真实 Windows sidecar 的端到端证据。需要完成：

1. 调用 `stream_start(binary=true)`。
2. 从返回的 TCP endpoint 建立 Python 连接。
3. 连续解码 RSM1 packet，并验证 PTS、duration、flags 和 payload。
4. 将配置包、IDR 和 P 帧转发给 WebSocket 前端，确认真实解码画面。

### 6.2 断线、重连和关键帧恢复

当前 TCP 输出仍为单客户端、有界队列模型；Python/Rust 的基础断线重连配合已经完成，还需要继续补充：

- 如何统计客户端断连、队列溢出、慢读和等待 IDR 时间。
- 真实客户端断线后重新连接时的恢复耗时和首帧成功率。
- 重连连续失败时的退避、告警和最终降级策略。

目标是保证：

```text
推流卡顿不会导致录制 PTS 断裂
抓帧短暂抖动不会导致视频时间轴断裂
```

### 6.3 录制和性能回归

需要补充以下验证：

- 录制 30 秒和 60 秒视频。
- 使用 ffprobe 检查 duration、PTS、平均帧率和 packet 间隔。
- 对水印进行连续性检查。
- 同时录制和推流，确认录制不受推流阻塞影响。
- 模拟抓帧抖动、编码延迟和推流阻塞。
- 检查 CPU、GPU、内存、队列长度和丢帧统计。

---

## 7. 下一步实施顺序

建议按照以下顺序继续，不要同时大改录制和推流：

### 第一步：真实二进制推流验收

优先验证 sidecar endpoint、RSM1 packet 和 WebSocket 前端解码的真实链路。

### 第二步：补齐重连观测和客户端恢复验收

验证新客户端连接后的 SPS/PPS + IDR 恢复，并补充慢客户端背压统计。

### 第三步：端到端和性能回归

完成 ffprobe、长时间录制、并发录制推流、异常恢复和资源指标验证后，才可以认为方案 C 完成。

---

## 8. 风险和注意事项

1. `FrameHub` 保存最新帧，适合录制补帧和低延迟推流；它不是完整帧序列队列。
2. 当前二进制 output 是单客户端模型，不适合多个 WebSocket 消费者直接共享同一 TCP 输出。
3. 连接前队列容量有限，连接延迟过长时仍可能丢弃旧 packet，这是有界实时系统的预期行为。
4. 二进制 packet 已携带 PTS，但当前 WebSocket 兼容层仍只传递 payload 和帧类型，前端尚未消费 packet 时间元数据。
5. 当前环境没有真实 Windows sidecar + 前端链路，不能用单元测试替代端到端验收。
6. 当前改动尚未形成完整提交，是否提交由后续集成流程决定。

---

## 9. 结论

当前改造已经完成了方案 C 的第一轮主链路实现和单元级验证，并进一步补齐了二进制通道首包恢复、断线重连和启动失败回滚：

- 录制 PTS 不再依赖实时系统时间。
- 水印不再每帧读取实时系统时间。
- 逻辑时间计算有独立模块和单元测试。
- Rust 媒体数据面已经接入 session，并有独立录制/推流 worker。
- RSM1 packet 和 loopback TCP 输出已经实现。
- Python 已有二进制 reader，并在 Windows H.264 WebSocket 路径优先使用。
- 新客户端不会直接消费旧 P 帧，而是等待可解码的关键帧上下文。
- RSM1 TCP 断线后 Python 会清理半包缓存并尝试重连，Rust 会重新执行关键帧恢复门控。
- `stream_start` 捕获线程启动失败时会清理 worker、队列和二进制输出端点。
- binary 启动失败会显式停止 sidecar 推流并降级到 JPEG。

但当前还不能宣称方案 C 完成。下一阶段的关键验收点是：

> 在真实 Windows 环境验证 binary stream、长时间录制、断线恢复和 ffprobe 时间轴之前，方案 C 仍处于“主链路已落地、最终验收未完成”状态。

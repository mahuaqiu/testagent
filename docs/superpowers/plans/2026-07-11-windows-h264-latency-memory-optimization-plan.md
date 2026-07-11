# Windows H.264 推流启动黑屏、延迟与内存优化计划

## 1. 背景

当前 Windows 屏幕推流采用 Rust sidecar 编码 H.264、RSM1 binary 数据面传输、Python WebSocket 转发、前端 JMuxer/MSE 播放的链路。

本计划依据 2026-07-11 实测日志制定。`2026-07-02-streaming-latency-optimization-plan.md` 主要针对旧 stderr 推流架构，其中部分结论已不适用于当前 RSM1 binary 数据面，后续实施应以本计划为准，但保留旧计划作为历史设计参考。

本计划已进入阶段 1 实施；阶段 0 的诊断日志已由前序工作补齐，后续以实机日志验证行为效果。

## 2. 已确认的现象与证据

### 2.1 启动黑屏

Rust 编码侧时序：

- 约 138～810ms：连续产生 13～14B 的占位或暖机 P 帧。
- 约 909ms：产生约 6176B、带编码配置的 IDR，画面仍为暖机黑帧。
- 约 1414ms：开始出现约 262958B 的真实画面 P 帧。
- 约 1909ms：产生约 292085B、带编码配置的首个可信真实 IDR。

前端约在 WebSocket 建连后 910ms 收到首包，`prefix=2`、`bytes=6177`，与暖机黑色 IDR 对应。此时 MSE 尚无有效缓冲区，视频处于暂停和未就绪状态。

初步结论：

- RSM1 binary sender 在真实画面门控前发送了暖机黑色 IDR。
- 现有 `PushWarmupGate` 主要保护旧 stderr 路径，没有覆盖 binary sender。
- 首要修复方向是让 binary 路径等待首个可信真实 IDR，再向浏览器发送编码配置和媒体数据。
- 更根本的问题可能位于 `prime_encoder_with_black_frame` 后的 MFT 流水线残留；该方向风险更高，应晚于 binary 门控实施。

### 2.2 稳态延迟约 2～3 秒

日志显示：

- Rust 单帧编码通常为 8～14ms，满足 10fps 目标，编码吞吐不是主要瓶颈。
- `dropped_late_ticks` 仅少量增长，`duplicated_ticks` 在启动后基本稳定。
- 浏览器稳定以约 10fps 收包，没有明显持续堆积或延迟不断增长。
- 用户观察到的延迟约 2～3 秒且基本稳定。

初步结论：

- 当前更像是播放器启动后落后 live edge，且 `video.currentTime` 没有主动追赶 `bufferedEnd`，初始缓冲差被长期保留。
- 服务端 binary FIFO 容量和背压策略仍可能放大慢消费者场景，但不是当前稳定延迟的唯一证据。
- 不应新增固定转发限速；当前链路已经稳定输出 10fps，再限速可能进一步增加排队延迟。

### 2.3 推流与录制内存

实测数据：

- 仅推流约 368MB。
- 推流与录制同时运行约 700MB。
- 连续四次录制启停后约 410MB，并在附近波动，没有逐次单调增长。

初步结论：

- 暂无典型持续内存泄漏证据。
- 高峰值更可能来自两套 MFT 编码器、D3D11 纹理、NV12/BGRA 缓冲以及 allocator 工作集高水位。
- 录制停止后内存明显回落，说明多数录制资源能够释放。
- 在缺少 private bytes、队列长度和资源实例数量数据前，不应先做大范围资源重构。

## 3. 目标与非目标

### 3.1 目标

1. 浏览器不展示编码器暖机阶段的黑色关键帧。
2. 播放器启动后快速贴近 live edge，并在网络或渲染抖动后自动恢复低延迟。
3. 慢消费者出现时优先保证实时性，避免 FIFO 保留过时视频。
4. 建立可区分泄漏、资源未释放和 allocator 高水位的内存观测能力。
5. 保持录制时长、PTS、水印、断线重连和旧协议回退能力不回归。

### 3.2 非目标

- 本计划不立即重写 MFT 编码器或替换 JMuxer。
- 不在缺少 profile 数据时承诺固定的进程内存绝对值。
- 不通过降低清晰度、降低帧率或增加固定缓冲来掩盖延迟。
- 不直接移除旧 stderr fallback、JPEG/MJPEG 等兼容路径。

## 4. 实施原则

1. 先补观测，再做行为修改；每个阶段必须能独立验证和回滚。
2. 优先实施低风险、与日志证据直接对应的改动。
3. 推流队列采用“最新画面优先”的实时语义，录制队列继续采用“完整性优先”的语义。
4. 启动门控基于 H.264 NAL/帧状态和真实画面判定，不把固定等待时间作为长期方案。
5. 前端 live edge 追赶必须带阈值和滞回，避免频繁 seek 或播放速率抖动。

## 5. 分阶段修改计划

### 阶段 0：补齐基线可观测性

#### Rust sidecar

- 为每个推流会话增加稳定的 session/stream 标识，串联采集、编码、binary queue 和发送日志。
- 记录首个采集帧、首个编码输出、首个真实画面、首个 IDR、首个 binary 入队和首个 binary 出队的相对时间。
- 周期性记录 binary queue 当前长度、峰值、丢弃数、等待 IDR 状态和消费者阻塞时长。
- 记录活动 capture producer、stream worker、recording worker、encoder 实例和线程数量。
- Windows 下周期性记录 process working set 与 private bytes，区分工作集回收和真实私有提交量。
- 对启动阶段输出 NAL 类型、包大小、是否包含 config、是否通过暖机门控；稳定后降采样，避免日志本身影响时序。

#### Python WebSocket 转发层

- 记录 sidecar 包读取时间、WebSocket 发送开始和结束时间、发送耗时及连续慢发送次数。
- 记录连接关闭原因、累计包数/字节数、sequence gap 和最后一个包距连接建立的时间。
- 日志只保留元数据，不输出 H.264 payload。

#### 前端

- 将 MSE 指标扁平化输出，确保控制台可直接查看：`currentTime`、`bufferedStart`、`bufferedEnd`、`liveEdgeLagSeconds`、`readyState`、`paused`、`playbackRate`。
- 记录首包、首个 config/IDR、首次 append、`loadedmetadata`、`canplay`、首次 `play()` 成功和首帧可见时间。
- 记录每次 live-edge seek、速率调整及触发前后的延迟。

#### 阶段产物

- 形成仅推流、推流加录制、断线重连三组基线日志。
- 明确 2～3 秒延迟分别由 sidecar 队列、WebSocket send、MSE append 和播放器位置贡献多少。

### 阶段 1：低风险修复启动黑屏与播放器落后

#### 1.1 RSM1 binary 接入暖机门控

- 将当前真实画面判定能力接入 binary sender，而不是只保护旧 stderr 输出路径。
- 在门控打开前消费并丢弃暖机占位 P 帧和黑色 IDR，不向 binary queue 发布。
- 门控打开时强制等待一个可信真实 IDR；首个对外包必须包含解码所需 config 和该真实 IDR。
- 若首次真实变化发生在 P 帧，触发或等待下一关键帧，不允许浏览器从不可独立解码的 P 帧开始。
- 为门控增加最大等待保护与诊断事件；超时策略应显式失败或降级，不能无提示永久黑屏。

#### 1.2 移除 binary 模式下无消费者的旧队列复制

- 核实 RSM1 binary 模式下旧 `stream_queue` 是否仍有实际消费者。
- 在没有 stderr/fallback 消费者时，停止向旧队列重复复制 H.264 payload。
- 保留明确的 fallback 分支，避免影响旧协议兼容性。

#### 1.3 前端首次贴近 live edge

- MSE 首次形成可播放缓冲区后，将 `video.currentTime` 定位到 `bufferedEnd - targetLatency`。
- `targetLatency` 初始建议为 0.2～0.3 秒，并配置最小安全边界，避免 seek 到尚未稳定 append 的尾点。
- 在浏览器自动播放受限时，区分 `play()` 被策略拒绝和解码未就绪，避免误判为链路延迟。

#### 1.4 前端持续追赶策略

- `liveEdgeLagSeconds > 1.0s`：硬跳到 `bufferedEnd - targetLatency`。
- `0.5s < liveEdgeLagSeconds <= 1.0s`：临时将 `playbackRate` 调整到 1.05～1.2，具体值按延迟分段。
- `liveEdgeLagSeconds < 0.4s`：恢复 `playbackRate = 1.0`。
- 增加最短动作间隔和阈值滞回，避免 seek/playbackRate 高频切换。
- 页面隐藏后重新可见、系统休眠恢复和网络短暂停顿时，主动重新计算 live edge。

#### 阶段回滚点

- binary 暖机门控、旧队列复制开关和前端 live-edge controller 均应可以独立关闭。
- 若 JMuxer 对外部 seek 行为不稳定，只回滚前端追赶，不影响服务端首帧门控。

### 阶段 2：建立实时背压与丢帧恢复语义

#### 2.1 缩小 binary queue

- 将当前约 32 包容量调整到约 5～10 包，最终数值依据阶段 0 的包大小、GOP 和发送抖动数据确定。
- 容量必须覆盖正常短抖动，但不能允许累计数秒历史画面。

#### 2.2 慢消费者时丢弃旧画面

- 队列拥塞时优先丢弃最旧 P 帧，保留最新实时位置。
- 不允许从丢帧后的任意 P 帧恢复解码；进入 `waiting_for_idr` 状态，直到下一组 config + IDR。
- 必要时向编码器请求关键帧，缩短恢复时间；若当前 MFT 封装不支持，应先使用既有 GOP 上限。
- 单独统计队列满、丢弃 P 帧、丢弃 GOP、等待 IDR 时长和恢复次数。

#### 2.3 明确队列所有权

- 推流队列采用低延迟、可丢帧策略。
- 录制队列不得复用该丢帧策略，继续保证帧完整和时间戳连续。
- 检查广播或多消费者实现，避免单个慢 WebSocket 客户端拖慢采集和其他消费者。

#### 阶段回滚点

- 保留原 FIFO 策略配置开关。
- 若丢帧恢复导致花屏或长时间等待，恢复原容量并保留新增指标继续采样。

### 阶段 3：治理 MFT 暖机根因

该阶段在阶段 1 稳定后实施，目标是减少门控前无效编码输出，而不是立即移除门控。

- 审查 `prime_encoder_with_black_frame` 的 input/output 调用顺序和 MFT stream 状态。
- 确认暖机后是否存在未排空 output sample、延迟帧、旧纹理引用或时间戳残留。
- 尝试在切换到真实采集帧前正确 drain、flush 或重新开始 stream，并验证各 Windows/MFT 实现兼容性。
- 建立编码器级测试，明确暖机完成后首个真实 input 对应的输出 NAL、时间戳和画面内容。
- 在真实机器上覆盖不同分辨率、DPI、GPU 和显示器配置。
- 只有在确认 MFT 不再输出暖机黑帧后，才评估简化 size-based/画面变化门控；协议层首 IDR 保证仍应保留。

### 阶段 4：内存占用治理

#### 4.1 先验证资源生命周期

- 连续执行至少 10 次录制启停，记录每轮开始、峰值、停止后 5秒和30秒的 working set/private bytes。
- 每轮核对 recording worker、encoder、线程、D3D11 texture、sample pool 和队列实例数量是否回到基线。
- 对停止流程增加阶段耗时和资源释放完成日志，区分“停止请求返回”与“底层资源已销毁”。

#### 4.2 清理可确认的冗余缓冲

- 优先移除 binary 模式下无消费者的旧 H.264 payload 复制。
- 检查停止录制后队列是否仍持有大容量 `Vec<u8>`、sample 或纹理引用。
- 对长期保留峰值容量的缓冲池设定合理上限；仅在 profile 证明收益后执行 shrink/recreate，避免频繁分配影响实时性。
- 检查录制结束后的 encoder drain、sink writer finalize 和 COM/D3D 资源释放顺序。

#### 4.3 评估共享编码结果

- 当前推流与录制已共享采集源；进一步评估两者编码参数一致时共享 NV12 转换或 H.264 编码结果。
- 必须先确认录制所需码率、GOP、PTS、水印和文件封装与推流参数兼容。
- 该项改动面大，应单独形成设计文档和性能对比，不与启动黑屏修复合并提交。

## 6. 预计涉及文件

### Autotest/Rust/Python

- `rust/windows-screen-sidecar/src/media/stream_worker.rs`
- `rust/windows-screen-sidecar/src/media/binary_output.rs`
- `rust/windows-screen-sidecar/src/win_recorder/h264_encoder.rs`
- `rust/windows-screen-sidecar/src/media/recording_worker.rs`
- `rust/windows-screen-sidecar/src/session.rs`
- `worker/screen/windows_sidecar.py`
- `worker/server.py`
- `tests/screen/test_push_streaming.py`

### 前端项目

- `D:\code\zq-platform\web\apps\web-ele\src\views\device-debug\hooks\useWebSocket.ts`
- `D:\code\zq-platform\web\apps\web-ele\src\views\device-debug\hooks\useMseDecoder.ts`

实际实施前应再次搜索调用关系，避免遗漏 JMuxer 初始化、页面销毁及重连逻辑所在文件。

## 7. 测试矩阵

### 7.1 启动与重连

- 冷启动首次推流。
- 同一进程内停止后重新推流。
- WebSocket 主动断开后重连。
- 页面刷新、切换路由后重新进入。
- 屏幕静止启动和屏幕正在变化时启动。
- 单屏、多屏、不同 DPI 和分辨率。

### 7.2 延迟与背压

- 10fps 正常网络连续运行 30 分钟。
- 人工阻塞 WebSocket send 0.5、1、3 秒后恢复。
- 浏览器标签页后台 10 秒后回到前台。
- CPU/GPU 高负载下观察队列深度、丢帧与恢复。
- 同时推流和录制，确认录制完整性不受推流丢帧策略影响。

### 7.3 内存与资源

- 仅推流运行 30 分钟。
- 推流加录制运行 30 分钟。
- 连续 10 次录制启停。
- 连续 10 次推流断开重连。
- 每轮记录 private bytes、working set、线程数和编码器/worker 数量。

### 7.4 回归

- 录制文件可播放，时长、PTS、水印连续。
- 断线后在一个 GOP 内恢复，无长期花屏。
- JPEG/MJPEG 和旧 stderr fallback 行为不变。
- sidecar 异常退出、会话取消和页面离开时资源可回收。

## 8. 验收指标

### 8.1 启动与延迟

- 浏览器不展示暖机黑色 IDR；首个展示画面必须是可信真实画面。
- WebSocket 建连后首个可播放真实画面阶段目标不超过 1.5 秒；当前首个真实 IDR 约在 1.9 秒，若阶段 1 仍受 GOP 限制，应记录为阶段 3 的优化项，不虚报达标。
- 稳态 `liveEdgeLagSeconds` 常态保持在 0.2～0.5 秒，P95 不超过 0.8 秒。
- 用户操作到画面反馈的肉眼延迟不超过 1 秒。
- 长时间运行时延迟不持续增长，sequence gap 和 queue depth 无异常单调增长。

### 8.2 内存

- 连续 10 次录制启停后，private bytes 不呈逐轮单调增长。
- 每轮录制停止 30 秒后，private bytes 回到首轮稳定基线的 `+10%` 或 `+50MB` 范围内，取较宽者作为初始标准。
- recording worker、encoder 和相关线程在停止后无残留实例。
- 推流加录制峰值先作为观测指标；完成 profile 前不设未经证实的绝对上限。

### 8.3 正确性

- 录制文件的帧序、PTS、时长和水印无回归。
- 丢帧后只从 config + IDR 恢复，浏览器无持续花屏。
- 所有新增追赶与背压动作均有可检索的原因和计数日志。

## 9. 建议实施与提交顺序

1. 单独提交阶段 0 可观测性，采集修改前基线。
2. 单独提交 binary 暖机门控及对应 Rust 测试。
3. 单独提交前端首次定位和持续 live-edge controller。
4. 验证延迟后，再提交旧队列冗余复制清理。
5. 单独提交 binary queue 背压、丢 P 帧和等待 IDR 机制。
6. 根据日志决定是否实施 MFT flush/drain 根因修复。
7. 根据 private bytes 和资源实例数据决定内存优化范围。

每一步均先在真实 Windows 推流环境验证，再进入下一步；不要把前端追赶、服务端丢帧和 MFT 生命周期重构合并为一个难以定位回归的提交。

## 10. 实施前待确认事项

- 当前 binary queue 的元素是一帧、一个 access unit，还是可能拆分的 NAL/字节块；丢帧策略必须在正确边界执行。
- RSM1 `prefix` 对 config、IDR 和普通媒体包的准确语义，以及前端当前是否依赖首包顺序。
- JMuxer 是否暴露 append 完成或 buffer 更新事件；若没有，应以原生 video/MSE 事件驱动追赶。
- 当前 MFT 能否显式请求关键帧，以及请求到实际 IDR 的最大等待时间。
- binary 模式下旧 `stream_queue` 是否存在隐藏消费者或测试依赖。
- 进程内存统计应选择 sidecar 进程、Python worker 进程还是两者分别统计，避免总量口径混淆。


## 11. 本次实施状态（2026-07-11）

### 已完成

- Rust RSM1 binary 首帧门控已接入：丢弃暖机 P 帧和小尺寸暖机 IDR，等待可信真实 IDR 后才向客户端输出。
- 暖机包中的配置数据只提取 SPS/PPS，不再缓存或转发黑色 IDR 整包。
- binary 队列容量调整为 8；队列溢出后清空旧包并重新等待关键帧，避免慢消费者累积历史画面。
- binary 模式停止向无消费者的旧 `stream_queue` 复制 H.264 payload；旧协议路径仍保留。
- 前端增加首次 live-edge 定位、延迟硬跳、临时加速播放和定时恢复播放逻辑。
- Rust 测试辅助 packet 已同步 RSM1 格式和真实关键帧尺寸门控要求，但按本次要求未运行测试。

### 编译验证

- Rust：`cargo check` 通过；仅有仓库已有的 unused/dead-code 警告。
- 前端：`pnpm build` 通过；仅有既有的浏览器兼容数据、模块 externalize 和 chunk warning。
- `cargo fmt -- --check` 仍会报告仓库其他文件的既有格式差异，本次未执行全仓库格式化。

### 待实机验证

- 首帧是否不再出现暖机黑屏，以及真实画面的首帧时间。
- `liveEdgeLagSeconds` 是否稳定降至 0.2～0.5 秒范围。
- 队列溢出、断线重连和丢帧后是否能在下一个 config + IDR 处恢复。
- 推流、推流加录制及多次启停时的工作集和 private bytes 曲线。

实机验证完成后，再根据日志决定是否进入阶段 3 的 MFT 暖机根因治理或阶段 4 的内存资源治理。
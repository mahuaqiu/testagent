# 鸿蒙 PC/移动端官方 HOScrcpy 集成实施计划

> 计划日期：2026-08-22，Worker 接入与真机验证更新：2026-08-23  
> 适用范围：`D:\code\autotest` Worker 的鸿蒙移动端和鸿蒙 PC 推流、截图、实时触摸、鼠标输入  
> 参考工程：`D:\code\developtools\HOScrcpy-python-main`、`D:\code\developtools\HOScrcpy-main`  
> 当前阶段：移动端官方会话已接入 Worker，并已完成 Java 会话、H.264 解码、最新帧截图、点击、滑动、HTTP/WebSocket 端到端回归及设备上线后台预热/600 秒空闲保活验证；鸿蒙移动和鸿蒙 PC 已开放 H.264 WebSocket 直通，鸿蒙 PC 真机验证待设备到位。

## 1. 目标与非目标

### 1.1 目标

1. 将鸿蒙实时画面采集从当前 `uitest agent/fport` 和 `snapshot_display` 轮询路径，逐步迁移到官方 `HosRemoteDevice` Java SDK 的 H.264 长连接会话。
2. 让鸿蒙移动端触摸和鸿蒙 PC 鼠标事件复用官方 Java API，减少高频 HDC shell 往返造成的延迟。
3. 复用同一个设备级会话提供推流、最新帧截图、失败截图和 OCR 图像，避免每次截图重新执行远程文件操作。
4. 保持现有 Worker 任务接口、ArtifactService、OCR 调用和 JPEG WebSocket 兼容，同时为鸿蒙开放 H.264 WebSocket 直通。
5. 在真实鸿蒙移动设备和真实鸿蒙 PC 上分别验证启动、首帧、输入、断线重连、设备拔出和资源回收。

### 1.2 非目标

1. 第一阶段不把 HOScrcpy GUI 搬进 Worker，也不引入 Swing、Java WebSocket 服务或 GUI 事件循环。
2. 不改变 `api.yaml` 的现有外部行为；鸿蒙 H.264 WebSocket 复用现有前端一字节帧类型前缀协议，不新增平台专属媒体协议。
3. 不修改 OCR 服务、用例工程、Windows sidecar、推流协议、安装升级流程和平台动作底层实现，除非后续验证证明是接入所必需。
4. 不承诺完全移除 HDC。官方 SDK 的设备发现、启动准备和部分控制能力仍可能依赖 HDC。

## 2. 总体技术路线

```text
                    控制面
  Worker ─────── HDC wrapper ─────── 设备发现/启动准备/应用/UI/文本/降级
     │
     │ 设备级会话
     ▼
  HarmonyOfficialSessionManager
     │
     ├── Java StreamBridge 子进程
     │     └── HosRemoteDevice
     │           ├── startCaptureScreen -> H.264
     │           ├── onTouchDown/Move/Up
     │           └── onMouseDown/Up/Move/Wheel
     │
     ├── H.264 reader/decoder
     ├── latest frame cache（JPEG/RGB/尺寸/时间戳/序号）
     └── 会话状态、重连、指标、资源回收
     │
     ├── ScreenManager/FrameSource -> JPEG WebSocket（兼容）
     ├── HarmonyOfficialFrameSource -> H.264 Binary WebSocket（直通）
     ├── HarmonyPlatformManager -> screenshot/input/action
     └── ArtifactService/OCR -> latest frame，必要时 HDC fallback
```

实时数据面应优先使用 Java SDK 会话；HDC 只承担控制面和降级路径。移动端和 PC 共用会话框架，但输入适配必须分开验证：移动端走触摸 API，PC 走鼠标 API。

## 3. 阶段划分与实施任务

### 阶段 0：资料冻结与环境准备（已完成基础项）

**目的**：在写 Worker 集成前，冻结真实依赖和接口事实。

任务：

1. 固定候选 JAR：优先检查 `hosScrcpy-1.0.15-beta.jar`；同时保留 POM 中 `1.0.14-beta` 作为兼容对照，不直接相信旧 API 文档。
2. 通过 JAR 真实签名确认 `HosRemoteDevice`、`HosRemoteConfig`、`ScreenCapCallback`、`Size` 的公开方法和常量。
3. 解包 JAR，确认 `libscrcpy_server*.so`、`uitest_agent_*.so` 的 CPU 架构、命名、版本选择规则和加载方式。
4. 记录 Java 运行时要求、classpath 依赖、native 库加载目录、HDC 参数和设备端系统版本要求。
5. 建立本地依赖清单和许可证清单，明确哪些文件可以随 Worker 发布，哪些只能由用户提供。

阶段产出：

- `docs/superpowers/specs/` 中的版本冻结记录。
- JAR API 签名、依赖、native 资产和许可证检查表。
- 一台移动端和一台 PC 真机的测试记录模板。

阶段通过条件：

- 能够使用最终候选 JAR 编译一个空的 Java 调用程序。
- 已明确 Java/JAR/native/HDC 的发布边界。
- 未确认的内容全部标记为“待真机验证”，不能作为实现假设。

已完成事实：JDK 17 可用；最终候选 JAR 为 `hosScrcpy-1.0.15-beta.jar`，SHA-256 为 `D2B8091FAF75CCDA27A1B5B7A8EEA2791A2D99C06A1ADE8B491EC4679FBDD649`；实际 `javap` 已确认视频、触摸、鼠标、滚轮、图片流和 `requestIDRFrame`，也确认没有公开按键 API。

### 阶段 1：独立 Java StreamBridge POC（移动端主链路已完成）

**目的**：不接 Worker，只验证官方 SDK 能否稳定完成低延迟视频和输入。

历史验证阶段曾使用独立 POC 目录；该验证材料现已合并到本文第 10 节，目录不再保留：

```text
本文第 10 节：已归档的移动真机验证结论
```

Bridge 只做四件事：

1. 从命令行接收 `udid`、HDC 路径、设备类别、分辨率、帧率、码率和端口。
2. 创建 `HosRemoteDevice`，调用 `startCaptureScreen`，在 `onReady` 后输出 READY 状态。
3. 将回调中的 H.264 数据以明确的长度前缀写到 stdout；所有日志写 stderr，禁止污染媒体流。
4. 从 stdin 读取版本化输入命令，调用触摸或鼠标 API；stdin EOF、异常和停止命令都要结束 Java 会话。

Bridge 协议第一版建议：

```text
stdout binary:
  magic HOS1
  version u8
  message_type u8
  payload_length u32
  payload

message_type:
  READY=1, H264=2, SIZE=3, ERROR=4, STATS=5, EOF=6

stdin text or binary command:
  TOUCH_DOWN x y
  TOUCH_MOVE x y
  TOUCH_UP x y
  MOUSE_DOWN button x y
  MOUSE_MOVE button-or-null x y
  MOUSE_UP button x y
  WHEEL_UP x y
  WHEEL_DOWN x y
  WHEEL_STOP x y
  REQUEST_IDR
  STOP
```

实现注意事项：

- `ByteBuffer` 必须按 `position` 到 `limit` 读取；不能无条件使用整个 backing array。
- 需要兼容 direct `ByteBuffer`，不能假设始终支持 `array()`。
- `requestIDRFrame()` 应在 READY 后调用，并记录请求到首个可解码 IDR 的耗时。
- Java 回调线程不能执行慢速磁盘、JPEG 编码或阻塞写入；必要时用有界队列交给输出线程。
- 队列满时丢弃旧 P 帧，保留 SPS/PPS/IDR；恢复时必须等待关键帧，不能把旧 P 帧直接发给客户端。
- `stopCaptureScreen`、Java 进程退出、HDC 子进程和设备端 server 必须在所有异常路径回收。

POC 验证内容：

| 类别 | 最少验证内容 |
|---|---|
| 连接 | USB、TCP（如果环境支持）、错误 UDID、设备拔出 |
| 视频 | READY、首帧时间、SPS/PPS/IDR、连续 30 秒、帧率、分辨率、码率 |
| 解码 | PyAV 或项目确定的解码器能够连续输出图像，不能只验证首帧 |
| 移动输入 | down、连续 move、up、点击、滑动、长按、坐标边界 |
| PC 输入 | 左/中/右键、移动、拖拽、滚轮、坐标边界 |
| 生命周期 | 重复 start/stop、异常回调、stdin EOF、进程杀死、设备重新连接 |
| 观测 | 启动耗时、READY、首帧、解码失败、重连次数、丢帧、队列深度 |

阶段通过条件：

- 移动端和 PC 必须分别通过视频和输入验证，不能相互推断。
- 连续运行 30 分钟无持续增长的进程、线程、队列或 HDC 资源。
- 断线后能够进入可观测的失败/重连状态，不能留下假在线 session。

移动端已完成的验收：

- `startCaptureScreen` READY 约 `1.8-2.2s`；H.264 回调含 SPS/PPS/IDR，PyAV 可连续解码。
- `onTouchDown/onTouchUp` 已实际打开系统设置；触摸发送前需等待输入 socket 建立。
- `latest_frame` 已从 H.264 解码结果保存为完整 `1260 x 2720` JPEG。
- `startImageScreenCapture` 当前为 READY 后零 `onData`，不能作为首期截图实现。

移动端仍待补：长按、纯滑动、坐标边界、旋转/锁屏、断线恢复、设备拔出和 30 分钟稳定性。

PC 仍待：真实鸿蒙 PC 的视频、左中右键、拖拽、滚轮、多屏和缩放验证。PC 未通过前，`official` 模式不能默认用于 PC。

### 阶段 2：Worker 设备级官方会话层（已完成首期）

**目的**：将 POC 封装成 Worker 内部能力，但暂时不改外部 WebSocket codec。

实际新增模块：

```text
worker/harmony/
├── official_session.py       # Python 会话生命周期和状态机
├── official_bridge.py        # Java 子进程启动、stdout/stderr、命令发送
├── official_decoder.py       # H.264 输入、解码、关键帧和最新帧
├── official_input.py         # 移动触摸/PC 鼠标统一适配
├── official_assets.py        # JAR/native/JRE/HDC 路径解析
└── official_metrics.py       # 启动、首帧、解码、重连、丢帧指标
```

`HarmonyOfficialSession` 建议维护以下状态：

```text
STOPPED -> STARTING -> READY -> STREAMING
                       │          │
                       └──────────┴─> RECONNECTING -> STARTING
                       任意状态 ─────> FAILED/STOPPING -> STOPPED
```

会话职责：

1. 按 `(platform, udid)` 建立唯一 session，避免同一设备启动多个 Java SDK 服务。
2. 提供 `start/stop/reconnect/is_ready/get_latest_frame/get_screen_size`。
3. 提供 `touch_down/move/up`、`mouse_down/move/up/wheel`，并根据平台拒绝不支持的输入类型。
4. 将 H.264 解码结果写入有界 latest-frame cache，而不是积压无限 FIFO。
5. 保存最后一帧时间、序号、分辨率、解码状态和错误原因，供 ScreenManager、OCR 和诊断使用。
6. 当所有 WebSocket 和任务引用释放时停止会话；设备下线时由 DeviceMonitor 或 Worker 统一关闭。

与现有代码的接入点：

| 现有模块 | 接入方式 |
|---|---|
| `worker/screen/frame_source.py` | 新增 `HarmonyOfficialFrameSource`，优先返回 session 的最新 JPEG，保留旧 `HarmonyFrameSource` 作为配置开关或 fallback |
| `worker/screen/manager.py` | 继续复用 ScreenManager；重点确认取帧语义改为“最新帧”，避免通用队列造成延迟 |
| `worker/server.py` | 第一阶段仍把鸿蒙 codec 归一为 JPEG；只切换 `_create_frame_source` 和会话生命周期 |
| `worker/platforms/harmony.py` | `take_screenshot/get_screenshot` 优先从官方 session 取最新帧，HDC 截图作为显式降级 |
| `worker/platforms/harmony.py` | 移动端 click/swipe/drag 走 touch session；PC move/drag/wheel 走 mouse session；按键和文本暂保留 HDC |
| `worker/device_monitor.py` | 设备下线、故障迁移和刷新时关闭对应 session，禁止设备拔出后残留 Java 进程 |
| `worker/worker.py` | Worker 停止时统一关闭 session manager；不要把 session 隐藏在单个 WebSocket 连接中 |
| `worker/runtime.py` | 如运行时已持有 `DeviceRegistry`、`ResourceScheduler`、`ArtifactService`，通过依赖注入使用，不新增全局事实状态 |
| `config/worker.yaml` | 增加官方 SDK 开关、JAR/JRE/Bridge 路径、启动超时、重连次数和 fallback 策略 |
| `tests/test_harmony_platform.py` | 增加 session mock、输入路由、截图优先级、断线状态和回收测试 |

第一阶段兼容策略：

- 默认仍使用 JPEG WebSocket，避免前端同步改造。
- `harmony_capture_mode` 建议支持 `legacy`、`official`、`auto`；初次发布可默认 `auto`，失败时回退旧实现。
- 生产日志必须标记实际使用的采集模式，不能只记录“鸿蒙推流已启动”。
- fallback 不能静默吞掉官方 session 的失败，应记录失败原因、回退次数和当前模式。

### 阶段 3：截图、OCR、失败附件和输入完善

**目的**：确保实时视频切换后，任务执行链路的截图语义不退化。

截图策略分三层：

1. `latest_frame`：默认实时截图、OCR 和失败截图使用，延迟最低。
2. `next_frame`：需要等待动作生效后的截图时使用，必须有超时和帧序号条件。
3. `hdc_snapshot`：显式高保真或官方会话不可用时使用，不作为实时循环默认路径。

实现决策已冻结：首期 `latest_frame` 来自 H.264/PyAV 解码结果，不依赖 `startImageScreenCapture`。后者作为后续设备兼容性能力保留，但不进入默认截图优先级。

需要明确的任务语义：

- `screenshot` 动作返回动作执行后获取到的最新帧，而不是动作前缓存。
- OCR action 在同一个 session 上读取一致的最新帧，必要时等待帧序号变化。
- 失败截图必须尝试 session latest frame；session 已断开时才调用 HDC fallback。
- 通过 ArtifactService 保存截图和元数据，不在平台模块内自行形成新的落盘规则。

输入适配规则：

- 移动端坐标以设备物理分辨率为基准，统一处理旋转、缩放和画面显示区域映射。
- PC 鼠标 `mouseType` 使用官方常量映射，移动事件使用 `null` 或明确的按键状态。
- move 事件采用最大频率、最小位移和有界发送队列，不能为了“平滑”让队列堆积。
- Java session 不支持的 key/text 能力继续走 HDC；后续若确认官方公共接口，再单独替换。

### 阶段 4：鸿蒙 H.264 Binary WebSocket（已实施）

**目的**：像 Windows sidecar 一样去掉鸿蒙实时推流链路中 H.264 到 JPEG 的重复编码。

当前实现已同时完成 Worker 与平台前端接入：

1. 复用现有 WebSocket 帧协议：`0x01` 参数集、`0x02` IDR、`0x03` P 帧，后接 Annex-B payload。
2. 直接转发 Java SDK 回调的 H.264 数据，不经过 Pillow/JPEG 重编码。
3. 新订阅请求 IDR；队列溢出后丢弃 P 帧并重新等待关键帧，避免向浏览器发送断链 P 帧。
4. 平台设备调试页对 `harmony_mobile`、`harmony_pc` 默认选择 H.264，MSE/JMuxer 复用现有实现。
5. 继续兼容 `codec=jpeg`；官方 H.264 会话不可用时不伪装成 H.264，连接明确失败并由前端按既有策略降级。

## 4. 配置和发布计划

当前配置已经落地：

```yaml
harmony_official:
  enabled: true
  mode: auto                 # legacy | official | auto
  jar_path: ""
  java_path: ""
  bridge_path: ""
  startup_timeout_seconds: 30
  reconnect_attempts: 3
  reconnect_backoff_seconds: 0.5
  prewarm_on_device_ready: true
  frame_queue_capacity: 2
  max_decode_width: 1600
  fallback_to_legacy: true
```

发布前必须完成：

1. Windows 打包时确认 JRE、JAR、native server 和 bridge 是否随包发布。
2. 明确 x86/x64/ARM64 主机与设备组合，不能只按主机架构选择 native 文件。
3. 记录 HOScrcpy SDK 的 LICENSE、依赖许可证和第三方二进制再分发要求。
4. 保留 legacy 模式和快速关闭开关，出现设备兼容性问题时可按配置回退。
5. 不把开发机绝对路径写入发布配置；路径解析应支持显式配置、项目内置目录和系统 PATH。

## 5. 测试计划

### 5.1 无真机测试

真机到位前可以完成：

- Java Bridge stdout/stderr framing 的单元测试。
- `ByteBuffer position/limit`、direct buffer 和空数据测试。
- H.264 Annex-B NAL 拆分、SPS/PPS/IDR 识别、关键帧门控测试。
- latest-frame cache 的并发读写、超时、序号和丢帧测试。
- session 状态机、重复启动/停止、进程异常退出和重连测试。
- 触摸/鼠标命令序列化、节流、坐标边界和平台路由测试。
- `HarmonyOfficialFrameSource` 与现有 ScreenManager 的 mock 集成测试。
- 截图优先级、HDC fallback、ArtifactService 调用和错误截图测试。
- legacy 模式回归测试，确保官方模式失败不会破坏原有路径。

### 5.2 移动真机测试

必须记录：

- 设备型号、系统版本、API 版本、CPU 架构、连接方式、HDC 版本。
- Java 进程启动到 READY、READY 到首个可解码 IDR、稳定帧率和端到端输入延迟。
- 竖屏、横屏、旋转、锁屏/唤醒、前后台切换和分辨率变化。
- 单击、长按、滑动、快速连续 move、边缘坐标和异常抬起。
- 截图、OCR、失败截图、设备拔出、重新连接和重复 session。

### 5.3 鸿蒙 PC 真机测试

必须单独验证：

- PC 桌面分辨率、缩放比例、多屏或虚拟屏行为。
- 左/中/右键、按下移动拖拽、普通移动、滚轮持续/停止。
- 窗口切换、锁屏唤醒、应用启动、鼠标坐标与视频画面坐标一致性。
- PC 设备是否支持 `startCaptureScreen`、官方鼠标 API 以及相同 native server。

### 5.4 验收指标

初始指标先作为目标值，真机测量后再冻结：

| 指标 | 目标 |
|---|---:|
| Java session READY | 30 秒内 |
| READY 到首个可解码 IDR | 2 秒内 |
| 稳定帧率 | 配置帧率的 80% 以上 |
| latest-frame 截图额外等待 | 200 ms 内 |
| 连续输入期间 HDC shell 调用 | 实时触摸/鼠标路径为 0 |
| 30 分钟稳定运行 | 无持续增长、无 session 泄漏 |
| 断线恢复 | 在配置重试次数内进入 READY 或明确 FAILED |

这些是工程目标，不是当前已经测得的结果；真实设备数据必须归档后再调整。

## 6. 真机到位前的调研清单

### A. 官方 SDK 和 JAR

- [ ] 从最终 JAR 导出真实公开 API 签名，确认方法名、参数类型、返回值和线程模型。
- [ ] 确认 `HosRemoteDevice` 是否有 `isOnline`、`requestIDRFrame` 以及公开的图片采集接口。
- [ ] 确认 1.0.15 beta 是否兼容当前目标 HarmonyOS 版本；若不兼容，寻找对应 SDK 版本。
- [ ] 确认 `HosRemoteConfig` 的分辨率、帧率、码率、I 帧间隔、HDC 路径和端口含义。
- [ ] 确认 H.264 回调一次对应一个 NAL、一个 access unit 还是任意 ByteBuffer 分片。
- [ ] 确认 direct ByteBuffer、回调线程并发和 stop 回调语义。

### B. native server 和设备端准备

- [ ] 解包 JAR，列出所有 `scrcpy_server`、`uitest_agent` 和架构后缀。
- [ ] 确认移动端和 PC 设备各自需要的设备端 native 文件。
- [ ] 确认 SDK 是否自动 push/启动 server，还是 Worker 必须先通过 HDC 准备。
- [ ] 确认设备端临时目录、服务端口、残留进程名称和停止命令。
- [ ] 确认不同系统版本首次启动是否需要授权、解锁或屏幕保持唤醒。

### C. 移动端输入

- [ ] 验证 touch 坐标是物理坐标、逻辑坐标还是旋转后的坐标。
- [ ] 验证长按、滑动、快速 move 和异常断开时是否自动补发 touch up。
- [ ] 验证是否只有单指，是否存在 contact/pointer id 的公共 API。
- [ ] 验证屏幕旋转前后 session 是否需要重启或重新获取尺寸。
- [ ] 对比 Java 官方触摸与 HDC `uitest uiInput` 的时延和可靠性。

### D. 鸿蒙 PC 输入

- [ ] 验证官方鼠标 API 在 PC 真机可用，而不是仅在移动设备兼容层存在。
- [ ] 确认 `mouseType` 常量的实际字符串值和中键/右键行为。
- [ ] 验证 `onMouseMove(null, x, y)` 是否代表普通移动，按键拖拽需传什么状态。
- [ ] 验证滚轮 Up/Down/Stop 的调用节奏和滚动方向。
- [ ] 确认系统缩放、多屏、窗口边界对坐标映射的影响。

### E. 按键和文本

- [ ] 用 JAR 反编译/签名检查确认是否存在公共 key event API；未经确认不依赖私有类。
- [ ] 盘点当前 `HarmonyPlatformManager.KEY_MAP` 与 HOScrcpy `KeyCodeUtil` 的差异。
- [ ] 验证 `uinput -K` 是否仍是移动端和 PC 通用的可靠 fallback。
- [ ] 验证 `uitest uiInput text` 对中文、特殊字符、换行、剪贴板和密码框的行为。
- [ ] 确认 Java session 复用 HDC shell 是否会与实时视频服务争用设备端资源。

### F. 解码、协议和前端

- [ ] 确认项目运行环境是否已有 PyAV/FFmpeg；没有时比较 PyAV、FFmpeg 子进程和 Java 解码转 JPEG 的方案。
- [ ] 使用参考工程样本检查 H.264 Annex-B 的 NAL 边界、SPS/PPS 发送顺序和 IDR 触发机制。
- [ ] 确认第一阶段 JPEG 输出是否足以满足 OCR 和截图质量要求。
- [ ] 研究 Windows RSM1 packet 的字段、关键帧门控和重连行为，提取可复用的抽象而不是复制 Windows 代码。
- [ ] 只有在第一阶段稳定后，才冻结鸿蒙 H.264 WebSocket 的外部协议和前端解码改造。

### G. 发布和合规

- [x] 开发/验证使用 JDK 17；运行 Bridge 只需 JRE 17 或兼容 Java Runtime，JDK 仅用于编译。
- [ ] 确认内置精简 JRE 17 的来源、许可证、补丁策略和 Windows 打包方式。
- [ ] 确认 JAR、native server、gRPC、Guava、FFmpeg 等依赖的许可证和再分发要求。
- [ ] 确认 Worker 安装包如何定位 JAR、JRE、HDC 和临时目录。
- [ ] 确认杀进程、设备拔出和 Worker 重启时不会误杀用户已有的 HDC 服务。
- [ ] 准备 legacy/official/auto 三种开关和失败回退策略。

## 7. 真机测试记录模板

拿到移动设备后，建议先填写以下信息，再开始集成测试：

```text
设备类别：harmony_mobile / harmony_pc
设备型号：
系统版本：
API 版本：
CPU 架构：
连接方式：USB / TCP
HDC 版本：
JAR 文件名和 SHA-256：
Java 版本：
屏幕物理分辨率：
系统缩放/旋转：

Java session 启动耗时：
READY 耗时：
首个 H.264 回调耗时：
首个可解码 IDR 耗时：
稳定帧率：
平均帧大小：
截图耗时：
点击延迟：
滑动延迟：
断线恢复耗时：

异常现象：
日志位置：
是否可回退 legacy：
```

## 8. 开始编码前的决策门

只有满足以下条件，才进入 Worker 代码实现：

1. 已有真实 JAR 的 API 签名和 native 资产清单。
2. 移动端至少能完成一轮视频、触摸和截图验证。
3. PC 端至少能确认视频和鼠标 API 的可用性；如果暂时没有 PC 真机，可以先实现移动端受配置保护的会话层，但 PC 必须保持 legacy/未启用，不能默认切换。
4. 已决定 Python 侧 H.264 解码路径，并能在无真机环境用 fixture 回归；鸿蒙 H.264 WebSocket 直通帧协议也已完成 fixture 回归。
5. 已确定 Java Bridge 的 framing、停止、错误和重连协议。
6. 已确定官方模式失败时的 legacy fallback 和配置开关。
7. 已确认不会破坏现有 `GET /worker_devices`、任务截图、失败附件和 JPEG WebSocket 行为；鸿蒙 H.264 由官方会话直接转发。

当前状态：移动端的第 1、2、4、5、6、7 项已完成实现，Java Bridge、H.264 WebSocket 帧拆分与 Worker 单元测试通过，移动端 Java 会话、H.264 解码、最新帧截图、点击、滑动、HTTP/WebSocket 端到端回归及设备上线预热/600 秒空闲保活已验证；启动失败时会在 Bridge 退出后立即结束首帧等待并快速重试。鸿蒙 PC 的官方视频和鼠标真机验收仍待设备到位；两端长稳和断线恢复仍需继续执行。

## 9. 推荐执行顺序

1. 完成移动端 Worker HTTP 和 JPEG WebSocket 端到端回归，确认任务动作和推流会话复用。
2. 补充移动端长按、旋转、断线恢复、设备拔出和 30 分钟稳定性数据。
3. 连接鸿蒙 PC 后，单独验证视频、鼠标、滚轮、多屏和缩放，不从移动端结果外推。
4. 在鸿蒙 PC 真机到位后完成 H.264 WebSocket 首帧、断线重连和连续推流验收，并补充两端长稳数据。

最终目标不是复制 HOScrcpy 项目，而是把官方 SDK 封装成 Worker 的一个可观测、可回收、可降级的设备级实时能力层。

## 10. 已归档的移动真机验证结论

以下内容合并自原 `research/harmony_hoscrcpy_poc/TEST_LOG.md`，作为当前计划的事实记录，避免再维护单独的调研目录。

### 10.1 验证环境

- 主机：Windows x64；项目 Python `3.12.10`；JDK `17+35-LTS-2724`。
- 官方 JAR：`hosScrcpy-1.0.15-beta.jar`。
- JAR SHA-256：`D2B8091FAF75CCDA27A1B5B7A8EEA2791A2D99C06A1ADE8B491EC4679FBDD649`。
- HDC：`tools/hdc/hdc.exe`；PyAV：`18.1.0`。
- 设备：`2LQ0224125000197`，HUAWEI Mate 60 Pro，`ALN-AL00`，`aarch64`，物理分辨率 `1260 x 2720`。
- 设备授权已完成，HDC 可稳定发现设备并执行 Shell、官方 SDK、推流和输入。

### 10.2 已验证事实

1. JDK 17 可以编译 Java Bridge；HOS1 framing、Annex-B H.264、`ByteBuffer position/limit` 和 direct buffer 兼容逻辑已验证。
2. `startCaptureScreen` 可以获得 H.264；真机样本含 SPS/PPS/IDR，PyAV 能连续解码，静止画面时回调减少属于官方行为。
3. 官方触摸 `onTouchDown/onTouchMove/onTouchUp` 已实际生效；`SDK_READY` 早于输入 socket 就绪，输入前需要约 `1.5s` 的就绪等待。
4. 同一 H.264 会话的最新解码帧可以保存为完整 `1260 x 2720` JPEG，适合作为截图、OCR 和失败附件来源。
5. `startImageScreenCapture` 在该设备/JAR 组合下虽能 READY，但没有稳定 `onData` JPEG 回调，不能作为首期截图后端。
6. JAR 没有公开按键 API；按键、文本、应用管理、UI dump 和显式 HDC 取证继续走 HDC 或回退路径。
7. 停止阶段偶发的 `UNAVAILABLE: Network closed for unknown reason` 与主动停止相邻出现，应按主动关闭状态处理，不能误判为运行中断线。

### 10.3 当前正式实现位置与未完成项

- Java Bridge：`worker/platforms/harmony_official/java/StreamBridge.java`。
- Python 会话、协议、解码和最新帧：`worker/platforms/harmony_official/`。
- 发布资产：`tools/harmony/`；Windows 打包通过 `scripts/build_windows.ps1` 注入 JRE 17。
- 回归测试：`tests/test_harmony_official.py`。
- 鸿蒙移动端已完成官方视频、最新帧截图、点击、滑动及 HTTP/WebSocket 回归；PC 尚无真机，必须单独完成视频、鼠标、滚轮、多屏和缩放验收。

# 鸿蒙 PC/移动推流、截图与输入官方方案集成分析

> 分析日期：2026-08-22，Worker 接入与真机验证更新：2026-08-23  
> 范围：官方 Java SDK 已接入 Worker；保持现有接口和 JPEG WebSocket 外部协议不变。  
> 参考工程：`D:\code\developtools\HOScrcpy-python-main`、`D:\code\developtools\HOScrcpy-main`

## 1. 结论摘要

建议后续以参考工程中的 `HosRemoteDevice` Java SDK 作为鸿蒙低延迟会话的底层实现，目标是：

1. 推流使用 `startCaptureScreen(ScreenCapCallback)`，设备端输出 H.264，Worker 维护每台设备一个长期 Java 会话。
2. 实时触摸使用同一会话的 `onTouchDown/onTouchMove/onTouchUp`，鸿蒙 PC 使用 `onMouseDown/onMouseUp/onMouseMove/onMouseWheel*`。
3. 截图优先从会话维护的“最新已解码帧”获取，不再为每次截图调用 `snapshot_display + file recv`。
4. HDC 仍保留为设备发现、设备选择、SDK 启动准备、应用管理、UI dump、文字输入和降级路径；不能把目标表述成整个鸿蒙链路完全不使用 HDC。
5. 对外 WebSocket 第一阶段可以继续输出现有 JPEG，内部先切换为官方 H.264 会话；第二阶段再像 Windows 一样开放鸿蒙 H.264 二进制推流，减少 H.264/JPEG 往返转换。

2026-08-23 已将该路径接入 Worker：官方 Java Bridge 的 H.264 经 PyAV 解码为每台设备的最新 JPEG，截图、移动端触摸与 PC 鼠标均优先使用同一设备级会话；官方失败时 `auto` 模式回退既有 HDC/agent 路径。鸿蒙移动真机已验证 `startCaptureScreen`、最新帧截图、点击和滑动；鸿蒙 PC 尚无真机验证，PC 只能按配置启用并须完成独立验收。

这个方向能直接解决当前两个主要延迟来源：

- 当前输入动作每次都通过 HDC shell 启动一次命令，触摸 move 会产生大量进程和 HDC 往返。
- 当前 `ScreenManager` 以 15 FPS 生产帧、通用队列最多积压 10 帧，而鸿蒙 WebSocket 只按 8 FPS 消费并且还会 JPEG 重编码，慢客户端可能看到约数百毫秒的旧帧。

## 2. 已检查的源码与版本事实

### 2.1 当前 Worker

- `worker/screen/frame_source.py:288-409` 的 `HarmonyFrameSource` 当前优先使用自维护的 `uitest daemon + agent.so + fport 8012` 帧流，失败后降级到 `snapshot_display` 轮询。
- `worker/platforms/harmony_capture.py` 会把 agent 推到设备、重启 `uitest start-daemon singleness`、创建 HDC `fport`，然后通过 Captures 协议接收 JPEG。该链路不是 HDC 截图轮询，但仍依赖 HDC 做部署和端口转发。
- `worker/screen/manager.py:226-325` 的通用捕获线程固定按 15 FPS 获取帧，`_frame_queue` 最大长度为 10；这套策略适合普通截图源，不适合低延迟实时镜像。
- `worker/server.py:1011-1037` 强制鸿蒙只使用 JPEG，默认鸿蒙帧率为 8 FPS；`worker/server.py:1200` 附近还会对鸿蒙 JPEG 做质量和长边缩放处理。
- `worker/platforms/harmony.py:282-327` 的任务截图直接调用 `HarmonyHdcWrapper.screenshot()`，底层是 `snapshot_display` 写远端文件后再 `file recv`。
- `worker/platforms/harmony.py:329-462` 的点击、滑动、鼠标移动、按键和文本输入均通过当前 HDC wrapper 的 shell 命令执行。`tap`/`swipe`/`send_key` 等最终都会产生一次 HDC shell 请求。
- `worker/discovery/harmony.py` 已能区分 `mobile` 和 `pc`，当前设备事实标识是 HDC UDID；但设备的连接地址没有完整进入 `HarmonyDeviceInfo`，后续 Java 会话需要补齐本地/远端 target 映射。

### 2.2 HOScrcpy Python 参考工程

- `hos_scrcpy/screen/capture.py:82-210` 提供 HDC `screenrecord` H.264、但它仍然是 HDC shell 管道，不能作为本次“降低 HDC 高频调用”的首选。
- `hos_scrcpy/screen/capture.py:260-326` 的 Java 模式启动 Java 子进程，读取带 4 字节大端长度前缀的帧，并返回 `FastTouchController`。
- `hos_scrcpy/bridge/StreamBridge.java:49-63` 通过 `HosRemoteConfig` 设置设备、HDC、缩放尺寸、帧率和码率，创建 `HosRemoteDevice`。
- `StreamBridge.java:86-150` 调用 `startCaptureScreen`，默认把 SDK 回调的 H.264 数据按 `[4 字节长度][H.264 Annex-B 数据]` 写到 stdout，并在 ready 后调用 `requestIDRFrame()`。
- `StreamBridge.java:350-375` 通过 stdin 读取 `D:x:y`、`M:x:y`、`U:x:y`，转调 Java SDK 的触摸方法。
- `hos_scrcpy/input/fast_touch.py:13-79` 的重点不是具体字符串格式，而是复用一条 Java 进程和一条 stdin 管道，避免每次 move 都执行 HDC shell。
- `hos_scrcpy/bridge/native_stream.py:303-388` 在启动 Java 前仍会清理旧的 HDC fport/设备端进程并预推送 scrcpy server，说明 Java SDK 不是“完全脱离 HDC”的实现。

### 2.3 HOScrcpy Java 工程与 JAR

当前参考目录中实际存在：

`D:\code\developtools\HOScrcpy-python-main\HOScrcpy-main\HOScrcpy-main\web_demo\libs\hosScrcpy-1.0.15-beta.jar`

该 JAR 的 Manifest 主类为 `com.huawei.hosscrcpy.Main`，包含：

- `com.huawei.hosscrcpy.api.HosRemoteDevice`
- `com.huawei.hosscrcpy.api.HosRemoteConfig`
- `com.huawei.hosscrcpy.api.ScreenCapCallback`
- `com.huawei.hosscrcpy.api.Size`
- `libscrcpy/libscrcpy_server*.z.so`
- 多个 `uitest_agent_*.so`，同时包含 x86 变体
- gRPC/协议及其他运行时类

版本存在不一致，后续不能只按文件名判断 API：

- `HOScrcpy-main/pom.xml` 仍引用 `hosscrcpy-1.0.14-beta.jar`。
- `web_demo` 使用的是 `hosScrcpy-1.0.15-beta.jar`。
- API 文档写过 `startImageCaptureScreen`，Java 示例实际调用 `startImageScreenCapture`。
- API 文档的版本表只列到 1.0.9 beta，和当前目录中的 1.0.15 beta 不同步。

因此第一步 POC 必须以实际要发布的 JAR 做编译/运行时签名校验，不能依赖文档或私有混淆类。

## 3. 与当前实现的性能差异

| 环节 | 当前 Worker | HOScrcpy 官方 Java 路径 | 评估 |
|---|---|---|---|
| 设备发现 | HDC `list targets` | Java SDK 仍需要 HDC/设备 target | 保留当前实现 |
| 视频采集 | uitest agent JPEG，失败后 snapshot 轮询 | `startCaptureScreen`，设备端 H.264 | 官方路径优先 |
| Worker 转换 | 鸿蒙 WebSocket 只允许 JPEG，质量/尺寸重编码 | 可直接转发 H.264；或内部解码成 JPEG | 分两阶段 |
| 触摸 | 每个动作调用 HDC shell | Java 长会话直接回调设备端触摸 | 官方路径明显更低延迟 |
| PC 鼠标 | HDC `uinput -M` | Java SDK 提供鼠标 down/up/move/wheel | 可以对齐官方 |
| 按键 | HDC `uitest uiInput keyEvent` | 公开 API 文档未列出等价的 `onKey*` | 暂不假设有无 HDC 的官方按键接口 |
| 文本输入 | HDC `uitest uiInput text/inputText` | 参考工程仍使用 HDC/uitest | 保留，后续再找官方公开接口 |
| 任务截图 | 每次 HDC 远端文件截图 | 可读 Java 会话最新帧 | 适合低延迟，但需定义“最新”语义 |

当前通用队列存在延迟放大问题：捕获端约 15 FPS，鸿蒙 WS 消费端约 8 FPS，队列长度 10。即使队列满时会丢弃旧帧，在队列达到上限前也可能先积压约 0.6 秒；另外 H.264/JPEG 解码、Pillow 重编码和 WebSocket 发送还会继续增加延迟。鸿蒙官方会话应采用“最新帧槽位/有界丢旧队列”，而不是复用现有 FIFO 截图队列。

真机 POC 已确认实际 JAR 的公开 `HosRemoteDevice` API 不含直接按键接口；参考工程的按键与文本输入仍通过 `uitest uiInput` 或 `uinput -K`。因此“按键改走官方 Java 会话”不属于首期目标。

## 4. 推荐集成架构

### 4.1 会话层

新增一个鸿蒙官方会话适配层，逻辑上类似 Windows sidecar 的长生命周期 session：

```text
Worker
  └─ HarmonyOfficialSessionManager
       └─ 每个 device_sn 一个 HarmonyOfficialSession
            ├─ Java StreamBridge 子进程
            ├─ HOScrcpy HosRemoteDevice
            ├─ H.264 帧接收线程
            ├─ 最新帧缓存 / 解码器
            ├─ 触摸、鼠标、按键发送通道
            └─ ready、重连、停止、设备离线清理
```

会话应由设备维度复用，而不是每个 WebSocket 或每个任务创建一次。否则启动 SDK、推送设备端库、建立视频服务本身会重新引入秒级启动成本，也会造成多个 Java 进程争用同一设备端 scrcpy 服务。

### 4.2 Java Bridge 进程协议

第一版可以沿用参考工程的成熟边界：

- stdout：`4 字节大端 payload 长度 + H.264 Annex-B payload`。
- stderr：READY、异常、重连、帧计数等诊断日志。
- stdin：沿用 `D/M/U`，并增加 PC 鼠标和生命周期命令。

更稳妥的生产协议建议在长度前加入固定 magic、版本、消息类型、序号和时间戳，至少区分 `H264`、`JPEG`、`READY` 和 `ERROR`。这样 Python 不会把 Java 日志、半包或错误输出误当成视频帧，也便于定位端到端延迟。

Bridge 必须处理 `ByteBuffer` 的 `position/limit`，不能无条件把整个 `array()` 当有效数据；如果 SDK 回调未来返回 direct buffer，应改用兼容性读取方式。

### 4.3 推流路径

推荐分两期：

**第一期，保持外部协议不变**

1. Java SDK 产生 H.264。
2. Worker 侧用 PyAV 或独立解码器维护最新 JPEG，仅在现有 WebSocket 边界转 JPEG。
3. WebSocket 继续按当前前端可识别的 JPEG 发送，先验证移动端和 PC 的设备兼容性、重连和资源回收。

这期已经能消除 HDC 高频截图和 HDC 高频输入，但仍保留一次 Worker 内部 H.264 到 JPEG 的转换。

**第二期，对齐 Windows 的 H.264 方式**

1. WebSocket 为鸿蒙开放 H.264 binary codec。
2. 直接转发 Java 回调的 Annex-B 数据，不经过 Pillow/JPEG。
3. 首帧必须发送 SPS/PPS；连接建立或重连后调用 `requestIDRFrame()`。
4. 前端沿用 Windows 的 H.264 解码协议或明确新增 Harmony H.264 帧协议。

第二期需要同步更新 `api.yaml`、前端解码约定和端到端验收，不能只在 Worker 内切换 codec。当前 `api.yaml` 明确记载鸿蒙只支持 JPEG，因此第一期不应偷偷改变对外语义。

### 4.4 截图路径

建议定义三种截图语义：

1. `latest_frame`：立即返回会话缓存的最近完整 JPEG，适合失败截图、OCR 和普通截图动作。
2. `next_frame`：等待 H.264 解码序号递增后返回下一帧，适合点击后截图，避免拿到点击前旧画面。
3. `hdc_snapshot`：保留为显式降级或高质量取证路径，不作为实时推流和普通截图默认路径。

如果第一期使用 H.264 内部解码，解码线程必须把最新 RGB/JPEG 和序号原子替换到缓存；不能从 `ScreenManager` 的 FIFO 队列取旧帧。移动真机 POC 已验证 `latest_frame` 可保存为完整 JPEG，因此这是首期截图、OCR 和失败附件的推荐主路径。

`startImageScreenCapture` 虽然是实际 JAR 的公开方法，且官方示例将其回调作为 JPEG 使用，但此真机在 READY 后立即输出 `Image channel finished` 且 5 秒内 `onData=0`。图片流不能作为当前默认截图后端，只能作为后续版本/设备兼容性调研项；HDC `snapshot_display` 继续作为会话不可用时的降级取证路径。

## 5. 触摸、鼠标、按键和文本输入可借鉴项

### 5.1 鸿蒙移动端触摸

直接移植官方 SDK 的三阶段接口：

- `onTouchDown(x, y)`
- `onTouchMove(x, y)`
- `onTouchUp(x, y)`

参考工程的 `FastTouchController` 已经验证了适合 Worker 的传输方式：down/up 立即发送，move 做节流和小位移过滤。Worker 的动作执行不应复用 GUI 的 20Hz 固定节流规则，而应由动作参数决定采样步数、持续时间和最大事件频率，避免自动化滑动轨迹被过度简化。

多指是一个需要补充的能力点：参考 `D/M/U` 协议没有携带 contact/pointer id，而当前 Worker 动作模型也没有多指动作契约。首期只对齐单指，不能宣称已经支持多指。

### 5.2 鸿蒙 PC 鼠标

官方 API 文档列出了：

- `onMouseDown(mouseType, x, y)`
- `onMouseUp(mouseType, x, y)`
- `onMouseMove(mouseType, x, y)`
- `onMouseWheelUp/Down/Stop(x, y)`

这比当前把右键映射成长按、把移动映射为 HDC `uinput -M` 更准确。鸿蒙 PC 的 `click/right_click/move/swipe/drag` 应分别映射到鼠标语义；触摸接口只作为移动端路径，不应让 PC 继续用 longClick 模拟右键。

### 5.3 按键

目前公开 API 文档和示例没有看到 `HosRemoteDevice.onKeyDown/onKeyUp` 这类与触摸同等级的直接接口。Java 主工程的键盘回调仍然拼装：

```text
uinput -K -d <keycode> -u <keycode>
```

所以可以借鉴并移植 `KeyCodeUtil` 的映射：方向键、Enter、Tab、Escape、字母、数字、符号、Shift 和 Ctrl+V；但不应依赖 JAR 内部混淆类或反射私有方法来伪造官方按键 API。首期建议：

- 触摸、鼠标走 Java 长会话。
- 按键继续走现有 HDC；如实测 HDC shell 延迟仍不可接受，再设计一个持久化 HDC shell 通道。
- 用实际 JAR `javap`/编译验证是否存在公开按键方法后，再决定是否升级为 Java 通道。

本次对 `hosScrcpy-1.0.15-beta.jar` 的实际 `javap` 结果已确认没有公开按键方法，因此该项结论从“待确认”更新为“首期保留 HDC”。

### 5.4 文本输入

参考 Python 工程仍通过 `uitest uiInput text` 支持中文和 Unicode。文本输入通常需要 IME/焦点语义，不能简单替换成逐字符 keycode。建议继续保留 HDC `uiInput text/inputText`，并把焦点点击与文本输入拆成独立可重试步骤。

## 6. HDC 依赖边界和“无需 HDC”的准确表述

根据参考工程的启动流程，HDC 仍承担以下工作：

- 查找/选择设备和连接 target。
- 清理旧的 fport、旧的 `screen_casting` 进程和设备端库。
- 预推送正确架构的 `libscrcpy_server*.so`。
- Java SDK 构造时传入 HDC 路径，SDK 可能自行完成设备端服务准备。
- 应用启动/停止、UI dump、文本输入、降级截图。

因此产品和接口说明建议写成：

> 鸿蒙实时推流、触摸和鼠标控制不再依赖高频 HDC shell；HDC 仍是设备管理和非实时系统操作的底层通道。

这与“完全不用 HDC”不同，但符合参考工程的真实实现，也更容易在不同 HarmonyOS 版本上稳定运行。

## 7. 需要重点验证的风险

| 风险 | 现象/原因 | 验证方式 |
|---|---|---|
| Java SDK 版本漂移 | POM、API 文档和 JAR 分别是 1.0.14/旧版/1.0.15 | 对最终 JAR 做编译签名检查和启动冒烟 |
| Harmony PC 兼容性 | 当前自维护 agent 文档明确说 PC 帧流尚未真机验证 | 移动、PC 分开验收，不因移动成功自动放行 PC |
| 设备端架构选择 | JAR 内有多个 scrcpy server 和 x86 agent | 按系统版本、CPU 架构建立选择矩阵，记录实际加载文件 |
| 资产缺失 | Python 参考代码引用 `hos_scrcpy/bridge/scrcpy_server`，当前工作目录清单中未见该目录 | 从实际 JAR 解包后再运行，不依赖源码目录是否完整 |
| 多设备并发 | 多个 Java 进程可能争用设备端 `libscreen_casting`/服务 | 每个 UDID 独立 session 和锁，禁止全局单例 device |
| 首帧和重连 | H.264 新连接可能没有 SPS/PPS 或 IDR | ready 后请求 IDR，验证断线重连首帧时间 |
| ByteBuffer 边界 | 回调 buffer 的有效区可能不是整个 backing array | 用 position/limit 复制有效区并做半包测试 |
| 帧延迟 | FIFO 队列积压会显示旧帧 | 使用最新帧槽位，记录 capture/decode/send 时间戳 |
| 进程回收 | Java、子进程、设备端 server 和 HDC 规则都可能残留 | 正常停止、异常退出、Worker 重启、设备拔出逐项验证 |
| 许可证/发布 | JAR 里带第三方 gRPC、Guava、native server 等组件，仓库 LICENSE 还保留模板字段 | 发布前清点 NOTICE、依赖许可证和二进制再分发要求 |

## 8. 推荐的后续实施顺序

### 阶段 0：独立 POC（已完成并归档）

用最终拟发布的 `hosScrcpy-1.0.15-beta.jar` 写一个最小 Java Bridge：

- 指定一个移动设备和一个鸿蒙 PC。
- 测量 `HosRemoteDevice` 创建、`onReady`、第一帧、稳定帧率和停止耗时。
- 验证 H.264 Annex-B 的 SPS/PPS/IDR、H.264 帧是否能被 Python/PyAV 解码。
- 验证移动端三阶段触摸、PC 鼠标五类事件。
- 验证同一视频会话缓存截图是否满足 OCR 画质和时效要求。
- 验证设备拔出、Java 进程异常、HDC 重启后的自动恢复。

**当前实测状态（移动端）**：

- 已完成：JDK 17 编译、HOS1 framing、H.264 SPS/PPS/IDR 识别、PyAV 真正解码、`latest_frame` JPEG、单击触摸。
- 已发现：SDK READY 早于 uitest 输入 socket 建立，首个输入前需等待约 1.5 秒或由 Bridge 显式上报输入就绪。
- 已发现：官方图片流在当前设备/JAR 组合零回调，不能替换视频帧截图。
- 未完成：长按/纯滑动边界、断线恢复/设备拔出、30 分钟稳定性、鸿蒙 PC 视频与鼠标真机验证。

### 阶段 1：接入 Worker 内部会话，不改外部协议（已完成）

- 新增官方 Java session manager 和设备级生命周期。
- WebSocket 仍发送 JPEG，但帧来自官方 H.264 会话的最新解码帧。
- `screenshot`、失败截图和 OCR 使用 session 的 `latest_frame/next_frame`。
- 触摸和 PC 鼠标切换到 Java session；按键、文本和系统操作先保留 HDC。
- 将现有自维护 uitest agent 流保留为配置开关或降级后端，不立即删除。

已实现模块包括 `worker/platforms/harmony_official/`、`HarmonyOfficialFrameSource` 和 `HarmonyPlatformManager` 输入路由。官方帧源使用最新帧语义，并在 `ScreenManager` 消费时排空通用 FIFO，避免重新积压旧画面。Windows 打包脚本可选内置 JRE 17+，发布配置会改为 `tools/jre/bin/java.exe`。

### 阶段 2：对齐 Windows 的 H.264 外部推流（后续）

- 扩展 Worker WebSocket codec 约定和 `api.yaml`。
- 前端复用或扩展 Windows H.264 解码协议。
- 去除鸿蒙实时路径的 JPEG 重编码和固定 8 FPS 限制。
- 增加首帧、丢帧、IDR、发送阻塞和端到端延迟指标。

### 阶段 3：能力收敛

- 根据真机结果决定是否移除 `uitest agent` 默认路径。
- 评估按键是否能使用官方公开 Java API；不能则把持久化 HDC shell 作为明确的系统输入后端。
- 决定截图是否始终取视频帧，或为高质量取证保留显式 HDC snapshot。
- 完成 Windows 打包、Java 运行时、JAR/native 资产和许可证清单。

发布时运行 Java Bridge 仅需要 **JRE 17 或兼容 Java 运行时**，不需要 JDK；JDK 只用于开发机编译 Bridge。推荐 Windows Worker 包内置经许可证审查的精简 JRE 17，并由配置优先使用内置 `java.exe`，外部 `java_path` 作为运维覆盖和开发回退。

## 9. 最终建议

已按该边界完成首期接入：Worker 保留自己的任务调度、ArtifactService、OCR 和 JPEG WebSocket 责任，只将官方 Java SDK 封装为设备级低延迟能力层。下一步是完成 HTTP/WebSocket 端到端回归和鸿蒙 PC 真机验收，再决定是否开放 H.264 WebSocket。

最值得优先移植的能力排序为：

1. `startCaptureScreen + requestIDRFrame`：低延迟视频主链路。
2. `onTouchDown/Move/Up`：移动端低延迟触摸。
3. `onMouseDown/Up/Move/Wheel`：鸿蒙 PC 正确鼠标语义。
4. 最新视频帧截图：消除普通截图的 HDC 文件往返。
5. `KeyCodeUtil` 映射：可以借鉴，但按键通道首期仍不承诺脱离 HDC。
6. Java Bridge 的 ready、重连、帧计数、进程回收和设备端 server 资产选择：这是生产稳定性所必需的配套，而不是可选 demo 代码。

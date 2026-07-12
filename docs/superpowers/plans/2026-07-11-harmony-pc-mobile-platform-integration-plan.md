# 鸿蒙 PC、鸿蒙移动设备及平台接入实施计划

> 状态：代码实现已完成，待鸿蒙真机验收
> 创建日期：2026-07-11  
> 涉及工程：`D:\code\autotest`、`D:\code\zq-platform`  
> 参考工程：`D:\code\hmdriver2-master\hmdriver2-master`  
> SDK：`D:\code\commandline-tools-windows-x64-6.1.0.850\command-line-tools`

## 当前实施状态

- 已完成 `harmony_mobile`、`harmony_pc` 两种正式设备类型的 Worker 和平台侧接入。
- 已完成 HDC 路径解析、Ready target 过滤、错误处理、有限重试、UDID 校验、设备监控、状态恢复、注册上报、资源池和基础调试链路。
- 鸿蒙移动和鸿蒙 PC 已分别使用 action 白名单；当前只声明 HDC 和 OCR/图像识别能够支撑的点击、双击、滑动/拖拽、输入、按键、截图、等待及应用启停等能力。
- 录屏、实时流、sidecar、鼠标右键/移动/滚轮、窗口控制、多点触控和未经真机验证的组合键暂不声明支持。
- 当前无可用鸿蒙真机，阶段 0、鸿蒙移动真机测试、鸿蒙 PC 真机测试和平台联调中的真机部分保留待验收；无设备单元测试已执行。

## 1. 背景

当前 `autotest` 已存在鸿蒙设备发现、HDC 命令封装、平台管理器、设备监控和部分 action 实现，但尚未形成可稳定交付的完整链路。

本次需要接入两类形态不同的设备：

1. 鸿蒙移动设备：通过 HDC UDID 区分设备，主要使用触屏、应用 Ability 和移动端屏幕控制能力。
2. 鸿蒙 PC：需要考虑鼠标、键盘、窗口、多显示器及桌面应用能力，不能直接等同于鸿蒙移动设备。

同时，`zq-platform` 当前只处理 Windows、Mac、Android 和 iOS，尚不能注册、展示、分配或调试鸿蒙设备。

## 2. 建设目标

- Worker 能稳定发现和连接 HDC 设备，并过滤非 Ready 设备。
- Worker 能可靠区分鸿蒙 PC 和鸿蒙移动设备。
- 鸿蒙移动设备具备与 Android/iOS 一致的设备校验、并发控制、状态维护和任务执行链路。
- 鸿蒙 PC 使用适合桌面形态的设备模型及输入能力，不复用不适用的移动端假设。
- Worker 只声明真实支持的鸿蒙 action，不出现前置验证通过、运行时缺少方法或静默成功。
- `zq-platform` 能注册、展示、筛选、分配、恢复和调试两类鸿蒙设备。
- 关键能力经过鸿蒙移动真机和鸿蒙 PC 真机验证。

## 3. 非目标

- 基于 UI hierarchy 的传统元素定位体系。
- 替换现有 OCR 服务或图像识别算法。
- 自研 HDC 服务或修改鸿蒙 SDK。
- 在没有真机验证的情况下宣称录屏、多点触控或窗口控制已可用。
- 为鸿蒙单独建设一套与现有任务协议完全不同的 API。

## 4. 已知问题基线

| 编号 | 优先级 | 问题 | 影响 |
|---|---|---|---|
| HARM-001 | P0 | `zq-platform` 注册接口忽略 `harmony` | 鸿蒙设备无法落库和使用 |
| HARM-002 | P0 | 未区分鸿蒙 PC 和鸿蒙移动设备 | 设备模型、输入方式和调度规则错误 |
| HARM-003 | P1 | Worker 任务校验只要求 Android/iOS 提供 `device_id` | 鸿蒙错误延迟到上下文创建阶段 |
| HARM-004 | P1 | 鸿蒙未进入 `ensure_device_service` 链路 | 应用启停等任务可能没有有效客户端 |
| HARM-005 | P1 | HDC 输出包含错误文本时可能仍按成功处理 | action 产生假成功结果 |
| HARM-006 | P1 | 基类 action 集无条件应用到鸿蒙 | 不支持的 action 可通过前置验证 |
| HARM-007 | P1 | 截图失败可能返回空字节 | OCR、图像识别和失败截图行为异常 |
| HARM-008 | P1 | `start_app` 固定使用 `EntryAbility` | 非默认 Ability 应用无法启动 |
| HARM-009 | P2 | 屏幕状态和分辨率依赖 `hidumper -s 10` | 系统版本及设备形态兼容性不足 |
| HARM-010 | P2 | 文本输入缺少可靠转义和中文验证 | 特殊字符及中文输入不稳定 |
| HARM-011 | P2 | `platforms.harmony.hdc_path` 未真正生效 | 配置与运行行为不一致 |
| HARM-012 | P2 | HDC 枚举没有连接状态及连接类型信息 | 无法过滤 Offline、Unauthorized 等设备 |

## 5. 设备模型设计

### 5.1 Worker 推荐模型

Worker 内部保留统一执行平台名称 `harmony`，增加设备形态字段：

```json
{
  "platform": "harmony",
  "device_category": "mobile",
  "udid": "device-serial",
  "connection_type": "usb",
  "connection_status": "ready",
  "name": "device-name",
  "model": "device-model",
  "sys_version": "system-version",
  "sdk_version": "api-version",
  "display_size": [1080, 2400],
  "capabilities": ["touch", "keyboard", "screenshot"]
}
```

`device_category` 初始支持：

- `mobile`：手机、平板及其他以触摸交互为主的鸿蒙设备。
- `pc`：鸿蒙 PC 或以桌面交互为主的设备。
- `unknown`：无法识别时使用，不自动进入可分配状态。

### 5.2 平台存储建议

推荐在 `zq-platform` 使用两个明确的 `device_type`：

- `harmony_mobile`
- `harmony_pc`

注册规则：

- `harmony_mobile`：按 `namespace + ip + device_type + device_sn` 唯一注册。
- `harmony_pc`：按 `namespace + ip + device_type` 注册，通常不依赖 `device_sn`。

最终字段命名必须在开始编码前完成一次设计评审，避免 Worker 和平台并行开发后协议不一致。

## 6. 分阶段实施计划

### 阶段 0：协议和真机能力确认

目标：修改业务代码前确认两类真实设备的 HDC 输出和基础能力。

- [ ] 准备一台鸿蒙移动设备和一台鸿蒙 PC。
- [ ] 记录 `hdc -v`、`hdc list targets`、`hdc list targets -v` 的完整输出。
- [ ] 验证 USB、UART、TCP 等实际使用的连接类型。
- [ ] 验证 Ready、Offline、Unauthorized、Connecting 状态表现。
- [ ] 收集两类设备的 `param get`、`hidumper` 和产品形态相关参数。
- [ ] 确定区分 PC/移动设备的可靠字段及降级策略。
- [ ] 验证 `uitest` 在鸿蒙 PC 上是否支持鼠标、右键、双击、滚轮和组合键。
- [ ] 形成 HDC 命令兼容矩阵，记录命令、设备形态、成功输出和失败输出。

验收标准：

- 有可重复执行的命令清单和真实输出样本。
- PC/移动分类规则有明确依据，不依赖设备名称猜测。
- 无法识别形态时默认不可分配，并提供清晰日志。

### 阶段 1：HDC 基础层加固

涉及文件：

- `worker/platforms/harmony_hdc.py`
- `worker/config.py`
- `config/worker.yaml`
- 新增 HDC 单元测试文件

任务：

- [ ] 让 `platforms.harmony.hdc_path` 真正传递到发现器和平台管理器。
- [ ] 保留仓库内 `tools/hdc/hdc.exe` 作为默认路径，并支持 SDK 路径和系统 PATH。
- [ ] 增加 HDC 版本检查及最低兼容版本日志。
- [ ] 解析详细 target 信息，提取 UDID、连接类型和连接状态。
- [ ] 仅将 Ready 设备放入在线列表。
- [ ] 将 `error:`、`[fail]`、超时、stderr 和非零退出码统一转换为失败。
- [ ] HDC 命令异常统一抛出 `HdcCommandError`，不得只记录日志后继续。
- [ ] 为 shell 参数、文件路径和文本输入增加可靠转义。
- [ ] 截图后校验远端命令、文件拉取、文件存在性、文件大小及图片格式。
- [ ] 使用稳定的 PowerManagerService 和 RenderService 命令获取屏幕状态与分辨率。
- [ ] 为命令超时设置按操作分类的默认值。
- [ ] 增加 HDC 服务短暂异常的有限重试及退避策略。

验收标准：

- 模拟错误文本但退出码为 0 时仍判定失败。
- Offline 或 Unauthorized 设备不会进入在线列表。
- 截图失败不会返回空字节伪装成功。
- 配置的 HDC 路径在日志和实际命令中一致。

### 阶段 2：设备发现及 PC/移动分类

涉及文件：

- `worker/discovery/harmony.py`
- `worker/device_monitor.py`
- `worker/reporter/models.py`
- `worker/reporter/client.py`
- `worker/worker.py`

任务：

- [ ] 扩展 `HarmonyDeviceInfo`，加入 `device_category`、连接状态、连接类型和 capabilities。
- [ ] 实现 PC/移动设备分类探测器。
- [ ] 对形态未知的设备保留可观测信息，但不加入可执行设备池。
- [ ] 设备监控保留完整设备信息，避免状态切换后退化成只有 UDID。
- [ ] 处理设备上线、离线、重连、UDID 变化及连接状态抖动。
- [ ] 确认同一设备通过不同连接方式出现时的去重规则。
- [ ] Worker `/worker_devices` 返回新的分类和能力字段。
- [ ] 平台上报协议增加两类鸿蒙设备列表。
- [ ] 更新配置窗口和安装器中的鸿蒙开关说明。

验收标准：

- 同一个 Worker 可同时展示鸿蒙 PC 和鸿蒙移动设备。
- 设备断开后在规定检测周期内变为离线或异常。
- 重连后恢复在线且不会生成重复设备。
- 形态未知设备不会被任务调度选中。

### 阶段 3：Worker 任务执行链路修复

- [ ] 鸿蒙移动任务强制要求 `device_id`。
- [ ] 鸿蒙移动任务执行前验证设备存在且 Ready。
- [ ] 将鸿蒙移动加入 `needs_device_service` 和 `ensure_device_service` 链路。
- [ ] 明确鸿蒙 PC 的 context 标识和并发锁键。
- [ ] 鸿蒙移动按 UDID 独立并发，同一设备串行执行。
- [ ] 鸿蒙 PC 默认同一台 PC 同时只执行一个任务。
- [ ] 修复纯 `stop_app` 任务无 context 或无 client 的问题。
- [ ] HDC 操作返回 `False` 时必须生成失败 ActionResult。
- [ ] HDC 连接失败时通知 DeviceMonitor 标记设备异常。
- [ ] 任务清理阶段不得错误清除其他设备的当前上下文。
- [ ] 增加设备断开期间任务失败和恢复测试。

验收标准：

- 缺少或传错 `device_id` 在前置校验阶段失败。
- HDC 命令失败时任务结果明确失败，并包含可定位错误。
- 两台鸿蒙移动设备可并行，同一台设备不可并行。
- 鸿蒙 PC 和鸿蒙移动设备之间的锁互不误伤。

### 阶段 4：action 能力白名单重构

- [ ] 调整 `BASE_SUPPORTED_ACTIONS` 的使用方式，允许平台过滤不适用 action。
- [ ] 鸿蒙移动和鸿蒙 PC 分别生成支持 action 集。
- [ ] action 能力可根据设备 capabilities 进一步收敛。
- [ ] 未支持 action 在任务前置验证阶段直接拒绝。
- [ ] 空实现不得返回成功，例如当前 `move()`。
- [ ] 为每个已声明 action 增加最少一个单元测试或真机用例。

鸿蒙移动第一阶段建议支持：

- [ ] `click`、`double_click`、`swipe`、`drag`
- [ ] `input`、`press`、`screenshot`、`wait`
- [ ] `start_app`、`stop_app`、`unlock_screen`
- [ ] OCR 点击、输入、等待、断言、取文本、存在性及位置动作
- [ ] 图像点击、等待、断言、存在性及位置动作

鸿蒙移动暂缓支持：

- [ ] `right_click`、`move`、`paste`、`pinch`
- [ ] `activate_window`、`start_recording`、`stop_recording`

鸿蒙 PC 第一阶段候选能力需以真机验证为准：

- [ ] 左键点击、右键点击、双击、鼠标移动、拖拽和滚轮
- [ ] 普通按键、组合键、文本输入和粘贴
- [ ] 截图及多显示器选择
- [ ] 窗口激活、窗口切换、桌面应用启动和停止
- [ ] OCR 和图像识别动作

验收标准：

- API 声明支持的 action 在对应设备形态上均有实现和验证记录。
- 不支持 action 不会进入执行阶段。
- 不允许通过空实现、空字节或忽略布尔返回值产生成功结果。

### 阶段 5：应用生命周期和输入能力完善

- [ ] `start_app` 支持显式传入 bundle 和 Ability。
- [ ] 研究并实现主 Ability 自动发现作为可选兜底。
- [ ] 明确 `stop_app`、强制停止、返回桌面之间的语义。
- [ ] 增加安装 HAP、卸载应用和清理应用数据能力。
- [ ] 增加获取当前前台应用能力。
- [ ] 移动端支持长按、亮屏、熄屏和锁屏状态查询。
- [ ] 文本输入覆盖中文、空格、单双引号、反斜杠、换行和 emoji。
- [ ] 鸿蒙 PC 支持常用组合键，并建立按键名称到 KeyCode 的映射测试。

建议扩展任务参数：

```json
{
  "action_type": "start_app",
  "value": "com.example.app",
  "ability": "MainAbility"
}
```

### 阶段 6：zq-platform 后端接入

涉及区域：

- `backend-fastapi/core/env_machine/api.py`
- `backend-fastapi/core/env_machine/schema.py`
- `backend-fastapi/core/env_machine/service.py`
- `backend-fastapi/core/env_machine/pool_manager.py`
- `backend-fastapi/core/env_machine/scheduler.py`
- 设备日志、监控、配置和升级相关服务

任务：

- [ ] 注册接口支持 `harmony_mobile` 和 `harmony_pc`。
- [ ] 鸿蒙移动按 device_sn 注册，鸿蒙 PC 按 IP 注册。
- [ ] 状态恢复和离线检测支持两类鸿蒙设备。
- [ ] 资源池正确处理鸿蒙移动 SN 占用和鸿蒙 PC 整机占用。
- [ ] API 调试请求为鸿蒙移动传递正确 `device_id`。
- [ ] 日志记录保留鸿蒙设备类型、SN 和形态信息。
- [ ] 命名空间筛选、批量启停和删除支持鸿蒙类型。
- [ ] 明确鸿蒙 PC Worker 的升级包类型和升级策略。
- [ ] 增加数据库迁移或初始化数据时的鸿蒙类型配置。
- [ ] 增加注册、状态恢复、资源分配和释放测试。

验收标准：

- Worker 上报后平台能生成正确数量的鸿蒙 PC 和移动设备记录。
- 同一 Worker 下多台鸿蒙移动设备分别展示和分配。
- 平台重启后能通过 `/worker_devices` 恢复鸿蒙在线状态。
- 鸿蒙设备离线后不会继续被资源池分配。

### 阶段 7：zq-platform 前端接入

- [ ] `DeviceType` 增加 `harmony_mobile` 和 `harmony_pc`。
- [ ] 设备类型下拉框、筛选器和显示名称增加鸿蒙类型。
- [ ] `isMobileDevice()` 和 `isDesktopDevice()` 正确分类。
- [ ] 标签前缀允许 `harmony`。
- [ ] 设备调试页路由和标题支持鸿蒙设备。
- [ ] action 控件根据后端返回的 capabilities 动态展示。
- [ ] 鸿蒙移动安装弹窗支持 `.hap`。
- [ ] 鸿蒙移动按键面板增加经过验证的 KeyCode。
- [ ] 鸿蒙 PC 提供鼠标、组合键、窗口和显示器控件。
- [ ] 对尚未实现的实时画面或 action 显示不可用状态，不发送请求。

### 阶段 8：实时画面、录屏及高级能力

该阶段不作为首轮接入阻塞项。

- [ ] Worker WebSocket 屏幕流增加鸿蒙移动实现。
- [ ] 评估鸿蒙 PC 是否复用桌面捕获方案或使用 HDC 截图流。
- [ ] 验证截图帧率、延迟、CPU、内存和 HDC 带宽。
- [ ] 增加录屏开始、停止、文件拉取和异常清理。
- [ ] 评估 UI hierarchy dump 是否作为 OCR 的辅助能力。
- [ ] 评估多点触控和 pinch 的真机支持。
- [ ] 评估远程 HDC Server 的配置和安全边界。

## 7. 测试计划

### 7.1 无设备单元测试

- [ ] HDC 路径优先级和配置覆盖。
- [ ] 普通及详细设备列表解析。
- [ ] Empty、Offline、Unauthorized、Connecting 输出处理。
- [ ] 错误文本但退出码为 0 的处理。
- [ ] 超时、进程启动失败和 stderr 错误处理。
- [ ] PC/移动分类参数解析。
- [ ] action 白名单和任务前置验证。
- [ ] 截图空文件及损坏文件检测。
- [ ] bundle、Ability、路径和输入文本转义。
- [ ] 平台注册和资源池分类逻辑。

### 7.2 鸿蒙移动真机测试

- [ ] USB 首次连接、授权、断开和重连。
- [ ] 多台设备同时连接和并行任务。
- [ ] 截图、点击、双击、滑动、拖拽、输入和按键。
- [ ] OCR 和图像识别完整链路。
- [ ] 启动指定 Ability、停止应用、安装和卸载 HAP。
- [ ] 亮屏、锁屏和密码解锁。
- [ ] 任务中途拔线及恢复。
- [ ] 中文和特殊字符输入。

### 7.3 鸿蒙 PC 真机测试

- [ ] HDC 枚举和设备形态识别。
- [ ] 单显示器和多显示器截图。
- [ ] 鼠标移动、左键、右键、双击、拖拽和滚轮。
- [ ] 普通按键、功能键和组合键。
- [ ] 窗口激活、切换及应用启停。
- [ ] OCR 和图像识别坐标映射。
- [ ] 分辨率或缩放变化后的坐标正确性。
- [ ] 并发任务锁和任务中断恢复。

### 7.4 平台联调测试

- [ ] Worker 注册两类鸿蒙设备。
- [ ] 列表、筛选、详情和状态变化。
- [ ] 申请、续用、释放和并发占用。
- [ ] 平台重启后的状态恢复。
- [ ] 设备离线后的资源池移除。
- [ ] 调试页 action 下发和错误展示。
- [ ] 安装 HAP 和实时画面能力展示。

## 8. 兼容性与回滚要求

- 新设备类型不得改变现有 Windows、Mac、Android 和 iOS 注册语义。
- Worker 上报新增字段应保持后向兼容，旧平台忽略字段时不能导致 Worker 启动失败。
- 平台上线鸿蒙支持前，应允许通过配置关闭鸿蒙设备上报。
- action 白名单调整不得误删其他平台已验证能力。
- 数据库迁移必须提供 downgrade，并避免修改现有设备记录。
- 真机能力不稳定时应关闭对应 capability，而不是保留假成功实现。

## 9. 里程碑建议

| 里程碑 | 范围 | 出口条件 |
|---|---|---|
| M1 | HDC 加固与设备分类 | 两类真机可稳定枚举并正确分类 |
| M2 | Worker 移动端闭环 | 鸿蒙移动基础 action 和任务调度通过真机测试 |
| M3 | 平台注册与资源池 | 两类鸿蒙设备可在平台展示、申请和释放 |
| M4 | 鸿蒙 PC 基础闭环 | PC 鼠标、键盘、截图和应用能力通过验证 |
| M5 | 调试及高级能力 | 实时画面、安装 HAP、录屏等按优先级交付 |

## 10. 开始实施前待确认事项

- [ ] 平台设备类型最终采用 `harmony_mobile`/`harmony_pc`，还是 `harmony + device_category`。
- [ ] 鸿蒙 PC 是否运行同一个 Windows Worker，还是作为 HDC 外接 target 管理。
- [ ] 鸿蒙 PC 的自动化输入首选 HDC uitest、系统原生接口还是其他驱动。
- [ ] 鸿蒙移动和平板是否统一归类为 `mobile`。
- [ ] 平台是否要求首期即支持实时调试画面。
- [ ] 首期是否要求安装 HAP、录屏和 UI hierarchy。
- [ ] Worker 安装包是否继续内置 HDC，还是使用外部 SDK 路径。
- [ ] HDC Server 是否存在远程连接场景及对应安全要求。

## 11. 推荐首期交付范围

1. HDC 稳定枚举、错误处理和 PC/移动分类。
2. 鸿蒙移动设备注册、状态管理和并发调度。
3. 截图、点击、双击、滑动、拖拽、输入、按键、应用启停和解锁。
4. 基于上述能力的 OCR 和图像识别 action。
5. `zq-platform` 的注册、列表、筛选、资源池和基础调试下发。
6. 鸿蒙 PC 先完成发现、注册和能力探测，交互 action 以真机验证结果决定是否进入首期。

录屏、实时画面、多点触控、UI hierarchy、远程 HDC Server 和完整鸿蒙 PC 桌面控制建议放入后续里程碑。

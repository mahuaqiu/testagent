# 各平台 Action、资源调度与鸿蒙能力优化实施计划

> 日期：2026-07-19  
> 状态：已实施（真机验收待执行）  
> 涉及工程：`D:\code\autotest`  
> 鸿蒙参考工程：`D:\code\hmdriver2-master\hmdriver2-master`  
> 鸿蒙 SDK：`D:\code\commandline-tools-windows-x64-6.1.0.850\command-line-tools`

## 一、目标

本次改造针对平台代码审查结果，重点解决以下问题：

1. `cmd_exec` 不再被 Web、Windows、Mac 或移动设备任务的资源锁阻塞。
2. 平台 Action 能力声明不再由基类无条件扩大，避免“不支持但前置校验通过”。
3. 统一单个 Action 的超时计算和执行控制。
4. 鸿蒙保持 HDC 直连和 OCR + 坐标操作路线，优化文本输入、按键映射和设备形态分类。
5. Android/iOS 基础操作在上下文无效时不再静默返回成功。

## 二、明确不在本次范围内

以下事项按当前部署约束保留，不在本次改造中处理：

- 不处理 Worker 内网环境下的未授权远程命令执行风险。
- 不改变 `cmd_exec background=true` 的语义：命令启动后脱离当前任务，不等待命令完成，也不要求任务取消时终止该命令。
- 不改 HTTP 请求入口的 Action 参数校验流程，不引入新的 API 鉴权或 API 版本。
- 不集成 `hmdriver2` 的 UI hierarchy、元素定位、uitest agent RPC、实时流、录屏、多点触控和高级手势能力。
- 不修改 OCR 服务、测试用例工程、推流协议、`win-control`、安装升级流程和平台前端。
- 不主动改变既有平台底层调用协议，只修复资源调度、能力声明、超时边界和明显错误处理。

## 三、核心设计

### 3.1 `cmd_exec` 独立资源域

当前 `TaskService.submit_async()` 在任务接受时按 `platform + device_id` 获取租约，导致宿主机命令会与设备自动化任务竞争同一资源。`background=true` 只影响命令启动后的等待行为，不能解决提交阶段的资源冲突。

本次采用独立资源域：

```text
普通 Action:
    platform:web
    platform:windows
    platform:mac
    device:android:{device_id}
    device:ios:{device_id}
    device:harmony_mobile:{device_id}
    device:harmony_pc:{device_id}

cmd_exec:
    host_command
```

规则：

- 只包含 `cmd_exec` 的任务获取 `host_command` 租约，不获取目标平台或设备租约。
- 包含 `cmd_exec` 和设备操作的混合任务首期直接拒绝，避免同一任务绕过设备互斥保护。
- `cmd_exec` 的资源键不依赖 `platform` 或 `device_id`，查询进程、杀进程等命令不会被设备任务阻塞。
- 继续使用当前“忙则拒绝”策略，不引入本地等待队列。
- `background=true` 启动后立即返回，后台进程继续保持当前脱离任务的业务语义。

### 3.2 Action 能力单一来源

现有 `BASE_SUPPORTED_ACTIONS` 同时包含 OCR、坐标、手势、录屏、窗口和宿主机命令，实际并非所有平台都支持。改造后采用“通用基础动作 + 平台显式动作”的方式：

- 基类只保留真正适合所有目标平台的 OCR、图像和基础坐标动作。
- `cmd_exec`、`pinch`、录屏、窗口控制等从基类移除。
- 每个平台显式声明自己的支持集合。
- 不改变本次之外的 Action 执行器，只修正前置能力判断和明显不支持的声明。

### 3.3 Action 超时控制

现有任务循环只检查任务总超时，`TaskConfig.action_timeout` 没有统一参与 Action 执行。改造后每个 Action 使用如下有效超时：

```text
effective_timeout = min(
    action 显式 timeout 或 task.config.action_timeout,
    task 剩余时间,
)
```

执行控制使用 `time.monotonic()` 和 `threading.Event`：

- Action 开始前检查任务取消和总超时。
- Action 执行前设置当前 Action 的 deadline。
- `wait`、OCR、Image 等等待循环使用可中断等待。
- 当前底层同步调用无法被线程强制终止时，至少保证返回后立即检查 deadline，并将结果标记为 timeout。
- `cmd_exec` 前台命令继续使用进程树超时终止；后台命令不纳入本次取消语义。

## 四、实施步骤

### 步骤 1：建立基线测试和能力契约

涉及文件：

- 新增 `tests/actions/test_platform_action_capabilities.py`
- 新增或修改 `tests/scheduling/test_resource_scheduler.py`
- 新增 `tests/task/test_cmd_exec_resource_scope.py`

工作内容：

1. 固化各平台当前声明的 Action 集合。
2. 增加测试：`cmd_exec` 与 Web、Windows、Android、iOS、Harmony 普通任务互不竞争。
3. 增加测试：同一时刻两个 `cmd_exec` 任务仍按 `host_command` 资源互斥。
4. 增加测试：不同移动设备的普通任务可以并行。
5. 增加测试：不支持的 Action 在 `_validate_task()` 阶段被拒绝。
6. 增加测试：平台声明的 Action 必须有对应 Registry 执行器或平台特有分发实现。

验收标准：

- 测试能够明确区分 `host_command`、平台资源和设备资源。
- 当前既有平台支持集合被记录，后续变更有明确差异。

### 步骤 2：为任务增加资源范围判断

涉及文件：

- `worker/scheduling/models.py`
- `worker/scheduling/scheduler.py`
- `worker/task/service.py`
- `worker/task/task.py`
- `worker/worker.py`

工作内容：

1. 在调度模型中增加任务资源范围解析函数，例如：

   ```python
   def task_resource_key(task: Task) -> str:
       ...
   ```

2. 纯 `cmd_exec` 任务解析为 `host_command`。
3. 含有 `cmd_exec` 与其他 Action 的混合任务返回明确失败，错误说明为 `cmd_exec cannot be mixed with device actions`。
4. 修改 `TaskService.submit_async()` 使用任务资源键获取租约。
5. 修改资源快照和忙碌查询，确保 `host_command` 不伪装成某个平台或设备忙碌。
6. 保持同步和异步任务共用同一资源申请逻辑。

验收标准：

- Web 长任务运行期间，独立 `cmd_exec` 查询任务可以立即提交并执行。
- Android、iOS、Harmony 设备任务运行期间，独立 `cmd_exec` 不返回 `Device is busy`。
- 两个宿主机命令同时提交时，第二个按 `host_command` 返回资源冲突。
- 混合任务不会绕过设备锁。

### 步骤 3：修正平台 Action 能力声明

涉及文件：

- `worker/platforms/base.py`
- `worker/platforms/web.py`
- `worker/platforms/windows.py`
- `worker/platforms/mac.py`
- `worker/platforms/android.py`
- `worker/platforms/ios.py`
- `worker/platforms/harmony.py`
- `worker/actions/registry.py`（仅在需要时补充能力契约）

建议的基类动作范围：

```text
ocr_click, ocr_input, ocr_wait, ocr_assert, ocr_get_text
ocr_double_click, ocr_exist, ocr_get_position
image_click, image_wait, image_assert, image_double_click
image_exist, image_get_position
click, double_click, swipe, drag, input, press, screenshot, wait
```

以下动作从基类移除，由平台单独声明：

```text
cmd_exec, right_click, move, paste, pinch
start_recording, stop_recording, activate_window
```

平台声明原则：

- Web：声明浏览器动作及已验证的系统级动作，不能仅因基类存在而支持 `pinch`、录屏或宿主机命令。
- Windows：声明窗口、右键、移动、粘贴、录屏和系统控制能力。
- Mac：只声明当前已实现并验证的桌面能力。
- Android/iOS：声明移动端点击、输入、滑动、按键、截图、OCR/Image 和已实现的 `pinch`。
- Harmony：继续使用移动和 PC 两套显式白名单，不加入宿主机命令、录屏、实时流、窗口控制和未实现的桌面操作。
- `cmd_exec` 由任务资源范围识别和独立命令执行能力控制，不再借用目标平台白名单。

验收标准：

- `get_supported_actions()` 不再返回平台明显不支持的动作。
- 不支持的动作不会创建 context 或调用底层平台方法。
- 已声明支持的动作在 Registry 或平台分发器中都有实现。

### 步骤 4：接入统一 Action 超时控制

涉及文件：

- `worker/actions/spec.py`
- `worker/task/task.py`
- `worker/worker.py`
- `worker/actions/coordinate.py`
- `worker/actions/ocr.py`
- `worker/actions/image.py`
- `worker/actions/window.py`
- `worker/actions/unlock.py`
- `worker/actions/cmd_exec.py`

工作内容：

1. 使用现有 `ExecutionControl` 提供的 `checkpoint()`、`remaining_seconds()` 和 `wait()`。
2. 在 `_execute_actions()` 中为每个 Action 创建独立 deadline。
3. Action 的显式 `timeout` 优先于 `TaskConfig.action_timeout`，但不能超过任务剩余时间。
4. 将 control 传递给等待型 Action；为兼容现有执行器，可先增加可选参数或执行上下文承载。
5. `wait` Action 使用 `control.wait()`，禁止直接 `time.sleep()`。
6. OCR、Image、窗口、解锁等待循环在截图、识别和休眠前后检查 control。
7. Action 超时返回明确的 timeout 结果，并保留已执行动作结果。
8. 任务总超时仍然是最终边界，不能因为 Action 自己的 timeout 更长而突破任务总超时。

验收标准：

- `action.timeout=1000` 的等待型 Action 不会突破任务剩余时间。
- `TaskConfig.action_timeout` 对未显式配置 Action timeout 的动作生效。
- Action timeout、task timeout 和取消状态可区分。
- `wait`、OCR wait、Image wait 和窗口等待可以响应取消事件。

### 步骤 5：优化鸿蒙文本输入，不引入完整 hmdriver2

涉及文件：

- `worker/platforms/harmony_hdc.py`
- `worker/platforms/harmony.py`
- `worker/actions/coordinate.py`
- `tests/test_harmony_platform.py`

设计原则：

- 继续使用 HDC 直连，不引入 `hmdriver2` 的 UI hierarchy 和 agent RPC。
- 以 OCR 定位和坐标操作为主。
- 只有在现有 HDC 命令无法稳定完成输入时，才评估引入 `hmdriver2` 的最小输入依赖；本次计划默认不引入。

工作内容：

1. 明确两种输入模式：
   - `input`：坐标点击目标输入框后输入。
   - `ocr_input`：OCR 找到目标文本后按 offset 计算坐标，再调用坐标输入。
2. 不再把普通输入无条件固定为 `(0, 0)`。
3. 保留“当前输入框已获得焦点”的 HDC 输入模式作为兼容路径，但在无焦点或命令失败时返回明确失败。
4. 对远端 Shell 参数统一进行安全引用，覆盖空格、单双引号、反斜杠、中文、换行和常见 Shell 特殊字符。
5. 让输入失败返回 `ActionStatus.FAILED`，不能因为 HDC 命令启动成功就返回成功。
6. 对移动设备和鸿蒙 PC 分别保留输入能力测试，避免把 PC 键盘输入和移动端触摸输入混为一谈。

真机验收用例：

- 中文文本。
- 英文和数字。
- 空格、单双引号、反斜杠。
- 多行文本。
- 输入框未聚焦。
- OCR 定位后输入。
- 坐标输入后输入。
- 输入过程中设备断开。

验收标准：

- 输入目标是实际输入框，不依赖屏幕左上角坐标。
- 复杂文本在目标鸿蒙版本上保持原样。
- HDC 失败、设备离线、输入框未聚焦时结果明确失败。

### 步骤 6：统一鸿蒙按键映射

涉及文件：

- `worker/platforms/harmony.py`
- `worker/platforms/harmony_hdc.py`
- 新增 `worker/platforms/harmony_keycodes.py`（推荐）
- `tests/test_harmony_platform.py`

工作内容：

1. 以 `hmdriver2/proto.py` 中的 `KeyCode` 定义为当前参考来源。
2. 保留一份共享映射，至少包含：

   ```text
   HOME=1
   BACK=2
   VOLUME_UP=16
   VOLUME_DOWN=17
   POWER=18
   ENTER=2054
   DPAD_UP=2012
   DPAD_DOWN=2013
   DPAD_LEFT=2014
   DPAD_RIGHT=2015
   DPAD_CENTER=2016
   ```

3. 删除 `HarmonyPlatformManager` 和 `HarmonyHdcWrapper` 中重复的本地映射。
4. `press()` 统一支持名称和数字 KeyCode。
5. 不声明未经验证的组合键；组合键继续明确返回不支持。
6. 增加每个保留按键的映射单元测试。

验收标准：

- 项目中只有一个鸿蒙 KeyCode 来源。
- 平台层和 HDC wrapper 对同一按键使用相同数字。
- 按键映射错误不会被静默忽略。

### 步骤 7：修复鸿蒙 PC/移动设备分类

涉及文件：

- `worker/discovery/harmony.py`
- `worker/platforms/harmony_hdc.py`
- `worker/device_monitor.py`
- `tests/test_harmony_platform.py`

工作内容：

1. 保留 `const.product.type`、`const.product.device_type`、`const.product.form`、`const.product.family` 等属性采集，但不只依赖任意字符串包含判断。
2. 建立标准化分类函数：

   ```python
   classify_harmony_device(properties: dict[str, str]) -> Literal["mobile", "pc", "unknown"]
   ```

3. 对属性值做统一清洗：大小写、空格、换行、冒号前缀和版本差异。
4. 按可靠性分层判断：明确设备类型字段优先，form/family 枚举其次，型号或产品名只能作为低置信度兜底。
5. 无法确定时返回 `unknown`，保留可观测信息，但不自动加入可执行设备池。
6. 保存原始属性和分类来源到设备元数据，便于真机问题诊断。
7. 分类变化时更新设备类别并迁移到对应 Harmony 设备集合，不丢失其他元数据。
8. 增加移动、PC、未知、大小写差异、字段缺失和异常输出测试。

验收标准：

- 同一设备分类结果稳定，不因某个属性为空而随机变化。
- `unknown` 设备可观测但不会被调度执行。
- `harmony_mobile` 与 `harmony_pc` 的资源键继续隔离。
- 分类变化不会丢失 UDID、型号、连接类型和 capabilities。

### 步骤 8：修复 Android/iOS 基础操作静默成功

涉及文件：

- `worker/platforms/android.py`
- `worker/platforms/ios.py`
- 相关 Action 执行器
- 新增 Android/iOS 平台单元测试

工作内容：

1. Android 的 `click`、`double_click`、`input_text`、`swipe`、`press` 等方法在 context/device 无效时抛出明确异常。
2. iOS 的对应基础操作执行相同的 context 校验。
3. 底层返回布尔值时必须检查，`False` 转换为失败结果或异常。
4. 空 context、设备断开、会话失效和底层调用异常分别覆盖测试。
5. 确认 `execute_action()` 的统一异常转换不会把失败转换为成功。
6. 不改变正常设备上的点击、输入、滑动和按键调用方式。

验收标准：

- 无效 context 的 Action 必须是 `FAILED`。
- 设备断开后不会生成成功 ActionResult。
- 底层 SDK 返回 `False` 时任务结果能定位到具体 Action。
- Android/iOS 行为保持对称。

### 步骤 9：回归、真机验证和文档更新

工作内容：

1. 运行调度、任务、Action、鸿蒙和平台基础测试。
2. 使用项目虚拟环境运行 Python 测试和脚本。
3. 使用鸿蒙 SDK 的 `hdc.exe` 验证版本、target 枚举、基础 shell、截图、点击、滑动、输入和按键。
4. 有鸿蒙真机时完成移动和 PC 分类矩阵。
5. 更新 Action/API 说明和鸿蒙能力说明，但不修改未纳入范围的安全和 HTTP 校验章节。

## 五、测试矩阵

### 5.1 资源调度

- Web 任务运行时提交 `cmd_exec`，成功执行。
- Android、iOS、Harmony 任务运行时提交 `cmd_exec`，成功执行。
- 两个 `cmd_exec` 并发提交，只有一个获得 `host_command`。
- 两台 Android 或两台 Harmony Mobile 设备任务仍可并行。
- 同一设备普通任务仍然互斥。
- 混合 `cmd_exec + click` 任务按明确规则拒绝。

### 5.2 超时和取消

- Action 显式 timeout 生效。
- 默认 `action_timeout` 生效。
- Action timeout 不超过任务剩余时间。
- `wait`、OCR wait、Image wait 能响应取消。
- 命令前台模式超时能结束进程树。
- `background=true` 只验证启动成功后立即返回，不验证任务取消终止后台进程。

### 5.3 平台能力

- 每个平台支持集合与实现一致。
- 录屏、窗口、pinch、宿主机命令不会被不支持的平台继承。
- 鸿蒙移动和鸿蒙 PC 的白名单保持独立。

### 5.4 鸿蒙

- SDK 路径解析到 `sdk/default/openharmony/toolchains/hdc.exe`。
- Ready、Offline、Unauthorized target 解析正确。
- 移动、PC、unknown 分类正确。
- 中文和特殊字符输入。
- 单一 KeyCode 映射。
- HDC 失败、空截图、损坏截图和设备断开均返回失败。

### 5.5 Android/iOS

- 无 context。
- context 失效。
- 设备断开。
- SDK 返回 False。
- 正常设备操作回归。

## 六、建议实施顺序

1. 先完成资源范围与 `cmd_exec` 独立资源域，解决当前实际阻塞问题。
2. 再完成平台 Action 能力集合重构，避免后续测试被错误白名单干扰。
3. 接入统一 Action timeout 和 ExecutionControl。
4. 修复 Android/iOS 静默成功。
5. 优化鸿蒙输入、KeyCode 和设备分类。
6. 完成单元测试、鸿蒙真机测试和回归。

## 七、完成标准

1. `cmd_exec` 不再获取目标平台或设备资源锁。
2. 普通任务资源互斥规则不变。
3. `background=true` 启动后不等待命令完成，且本次不新增取消终止语义。
4. 基类 Action 集合只包含真正通用的动作。
5. 每个 Action 的有效 timeout 受 Action timeout 和任务剩余时间共同约束。
6. 鸿蒙不引入完整 `hmdriver2`，但 OCR + 坐标输入、点击、滑动、按键和截图能力有明确实现和测试。
7. 鸿蒙 KeyCode 只有一份定义。
8. 鸿蒙 unknown 设备不会被调度执行。
9. Android/iOS 无效上下文和底层失败不会返回成功。
10. 未纳入范围的安全、HTTP 校验、后台命令生命周期和平台底层协议保持不变。

## 八、实施结果（2026-07-19）

已完成：

- 纯 `cmd_exec` 使用独立 `host:command` 资源键，不再占用平台或设备租约；混合平台动作任务明确拒绝。
- `background=true` 使用独立进程组启动，Worker 停止和任务取消均不回收该进程。
- `BASE_SUPPORTED_ACTIONS` 已收敛，右键、移动、粘贴、缩放、录屏、窗口激活和宿主命令由实际平台或 Worker 单独声明。
- 每个 Action 已接入 `ExecutionControl`，显式 Action timeout、任务默认 Action timeout 和任务剩余时间统一参与截止时间计算。
- `wait`、OCR wait、Image wait 和前台 `cmd_exec` 已接入可取消、可超时执行控制；不可强制中断的设备 SDK 调用在返回后立即检查截止时间，避免提前释放设备租约。
- 鸿蒙输入增加 `input_text_at()` 坐标接口，`input` 与 `ocr_input` 均把真实目标坐标传给 HDC，不再固定使用 `(0, 0)`。
- 鸿蒙 KeyCode 已统一到 `worker/platforms/harmony_keycodes.py`，方向键采用 `2012` 至 `2016`。
- 鸿蒙设备分类已改为按标准属性优先级进行规范值匹配，无法确认时返回 `unknown`。
- Android/iOS 点击、双击、输入、滑动和按键在无 context 时明确失败；iOS WDA 返回 `False` 时明确抛错。

验证结果：

- 调度、Action 校验、统一 timeout、平台能力、移动端失败传播和不依赖临时目录的鸿蒙测试均已通过。
- 一组集中测试结果为 `36 passed`，另一个定向测试结果为 `21 passed`。
- 依赖 pytest `tmp_path` 的测试仍受本机目录 ACL 影响，报 `PermissionError: [WinError 5]`；代码编译检查和 `git diff --check` 通过。
- 当前无已连接鸿蒙设备，SDK/HDC 真机输入、按键、截图和设备分类矩阵仍需在设备接入后验收。

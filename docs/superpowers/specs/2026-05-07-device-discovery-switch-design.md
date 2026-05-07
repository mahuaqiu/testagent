# 设备发现开关设计文档

## 概述

为 Worker 添加两个配置开关，控制是否执行 Android/iOS 设备发现及相关逻辑。大部分 Worker 不连接手机，默认关闭开关可避免不必要的设备检查开销。

## 需求

- 默认关闭（`false`），大部分 Worker 不执行手机设备发现
- 开关关闭后，跳过该平台所有相关逻辑：
  - 启动时不发现设备
  - 不初始化平台管理器
  - 不启动设备服务（WDA/uiautomator2）
  - 定时不检查设备
  - 不上报该平台设备信息
- GUI 设置界面显示两个 CheckBox，默认不勾选
- 安装界面显示两个 CheckBox，默认不勾选

## 配置文件变更

### worker.yaml 新增字段

```yaml
worker:
  id: null
  ip: null
  port: 8088
  namespace: meeting_public
  discover_android_devices: false  # 是否发现 Android 设备（关闭则跳过所有 Android 相关逻辑）
  discover_ios_devices: false      # 是否发现 iOS 设备（关闭则跳过所有 iOS 相关逻辑）
  device_check_interval: 300
  service_retry_count: 3
  service_retry_interval: 10
  action_step_delay: 0.5
```

### config.py 新增属性

```python
@dataclass
class WorkerConfig:
    ...
    discover_android_devices: bool = False
    discover_ios_devices: bool = False
```

## 设计决策

### `supported_platforms` 是否过滤？

**不过滤。** 开关关闭时：
- `supported_platforms` 保持操作系统返回的原始值（如 `["web", "windows", "android", "ios"]`）
- 设备列表返回空数组，外部系统通过空数组判断该平台无可用设备
- 区分"平台能力支持"和"当前无设备连接"两种状态

### 开关关闭时的任务请求处理

**保持现有逻辑。** 原因：
- 开关关闭时，上报注册的设备列表不含该平台设备
- 平台不会向该 Worker 下发该平台的任务
- 即使收到任务（如手动调用 API），失败于 "Platform manager not available" 也足够明确

### Worker 启动逻辑变更

### worker.py 修改点

1. **`_init_platform_managers()`** - 根据开关决定是否初始化平台管理器

```python
for platform in self.supported_platforms:
    # Android/iOS 平台根据开关跳过初始化
    if platform == "android" and not self.config.discover_android_devices:
        continue
    if platform == "ios" and not self.config.discover_ios_devices:
        continue
    
    platform_config = PlatformConfig.from_dict(...)
    ...
```

2. **`_discover_mobile_devices()`** - 根据开关决定是否发现设备

```python
def _discover_mobile_devices(self) -> None:
    # Android 设备
    if self.config.discover_android_devices and AndroidDiscoverer.check_adb_available():
        self.android_devices = AndroidDiscoverer.discover()
        logger.info(f"Found {len(self.android_devices)} Android devices")
    
    # iOS 设备
    if self.config.discover_ios_devices and iOSDiscoverer.check_tidevice_available():
        self.ios_devices = iOSDiscoverer.discover()
        logger.info(f"Found {len(self.ios_devices)} iOS devices")
```

3. **`_init_platform_managers()`** - DeviceMonitor 初始化条件

```python
# 只有当至少一个平台开启时才创建 DeviceMonitor
if self.config.discover_android_devices or self.config.discover_ios_devices:
    self.device_monitor = DeviceMonitor(self.config)
    self.device_monitor.set_platform_managers(
        android_manager=self.android_manager,
        ios_manager=self.ios_manager
    )
```

4. **`start()` 方法** - 启动移动端平台管理器时检查开关

```python
# 启动移动端平台管理器
for platform in ("android", "ios"):
    # 根据开关跳过
    if platform == "android" and not self.config.discover_android_devices:
        continue
    if platform == "ios" and not self.config.discover_ios_devices:
        continue
    
    manager = self.platform_managers.get(platform)
    if manager:
        manager.start()
```

## DeviceMonitor 逻辑变更

### device_monitor.py 修改点

1. **构造函数** - 存储配置引用

```python
def __init__(self, config: WorkerConfig):
    self.config = config
    self.discover_android = config.discover_android_devices
    self.discover_ios = config.discover_ios_devices
    ...
```

2. **`set_platform_managers()`** - 只设置开启的平台管理器

```python
def set_platform_managers(self, android_manager=None, ios_manager=None) -> None:
    if self.discover_android:
        self._android_manager = android_manager
    if self.discover_ios:
        self._ios_manager = ios_manager
```

3. **`_detect_physical_devices()`** - 根据开关跳过检测

```python
def _detect_physical_devices(self) -> None:
    # Android 设备检测
    if self._android_manager and self.discover_android:
        try:
            from worker.discovery.android import AndroidDiscoverer
            devices = AndroidDiscoverer.discover()
            ...
        except Exception as e:
            logger.error(f"Android device detection failed: {e}")
    
    # iOS 设备检测
    if self._ios_manager and self.discover_ios:
        try:
            from worker.discovery.ios import iOSDiscoverer
            devices = iOSDiscoverer.discover()
            ...
        except Exception as e:
            logger.error(f"iOS device detection failed: {e}")
```

4. **`_maintain_services()`** - 根据开关跳过维护

```python
def _maintain_services(self) -> None:
    if self.discover_android:
        for device in self._faulty_android_devices[:]:
            self._try_start_service("android", device["udid"])
    
    if self.discover_ios:
        for device in self._faulty_ios_devices[:]:
            self._try_start_service("ios", device["udid"])
    
    self._check_online_devices()
```

5. **`_check_online_devices()`** - 根据开关跳过检查

```python
def _check_online_devices(self) -> None:
    if self.discover_android and self._android_manager:
        # Android 设备检查逻辑
        ...
    
    if self.discover_ios and self._ios_manager:
        # iOS 设备检查逻辑
        ...
```

## GUI 设置界面变更

### settings_window.py 修改点

1. **窗口尺寸调整**

```python
self.setMinimumWidth(500)
self.setMinimumHeight(520)  # 从 420 调整到 520
```

2. **新增 CheckBox 控件** - 在"日志级别"下方

```python
row += 1  # 日志级别之后

# Android 设备发现
self.discover_android_checkbox = QCheckBox("发现 Android 设备")
self.discover_android_checkbox.setStyleSheet("font-size: 14px; color: #555555;")
grid.addWidget(self.discover_android_checkbox, row, 0)
row += 1

# iOS 设备发现
self.discover_ios_checkbox = QCheckBox("发现 iOS 设备")
self.discover_ios_checkbox.setStyleSheet("font-size: 14px; color: #555555;")
grid.addWidget(self.discover_ios_checkbox, row, 0)
row += 1
```

3. **`_load_values()`** - 加载配置值

```python
discover_android = worker.get("discover_android_devices", False)
self.discover_android_checkbox.setChecked(discover_android)

discover_ios = worker.get("discover_ios_devices", False)
self.discover_ios_checkbox.setChecked(discover_ios)
```

4. **`_on_save()`** - 保存配置值

```python
# 保存 discover_android_devices
original_content = self._update_yaml_value(
    original_content,
    "discover_android_devices",
    "true" if self.discover_android_checkbox.isChecked() else "false"
)

# 保存 discover_ios_devices
original_content = self._update_yaml_value(
    original_content,
    "discover_ios_devices",
    "true" if self.discover_ios_checkbox.isChecked() else "false"
)
```

## 安装界面变更

### installer.iss 修改点

1. **新增变量声明**

```pascal
var
  ...
  DiscoverAndroidCheckbox, DiscoverIosCheckbox: TNewCheckBox;
```

2. **`InitializeWizard` 新增 CheckBox** - 在 OCR 服务地址下方

```pascal
// OCR service edit 之后的 top 是 194，新增控件从 220 开始

// Android 设备发现
DiscoverAndroidCheckbox := TNewCheckBox.Create(ConfigPage);
DiscoverAndroidCheckbox.Parent := ConfigPage.Surface;
DiscoverAndroidCheckbox.Caption := '发现 Android 设备';
DiscoverAndroidCheckbox.Left := ScaleX(0);
DiscoverAndroidCheckbox.Top := ScaleY(220);
DiscoverAndroidCheckbox.Checked := False;

// iOS 设备发现
DiscoverIosCheckbox := TNewCheckBox.Create(ConfigPage);
DiscoverIosCheckbox.Parent := ConfigPage.Surface;
DiscoverIosCheckbox.Caption := '发现 iOS 设备';
DiscoverIosCheckbox.Left := ScaleX(180);
DiscoverIosCheckbox.Top := ScaleY(220);
DiscoverIosCheckbox.Checked := False;
```

3. **`CurStepChanged` 写入配置**

```pascal
// 替换 discover_android_devices
if Pos('discover_android_devices:', LineContent) > 0 then
begin
  if DiscoverAndroidCheckbox.Checked then
    LineContent := '  discover_android_devices: true'
  else
    LineContent := '  discover_android_devices: false';
end;

// 替换 discover_ios_devices
if Pos('discover_ios_devices:', LineContent) > 0 then
begin
  if DiscoverIosCheckbox.Checked then
    LineContent := '  discover_ios_devices: true'
  else
    LineContent := '  discover_ios_devices: false';
end;
```

## 影响范围

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| config/worker.yaml | 新增字段 | 添加两个配置开关 |
| worker/config.py | 新增属性 | WorkerConfig 新增两个布尔字段 |
| worker/worker.py | 逻辑变更 | 根据开关跳过平台初始化和设备发现 |
| worker/device_monitor.py | 逻辑变更 | 根据开关跳过设备检测和维护 |
| worker/settings_window.py | UI 变更 | 新增两个 CheckBox，调整窗口高度 |
| installer/installer.iss | UI 变更 | 新增两个 CheckBox，写入配置 |

## 测试要点

1. 默认关闭时，Worker 启动不发现任何移动设备
2. 开启 Android 开关，仅发现 Android 设备并初始化相关逻辑
3. 开启 iOS 开关，仅发现 iOS 设备并初始化相关逻辑
4. 两个开关都开启，正常执行所有移动设备逻辑
5. GUI 设置界面正确显示和保存配置
6. 安装界面正确显示配置选项并写入配置文件
7. 配置更新后重启 Worker，新配置生效
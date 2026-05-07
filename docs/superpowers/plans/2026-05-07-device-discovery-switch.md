# 设备发现开关实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 Worker 添加两个配置开关 `discover_android_devices` 和 `discover_ios_devices`，控制是否执行 Android/iOS 设备发现及相关逻辑。

**Architecture:** 在配置层新增两个布尔开关，Worker 启动时根据开关跳过平台初始化和设备发现，DeviceMonitor 定时检查时根据开关跳过检测维护，GUI 和安装界面提供用户配置入口。

**Tech Stack:** Python 3.10+, YAML, PyQt5, Inno Setup Pascal

---

## 文件结构

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `config/worker.yaml` | 新增字段 | 添加两个配置开关（默认 false） |
| `worker/config.py` | 新增属性 | WorkerConfig 新增两个布尔字段 |
| `worker/worker.py` | 逻辑变更 | 根据开关跳过平台初始化和设备发现 |
| `worker/device_monitor.py` | 逻辑变更 | 根据开关跳过设备检测和维护 |
| `worker/settings_window.py` | UI 变更 | 新增两个 CheckBox，调整窗口高度 |
| `installer/installer.iss` | UI 变更 | 新增两个 CheckBox，写入配置 |

---

### Task 1: 配置文件变更

**Files:**
- Modify: `config/worker.yaml:9-12`
- Modify: `worker/config.py`

- [ ] **Step 1: 在 worker.yaml 新增字段**

在 `config/worker.yaml` 的 `worker` 节点下，`namespace` 和 `device_check_interval` 之间添加：

```yaml
  discover_android_devices: false  # 是否发现 Android 设备（关闭则跳过所有 Android 相关逻辑）
  discover_ios_devices: false      # 是否发现 iOS 设备（关闭则跳过所有 iOS 相关逻辑）
```

位置：第 10 行之后（`namespace: meeting_public` 下方）

- [ ] **Step 2: 在 config.py 新增属性**

在 `worker/config.py` 的 `WorkerConfig` dataclass 中添加两个属性：

找到 `WorkerConfig` 类定义，在 `action_step_delay` 字段之后添加：

```python
    discover_android_devices: bool = False
    discover_ios_devices: bool = False
```

同时在 `_load_worker_config()` 函数中解析这两个字段：

```python
    discover_android_devices=worker_data.get("discover_android_devices", False),
    discover_ios_devices=worker_data.get("discover_ios_devices", False),
```

- [ ] **Step 3: 验证配置加载**

启动 Worker 验证配置加载正常：

```bash
python -m worker.main
```

预期日志中应无配置相关错误。

- [ ] **Step 4: Commit**

```bash
git add config/worker.yaml worker/config.py
git commit -m "feat(config): 新增设备发现开关配置项"
```

---

### Task 2: Worker 启动逻辑变更

**Files:**
- Modify: `worker/worker.py`

- [ ] **Step 1: 修改 `_init_platform_managers()` 跳过关闭的平台**

在 `worker/worker.py` 的 `_init_platform_managers()` 方法中，`for platform in self.supported_platforms:` 循环开始处添加开关检查：

找到第 316-317 行附近的循环：

```python
        for platform in self.supported_platforms:
```

修改为：

```python
        for platform in self.supported_platforms:
            # Android/iOS 平台根据开关跳过初始化
            if platform == "android" and not self.config.discover_android_devices:
                logger.info("Android platform skipped: discover_android_devices=false")
                continue
            if platform == "ios" and not self.config.discover_ios_devices:
                logger.info("iOS platform skipped: discover_ios_devices=false")
                continue

            platform_config = PlatformConfig.from_dict(
```

- [ ] **Step 2: 修改 `_discover_mobile_devices()` 根据开关发现设备**

在 `worker/worker.py` 的 `_discover_mobile_devices()` 方法中添加开关检查：

找到第 286-300 行：

```python
    def _discover_mobile_devices(self) -> None:
        """发现移动设备。"""
        # Android 设备
        if AndroidDiscoverer.check_adb_available():
            self.android_devices = AndroidDiscoverer.discover()
            logger.info(f"Found {len(self.android_devices)} Android devices")
        else:
            logger.warning("ADB not available, skipping Android device discovery")

        # iOS 设备
        if iOSDiscoverer.check_tidevice_available():
            self.ios_devices = iOSDiscoverer.discover()
            logger.info(f"Found {len(self.ios_devices)} iOS devices")
        else:
            logger.warning("libimobiledevice not available, skipping iOS device discovery")
```

修改为：

```python
    def _discover_mobile_devices(self) -> None:
        """发现移动设备。"""
        # Android 设备
        if self.config.discover_android_devices:
            if AndroidDiscoverer.check_adb_available():
                self.android_devices = AndroidDiscoverer.discover()
                logger.info(f"Found {len(self.android_devices)} Android devices")
            else:
                logger.warning("ADB not available, skipping Android device discovery")
        else:
            logger.info("Android device discovery disabled")

        # iOS 设备
        if self.config.discover_ios_devices:
            if iOSDiscoverer.check_tidevice_available():
                self.ios_devices = iOSDiscoverer.discover()
                logger.info(f"Found {len(self.ios_devices)} iOS devices")
            else:
                logger.warning("libimobiledevice not available, skipping iOS device discovery")
        else:
            logger.info("iOS device discovery disabled")
```

- [ ] **Step 3: 修改 DeviceMonitor 初始化条件**

在 `worker/worker.py` 的 `_init_platform_managers()` 方法末尾，DeviceMonitor 创建处添加条件检查：

找到第 346-356 行：

```python
        # 初始化设备监控
        if self.android_manager or self.ios_manager:
            self.device_monitor = DeviceMonitor(self.config)
            self.device_monitor.set_platform_managers(
                android_manager=self.android_manager,
                ios_manager=self.ios_manager
            )
            self.device_monitor.on_device_change = self._on_device_change
```

修改为：

```python
        # 初始化设备监控（只有当至少一个平台开启时才创建）
        if self.config.discover_android_devices or self.config.discover_ios_devices:
            self.device_monitor = DeviceMonitor(self.config)
            self.device_monitor.set_platform_managers(
                android_manager=self.android_manager,
                ios_manager=self.ios_manager
            )
            self.device_monitor.on_device_change = self._on_device_change
```

- [ ] **Step 4: 修改 `start()` 方法启动平台管理器时检查开关**

在 `worker/worker.py` 的 `start()` 方法中，移动端平台管理器启动处添加开关检查：

找到第 209-218 行：

```python
        # 4. 启动移动端平台管理器（必须在设备发现之前，否则 GoIOSClient 未初始化）
        for platform in ("android", "ios"):
            manager = self.platform_managers.get(platform)
            if manager:
                try:
                    manager.start()
```

修改为：

```python
        # 4. 启动移动端平台管理器（必须在设备发现之前，否则 GoIOSClient 未初始化）
        for platform in ("android", "ios"):
            # 根据开关跳过
            if platform == "android" and not self.config.discover_android_devices:
                continue
            if platform == "ios" and not self.config.discover_ios_devices:
                continue

            manager = self.platform_managers.get(platform)
            if manager:
                try:
                    manager.start()
```

- [ ] **Step 5: Commit**

```bash
git add worker/worker.py
git commit -m "feat(worker): 根据设备发现开关跳过平台初始化"
```

---

### Task 3: DeviceMonitor 逻辑变更

**Files:**
- Modify: `worker/device_monitor.py`

- [ ] **Step 1: 修改构造函数存储配置引用**

在 `worker/device_monitor.py` 的 `__init__` 方法中添加配置存储：

找到第 28-32 行：

```python
    def __init__(self, config: WorkerConfig):
        self.config = config
        self.check_interval = config.device_check_interval
        self.retry_count = config.service_retry_count
        self.retry_interval = config.service_retry_interval
```

修改为：

```python
    def __init__(self, config: WorkerConfig):
        self.config = config
        self.discover_android = config.discover_android_devices
        self.discover_ios = config.discover_ios_devices
        self.check_interval = config.device_check_interval
        self.retry_count = config.service_retry_count
        self.retry_interval = config.service_retry_interval
```

- [ ] **Step 2: 修改 `set_platform_managers()` 只设置开启的平台**

在 `worker/device_monitor.py` 的 `set_platform_managers()` 方法中添加开关检查：

找到第 51-54 行：

```python
    def set_platform_managers(self, android_manager=None, ios_manager=None) -> None:
        """设置平台管理器引用。"""
        self._android_manager = android_manager
        self._ios_manager = ios_manager
```

修改为：

```python
    def set_platform_managers(self, android_manager=None, ios_manager=None) -> None:
        """设置平台管理器引用。"""
        if self.discover_android:
            self._android_manager = android_manager
        if self.discover_ios:
            self._ios_manager = ios_manager
```

- [ ] **Step 3: 修改 `_detect_physical_devices()` 根据开关跳过检测**

在 `worker/device_monitor.py` 的 `_detect_physical_devices()` 方法中添加开关检查：

找到第 99-119 行的 Android 设备检测块，在 `if self._android_manager:` 后添加开关检查：

```python
        # Android 设备检测
        if self._android_manager and self.discover_android:
```

找到第 121-140 行的 iOS 设备检测块，在 `if self._ios_manager:` 后添加开关检查：

```python
        # iOS 设备检测
        if self._ios_manager and self.discover_ios:
```

- [ ] **Step 4: 修改 `_maintain_services()` 根据开关跳过维护**

在 `worker/device_monitor.py` 的 `_maintain_services()` 方法中添加开关检查：

找到第 246-252 行：

```python
    def _maintain_services(self) -> None:
        """维护服务状态，检查异常设备恢复。"""
        for device in self._faulty_android_devices[:]:
            self._try_start_service("android", device["udid"])

        for device in self._faulty_ios_devices[:]:
            self._try_start_service("ios", device["udid"])

        self._check_online_devices()
```

修改为：

```python
    def _maintain_services(self) -> None:
        """维护服务状态，检查异常设备恢复。"""
        if self.discover_android:
            for device in self._faulty_android_devices[:]:
                self._try_start_service("android", device["udid"])

        if self.discover_ios:
            for device in self._faulty_ios_devices[:]:
                self._try_start_service("ios", device["udid"])

        self._check_online_devices()
```

- [ ] **Step 5: 修改 `_check_online_devices()` 根据开关跳过检查**

在 `worker/device_monitor.py` 的 `_check_online_devices()` 方法中添加开关检查：

找到第 256-276 行的物理检测部分：

```python
        if self._android_manager:
            try:
                from worker.discovery.android import AndroidDiscoverer
                devices = AndroidDiscoverer.discover()
                physical_android_udids = {d.udid for d in devices}
```

修改为：

```python
        if self._android_manager and self.discover_android:
            try:
                from worker.discovery.android import AndroidDiscoverer
                devices = AndroidDiscoverer.discover()
                physical_android_udids = {d.udid for d in devices}
```

找到第 270-276 行的 iOS 部分：

```python
        if self._ios_manager:
            try:
                from worker.discovery.ios import iOSDiscoverer
                physical_ios_udids = set(iOSDiscoverer.list_devices())
```

修改为：

```python
        if self._ios_manager and self.discover_ios:
            try:
                from worker.discovery.ios import iOSDiscoverer
                physical_ios_udids = set(iOSDiscoverer.list_devices())
```

找到第 278-284 行的 Android 设备检查：

```python
        # 检查 Android 设备
        if self._android_manager:
            for device in self._android_devices[:]:
```

修改为：

```python
        # 检查 Android 设备
        if self._android_manager and self.discover_android:
            for device in self._android_devices[:]:
```

找到第 287-293 行的 iOS 设备检查：

```python
        # 检查 iOS 设备
        if self._ios_manager:
            for device in self._ios_devices[:]:
```

修改为：

```python
        # 检查 iOS 设备
        if self._ios_manager and self.discover_ios:
            for device in self._ios_devices[:]:
```

- [ ] **Step 6: Commit**

```bash
git add worker/device_monitor.py
git commit -m "feat(monitor): 根据设备发现开关跳过设备检测和维护"
```

---

### Task 4: GUI 设置界面变更

**Files:**
- Modify: `worker/settings_window.py`

- [ ] **Step 1: 调整窗口尺寸**

在 `worker/settings_window.py` 的 `_setup_ui()` 方法中修改窗口高度：

找到第 118-119 行：

```python
        self.setMinimumWidth(500)
        self.setMinimumHeight(420)
```

修改为：

```python
        self.setMinimumWidth(500)
        self.setMinimumHeight(520)
```

- [ ] **Step 2: 新增两个 CheckBox 控件**

在 `worker/settings_window.py` 的 `_setup_ui()` 方法中，日志级别 ComboBox 之后添加两个 CheckBox：

需要在导入部分添加 `QCheckBox`：

找到第 14-22 行的导入：

```python
from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QComboBox,
    QPushButton,
    QGridLayout,
    QFrame,
    QWidget,
    QMessageBox,
)
```

修改为：

```python
from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QComboBox,
    QPushButton,
    QCheckBox,
    QGridLayout,
    QFrame,
    QWidget,
    QMessageBox,
)
```

在 `_setup_ui()` 方法中，找到日志级别之后的 `row` 变量（约第 195-198 行）：

```python
        # 日志级别
        grid.addWidget(self._create_label("日志级别"), row, 0)
        self.log_level_combo = QComboBox()
        self.log_level_combo.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
        grid.addWidget(self.log_level_combo, row, 1)
```

在之后添加：

```python
        row += 1

        # Android 设备发现
        self.discover_android_checkbox = QCheckBox("发现 Android 设备")
        self.discover_android_checkbox.setStyleSheet("font-size: 14px; color: #555555;")
        grid.addWidget(self.discover_android_checkbox, row, 0)
        row += 1

        # iOS 设备发现
        self.discover_ios_checkbox = QCheckBox("发现 iOS 设备")
        self.discover_ios_checkbox.setStyleSheet("font-size: 14px; color: #555555;")
        grid.addWidget(self.discover_ios_checkbox, row, 0)
```

- [ ] **Step 3: 修改 `_load_values()` 加载配置**

在 `worker/settings_window.py` 的 `_load_values()` 方法末尾添加配置加载：

找到第 306-331 行：

```python
    def _load_values(self):
        """从配置加载值。"""
        worker = self._config.get("worker", {})
        external = self._config.get("external_services", {})
        logging_cfg = self._config.get("logging", {})

        ip = worker.get("ip")
        if ip:
            self.ip_input.setText(ip)

        port = worker.get("port", 8088)
        self.port_input.setText(str(port))

        namespace = worker.get("namespace", "meeting_public")
        self.namespace_input.setText(namespace)

        platform_api = external.get("platform_api", "")
        self.platform_api_input.setText(platform_api)

        ocr_service = external.get("ocr_service", "")
        self.ocr_service_input.setText(ocr_service)

        log_level = logging_cfg.get("level", "INFO")
        index = self.log_level_combo.findText(log_level)
        if index >= 0:
            self.log_level_combo.setCurrentIndex(index)
```

在末尾添加：

```python
        # 设备发现开关
        discover_android = worker.get("discover_android_devices", False)
        self.discover_android_checkbox.setChecked(discover_android)

        discover_ios = worker.get("discover_ios_devices", False)
        self.discover_ios_checkbox.setChecked(discover_ios)
```

- [ ] **Step 4: 修改 `_on_save()` 保存配置**

在 `worker/settings_window.py` 的 `_on_save()` 方法中，保存配置部分添加开关保存：

找到第 407-414 行（在 `if original_content:` 块内）：

```python
            # Update specific fields using string replacement (preserve comments)
            original_content = self._update_yaml_value(original_content, "ip", self.ip_input.text().strip() or "null")
            original_content = self._update_yaml_value(original_content, "port", self.port_input.text().strip())
            original_content = self._update_yaml_value(original_content, "namespace", self.namespace_input.text().strip())
            original_content = self._update_yaml_value(original_content, "platform_api", self.platform_api_input.text().strip())
            original_content = self._update_yaml_value(original_content, "ocr_service", self.ocr_service_input.text().strip())
            original_content = self._update_yaml_value(original_content, "level", self.log_level_combo.currentText())
```

修改为：

```python
            # Update specific fields using string replacement (preserve comments)
            original_content = self._update_yaml_value(original_content, "ip", self.ip_input.text().strip() or "null")
            original_content = self._update_yaml_value(original_content, "port", self.port_input.text().strip())
            original_content = self._update_yaml_value(original_content, "namespace", self.namespace_input.text().strip())
            original_content = self._update_yaml_value(original_content, "platform_api", self.platform_api_input.text().strip())
            original_content = self._update_yaml_value(original_content, "ocr_service", self.ocr_service_input.text().strip())
            original_content = self._update_yaml_value(original_content, "level", self.log_level_combo.currentText())
            original_content = self._update_yaml_value(original_content, "discover_android_devices", "true" if self.discover_android_checkbox.isChecked() else "false")
            original_content = self._update_yaml_value(original_content, "discover_ios_devices", "true" if self.discover_ios_checkbox.isChecked() else "false")
```

同时在 fallback 分支（`else:` 块内，约第 427-447 行）也添加：

```python
            self._config.setdefault("worker", {})
            self._config["worker"]["ip"] = self.ip_input.text().strip() or None
            self._config["worker"]["port"] = int(self.port_input.text().strip())
            self._config["worker"]["namespace"] = self.namespace_input.text().strip()
            self._config["worker"]["discover_android_devices"] = self.discover_android_checkbox.isChecked()
            self._config["worker"]["discover_ios_devices"] = self.discover_ios_checkbox.isChecked()
```

- [ ] **Step 5: Commit**

```bash
git add worker/settings_window.py
git commit -m "feat(gui): 设置界面新增设备发现开关选项"
```

---

### Task 5: 安装界面变更

**Files:**
- Modify: `installer/installer.iss`

- [ ] **Step 1: 新增变量声明**

在 `installer/installer.iss` 的 `[Code]` 段变量声明部分添加：

找到第 87-90 行：

```pascal
var
  ConfigPage: TInputQueryWizardPage;
  IpLabel, PortLabel, NamespaceLabel, PlatformApiLabel, OcrServiceLabel: TLabel;
  IpEdit, PortEdit, NamespaceEdit, PlatformApiEdit, OcrServiceEdit: TNewEdit;
  CmdIp, CmdPort, CmdNamespace, CmdPlatformApi, CmdOcrService: String;
```

修改为：

```pascal
var
  ConfigPage: TInputQueryWizardPage;
  IpLabel, PortLabel, NamespaceLabel, PlatformApiLabel, OcrServiceLabel: TLabel;
  IpEdit, PortEdit, NamespaceEdit, PlatformApiEdit, OcrServiceEdit: TNewEdit;
  DiscoverAndroidCheckbox, DiscoverIosCheckbox: TNewCheckBox;
  CmdIp, CmdPort, CmdNamespace, CmdPlatformApi, CmdOcrService: String;
```

- [ ] **Step 2: 新增 CheckBox 创建代码**

在 `installer/installer.iss` 的 `InitializeWizard` 函数中，OCR 服务地址控件之后添加：

找到第 269-285 行（OcrServiceEdit 创建之后）：

```pascal
  OcrServiceEdit := TNewEdit.Create(ConfigPage);
  OcrServiceEdit.Parent := ConfigPage.Surface;
  OcrServiceEdit.Left := ScaleX(0);
  OcrServiceEdit.Top := ScaleY(194);
  OcrServiceEdit.Width := ScaleX(350);
  if CmdOcrService <> '' then
    OcrServiceEdit.Text := CmdOcrService
  else
    OcrServiceEdit.Text := 'http://192.168.0.102:9021';
end;
```

修改为：

```pascal
  OcrServiceEdit := TNewEdit.Create(ConfigPage);
  OcrServiceEdit.Parent := ConfigPage.Surface;
  OcrServiceEdit.Left := ScaleX(0);
  OcrServiceEdit.Top := ScaleY(194);
  OcrServiceEdit.Width := ScaleX(350);
  if CmdOcrService <> '' then
    OcrServiceEdit.Text := CmdOcrService
  else
    OcrServiceEdit.Text := 'http://192.168.0.102:9021';

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
end;
```

- [ ] **Step 3: 新增配置写入逻辑**

在 `installer/installer.iss` 的 `CurStepChanged` 函数中，配置替换部分添加：

找到第 315-341 行（在 `for LineIndex := 0 to GetArrayLength(ConfigLines) - 1 do` 循环内）：

```pascal
          // Replace OCR service URL
          if Pos('ocr_service: "http://192.168.0.102:9021"', LineContent) > 0 then
            LineContent := '  ocr_service: "' + OcrServiceEdit.Text + '"   # OCR service URL';

          ConfigLines[LineIndex] := LineContent;
```

修改为：

```pascal
          // Replace OCR service URL
          if Pos('ocr_service: "http://192.168.0.102:9021"', LineContent) > 0 then
            LineContent := '  ocr_service: "' + OcrServiceEdit.Text + '"   # OCR service URL';

          // Replace discover_android_devices
          if Pos('discover_android_devices:', LineContent) > 0 then
          begin
            if DiscoverAndroidCheckbox.Checked then
              LineContent := '  discover_android_devices: true  # 是否发现 Android 设备（关闭则跳过所有 Android 相关逻辑）'
            else
              LineContent := '  discover_android_devices: false  # 是否发现 Android 设备（关闭则跳过所有 Android 相关逻辑）';
          end;

          // Replace discover_ios_devices
          if Pos('discover_ios_devices:', LineContent) > 0 then
          begin
            if DiscoverIosCheckbox.Checked then
              LineContent := '  discover_ios_devices: true       # 是否发现 iOS 设备（关闭则跳过所有 iOS 相关逻辑）'
            else
              LineContent := '  discover_ios_devices: false      # 是否发现 iOS 设备（关闭则跳过所有 iOS 相关逻辑）';
          end;

          ConfigLines[LineIndex] := LineContent;
```

- [ ] **Step 4: Commit**

```bash
git add installer/installer.iss
git commit -m "feat(installer): 安装界面新增设备发现开关选项"
```

---

### Task 6: 验证测试

**Files:**
- None (运行验证)

- [ ] **Step 1: 验证默认配置启动**

修改 `config/worker.yaml` 确保开关为 `false`，启动 Worker：

```bash
python -m worker.main
```

预期日志：
- `Android platform skipped: discover_android_devices=false`
- `iOS platform skipped: discover_ios_devices=false`
- 无设备发现相关日志
- 无 DeviceMonitor 启动日志

- [ ] **Step 2: 验证开启 Android 开关**

修改 `config/worker.yaml` 设置 `discover_android_devices: true`，重启 Worker：

预期日志：
- Android 平台管理器初始化成功
- Android 设备发现日志（如连接了设备）
- DeviceMonitor 启动日志

- [ ] **Step 3: 验证开启 iOS 开关**

修改 `config/worker.yaml` 设置 `discover_ios_devices: true`，重启 Worker：

预期日志：
- iOS 平台管理器初始化成功
- iOS 设备发现日志（如连接了设备）
- DeviceMonitor 启动日志

- [ ] **Step 4: 验证 GUI 设置界面**

运行 GUI 应用：

```bash
python worker/gui_main.py
```

打开设置界面，验证：
- 两个 CheckBox 显示正常
- 默认不勾选
- 修改后保存能重启 Worker

- [ ] **Step 5: 最终 Commit**

```bash
git status
git log --oneline -5
```

确认所有变更已提交。
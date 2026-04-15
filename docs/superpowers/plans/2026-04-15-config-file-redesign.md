# Worker 配置文件管理重新设计实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现双层配置文件管理，用户配置与默认模板分离，升级安装时保留用户配置。

**Architecture:** 用户配置放在根目录 `config/worker.yaml`，默认模板在 `_internal/config/worker.yaml`。配置读取优先级：用户配置 → 默认模板 → WorkerConfig 默认值。安装脚本使用 Excludes 排除用户配置目录。

**Tech Stack:** Python 3.x, PyInstaller, Inno Setup 6.x

---

## 文件结构

| 文件 | 改动类型 | 说明 |
|------|----------|------|
| `worker/config.py` | 修改 | 新增 `get_user_config_path()`、重命名函数、修改 `load_config()` |
| `worker/settings_window.py` | 修改 | 移除 `config_path` 参数、内部自动获取路径、新增 `_copy_default_config` 方法 |
| `worker/gui_main.py` | 修改 | 移除传入 `config_path` 参数 |
| `installer/installer.iss` | 修改 | 移除注册表、修改配置路径、排除目录、移除内存保存机制 |
| `tests/test_config_path.py` | 创建 | 测试配置路径函数和优先级逻辑 |

---

## Task 1: 配置读取逻辑改动 (worker/config.py)

**Files:**
- Modify: `worker/config.py`
- Create: `tests/test_config_path.py`

- [ ] **Step 1: 新增 `get_user_config_path` 函数**

在 `worker/config.py` 中 `get_default_config_path` 函数之前添加：

```python
def get_user_config_path() -> str:
    """获取用户配置文件路径（安装目录根目录的 config/worker.yaml）。
    
    此路径用于：
    - 安装时写入用户配置
    - 设置界面保存配置
    - Worker 启动时读取配置
    """
    if getattr(sys, 'frozen', False):
        # PyInstaller 打包模式
        exe_dir = os.path.dirname(sys.executable)
        return os.path.join(exe_dir, "config", "worker.yaml")
    else:
        # 开发模式
        return os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "config",
            "worker.yaml"
        )
```

- [ ] **Step 2: 新增 `get_default_template_path` 函数**

在 `get_user_config_path` 之后添加（保留原有 `get_default_config_path` 的逻辑）：

```python
def get_default_template_path() -> str:
    """获取默认配置模板路径（_internal/config/worker.yaml）。
    
    此路径用于：
    - 作为用户配置的备份来源
    - 用户配置不存在时自动复制
    """
    if getattr(sys, 'frozen', False):
        exe_dir = os.path.dirname(sys.executable)
        return os.path.join(exe_dir, "_internal", "config", "worker.yaml")
    else:
        return os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "config",
            "worker.yaml"
        )
```

- [ ] **Step 3: 新增 `_copy_default_to_user_config` 辅助函数**

在 `get_default_template_path` 之后添加：

```python
def _copy_default_to_user_config(src: str, dst: str) -> None:
    """复制默认配置模板到用户配置路径。
    
    Args:
        src: 默认配置模板路径
        dst: 用户配置文件路径
    """
    import shutil
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)
    logger.info(f"Default config copied to user config: {dst}")
```

- [ ] **Step 4: 重写 `load_config` 函数**

替换现有的 `load_config` 函数（约 207-221 行）：

```python
def load_config() -> WorkerConfig:
    """加载 Worker 配置。
    
    优先级：根目录 config/worker.yaml → _internal/config/worker.yaml
    若用户配置不存在，自动从默认模板复制一份。
    
    Returns:
        WorkerConfig: 配置对象
    """
    user_config_path = get_user_config_path()
    default_template_path = get_default_template_path()
    
    # 优先读取用户配置
    if os.path.exists(user_config_path):
        logger.info(f"Loading user config: {user_config_path}")
        return WorkerConfig.from_yaml(user_config_path)
    
    # 用户配置不存在，检查默认模板
    if os.path.exists(default_template_path):
        logger.info(f"User config not found, copying default template to: {user_config_path}")
        _copy_default_to_user_config(default_template_path, user_config_path)
        return WorkerConfig.from_yaml(user_config_path)
    
    # 都不存在，使用默认配置
    logger.warning("No config file found, using default WorkerConfig")
    return WorkerConfig()
```

- [ ] **Step 5: 修改 `get_default_config_path` 为向后兼容别名**

修改现有的 `get_default_config_path` 函数（约 184-204 行），改为返回用户配置路径：

```python
def get_default_config_path() -> str:
    """获取配置文件路径（向后兼容别名）。
    
    注意：此函数现在返回用户配置路径，而非默认模板路径。
    若需要获取默认模板路径，请使用 get_default_template_path()。
    """
    return get_user_config_path()
```

- [ ] **Step 6: 创建测试文件验证路径函数**

创建 `tests/test_config_path.py`：

```python
"""测试配置路径函数。"""

import os
import sys
import tempfile
import shutil
from pathlib import Path

import pytest

from worker.config import get_user_config_path, get_default_template_path, load_config, WorkerConfig


class TestConfigPathFunctions:
    """测试配置路径获取函数。"""

    def test_get_user_config_path_development_mode(self):
        """开发模式下返回项目根目录 config/worker.yaml。"""
        # 开发模式（sys.frozen 不存在）
        path = get_user_config_path()
        # 验证路径格式
        assert path.endswith("config/worker.yaml") or path.endswith("config\\worker.yaml")
        assert "config" in path

    def test_get_default_template_path_development_mode(self):
        """开发模式下返回项目根目录 config/worker.yaml（与用户配置相同）。"""
        path = get_default_template_path()
        assert path.endswith("config/worker.yaml") or path.endswith("config\\worker.yaml")

    def test_user_and_template_path_same_in_dev_mode(self):
        """开发模式下用户配置和默认模板路径相同。"""
        user_path = get_user_config_path()
        template_path = get_default_template_path()
        assert user_path == template_path


class TestLoadConfigPriority:
    """测试配置加载优先级逻辑。"""

    def test_load_config_from_existing_user_config(self):
        """用户配置存在时直接读取。"""
        # 现有测试：config/worker.yaml 存在
        config = load_config()
        assert config is not None
        assert isinstance(config, WorkerConfig)

    def test_load_config_copies_template_if_user_missing(self, tmp_path):
        """用户配置不存在时从模板复制（模拟打包环境）。"""
        # 创建模拟的打包目录结构
        app_dir = tmp_path / "app"
        app_dir.mkdir()
        
        internal_config = app_dir / "_internal" / "config"
        internal_config.mkdir(parents=True)
        
        # 写入默认模板
        template_content = """
worker:
  port: 9999
external_services:
  platform_api: "http://test.example.com:8000"
  ocr_service: "http://test.example.com:9021"
"""
        (internal_config / "worker.yaml").write_text(template_content)
        
        # 创建用户配置目录（空）
        user_config_dir = app_dir / "config"
        user_config_dir.mkdir()
        
        # 模拟打包环境
        original_frozen = getattr(sys, 'frozen', False)
        original_executable = getattr(sys, 'executable', None)
        
        try:
            # 模拟打包环境
            sys.frozen = True
            sys.executable = str(app_dir / "test-worker.exe")
            
            # 用户配置文件不存在
            user_config_path = app_dir / "config" / "worker.yaml"
            assert not user_config_path.exists()
            
            # 加载配置
            config = load_config()
            
            # 验证用户配置已创建
            assert user_config_path.exists()
            assert config.port == 9999
        finally:
            # 恢复原始状态
            if original_frozen:
                sys.frozen = original_frozen
            else:
                delattr(sys, 'frozen')
            if original_executable:
                sys.executable = original_executable


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
```

- [ ] **Step 7: 运行测试验证改动**

运行: `pytest tests/test_config_path.py -v`
预期: PASS（开发模式下路径函数正常）

- [ ] **Step 8: 提交配置读取逻辑改动**

```bash
git add worker/config.py tests/test_config_path.py
git commit -m "feat(config): 实现双层配置文件管理

- 新增 get_user_config_path() 获取用户配置路径
- 新增 get_default_template_path() 获取默认模板路径
- 新增 _copy_default_to_user_config() 复制默认配置
- 修改 load_config() 实现优先级逻辑
- get_default_config_path() 改为向后兼容别名"
```

---

## Task 2: 设置界面改动 (worker/settings_window.py)

**Files:**
- Modify: `worker/settings_window.py`

- [ ] **Step 1: 修改 SettingsWindow 类签名**

修改 `__init__` 方法（约第 34-45 行），移除 `config_path` 参数：

```python
class SettingsWindow(QDialog):
    """设置窗口。"""

    def __init__(self, icon_path: str = None, parent=None):
        super().__init__(parent)
        # 内部自动获取用户配置路径
        from worker.config import get_user_config_path
        self.config_path = get_user_config_path()
        self._config = self._load_config()

        # 设置窗口图标
        if icon_path:
            self.setWindowIcon(QIcon(icon_path))

        self._setup_ui()
        self._apply_styles()
        self._load_values()
```

- [ ] **Step 2: 修改 `_load_config` 方法**

修改 `_load_config` 方法（约第 47-89 行），添加自动复制默认模板逻辑：

```python
def _load_config(self) -> dict:
    """加载配置文件。
    
    优先从根目录 config/worker.yaml 读取，
    若不存在则从 _internal/config/worker.yaml 复制一份。
    """
    from worker.config import get_user_config_path, get_default_template_path
    
    config_path = get_user_config_path()
    
    # 用户配置不存在，从默认模板复制
    if not os.path.exists(config_path):
        default_template = get_default_template_path()
        if os.path.exists(default_template):
            self._copy_default_config(default_template, config_path)
            logger.info(f"Default config copied to: {config_path}")

    if not os.path.exists(self.config_path):
        return {}

    # 尝试多种编码
    encodings = ["utf-8", "gbk", "gb18030"]
    data = None
    last_error = None

    for encoding in encodings:
        try:
            with open(self.config_path, "r", encoding=encoding) as f:
                data = yaml.safe_load(f) or {}
            logger.info(f"Config loaded successfully with {encoding} encoding")
            return data
        except UnicodeDecodeError as e:
            last_error = f"编码错误 ({encoding}): {e}"
            logger.warning(f"Failed to load config with {encoding}: {e}")
            continue
        except yaml.YAMLError as e:
            last_error = f"YAML 格式错误: {e}"
            logger.error(f"YAML parse error: {e}")
            break
        except Exception as e:
            last_error = f"读取失败: {e}"
            logger.error(f"Failed to load config: {e}")
            break

    # 加载失败时弹出提示
    if data is None and last_error:
        QMessageBox.warning(
            self,
            "配置加载失败",
            f"无法加载配置文件:\n{self.config_path}\n\n错误: {last_error}\n\n将使用默认值，保存时会覆盖原有配置。"
        )

    return {}
```

- [ ] **Step 3: 新增 `_copy_default_config` 方法**

在 `_load_config` 方法之后添加：

```python
def _copy_default_config(self, src: str, dst: str) -> None:
    """复制默认配置模板到用户配置路径。
    
    Args:
        src: 默认配置模板路径
        dst: 用户配置文件路径
    """
    import shutil
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)
    logger.info(f"Default config copied from {src} to {dst}")
```

- [ ] **Step 4: 提交设置界面改动**

```bash
git add worker/settings_window.py
git commit -m "feat(settings): 设置界面自动获取配置路径

- 移除 config_path 参数，内部使用 get_user_config_path()
- 修改 _load_config() 添加自动复制默认模板逻辑
- 新增 _copy_default_config() 方法"
```

---

## Task 3: GUI 主程序改动 (worker/gui_main.py)

**Files:**
- Modify: `worker/gui_main.py`

- [ ] **Step 1: 修改 `_show_settings_dialog` 方法**

修改 `_show_settings_dialog` 方法（约第 318-330 行），移除传入 `config_path` 参数：

```python
def _show_settings_dialog(self) -> None:
    """显示设置对话框（在 Qt 主线程中）。"""
    try:
        logger.info("Showing settings dialog")
        # 不再传入 config_path，让 SettingsWindow 内部获取
        dialog = SettingsWindow(icon_path=self._icon_path)
        result = dialog.exec_()

        if result == QDialog.Accepted:
            logger.info("Settings saved, restarting Worker...")
            self._do_restart()
    except Exception as e:
        logger.error(f"Settings dialog error: {e}")
```

- [ ] **Step 2: 验证导入和调用**

确认 `SettingsWindow` 导入语句正确（约第 42 行）：
```python
from worker.settings_window import SettingsWindow
```

确认 `_do_restart` 方法中 `load_config()` 调用正常（约第 351 行）。

- [ ] **Step 3: 提交 GUI 主程序改动**

```bash
git add worker/gui_main.py
git commit -m "feat(gui): 移除设置对话框的 config_path 参数

SettingsWindow 内部自动获取配置路径"
```

---

## Task 4: 安装脚本改动 (installer/installer.iss)

**Files:**
- Modify: `installer/installer.iss`

**注意**: 安装脚本改动为手动测试验证，无需自动化测试。

- [ ] **Step 1: 添加根目录 config 目录声明**

确认 `[Dirs]` 段（约第 47-50 行）包含根目录 `config` 目录：

```pascal
[Dirs]
Name: "{app}\config"; Permissions: users-modify
Name: "{app}\_internal\config"; Permissions: users-modify
Name: "{app}\temp"; Permissions: users-modify
Name: "{app}\data"; Permissions: users-modify
```

**注意**：现有代码已包含 `_internal\config`，只需确认是否有根目录 `config`。如果缺少，添加：
```pascal
Name: "{app}\config"; Permissions: users-modify
```

- [ ] **Step 2: 修改 [Files] 段的 Excludes 参数**

修改第 44 行，只排除 `config\*`：

```pascal
[Files]
; 只排除用户配置目录 config\，不排除默认模板 _internal\config
; 这样默认模板可以正常安装，用户配置升级时保留
Source: "..\dist\windows\test-worker\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs; Excludes: "config\*"
```

- [ ] **Step 3: 移除 [Registry] 段**

删除第 53-56 行的注册表写入：

```pascal
; 删除以下内容：
[Registry]
Root: HKCU; Subkey: "Software\Test Worker"; ValueType: string; ValueName: "InstallPath"; ValueData: "{app}"
```

- [ ] **Step 4: 替换 IsUpgradeInstall 函数**

替换第 175-181 行的函数：

```pascal
function IsUpgradeInstall: Boolean;
begin
  // 检测根目录 config/worker.yaml 是否存在
  Result := FileExists(ExpandConstant('{app}\config\worker.yaml'));
end;
```

- [ ] **Step 5: 删除变量声明**

删除第 92-93 行：

```pascal
; 删除这两行：
SavedConfigContent: AnsiString;  // 保存在内存中的配置内容
HasSavedConfig: Boolean;         // 是否已保存配置
```

- [ ] **Step 6: 修改 CurStepChanged 函数**

修改 `CurStepChanged` 函数（约第 293-426 行），简化为：

1. 删除第 302-303 行的初始化代码
2. 删除第 306-315 行的 ssInstall 分支
3. 修改第 299 行的 ConfigFile 路径
4. 删除第 320-325 行的升级恢复分支
5. 保留第 327-420 行的全新安装分支

简化后的 `CurStepChanged`：

```pascal
procedure CurStepChanged(CurStep: TSetupStep);
var
  ConfigFile: String;
  ConfigContent: AnsiString;
  ResultCode: Integer;
begin
  ConfigFile := ExpandConstant('{app}\config\worker.yaml');

  if CurStep = ssPostInstall then
  begin
    // 全新安装时：写入用户配置
    if not IsUpgradeInstall() then
    begin
      ConfigContent := '# Worker Configuration File' + #13#10 +
      '# Edit this file after installation based on your environment' + #13#10 +
        '' + #13#10 +
        '# Worker Basic Settings' + #13#10 +
        'worker:' + #13#10 +
        '  id: null                          # Auto-generated, or specify manually' + #13#10 +
        '  ip: "' + IpEdit.Text + '"                          # Specify IP address, null means auto-detect' + #13#10 +
        '  port: ' + PortEdit.Text + '                        # HTTP service port' + #13#10 +
        '  namespace: ' + NamespaceEdit.Text + '         # Namespace for categorizing Workers' + #13#10 +
        '  device_check_interval: 300        # Device check interval (seconds), 5 minutes' + #13#10 +
        '  service_retry_count: 3            # Service startup retry count' + #13#10 +
        '  service_retry_interval: 10        # Retry interval (seconds)' + #13#10 +
        '  action_step_delay: 0.5            # Action step delay (seconds)' + #13#10 +
        '' + #13#10 +
        '# External Services (Required)' + #13#10 +
        'external_services:' + #13#10 +
        '  platform_api: "' + PlatformApiEdit.Text + '"  # Platform API URL' + #13#10 +
        '  ocr_service: "' + OcrServiceEdit.Text + '"   # OCR service URL' + #13#10 +
        '' + #13#10 +
        '# Platform Settings' + #13#10 +
        'platforms:' + #13#10 +
        '  web:' + #13#10 +
        '    enabled: null                   # Auto-detect based on system' + #13#10 +
        '    headless: false                 # Headless mode' + #13#10 +
        '    browser_type: chromium          # chromium / firefox / webkit' + #13#10 +
        '    timeout: 30000                  # Timeout (ms)' + #13#10 +
        '    session_timeout: 300            # Session timeout (seconds)' + #13#10 +
        '    screenshot_dir: data/screenshots' + #13#10 +
        '    ignore_https_errors: true       # Ignore HTTPS certificate errors' + #13#10 +
        '    user_data_dir: data/chrome_profile  # Browser user data directory' + #13#10 +
        '    permissions:                    # Web permissions' + #13#10 +
        '      - camera' + #13#10 +
        '      - microphone' + #13#10 +
        '    clear_profile_on_start: true' + #13#10 +
        '    request_blacklist:' + #13#10 +
        '      - pattern: "uba.js"' + #13#10 +
        '        action: "404"' + #13#10 +
        '      - pattern: "tinyReporter.min.js"' + #13#10 +
        '        action: "404"' + #13#10 +
        '    token_headers:' + #13#10 +
        '      - "X-Auth-Token"' + #13#10 +
        '      - "X-Request-Operator"' + #13#10 +
        '' + #13#10 +
        '  android:' + #13#10 +
        '    enabled: null                   # Only on Windows' + #13#10 +
        '    u2_port: 7912                   # uiautomator2 port' + #13#10 +
        '    session_timeout: 300' + #13#10 +
        '    screenshot_dir: data/screenshots' + #13#10 +
        '' + #13#10 +
        '  ios:' + #13#10 +
        '    enabled: null                   # Only on Windows' + #13#10 +
        '    wda_base_port: 8100             # WDA base port' + #13#10 +
        '    wda_ipa_path: wda/WebDriverAgent.ipa' + #13#10 +
        '    session_timeout: 300' + #13#10 +
        '    screenshot_dir: data/screenshots' + #13#10 +
        '' + #13#10 +
        '  windows:' + #13#10 +
        '    enabled: null                   # Only on Windows' + #13#10 +
        '    session_timeout: 300' + #13#10 +
        '    screenshot_dir: data/screenshots' + #13#10 +
        '' + #13#10 +
        '  mac:' + #13#10 +
        '    enabled: null                   # Only on macOS' + #13#10 +
        '    session_timeout: 300' + #13#10 +
        '    screenshot_dir: data/screenshots' + #13#10 +
        '' + #13#10 +
        '# Image Matching Settings' + #13#10 +
        'image_matching:' + #13#10 +
        '  default_threshold: 0.8            # Default matching threshold' + #13#10 +
        '  methods:' + #13#10 +
        '    - template                      # Template matching' + #13#10 +
        '    - sift                          # Feature point matching' + #13#10 +
        '' + #13#10 +
        '# Logging Settings' + #13#10 +
        'logging:' + #13#10 +
        '  level: INFO                       # Log level: DEBUG / INFO / WARNING / ERROR' + #13#10 +
        '  file: null                        # Log file path' + #13#10 +
        '  max_size: 52428800                # Max file size, default 50MB' + #13#10 +
        '  backup_count: 5                   # Number of backup files' + #13#10 +
        '' + #13#10 +
        '# Upgrade Settings' + #13#10 +
        'upgrade:' + #13#10 +
        '  check_url: ""                     # Upgrade check API URL' + #13#10 +
        '  check_timeout: 30                 # Check timeout (seconds)' + #13#10 +
        '  download_timeout: 300             # Download timeout (seconds)' + #13#10 +
        '' + #13#10;

      DeleteFile(ConfigFile);
      SaveStringToFile(ConfigFile, ConfigContent, True);
    end;
    // 升级安装时：不覆盖用户配置（通过 Excludes 排除）

    // 静默安装时自动启动
    if WizardSilent then
      ShellExec('', ExpandConstant('{app}\test-worker.exe'), '', '', SW_HIDE, ewNoWait, ResultCode);
  end;
end;
```

- [ ] **Step 7: 修改 ShouldSkipPage 函数**

确认第 183-188 行的 `ShouldSkipPage` 逻辑正确（升级时跳过配置页面）：

```pascal
function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  if PageID = ConfigPage.ID then
    Result := IsUpgradeInstall();  // 使用新的文件检测逻辑
end;
```

- [ ] **Step 8: 提交安装脚本改动**

```bash
git add installer/installer.iss
git commit -m "feat(installer): 实现配置文件升级保护

- 移除注册表依赖
- 只排除 config\* 目录，保留默认模板
- 替换 IsUpgradeInstall 使用文件检测
- 移除内存保存恢复机制
- 配置文件写入根目录 config/"
```

---

## Task 5: 打包配置确认

**Files:**
- None (仅确认现有配置正确)

- [ ] **Step 1: 确认 PyInstaller spec 配置**

检查 `scripts/pyinstaller.spec` 第 19-22 行：

```python
datas = [
    (os.path.join(PROJECT_ROOT, 'config'), 'config'),
    (os.path.join(PROJECT_ROOT, 'assets'), 'assets'),
]
```

**验证**：此配置将 `config/` 目录打包到 `_internal/config/`（PyInstaller 默认行为），符合设计要求。无需修改。

- [ ] **Step 2: 确认打包输出结构**

打包后输出目录应为：
```
dist/windows/test-worker/
├── test-worker.exe
├── _internal/
│   ├── config/
│   │   └── worker.yaml    # 默认配置模板
│   └── ...
└── # 注意：没有根目录 config/（安装时创建）
```

---

## Task 6: 手动测试验证

**Files:**
- None (手动测试)

- [ ] **Step 1: 开发环境测试**

运行 Worker 并验证配置路径：
```bash
python -m worker.main
```

检查日志输出，确认配置从 `config/worker.yaml` 加载。

- [ ] **Step 2: 打包测试**

执行打包脚本：
```bash
powershell scripts/build_windows.ps1
```

验证 `dist/windows/test-worker/_internal/config/worker.yaml` 存在。

- [ ] **Step 3: 安装包构建测试**

执行安装包构建：
```bash
powershell installer/build_installer.ps1
```

验证 `dist/test-worker-installer.exe` 生成成功。

- [ ] **Step 4: 全新安装测试**

1. 运行安装包
2. 检查安装目录结构：
   - `{app}\config\worker.yaml` 存在（用户配置）
   - `{app}\_internal\config\worker.yaml` 存在（默认模板）
3. 启动 Worker，确认配置正确加载

- [ ] **Step 5: 升级安装测试**

1. 修改 `{app}\config\worker.yaml` 中的端口为非默认值
2. 运行新版安装包升级
3. 验证 `{app}\config\worker.yaml` 未被覆盖，端口值保留
4. 验证 `{app}\_internal\config\worker.yaml` 已更新

- [ ] **Step 6: 配置恢复测试**

1. 删除 `{app}\config\worker.yaml`
2. 启动 Worker
3. 验证自动从 `{app}\_internal\config\worker.yaml` 复制恢复

- [ ] **Step 7: 提交测试验证记录**

如果所有测试通过：
```bash
git add -A
git commit -m "test: 配置文件管理手动测试通过"
```

---

## 实现完成检查清单

- [ ] 所有代码改动已提交
- [ ] 单元测试通过
- [ ] 打包脚本正常
- [ ] 安装包生成成功
- [ ] 全新安装测试通过
- [ ] 升级安装测试通过（配置保留）
- [ ] 配置恢复测试通过
- [ ] 设置界面正常工作
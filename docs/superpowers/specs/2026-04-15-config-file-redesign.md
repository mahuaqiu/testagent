# Worker 配置文件管理重新设计

## 概述

本文档描述 Test Worker 配置文件管理的重新设计方案，解决升级安装时配置文件被覆盖丢失的问题。

**问题背景**：
- 当前配置文件位于 `_internal/config/worker.yaml`（打包后）
- 升级安装时使用内存保存恢复机制，不稳定，经常导致配置被覆盖丢失
- 安装脚本使用注册表存储安装路径，升级时可能失败

**设计目标**：
1. 不使用注册表，纯文件管理
2. 用户配置与默认配置分离，升级时保留用户配置
3. 配置丢失时可以自动恢复默认配置
4. 改动集中，影响面小

## 设计方案

### 双层配置文件结构

```
安装目录/
├── test-worker.exe
├── config/                  # 用户配置目录（安装时创建，升级时保留）
│   └── worker.yaml          # 用户配置文件
├── _internal/
│   ├── config/              # 默认配置目录（打包内置）
│   │   └── worker.yaml      # 默认配置模板（只读）
│   └── ...                  # 其他依赖文件
```

**设计理念**：
- `_internal/config/worker.yaml` 作为**默认配置模板**（打包内置，只读）
- `config/worker.yaml` 作为**用户配置文件**（安装时创建，升级时保留）

### 配置读取优先级

```
用户配置 (config/worker.yaml)
    │
    │ 存在 → 读取用户配置
    │
    └不存在
    │
    ▼
默认模板 (_internal/config/worker.yaml)
    │
    │ 存在 → 复制到用户配置路径 → 读取用户配置
    │
    │ 不存在 → 使用 WorkerConfig 默认值
    │
    ▼
WorkerConfig()
```

**优先级逻辑**：
1. 优先读取用户配置 `config/worker.yaml`
2. 用户配置不存在时，从默认模板 `_internal/config/worker.yaml` 复制一份
3. 默认模板也不存在时，使用 `WorkerConfig` 默认值

## 实现细节

### 一、安装脚本改动 (`installer/installer.iss`)

#### 1.1 移除注册表相关代码

删除 `[Registry]` 段中的注册表写入：
```pascal
; 删除以下内容
[Registry]
Root: HKCU; Subkey: "Software\Test Worker"; ValueType: string; ValueName: "InstallPath"; ValueData: "{app}"
```

删除升级检测中的注册表检查逻辑，改为文件存在检查。

#### 1.2 创建根目录 config 目录

```pascal
[Dirs]
Name: "{app}\config"; Permissions: users-modify
Name: "{app}\_internal\config"; Permissions: users-modify
Name: "{app}\temp"; Permissions: users-modify
Name: "{app}\data"; Permissions: users-modify
```

#### 1.3 安装时写入根目录配置文件

修改 `CurStepChanged` 中 `ssPostInstall` 的配置写入路径：

```pascal
procedure CurStepChanged(CurStep: TSetupStep);
var
  ConfigFile: String;  // 改为根目录 config
begin
  // 全新安装时：写入根目录 config/worker.yaml
  ConfigFile := ExpandConstant('{app}\config\worker.yaml');
  
  if CurStep = ssPostInstall then
  begin
    if not IsUpgradeInstall() then
    begin
      // 写入用户配置
      SaveStringToFile(ConfigFile, ConfigContent, True);
    end;
    // 升级安装时：不覆盖用户配置（通过 Excludes 排除）
  end;
end;
```

#### 1.4 升级保护（排除目录）

修改 `[Files]` 段，只排除用户配置目录 `config/`：

```pascal
[Files]
; 只排除用户配置目录 config\，不排除默认模板 _internal\config
; 这样默认模板可以正常安装，用户配置升级时保留
Source: "..\dist\windows\test-worker\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs; Excludes: "config\*"
```

**重要说明**：
- **只排除 `config\*`**：升级时不覆盖用户配置目录
- **不排除 `_internal\config\*`**：让默认配置模板正常安装，确保自动恢复机制可用
- 原 Excludes 同时排除两者会导致 `_internal/config/worker.yaml` 不存在，破坏自动恢复机制

#### 1.5 替换升级检测函数

**完全替换** `IsUpgradeInstall` 函数（现有代码第 175-181 行），改为文件存在检查：

```pascal
// 替换整个函数（删除原注册表检查逻辑）
function IsUpgradeInstall: Boolean;
begin
  // 检测根目录 config/worker.yaml 是否存在
  Result := FileExists(ExpandConstant('{app}\config\worker.yaml'));
end;
```

#### 1.6 移除内存保存恢复机制

删除以下代码：

**变量声明（第 92-93 行）**：
```pascal
; 删除这两行
SavedConfigContent: AnsiString;  // 保存在内存中的配置内容
HasSavedConfig: Boolean;         // 是否已保存配置
```

**CurStepChanged 中的初始化代码（第 302-303 行）**：
```pascal
; 删除这两行
HasSavedConfig := False;
SavedConfigContent := '';
```

**CurStepChanged 中 ssInstall 的内存保存逻辑（第 306-315 行）**：
```pascal
; 删除整个 ssInstall 分支
if CurStep = ssInstall then
begin
  if IsUpgradeInstall and FileExists(ConfigFile) then
  begin
    LoadStringFromFile(ConfigFile, SavedConfigContent);
    HasSavedConfig := True;
    Log('Config saved to memory: ' + ConfigFile + ' (length: ' + IntToStr(Length(SavedConfigContent)) + ')');
  end;
end;
```

**升级安装时的恢复逻辑（第 320-325 行）**：
```pascal
; 删除升级安装恢复分支，只保留全新安装分支
if IsUpgradeInstall and HasSavedConfig then
begin
  SaveStringToFile(ConfigFile, SavedConfigContent, False);
  Log('Config restored from memory: ' + ConfigFile);
end
```

**原因**：使用 `Excludes` 排除 `config/` 目录后，安装程序不会覆盖用户配置，无需内存保存恢复。

### 二、配置读取逻辑改动 (`worker/config.py`)

#### 2.1 新增函数：获取用户配置路径

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

#### 2.2 重命名原函数：获取默认配置模板路径

将 `get_default_config_path()` 重命名为 `get_default_template_path()`，保持原有逻辑：

```python
def get_default_template_path() -> str:
    """获取默认配置模板路径（_internal/config/worker.yaml）。
    
    此路径用于：
    - 作为用户配置的备份来源
    - 用户配置不存在时自动复制
    """
    # 保持原有逻辑不变
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

#### 2.3 新增辅助函数：复制默认配置

```python
def _copy_default_to_user_config(src: str, dst: str) -> None:
    """复制默认配置模板到用户配置路径。
    
    Args:
        src: 默认配置模板路径
        dst: 用户配置文件路径
    """
    import shutil
    # 确保目标目录存在
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)
    logger.info(f"Default config copied to user config: {dst}")
```

#### 2.4 重写 load_config 函数

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
    
    # 都不存在，使用默认配置（创建新配置对象）
    logger.warning("No config file found, using default WorkerConfig")
    return WorkerConfig()
```

#### 2.5 向后兼容性处理

为了保持向后兼容（开发环境等），保留 `get_default_config_path()` 函数作为别名：

```python
def get_default_config_path() -> str:
    """获取配置文件路径（向后兼容别名）。
    
    注意：此函数现在返回用户配置路径，而非默认模板路径。
    若需要获取默认模板路径，请使用 get_default_template_path()。
    """
    return get_user_config_path()
```

### 三、设置界面改动 (`worker/settings_window.py`)

#### 3.1 修改类签名

移除 `config_path` 参数，改为内部自动获取：

```python
class SettingsWindow(QDialog):
    """设置窗口。"""

    def __init__(self, icon_path: str = None, parent=None):
        super().__init__(parent)
        # 内部自动获取用户配置路径
        from worker.config import get_user_config_path
        self.config_path = get_user_config_path()
        self._config = self._load_config()
        
        # ... 后续初始化代码 ...
```

#### 3.2 修改配置加载逻辑

在 `_load_config()` 中添加自动复制默认模板的逻辑：

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
    
    # 尝试读取配置（多编码兼容）
    if os.path.exists(config_path):
        # ... 原有的多编码读取逻辑 ...
    
    return {}
```

**新增类方法 `_copy_default_config`**（在类内部添加）：

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
```

#### 3.3 保存逻辑保持不变

`_on_save()` 方法无需修改，因为 `self.config_path` 已改为用户配置路径。

### 四、GUI 主程序改动 (`worker/gui_main.py`)

#### 4.1 修改设置对话框调用

移除传入 `config_path` 参数：

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

## 改动文件汇总

| 文件 | 改动内容 |
|------|----------|
| `installer/installer.iss` | 移除注册表、写入根目录 config/、使用 Excludes 排除升级覆盖、移除内存保存恢复机制 |
| `worker/config.py` | 新增 `get_user_config_path()`、重命名 `get_default_config_path()` 为 `get_default_template_path()`、修改 `load_config()` 实现优先级逻辑 |
| `worker/settings_window.py` | 移除 `config_path` 参数、内部自动获取用户配置路径、自动复制默认模板 |
| `worker/gui_main.py` | 移除传入 `config_path` 参数 |

## 测试场景

### 场景 1：全新安装

1. 安装程序写入 `{app}\config\worker.yaml`
2. Worker 启动，读取 `config/worker.yaml`
3. 设置界面打开，显示 `config/worker.yaml` 内容
4. 设置界面保存，写入 `config/worker.yaml`

### 场景 2：升级安装

1. 用户已安装，`config/worker.yaml` 存在且包含用户配置
2. 运行新版安装包
3. 安装程序排除 `config/` 目录，不覆盖用户配置
4. Worker 启动，读取原有的 `config/worker.yaml`

### 场景 3：用户配置文件被删除

1. 用户删除了 `config/worker.yaml`（文件删除，目录存在）
2. Worker 启动
3. 检测到用户配置不存在，从 `_internal/config/worker.yaml` 复制一份
4. Worker 使用恢复后的配置启动

### 场景 4：用户配置目录被删除

1. 用户删除了整个 `config/` 目录
2. Worker 启动
3. 检测到用户配置路径不存在，创建目录并从 `_internal/config/worker.yaml` 复制一份
4. Worker 使用恢复后的配置启动

### 场景 5：设置界面首次打开（配置被删除）

1. 用户删除了 `config/worker.yaml`
2. 打开设置界面
3. 检测到用户配置不存在，从 `_internal/config/worker.yaml` 复制一份
4. 显示恢复后的配置内容
5. 用户保存，写入 `config/worker.yaml`

## 风险与注意事项

1. **权限问题**：确保 `{app}\config` 目录有写入权限（`Permissions: users-modify`）
2. **编码兼容**：配置文件写入使用 UTF-8 编码，读取时兼容 GBK/GB18030（保留现有逻辑）
3. **向后兼容**：开发环境下 `get_user_config_path()` 和 `get_default_template_path()` 都返回项目根目录 `config/worker.yaml`，行为不变

## 打包配置说明

### PyInstaller 打包

打包脚本 `scripts/build_windows.ps1` 或 PyInstaller spec 文件需要确保：

1. **默认配置模板打包到 `_internal/config/`**：
   ```
   --add-data "config/worker.yaml;_internal/config"
   ```
   或在 spec 文件中：
   ```python
   datas=[('config/worker.yaml', '_internal/config')]
   ```

2. **打包输出目录结构**：
   ```
   dist/windows/test-worker/
   ├── test-worker.exe
   ├── _internal/
   │   ├── config/
   │   │   └── worker.yaml    # 默认配置模板
   │   └── ...
   └── # 注意：打包时不创建根目录 config/（安装时创建）
   ```

**注意**：打包输出不包含根目录 `config/`，该目录由安装程序在安装时创建。

## 旧版本迁移说明

### 从旧版本升级（配置在 `_internal/config/`）

对于旧版本用户，配置文件位于 `_internal/config/worker.yaml`：

1. **升级安装时**：
   - 新版安装包安装 `_internal/config/worker.yaml`（默认模板）
   - 安装程序检测是否为升级安装（检查 `config/worker.yaml` 是否存在）
   - 如果 `config/worker.yaml` 不存在，安装程序不写入新配置

2. **首次启动时**：
   - Worker 启动，`config/worker.yaml` 不存在
   - 从 `_internal/config/worker.yaml` 复制一份到 `config/worker.yaml`
   - 用户获得一份新的配置文件（基于默认模板）

**注意**：旧版本用户的个性化配置会丢失，需要重新配置。这是因为旧版本配置与默认模板在同一位置，无法区分。

**迁移优化方案（可选）**：安装程序可以在升级时检测 `_internal/config/worker.yaml` 是否有用户修改痕迹（如非默认 IP），如果有则复制到 `config/worker.yaml`。但这增加了安装脚本复杂度，建议用户重新配置。
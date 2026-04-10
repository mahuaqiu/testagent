# Windows 系统托盘 GUI 设计文档

## 概述

为 Test Worker 添加 Windows 系统托盘功能，实现管理员权限启动、无 CMD 窗口、托盘菜单管理、自动升级等功能。

## 目标

1. EXE 启动时自动请求管理员权限
2. 启动后无 CMD 窗口，直接最小化到系统托盘
3. 托盘右键菜单提供完整管理功能
4. 实现自动检查更新和静默升级
5. 提供设置界面修改配置参数

## 技术方案

采用 **纯 Python 实现**：
- `pystray` 库实现系统托盘
- `PyQt5` 实现设置界面和进度窗口
- Worker 作为后台线程运行（不使用子进程）

## 系统架构

### 入口改造

- **开发调试入口**：`worker/main.py`（保留，命令行方式）
- **打包入口**：`worker/gui_main.py`（新增，带托盘）

### 启动流程

```
GUI 入口启动 → 创建托盘图标 → 后台启动 Worker 线程 → 托盘管理 Worker 生命周期
```

### 托盘职责

1. 显示 Worker 状态（运行中/已停止）
2. 提供右键菜单操作
3. 管理 Worker 线程的启动和停止
4. 处理配置更新和重启逻辑
5. 执行升级检查和自动安装

### 进程模型

- GUI 主线程运行托盘事件循环
- Worker 作为独立线程在后台运行
- 同一进程内，避免进程间通信复杂度

## 托盘图标设计

**图标来源**：
- 图标文件嵌入到 EXE 资源中（通过 PyInstaller）
- 图标路径：`assets/icon.ico`（需准备）

**图标样式**：
- 单一图标，不区分状态（简化实现）
- 后续可扩展为运行中/已停止两种状态

**鼠标悬停提示**：
- 提示文本：`Test Worker - 运行中` 或 `Test Worker - 已停止`

## 托盘菜单

| 菢单项 | 功能说明 |
|--------|----------|
| 升级 | 检查更新，有新版本自动下载静默安装 |
| 重启 | 重启 Worker 服务 |
| 日志 | 打开日志文件所在目录 |
| 设置 | 打开设置窗口，修改配置参数 |
| 退出 | 停止 Worker 并关闭程序 |

## 功能详细设计

### 1. 管理员权限

**实现方式**：修改 `pyinstaller.spec`，添加 UAC manifest。

```python
exe = EXE(
    ...
    console=False,       # 关闭 CMD 窗口
    uac_admin=True,      # 请求管理员权限
)
```

**UAC 行为**：
- 父进程已是管理员权限 → 静默启动，无 UAC 提示
- 父进程是普通权限 → 弹出 UAC 提示框，用户确认后启动

### 2. 升级功能

**升级检查接口**：

```
GET /get_worker_upgrade

响应：
{
  "version": "202604101500",
  "download_url": "http://192.168.0.102:8000/downloads/test-worker-installer.exe"
}
```

**检查流程**：

1. 用户点击"升级"
2. 发送 GET 请求到升级检查接口
3. 对比版本号（格式：yyyyMMddHHmm，数字对比）
4. 结果处理：
   - 无新版本 → 弹窗提示"已是最新版本"
   - 有新版本 → 弹窗确认"发现新版本 vX.X，是否升级？"
   - 请求失败 → 弹窗提示"升级检查失败"，不自动重试，由用户手动重试

**自动升级流程**：

```
用户确认升级 → 显示下载进度窗口 → 下载安装包到临时目录
    → 关闭 Worker 和托盘 → 启动安装包（静默模式） → 安装完成后自动启动新版本
```

**静默安装参数**：

```
/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /DIR="{安装目录}"
```

**installer.iss 需修改**：
- 静默安装模式下自动启动程序（调整 postinstall 配置）

### 3. 重启功能

**重启流程**：

1. 弹窗确认"是否重启 Worker 服务？"
2. 用户确认后，停止当前 Worker
3. 重新加载配置文件 `worker.yaml`
4. 启动新的 Worker

**重启期间**：
- HTTP API 暂时不可用
- 设备连接清理后重新建立
- 托盘图标保持

### 4. 日志功能

**实现**：
- 日志目录：EXE 同目录下的 `logs` 文件夹（或配置路径）
- 使用 `os.startfile()` 或 `explorer` 命令打开目录

### 5. 设置功能

**设置窗口布局**：

```
┌─────────────────────────────────────────┐
│         Test Worker 设置                │
├─────────────────────────────────────────┤
│ Worker IP 地址:    [________________]   │
│ Worker 端口:       [______]             │
│ 命名空间:          [________________]   │
│ 平台 API 地址:     [________________]   │
│ OCR 服务地址:      [________________]   │
│ 日志级别:          [下拉选择 ▼]         │
│                                         │
│           [保存并重启]  [取消]           │
└─────────────────────────────────────────┘
```

**字段**：

| 字段 | 输入类型 | 必填/可选 | 配置路径 |
|------|----------|-----------|----------|
| Worker IP | 文本框 | 可选 | `worker.ip` |
| Worker 端口 | 文本框 | 必填 | `worker.port` |
| 命名空间 | 文本框 | 必填 | `worker.namespace` |
| 平台 API 地址 | 文本框 | 必填 | `external_services.platform_api` |
| OCR 服务地址 | 文本框 | 必填 | `external_services.ocr_service` |
| 日志级别 | 下拉框 | 必填 | `logging.level` |

**保存逻辑**：
- 点击"保存并重启" → 写入 `worker.yaml` → 重启 Worker
- 点击"取消" → 关闭窗口，不保存

**字段验证**：

| 字段 | 验证规则 |
|------|----------|
| Worker 端口 | 必填，范围 1-65535，默认 8088 |
| Worker IP | 可选，IPv4 格式验证（如填写则验证格式） |
| 平台 API 地址 | 必填，URL 格式验证（http:// 或 https://） |
| OCR 服务地址 | 必填，URL 格式验证（http:// 或 https://） |
| 命名空间 | 必填，非空字符串 |
| 日志级别 | 下拉选择，无需验证 |

### 6. 退出功能

**退出流程**：

1. 弹窗确认"是否退出 Test Worker？"
2. 用户确认后，停止 Worker 服务
3. 清理所有资源（设备连接、浏览器会话等）
4. 关闭托盘图标
5. 程序完全退出

## 文件结构

新增文件：

```
worker/
├── gui_main.py          # GUI 入口
├── tray_manager.py      # 托盘管理器
├── settings_window.py   # PyQt5 设置窗口
├── upgrade_manager.py   # 升级管理器
└── download_dialog.py   # PyQt5 下载进度窗口
```

修改文件：

```
scripts/pyinstaller.spec  # 添加 uac_admin、console=False
installer/installer.iss   # 静默安装自动启动配置
config/worker.yaml        # 添加 upgrade_check_url 配置项
```

## 依赖新增

```python
# pyproject.toml 或 requirements.txt
pystray>=1.9.0
PyQt5>=5.15.0
```

## 打包配置修改

**pyinstaller.spec**：

```python
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='test-worker',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,        # 新增：关闭 CMD 窗口
    uac_admin=True,       # 新增：管理员权限
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
```

## 升级检查地址

升级检查接口地址暂存配置文件，后续确定后配置：

```yaml
# config/worker.yaml 新增
upgrade:
  check_url: ""           # 后续配置，如 "http://192.168.0.102:8000/get_worker_upgrade"
  check_timeout: 30       # 升级检查超时（秒）
  download_timeout: 300   # 下载超时（秒）
```

设置界面不显示此项，由配置文件或部署时指定。

## 静默安装配置

**installer.iss 需调整**：

确保静默安装完成后自动启动程序。当前 `[Run]` 配置在静默模式下可能不执行，需要修改：

```iss
[Run]
Filename: "{app}\test-worker.exe"; Description: "启动 Test Worker"; Flags: nowait postinstall skipifsilent unchecked

; 新增：静默安装后自动启动
[Registry]
Root: HKLM; Subkey: "Software\Test Worker"; ValueType: string; ValueName: "InstallPath"; ValueData: "{app}"

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    // 静默安装模式下自动启动（隐藏窗口，启动后直接最小化到托盘）
    if WizardSilent then
      ShellExec('', ExpandConstant('{app}\test-worker.exe'), '', '', SW_HIDE, ewNoWait, 0);
  end;
end;
```

> **说明**：`SW_HIDE` 以隐藏窗口方式启动是预期行为，程序启动后自动最小化到系统托盘，无需显示主窗口。用户可通过托盘图标确认程序已运行。

## 注意事项

1. **线程安全**：Worker 线程与托盘主线程需要安全的启动/停止机制
2. **资源清理**：重启/退出时确保所有资源正确释放
3. **错误处理**：升级下载失败时需要提示用户，不中断当前运行
4. **配置备份**：升级前备份用户配置文件，避免丢失

## Worker 线程停止机制

**优雅停止流程**：

1. 设置 `threading.Event` 停止信号
2. Worker 线程检测信号，停止接收新任务
3. 等待当前任务完成（超时 10 秒）
4. 超时后强制停止：关闭 HTTP Server、清理设备连接
5. 托盘更新状态为"已停止"

**实现要点**：

```python
# 停止信号
stop_event = threading.Event()

# Worker 线程循环检测
while not stop_event.is_set():
    # 执行任务
    ...

# 优雅停止超时
def stop_worker(timeout=10):
    stop_event.set()
    worker_thread.join(timeout)
    if worker_thread.is_alive():
        # 强制停止：清理资源
        force_stop()

def force_stop():
    """强制停止 Worker，清理所有资源。"""
    # 1. 关闭 HTTP Server
    if http_server:
        http_server.should_exit = True
    # 2. 清理所有平台管理器
    for platform in platforms:
        platform.stop()
    # 3. 清理设备连接
    for device in devices:
        device.disconnect()
    # 4. Worker 线程设置为 daemon，随主线程退出
```

## 异常处理

### Worker 启动失败

- 托盘图标显示"已停止"状态
- 弹窗提示启动失败原因（如：端口被占用、配置错误）
- 用户可通过"重启"菜单项重新尝试启动

### 升级下载失败

- 弹窗提示下载失败原因（网络错误、文件损坏等）
- Worker 继续运行，不中断服务
- 保留已下载的部分文件，下次升级时重新下载覆盖

### 升级过程中用户退出

- 下载进度窗口提供"取消"按钮
- 用户取消后，停止下载，清理临时文件
- Worker 继续运行，下次点击"升级"重新开始

## 多实例防护

**单实例锁机制**：

- 使用 Windows Mutex 实现单实例锁
- 启动时检查是否已有实例运行
- 如已有实例：弹窗提示"Test Worker 已在运行"，然后退出

**实现方式**：

```python
import ctypes

def check_single_instance():
    """检查是否已有实例运行。"""
    mutex_name = "Global\\TestWorkerSingleInstance"
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, mutex_name)
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        return False  # 已有实例
    return True  # 可以启动
```

## 配置兼容性

**升级时配置处理**：

1. 安装前备份当前配置文件到 `{app}\config\worker.yaml.backup`
2. 新版本安装后，检测是否存在旧配置文件
3. 如存在旧配置：
   - 保留旧配置中用户修改的值
   - 合并新增的配置项（使用默认值）
4. 配置合并逻辑在 Worker 启动时执行

**配置字段兼容**：

- 新增字段使用默认值，不影响旧配置
- 字段重命名时保留旧字段映射
- 字段删除时忽略旧值

**配置合并实现**：

```python
def merge_config(old_config: dict, new_template: dict) -> dict:
    """合并配置：保留旧配置值，补充新字段。"""
    merged = new_template.copy()

    # 遍历旧配置，保留用户已设置的值
    for section, values in old_config.items():
        if section in merged:
            for key, value in values.items():
                # 保留用户修改的值（非 None）
                if value is not None and key in merged[section]:
                    merged[section][key] = value
        else:
            # 保留旧配置中已删除的 section（向后兼容）
            merged[section] = values

    return merged
```

## 下载进度窗口设计

**窗口布局**：

```
┌─────────────────────────────────────────┐
│         正在下载更新                     │
├─────────────────────────────────────────┤
│ 版本: v202604101500                     │
│                                         │
│ ████████████░░░░░░░░░░░  45%            │
│                                         │
│ 已下载: 15.2 MB / 33.5 MB               │
│                                         │
│           [取消下载]                     │
└─────────────────────────────────────────┘
```

**功能说明**：

| 元素 | 说明 |
|------|------|
| 版本标签 | 显示目标版本号 |
| 进度条 | 实时更新下载进度 |
| 进度百分比 | 显示当前下载百分比 |
| 文件大小 | 显示已下载和总大小 |
| 取消按钮 | 点击后停止下载，清理临时文件 |

**进度更新机制**：

- HTTP 请求获取 `Content-Length` 头，确定文件总大小
- 使用 `stream=True` 分块下载，累加已下载字节
- 每下载 1MB 更新一次进度条

**取消下载处理**：

1. 停止 HTTP 请求，关闭连接
2. 删除临时文件 `%TEMP%\test-worker-update.exe`
3. 关闭进度窗口
4. Worker 继续运行，不中断服务

## 版本对比

版本号格式：`yyyyMMddHHmm`（年月日时分，如 `202604101500`）

对比规则：字符串转整数后比较，数值大者为新版本。

## 临时文件

升级时下载的安装包存放路径：

```
%TEMP%\test-worker-update.exe
```

安装完成后由 Inno Setup 自动清理，或程序启动时检查并删除残留。
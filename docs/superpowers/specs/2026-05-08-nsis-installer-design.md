---
name: NSIS Installer Migration
description: 将打包方案从 Inno Setup 切换到 NSIS
type: project
---

# NSIS Installer Migration Design

## 概述

**目标**：将打包方案从 Inno Setup 切换到 NSIS，保留所有现有功能。

**原因**：Inno Setup 有商业风险，公司不允许使用。

## 核心改动

| 文件 | 改动 |
|------|------|
| `installer/installer.nsi` | 新建 NSIS 主脚本（替代 installer.iss） |
| `installer/build_installer.ps1` | 修改为调用 makensis.exe，从 worker.yaml 读取默认值 |
| `installer/installer.iss` | 删除（不再使用） |

## 架构

```
installer/
├── installer.nsi          # NSIS 主脚本
├── build_installer.ps1    # 构建脚本（调用 makensis）
└── header.bmp             # 安装向导头部图片（可选）
```

**构建流程**：
1. PowerShell 脚本检测 NSIS 安装路径
2. 从 `config/worker.yaml` 读取默认值（platform_api、ocr_service）
3. 调用 `makensis.exe` 编译 `.nsi` 脚本
4. 输出到 `dist\test-worker-installer.exe`

## 功能迁移清单

| 功能 | Inno Setup 实现 | NSIS 实现 |
|------|-----------------|-----------|
| 权限设置 | `PrivilegesRequired=lowest` | `RequestExecutionLevel admin`（用户确认变更，强制管理员权限） |
| 桌面快捷方式选项 | `[Tasks]` 段复选框 | nsDialogs 自定义页面添加复选框 |
| 基本安装 | `[Files]` 段 | `File` 命令 |
| 目录创建 | `[Dirs]` 段 | `CreateDirectory` 命令 |
| 快捷方式 | `[Icons]` 段 | `CreateShortcut` 命令 |
| 卸载 | 自动生成 | `WriteUninstaller` 命令 |
| 中文界面 | `ChineseSimplified.isl` | Modern UI 2 内置中文语言文件 |
| 配置向导页面 | `TInputQueryWizardPage` | nsDialogs 自定义页面 |
| 自动 IP 检测 | Pascal 代码读取注册表 | NSIS `ReadRegStr` + 循环 |
| 命令行参数 | `GetCmdParam` 函数 | `GetParameters` + `GetOption` |
| 升级检测 | 检查 `config\worker.yaml` | 检查 `config\worker.yaml` |
| 进程清理 | `Exec('taskkill')` + PowerShell | `ExecWait 'taskkill'` + PowerShell |
| 配置文件合并 | Pascal 文件操作 | NSIS `FileOpen`/`FileRead`/`FileWrite` |
| 静默安装 | `/VERYSILENT` 参数 | `/S` 参数 |
| 安装后启动 | `[Run]` 段 | `Exec` 命令 |

## 关键设计细节

### 进程清理

**背景**：安装前需要清理占用安装目录的进程，避免文件替换失败。

**原 Inno Setup 实现**：已修复，使用 PowerShell `StartsWith` 进行路径匹配。NSIS 迁移时需要保持相同的逻辑，但需要注意以下改进点：

**NSIS 实现要点**：
- 执行时机：INSTFILES 页面初始化时（此时 `$INSTDIR` 已确定）
- 进程名：test-worker.exe（全局杀）、ios.exe、adb.exe、ffmpeg.exe（按路径筛选）
- 路径匹配：确保不区分大小写，路径末尾有分隔符

```nsis
Function KillProcessesAndCleanup
  ; 1. 杀主进程（全局杀，名称唯一）
  ExecWait '"taskkill" /f /im test-worker.exe' $0

  ; 2. 准备路径变量（确保末尾有斜杠，避免匹配到其他路径）
  StrCpy $2 "$INSTDIR"
  StrCpy $3 "$2\"  ; 路径末尾加分隔符

  ; 3. PowerShell 按路径精准杀 ios.exe、adb.exe、ffmpeg.exe
  ; 注意：NSIS 字符串拼接使用 "$变量" 格式，$1 会被替换为已存储的值
  ; PowerShell 脚本中的路径使用 -like 操作符（不区分大小写）
  StrCpy $1 '"powershell" -NoProfile -ExecutionPolicy Bypass -Command "$p = Get-Process -Name ios,adb,ffmpeg -ErrorAction SilentlyContinue; foreach ($x in $p) { if ($x.Path -like \'$3*\' -or $x.Path -like \'$2\\*\') { $x.Kill() } }"'
  ExecWait $1 $0

  ; 4. 删除 playwright 目录（避免升级不兼容问题）
  ; Playwright chromium 版本可能随 Playwright 库更新而变化，升级时需清理旧缓存
  IfFileExists "$INSTDIR\playwright\*.*" 0 NoPlaywright
    RMDir /r "$INSTDIR\playwright"
  NoPlaywright:
FunctionEnd
```

**关键技术点**：
1. **路径末尾分隔符**：`$3 = "$INSTDIR\"`，确保不会误匹配到其他路径（如 "Test Worker" 匹配到 "Test Worker2")
2. **大小写不敏感**：使用 PowerShell `-like` 操作符替代 `StartsWith`，Windows 文件系统不区分大小写
3. **变量展开时机**：NSIS 变量 `$INSTDIR` 在运行时展开，不是编译时，路径中的空格和特殊字符会被正确处理
4. **单引号替代双引号**：PowerShell 脚本中的路径使用单引号包裹，避免 NSIS 双引号转义问题

**调用时机**：
```nsis
!define MUI_PAGE_CUSTOMFUNCTION_PRE KillProcessesAndCleanup
!insertmacro MUI_PAGE_INSTFILES
```

### 默认值读取

**问题**：配置页面默认值（platform_api、ocr_service）需从 worker.yaml 模板读取，避免硬编码维护两处。

**方案**：构建时预读取

```powershell
# 解析 worker.yaml 获取默认值
$WorkerYaml = "config\worker.yaml"
$YamlContent = Get-Content $WorkerYaml

# 提取 platform_api 和 ocr_service 默认值
# 注意：由于 YAML 行格式固定（缩进 + 字段名），直接匹配字段名即可
# 不需要考虑完整路径（external_services.platform_api）
$PlatformApi = ($YamlContent | Select-String 'platform_api:\s*"([^"]+)"').Matches.Groups[1].Value
$OcrService = ($YamlContent | Select-String 'ocr_service:\s*"([^"]+)"').Matches.Groups[1].Value

# 传给 NSIS
& $NsisPath "/DVersion=$Version" "/DPlatformApi=$PlatformApi" "/DOcrService=$OcrService" "installer\installer.nsi"
```

**说明**：`worker.yaml` 中字段格式固定，如 `platform_api: "http://192.168.0.102:8000"`，正则表达式 `platform_api:\s*"([^"]+)"` 可以直接匹配，无需考虑 YAML 节点层级。

### 安装向导页面流程

```
1. 欢迎页面
2. 安装路径选择页面
3. 配置参数页面（自定义页面 - nsDialogs）
4. 安装进度页面
5. 完成页面
```

**配置参数页面内容**：

| 控件 | 类型 | 默认值 |
|------|------|--------|
| Worker IP 地址 | 文本框 | 自动检测本地 IP |
| Worker 端口 | 文本框 | 8088 |
| 命名空间 | 文本框 | meeting_public |
| 平台 API 地址 | 文本框 | 从 worker.yaml 读取 |
| OCR 服务地址 | 文本框 | 从 worker.yaml 读取 |
| 设备发现选项 | 复选框 × 2 | Android/iOS 设备发现开关（对应 config 中的 discover_android_devices 和 discover_ios_devices 字段，默认 false） |

**升级安装行为**：
- 检测到 `config\worker.yaml` 存在时，跳过配置参数页面
- 保留用户原有配置

**桌面快捷方式选项**：
在配置参数页面添加"创建桌面快捷方式"复选框，默认勾选。实现方式：
```nsis
; nsDialogs 创建复选框
${NSD_CreateCheckbox} 0 220 100% 12u "创建桌面快捷方式"
Var /GLOBAL DesktopCheckbox
${NSD_GetState} $DesktopCheckbox $0
; 安装时根据 $0 决定是否创建快捷方式
```

### 自动 IP 检测

**逻辑**：遍历注册表网络接口，优先返回 10.x.x.x、192.168.x.x、172.16-31.x.x 范围的 IP。

**NSIS 实现思路**（完整实现）：
```nsis
Function GetLocalIP
  ; 输出：$R0 = 最佳 IP 地址
  Push $R1  ; 子键索引
  Push $R2  ; 当前 IP
  Push $R3  ; 10.x IP
  Push $R4  ; 192.168.x IP
  Push $R5  ; 172.x IP
  Push $R6  ; 其他 IP

  StrCpy $R3 ""
  StrCpy $R4 ""
  StrCpy $R5 ""
  StrCpy $R6 ""

  ; 遍历注册表子键
  StrCpy $R1 0
  loop:
    EnumRegKey $R2 HKLM "SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces" $R1
    StrCmp $R2 "" done

    ; 尝试读取 DhcpIPAddress
    ReadRegStr $R2 HKLM "SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces\$R2" "DhcpIPAddress"
    StrCmp $R2 "" try_static
    Goto check_ip

  try_static:
    ReadRegStr $R2 HKLM "SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces\$R2" "IPAddress"

  check_ip:
    ; 过滤无效 IP（空、0.0.0.0、127.x）
    StrCmp $R2 "" next
    StrCmp $R2 "0.0.0.0" next
    StrCpy $R0 $R2 4
    StrCmp $R0 "127." next

    ; 按优先级存储（只存第一个）
    StrCpy $R0 $R2 3
    StrCmp $R0 "10." store_10
    StrCpy $R0 $R2 8
    StrCmp $R0 "192.168." store_192
    StrCpy $R0 $R2 4
    StrCmp $R0 "172." store_172
    ; 注意：172.x 验证简化，实际私有范围是 172.16-31.x.x
    ; 但 NSIS 字符串处理较复杂，这里接受所有 172.x 作为私有地址（优先级影响较小）
    StrCmp $R6 "" store_other
    Goto next

  store_10:
    StrCmp $R3 "" 0 next
    StrCpy $R3 $R2
    Goto next
  store_192:
    StrCmp $R4 "" 0 next
    StrCpy $R4 $R2
    Goto next
  store_172:
    StrCmp $R5 "" 0 next
    StrCpy $R5 $R2
    Goto next
  store_other:
    StrCpy $R6 $R2

  next:
    IntOp $R1 $R1 + 1
    Goto loop

  done:
    ; 按优先级返回
    StrCmp $R3 "" 0 return_10
    StrCmp $R4 "" 0 return_192
    StrCmp $R5 "" 0 return_172
    StrCmp $R6 "" 0 return_other
    StrCpy $R0 "127.0.0.1"
    Goto end

  return_10:
    StrCpy $R0 $R3
    Goto end
  return_192:
    StrCpy $R0 $R4
    Goto end
  return_172:
    StrCpy $R0 $R5
    Goto end
  return_other:
    StrCpy $R0 $R6

  end:
    Pop $R6
    Pop $R5
    Pop $R4
    Pop $R3
    Pop $R2
    Pop $R1
FunctionEnd
```

### 安装后操作

**目录创建**：
- `{app}\config` - 用户配置目录
- `{app}\_internal\config` - 配置模板备份
- `{app}\temp` - 临时文件目录
- `{app}\data` - 数据目录

**配置文件处理**：
- 新安装：从 `_internal\config\worker.yaml` 复制模板，逐行替换用户输入值
- 升级安装：保留原有 `config\worker.yaml`，不修改

**配置替换机制**：
使用 NSIS `FileOpen`/`FileRead`/`FileWrite` 逐行读取模板文件，匹配特定字段行并替换：
```nsis
; 示例：替换 ip: null 行为用户输入的 IP
FileOpen $4 "$INSTDIR\_internal\config\worker.yaml" r
FileOpen $5 "$INSTDIR\config\worker.yaml" w
Loop:
  FileRead $4 $6
  StrCmp $6 "" Close
  ; 检查是否是 ip: null 行
  StrCmp $6 "  ip: null$\r$\n" 0 NotIpLine
    StrCpy $6 "  ip: \"$IpInput\"$\r$\n"
  NotIpLine:
  ; ... 其他字段替换
  FileWrite $5 $6
  Goto Loop
Close:
  FileClose $4
  FileClose $5
```

**启动程序**：
- 交互安装：完成页面显示"启动"复选框，勾选后启动（带 UAC 提升）
- 静默安装：自动启动（不带 UAC，作为当前用户运行）

### 卸载功能

**卸载时执行的操作**：
- 杀进程（按安装路径筛选）
- 删除所有目录（包括 config、logs、data、temp）
- 删除快捷方式
- 删除安装目录所有文件

**卸载行为变更说明**：
- 原 Inno Setup 卸载时保留 config 目录
- 新 NSIS 方案卸载时完全删除所有文件（包括 config）
- 变更原因：用户确认，干净卸载不留残留文件；升级安装时 config 目录会被保留（升级检测机制）

### 构建脚本改造

**PowerShell 构建脚本改造要点**：

```powershell
# 检测 NSIS 安装路径
$NsisPath = "C:\Program Files (x86)\NSIS\makensis.exe"
if (-not (Test-Path $NsisPath)) {
    $NsisPath = "C:\Program Files\NSIS\makensis.exe"
}
if (-not (Test-Path $NsisPath)) {
    $NsisPath = (Get-Command makensis -ErrorAction SilentlyContinue).Source
}

# 从 worker.yaml 读取默认值
$PlatformApi = ...
$OcrService = ...

# 编译命令
& $NsisPath "/DVersion=$Version" "/DPlatformApi=$PlatformApi" "/DOcrService=$OcrService" "installer\installer.nsi"
```

## 依赖安装

| 软件 | 说明 | 下载地址 |
|------|------|----------|
| NSIS 3.x | Nullsoft Scriptable Install System，约 3MB | https://nsis.sourceforge.io/Download |

**安装步骤**：
1. 下载 NSIS 3.x（推荐 3.09 或更高版本）
2. 运行安装程序，默认安装到 `C:\Program Files (x86)\NSIS`
3. 无需安装额外插件（Modern UI 2 和 nsDialogs 都是内置的）

**验证安装**：
```powershell
makensis /VERSION
```

## 风险和注意事项

1. **NSIS 学习曲线**：语法与 Inno Setup Pascal 不同，需要学习基本语法
2. **进程名验证**：ios.exe 进程名需在实际环境中验证
3. **测试覆盖**：需要测试新安装、升级安装、静默安装、卸载等场景
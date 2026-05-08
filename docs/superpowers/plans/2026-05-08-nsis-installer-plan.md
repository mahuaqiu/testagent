# NSIS Installer Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将打包方案从 Inno Setup 切换到 NSIS，保留所有现有功能。

**Architecture:** 使用 NSIS Modern UI 2 + nsDialogs 创建安装向导，包含配置参数页面、进程清理、自动 IP 检测、配置文件替换等功能。构建脚本从 worker.yaml 读取默认值传给 NSIS。

**Tech Stack:** NSIS 3.x, Modern UI 2, nsDialogs, PowerShell

---

## Task 1: 创建 NSIS 主脚本基本结构

**Files:**
- Create: `installer/installer.nsi`

- [ ] **Step 1: 创建产品元数据和 Modern UI 2 配置**

创建文件 `installer/installer.nsi`，写入基本配置：

```nsis
; installer/installer.nsi
; Test Worker Install Script
; NSIS Modern UI 2

; 产品元数据（VERSION 从构建脚本传入）
!define PRODUCT_NAME "Test Worker"
!define PRODUCT_VERSION "${VERSION}"
!define PRODUCT_PUBLISHER "Test Worker Team"
!define PRODUCT_DIR_REGKEY "Software\Microsoft\Windows\CurrentVersion\App Paths\test-worker.exe"
!define PRODUCT_UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCT_NAME}"

; Modern UI 2 配置
!include "MUI2.nsh"
!define MUI_ABORTWARNING
!define MUI_ICON "..\assets\icon.ico"
!define MUI_UNICON "..\assets\icon.ico"
!define MUI_WELCOMEFINISHPAGE_BITMAP "..\installer\header.bmp"
!define MUI_FINISHPAGE_RUN "$INSTDIR\test-worker.exe"
!define MUI_FINISHPAGE_RUN_TEXT "启动 Test Worker"
!define MUI_FINISHPAGE_RUN_NOTCHECKED

; 安装程序基本信息
Name "${PRODUCT_NAME} ${PRODUCT_VERSION}"
OutFile "..\dist\test-worker-installer.exe"
InstallDir "$PROGRAMFILES64\${PRODUCT_NAME}"
InstallDirRegKey HKLM "${PRODUCT_DIR_REGKEY}" ""
ShowInstDetails show
RequestExecutionLevel admin
SetCompressor /SOLID lzma

; 中文语言
!insertmacro MUI_LANGUAGE "SimpChinese"

; 变量定义
Var IpInput
Var PortInput
Var NamespaceInput
Var PlatformApiInput
Var OcrServiceInput
Var DiscoverAndroid
Var DiscoverIos
Var DesktopCheckbox
Var IsUpgrade
```

- [ ] **Step 2: 定义安装页面顺序**

添加页面定义：

```nsis
; 页面顺序
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
Page custom ConfigPageCreate ConfigPageLeave
!define MUI_PAGE_CUSTOMFUNCTION_PRE KillProcessesAndCleanup
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

; 卸载页面
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
```

- [ ] **Step 3: 定义安装 Section**

添加安装 Section：

```nsis
Section "MainSection" SEC01
  ; 创建目录
  CreateDirectory "$INSTDIR\config"
  CreateDirectory "$INSTDIR\_internal\config"
  CreateDirectory "$INSTDIR\temp"
  CreateDirectory "$INSTDIR\data"

  ; 复制文件（排除根目录 config）
  SetOutPath "$INSTDIR"
  File /r /x "config" "..\dist\windows\test-worker\*"

  ; 创建快捷方式
  CreateDirectory "$SMPROGRAMS\${PRODUCT_NAME}"
  CreateShortCut "$SMPROGRAMS\${PRODUCT_NAME}\${PRODUCT_NAME}.lnk" "$INSTDIR\test-worker.exe"
  CreateShortCut "$SMPROGRAMS\${PRODUCT_NAME}\卸载 ${PRODUCT_NAME}.lnk" "$INSTDIR\uninst.exe"

  ; 桌面快捷方式（根据复选框）
  ${If} $DesktopCheckbox == ${BST_CHECKED}
    CreateShortCut "$DESKTOP\${PRODUCT_NAME}.lnk" "$INSTDIR\test-worker.exe"
  ${EndIf}

  ; 写入卸载程序
  WriteUninstaller "$INSTDIR\uninst.exe"

  ; 写入注册表
  WriteRegStr HKLM "${PRODUCT_DIR_REGKEY}" "" "$INSTDIR\test-worker.exe"
  WriteRegStr HKLM "${PRODUCT_UNINST_KEY}" "DisplayName" "${PRODUCT_NAME}"
  WriteRegStr HKLM "${PRODUCT_UNINST_KEY}" "UninstallString" "$INSTDIR\uninst.exe"
  WriteRegStr HKLM "${PRODUCT_UNINST_KEY}" "DisplayVersion" "${PRODUCT_VERSION}"
  WriteRegStr HKLM "${PRODUCT_UNINST_KEY}" "Publisher" "${PRODUCT_PUBLISHER}"
SectionEnd
```

- [ ] **Step 4: 提交基本结构**

```bash
git add installer/installer.nsi
git commit -m "feat: 创建 NSIS 主脚本基本结构"
```

---

## Task 2: 实现进程清理和 Playwright 目录删除

**Files:**
- Modify: `installer/installer.nsi`

- [ ] **Step 1: 添加进程清理函数**

在 `installer.nsi` 中添加 `KillProcessesAndCleanup` 函数：

```nsis
Function KillProcessesAndCleanup
  ; 1. 杀主进程（全局杀，名称唯一）
  ExecWait '"taskkill" /f /im test-worker.exe' $0

  ; 2. 准备路径变量（确保末尾有斜杠，避免匹配到其他路径）
  StrCpy $2 "$INSTDIR"
  StrCpy $3 "$2\"  ; 路径末尾加分隔符

  ; 3. PowerShell 按路径精准杀 ios、adb、ffmpeg
  ; 注意：NSIS 字符串中 \ 是字面反斜杠，不需要转义
  ; PowerShell 单引号字符串中 \ 也是字面反斜杠
  ; $2 展开后路径末尾无反斜杠，$2\* 匹配该路径下的所有子目录
  ; $3 展开后路径末尾有反斜杠，$3* 匹配该路径下的所有文件
  StrCpy $1 '"powershell" -NoProfile -ExecutionPolicy Bypass -Command "$p = Get-Process -Name ios,adb,ffmpeg -ErrorAction SilentlyContinue; foreach ($x in $p) { if ($x.Path -like ''$3*'' -or $x.Path -like ''$2\*'') { $x.Kill() } }"'
  ExecWait $1 $0

  ; 4. 删除 playwright 目录（避免升级不兼容问题）
  IfFileExists "$INSTDIR\playwright\*.*" 0 NoPlaywright
    RMDir /r "$INSTDIR\playwright"
  NoPlaywright:
FunctionEnd
```

- [ ] **Step 2: 提交进程清理函数**

```bash
git add installer/installer.nsi
git commit -m "feat: 添加进程清理和 Playwright 目录删除函数"
```

---

## Task 3: 实现自动 IP 检测函数

**Files:**
- Modify: `installer/installer.nsi`

- [ ] **Step 1: 添加 GetLocalIP 函数**

在 `installer.nsi` 中添加 `GetLocalIP` 函数：

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

- [ ] **Step 2: 提交 IP 检测函数**

```bash
git add installer/installer.nsi
git commit -m "feat: 添加自动 IP 检测函数"
```

---

## Task 4: 实现配置参数页面

**Files:**
- Modify: `installer/installer.nsi`

- [ ] **Step 1: 添加 nsDialogs 头文件引用**

在变量定义后添加：

```nsis
; nsDialogs 用于自定义页面
!include "nsDialogs.nsh"
```

- [ ] **Step 2: 添加升级检测函数**

```nsis
Function IsUpgradeInstall
  ; 检查是否是升级安装（config\worker.yaml 存在）
  IfFileExists "$INSTDIR\config\worker.yaml" 0 not_upgrade
    StrCpy $IsUpgrade "1"
    Goto done
  not_upgrade:
    StrCpy $IsUpgrade "0"
  done:
FunctionEnd
```

- [ ] **Step 3: 添加配置页面创建函数**

```nsis
Function ConfigPageCreate
  ; 检查是否升级安装
  Call IsUpgradeInstall
  StrCmp $IsUpgrade "1" skip_page

  ; 创建自定义页面
  !insertmacro MUI_HEADER_TEXT "配置 Worker 参数" "请填写以下配置信息"

  nsDialogs::Create 1018
  Pop $0

  ; IP 地址
  ${NSD_CreateLabel} 0 0 100% 12u "Worker IP 地址:"
  ${NSD_CreateText} 0 18 300 12u ""
  Pop $IpInput
  Call GetLocalIP
  ${NSD_SetText} $IpInput $R0

  ; 端口
  ${NSD_CreateLabel} 0 44 100% 12u "Worker 端口:"
  ${NSD_CreateText} 0 62 100 12u "8088"
  Pop $PortInput

  ; 命名空间
  ${NSD_CreateLabel} 0 88 100% 12u "命名空间:"
  ${NSD_CreateText} 0 106 200 12u "meeting_public"
  Pop $NamespaceInput

  ; 平台 API 地址
  ${NSD_CreateLabel} 0 132 100% 12u "平台 API 地址:"
  ${NSD_CreateText} 0 150 350 12u "${PLATFORM_API}"
  Pop $PlatformApiInput

  ; OCR 服务地址
  ${NSD_CreateLabel} 0 176 100% 12u "OCR 服务地址:"
  ${NSD_CreateText} 0 194 350 12u "${OCR_SERVICE}"
  Pop $OcrServiceInput

  ; 设备发现选项
  ${NSD_CreateLabel} 0 220 100% 12u "设备发现选项:"
  ${NSD_CreateCheckbox} 0 238 80 12u "Android"
  Pop $DiscoverAndroid
  ${NSD_CreateCheckbox} 90 238 80 12u "iOS"
  Pop $DiscoverIos

  ; 桌面快捷方式
  ${NSD_CreateCheckbox} 0 264 100% 12u "创建桌面快捷方式"
  Pop $DesktopCheckbox
  ${NSD_SetState} $DesktopCheckbox ${BST_CHECKED}

  nsDialogs::Show

  skip_page:
FunctionEnd
```

- [ ] **Step 4: 添加配置页面离开函数**

```nsis
Function ConfigPageLeave
  ; 获取用户输入
  ${NSD_GetText} $IpInput $IpInput
  ${NSD_GetText} $PortInput $PortInput
  ${NSD_GetText} $NamespaceInput $NamespaceInput
  ${NSD_GetText} $PlatformApiInput $PlatformApiInput
  ${NSD_GetText} $OcrServiceInput $OcrServiceInput
  ${NSD_GetState} $DiscoverAndroid $DiscoverAndroid
  ${NSD_GetState} $DiscoverIos $DiscoverIos
  ${NSD_GetState} $DesktopCheckbox $DesktopCheckbox
FunctionEnd
```

- [ ] **Step 5: 提交配置页面**

```bash
git add installer/installer.nsi
git commit -m "feat: 添加配置参数页面（nsDialogs）"
```

---

## Task 5: 实现配置文件替换机制

**Files:**
- Modify: `installer/installer.nsi`

- [ ] **Step 1: 添加配置文件替换函数**

```nsis
Function ReplaceConfigFile
  ; 仅在新安装时执行
  StrCmp $IsUpgrade "1" done

  ; 复制模板文件到用户配置目录
  CopyFiles "$INSTDIR\_internal\config\worker.yaml" "$INSTDIR\config\worker.yaml"

  ; 逐行替换用户输入值
  FileOpen $4 "$INSTDIR\config\worker.yaml" r
  FileOpen $5 "$INSTDIR\config\worker.yaml.new" w

  Loop:
    FileRead $4 $6
    StrCmp $6 "" Close

    ; 替换 ip: null
    StrCmp $6 "  ip: null$\r$\n" 0 NotIpLine
      StrCpy $6 "  ip: \"$IpInput\"$\r$\n"
    NotIpLine:

    ; 替换 port
    StrCmp $6 "  port: 8088$\r$\n" 0 NotPortLine
      StrCpy $6 "  port: $PortInput$\r$\n"
    NotPortLine:

    ; 替换 namespace
    StrCmp $6 "  namespace: meeting_public$\r$\n" 0 NotNamespaceLine
      StrCpy $6 "  namespace: $NamespaceInput$\r$\n"
    NotNamespaceLine:

    ; 替换 platform_api
    StrCmp $6 '  platform_api: "${PLATFORM_API}"$\r$\n' 0 NotPlatformApiLine
      StrCpy $6 '  platform_api: "$PlatformApiInput"$\r$\n'
    NotPlatformApiLine:

    ; 替换 ocr_service
    StrCmp $6 '  ocr_service: "${OCR_SERVICE}"$\r$\n' 0 NotOcrServiceLine
      StrCpy $6 '  ocr_service: "$OcrServiceInput"$\r$\n'
    NotOcrServiceLine:

    ; 替换 discover_android_devices
    StrCmp $6 "  discover_android_devices: false$\r$\n" 0 NotAndroidLine
      ${If} $DiscoverAndroid == ${BST_CHECKED}
        StrCpy $6 "  discover_android_devices: true$\r$\n"
      ${EndIf}
    NotAndroidLine:

    ; 替换 discover_ios_devices
    StrCmp $6 "  discover_ios_devices: false$\r$\n" 0 NotIosLine
      ${If} $DiscoverIos == ${BST_CHECKED}
        StrCpy $6 "  discover_ios_devices: true$\r$\n"
      ${EndIf}
    NotIosLine:

    FileWrite $5 $6
    Goto Loop

  Close:
    FileClose $4
    FileClose $5

    ; 替换原文件
    Delete "$INSTDIR\config\worker.yaml"
    Rename "$INSTDIR\config\worker.yaml.new" "$INSTDIR\config\worker.yaml"

  done:
FunctionEnd
```

- [ ] **Step 2: 在 Section 中调用配置替换**

修改 Section SEC01，在复制文件后添加：

```nsis
  ; 配置文件替换
  Call ReplaceConfigFile
```

- [ ] **Step 3: 提交配置文件替换**

```bash
git add installer/installer.nsi
git commit -m "feat: 添加配置文件替换机制"
```

---

## Task 6: 实现卸载功能

**Files:**
- Modify: `installer/installer.nsi`

- [ ] **Step 1: 添加卸载 Section**

```nsis
Section Uninstall
  ; 杀进程
  ExecWait '"taskkill" /f /im test-worker.exe' $0
  StrCpy $2 "$INSTDIR"
  StrCpy $3 "$2\"
  ; 注意：$2\* 匹配路径下的所有子目录（单反斜杠）
  StrCpy $1 '"powershell" -NoProfile -ExecutionPolicy Bypass -Command "$p = Get-Process -Name ios,adb,ffmpeg -ErrorAction SilentlyContinue; foreach ($x in $p) { if ($x.Path -like ''$3*'' -or $x.Path -like ''$2\*'') { $x.Kill() } }"'
  ExecWait $1 $0

  ; 删除快捷方式
  Delete "$DESKTOP\${PRODUCT_NAME}.lnk"
  Delete "$SMPROGRAMS\${PRODUCT_NAME}\*"
  RMDir "$SMPROGRAMS\${PRODUCT_NAME}"

  ; 删除所有目录
  RMDir /r "$INSTDIR\config"
  RMDir /r "$INSTDIR\logs"
  RMDir /r "$INSTDIR\data"
  RMDir /r "$INSTDIR\temp"
  RMDir /r "$INSTDIR\playwright"

  ; 删除安装目录所有文件
  RMDir /r "$INSTDIR"

  ; 删除注册表
  DeleteRegKey HKLM "${PRODUCT_UNINST_KEY}"
  DeleteRegKey HKLM "${PRODUCT_DIR_REGKEY}"
SectionEnd
```

- [ ] **Step 2: 提交卸载功能**

```bash
git add installer/installer.nsi
git commit -m "feat: 添加卸载功能"
```

---

## Task 7: 添加静默安装启动和 .onInit 函数

**Files:**
- Modify: `installer/installer.nsi`

- [ ] **Step 1: 添加 .onInit 函数处理命令行参数**

```nsis
Function .onInit
  ; 获取命令行参数
  ${GetParameters} $0

  ; 解析参数（可选：支持 /IP= /PORT= 等）
  ${GetOption} $0 "/IP=" $1
  StrCmp $1 "" 0 +2
    StrCpy $IpInput $1

  ${GetOption} $0 "/PORT=" $1
  StrCmp $1 "" 0 +2
    StrCpy $PortInput $1

  ${GetOption} $0 "/NAMESPACE=" $1
  StrCmp $1 "" 0 +2
    StrCpy $NamespaceInput $1

  ${GetOption} $0 "/PLATFORM_API=" $1
  StrCmp $1 "" 0 +2
    StrCpy $PlatformApiInput $1

  ${GetOption} $0 "/OCR_SERVICE=" $1
  StrCmp $1 "" 0 +2
    StrCpy $OcrServiceInput $1

  ; 如果静默安装且未提供 IP，自动检测
  IfSilent 0 done
    StrCmp $IpInput "" 0 done
    Call GetLocalIP
    StrCpy $IpInput $R0
  done:
FunctionEnd
```

- [ ] **Step 2: 添加 .onInstSuccess 函数**

```nsis
Function .onInstSuccess
  ; 静默安装时自动启动程序
  IfSilent 0 done
    ; 使用 Explorer 启动，避免 UAC 提升问题
    Exec '"$WINDIR\explorer.exe" "$INSTDIR\test-worker.exe"'
  done:
FunctionEnd
```

- [ ] **Step 3: 添加 GetParameters 和 GetOption 函数定义**

添加必要的辅助函数：

```nsis
; 命令行参数解析辅助函数
!include "FileFunc.nsh"
!insertmacro GetParameters
!insertmacro GetOption
```

- [ ] **Step 4: 提交静默安装支持**

```bash
git add installer/installer.nsi
git commit -m "feat: 添加静默安装支持和命令行参数解析"
```

---

## Task 8: 修改构建脚本

**Files:**
- Modify: `installer/build_installer.ps1`

- [ ] **Step 1: 读取当前构建脚本**

当前脚本路径：`installer/build_installer.ps1`

- [ ] **Step 2: 替换 Inno Setup 检测为 NSIS 检测**

修改构建脚本，替换 Inno Setup 相关代码为：

```powershell
# installer/build_installer.ps1
# Windows Installer Build Script (NSIS)

param(
    [string]$Version = "",
    [string]$BuildOutput = "..\dist\windows\test-worker"
)

Write-Host "=========================================="
Write-Host "Building Test Worker Installer (NSIS)"
Write-Host "=========================================="

# Check NSIS
$NsisPath = "C:\Program Files (x86)\NSIS\makensis.exe"
if (-not (Test-Path $NsisPath)) {
    $NsisPath = "C:\Program Files\NSIS\makensis.exe"
}
if (-not (Test-Path $NsisPath)) {
    $NsisPath = (Get-Command makensis -ErrorAction SilentlyContinue).Source
}
if (-not $NsisPath -or -not (Test-Path $NsisPath)) {
    Write-Error "NSIS not found!"
    Write-Host "Please download from: https://nsis.sourceforge.io/Download"
    exit 1
}

Write-Host "NSIS found: $NsisPath"

# Check build output
$OutputDir = Join-Path $PSScriptRoot $BuildOutput
if (-not (Test-Path $OutputDir)) {
    Write-Error "Build output not found: $OutputDir"
    Write-Host "Please run scripts/build_windows.ps1 first"
    exit 1
}

# Auto-read version
if ($Version -eq "") {
    $VersionFile = Join-Path $OutputDir "VERSION"
    if (Test-Path $VersionFile) {
        $Version = Get-Content $VersionFile -Raw
        $Version = $Version.Trim()
        Write-Host "Auto-detected version: $Version"
    } else {
        $Version = Get-Date -Format "yyyyMMddHHmm"
        Write-Host "No VERSION file found, using timestamp: $Version"
    }
}

Write-Host "Version: $Version"

# Read default values from worker.yaml
$WorkerYaml = Join-Path $PSScriptRoot "..\config\worker.yaml"
if (Test-Path $WorkerYaml) {
    $YamlContent = Get-Content $WorkerYaml
    
    # Extract platform_api
    $PlatformApiMatch = $YamlContent | Select-String 'platform_api:\s*"([^"]+)"'
    if ($PlatformApiMatch) {
        $PlatformApi = $PlatformApiMatch.Matches.Groups[1].Value
        Write-Host "Platform API from config: $PlatformApi"
    } else {
        $PlatformApi = "http://192.168.0.102:8000"
        Write-Host "Platform API default: $PlatformApi"
    }
    
    # Extract ocr_service
    $OcrServiceMatch = $YamlContent | Select-String 'ocr_service:\s*"([^"]+)"'
    if ($OcrServiceMatch) {
        $OcrService = $OcrServiceMatch.Matches.Groups[1].Value
        Write-Host "OCR Service from config: $OcrService"
    } else {
        $OcrService = "http://192.168.0.102:9021"
        Write-Host "OCR Service default: $OcrService"
    }
} else {
    Write-Warning "worker.yaml not found, using hardcoded defaults"
    $PlatformApi = "http://192.168.0.102:8000"
    $OcrService = "http://192.168.0.102:9021"
}

# Check dist directory
$DistDir = Join-Path $PSScriptRoot "..\dist"
if (-not (Test-Path $DistDir)) {
    New-Item -ItemType Directory -Force -Path $DistDir | Out-Null
}

# Compile installer script
Write-Host "Compiling installer script..."
$ScriptPath = Join-Path $PSScriptRoot "installer.nsi"

& $NsisPath "/DVERSION=$Version" "/DPLATFORM_API=$PlatformApi" "/DOCR_SERVICE=$OcrService" $ScriptPath

if ($LASTEXITCODE -ne 0) {
    Write-Error "NSIS compilation failed!"
    exit 1
}

$InstallerPath = Join-Path $DistDir "test-worker-installer.exe"
if (-not (Test-Path $InstallerPath)) {
    Write-Error "Installer not generated: $InstallerPath"
    exit 1
}

Write-Host "=========================================="
Write-Host "Installer build complete!"
Write-Host "Output: $InstallerPath"
Write-Host "Size: $([math]::Round((Get-Item $InstallerPath).Length / 1MB, 2)) MB"
Write-Host "=========================================="
```

- [ ] **Step 3: 提交构建脚本修改**

```bash
git add installer/build_installer.ps1
git commit -m "feat: 修改构建脚本使用 NSIS 替代 Inno Setup"
```

---

## Task 9: 删除旧的 Inno Setup 脚本

**Files:**
- Delete: `installer/installer.iss`

- [ ] **Step 1: 删除 installer.iss**

```bash
git rm installer/installer.iss
git commit -m "chore: 删除旧的 Inno Setup 脚本"
```

---

## Task 10: 测试安装包构建

**Files:**
- None (验证)

- [ ] **Step 1: 检查 NSIS 是否已安装**

```powershell
makensis /VERSION
```

Expected: 显示 NSIS 版本号（如 3.09）

- [ ] **Step 2: 运行构建脚本**

先确保已有构建输出：

```powershell
# 如果没有构建输出，先运行构建
powershell scripts/build_windows.ps1 -BuildInstaller
```

或者单独运行安装包构建：

```powershell
powershell installer/build_installer.ps1 -Version "20260508-120000"
```

Expected: 成功生成 `dist\test-worker-installer.exe`

- [ ] **Step 3: 验证安装包**

```powershell
# 检查文件大小
(Get-Item dist\test-worker-installer.exe).Length / 1MB
```

Expected: 约 50-100 MB（取决于 Nuitka 构建输出大小）

- [ ] **Step 4: 提交最终改动**

```bash
git add -A
git commit -m "feat: 完成 NSIS 打包方案改造"
```

---

## Task 11: 验证安装功能

**Files:**
- None (验证)

- [ ] **Step 1: 测试新安装**

运行安装包，手动安装：

```powershell
# 运行安装包（交互模式）
Start-Process dist\test-worker-installer.exe
```

验证点：
- 安装向导显示正常（欢迎、路径选择、配置参数、安装进度、完成页面）
- 配置参数页面默认值正确（IP 自动检测、端口 8088、命名空间 meeting_public、API/OCR 地址来自 worker.yaml）
- 安装完成后程序目录结构正确
- 桌面快捷方式已创建（默认勾选）
- 启动程序正常（完成页面勾选"启动 Test Worker"）

- [ ] **Step 2: 检查配置文件**

```powershell
# 检查配置文件内容
Get-Content "C:\Program Files\Test Worker\config\worker.yaml" | Select-String "ip:|port:|namespace:"
```

Expected: 配置文件包含用户输入的值

- [ ] **Step 3: 测试升级安装**

在已有安装上再次运行安装包：

```powershell
# 再次运行安装包（升级模式）
Start-Process dist\test-worker-installer.exe
```

验证点：
- 配置参数页面被跳过（检测到 `config\worker.yaml` 存在）
- 用户原有配置保留
- 进程清理正常（test-worker.exe、ios、adb、ffmpeg 被杀掉）
- Playwright 目录被删除

- [ ] **Step 4: 测试静默安装**

先卸载现有安装，然后测试静默安装：

```powershell
# 静默安装（自动启动）
Start-Process dist\test-worker-installer.exe -ArgumentList "/S" -Wait

# 验证程序是否启动
Get-Process test-worker -ErrorAction SilentlyContinue
```

Expected: 安装完成后程序自动启动

- [ ] **Step 5: 测试静默安装命令行参数**

```powershell
# 静默安装 + 自定义参数
Start-Process dist\test-worker-installer.exe -ArgumentList "/S /IP=192.168.1.100 /PORT=9000 /NAMESPACE=test_ns" -Wait

# 检查配置文件
Get-Content "C:\Program Files\Test Worker\config\worker.yaml" | Select-String "192.168.1.100|9000|test_ns"
```

Expected: 配置文件包含命令行参数指定的值

---

## Task 12: 验证卸载功能

**Files:**
- None (验证)

- [ ] **Step 1: 测试卸载**

```powershell
# 运行卸载程序
Start-Process "C:\Program Files\Test Worker\uninst.exe" -Wait
```

验证点：
- 进程清理正常（test-worker.exe、ios、adb、ffmpeg 被杀掉）
- 所有目录被删除（config、logs、data、temp、playwright）
- 快捷方式被删除（桌面、开始菜单）
- 注册表被清理

- [ ] **Step 2: 验证卸载完整性**

```powershell
# 检查安装目录是否存在
Test-Path "C:\Program Files\Test Worker"

# 检查注册表是否清理
Test-Path "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Test Worker"
```

Expected: 都返回 False（目录和注册表已删除）

---

## Notes

- NSIS 语法与 Inno Setup Pascal 不同，注意字符串转义规则
- 进程名 `ios` 不需要 `.exe` 后缀（PowerShell `-Name` 参数）
- 静默安装使用 `/S` 参数，安装完成后自动启动程序
- 卸载时完全删除所有文件（包括 config 目录）
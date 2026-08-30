; installer/installer.nsi
; Test Worker Install Script
; NSIS Modern UI 2

; Product metadata (VERSION passed from build script)
!define PRODUCT_NAME "Test Worker"
!define PRODUCT_VERSION "${VERSION}"
!define PRODUCT_PUBLISHER "Test Worker Team"
!define PRODUCT_DIR_REGKEY "Software\Microsoft\Windows\CurrentVersion\App Paths\test-worker.exe"
!define PRODUCT_UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCT_NAME}"

; Modern UI 2 configuration
!include "MUI2.nsh"
!define MUI_ABORTWARNING
!define MUI_ICON "..\assets\icon.ico"
!define MUI_UNICON "..\assets\icon.ico"
!define MUI_FINISHPAGE_RUN "$INSTDIR\test-worker.exe"
!define MUI_FINISHPAGE_RUN_TEXT "Launch Test Worker"
!define MUI_FINISHPAGE_RUN_NOTCHECKED

; Installer basic info
Name "${PRODUCT_NAME} ${PRODUCT_VERSION}"
OutFile "..\dist\test-worker-installer.exe"
InstallDir "$PROGRAMFILES64\${PRODUCT_NAME}"
InstallDirRegKey HKLM "${PRODUCT_DIR_REGKEY}" ""
ShowInstDetails show
RequestExecutionLevel admin
SetCompressor /SOLID lzma

; Command line parameter parsing helpers
!include "FileFunc.nsh"
!insertmacro GetParameters
!insertmacro GetOptions

; nsDialogs for custom pages
!include "nsDialogs.nsh"

; Variables
Var DetectedIP        ; IP detected in .onInit (stored separately from IpInput control handle)
Var IpInput
Var PortInput
Var NamespaceInput
Var PlatformApiInput
Var OcrServiceInput
Var DiscoverAndroid
Var DiscoverIos
Var DiscoverHarmonyMobile
Var DiscoverHarmonyPc
Var AutoStart
Var IsUpgrade

; Page order
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
Page custom ConfigPageCreate ConfigPageLeave
Page custom OptionsPageCreate OptionsPageLeave
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

; Uninstaller pages
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

; Chinese language (must be after page macros)
!insertmacro MUI_LANGUAGE "SimpChinese"

; ============================================
; Install Section
; ============================================
Section "MainSection" SEC01
  ; 64 位应用目录 + 64 位 Worker：注册表统一走 64 位视图，避免写到 WOW6432Node
  SetRegView 64

  ; Check upgrade install before any operations (needed for silent install)
  Call IsUpgradeInstall

  ; Kill processes first (show progress before heavy operations)
  DetailPrint "Stopping running processes..."
  Call KillProcessesAndCleanup

  ; Create directories
  CreateDirectory "$INSTDIR\config"
  CreateDirectory "$INSTDIR\_internal\config"
  CreateDirectory "$INSTDIR\temp"
  CreateDirectory "$INSTDIR\data"

  ; Copy files - first copy _internal directory (includes _internal\config)
  DetailPrint "Copying internal files..."
  SetOutPath "$INSTDIR\_internal"
  File /r "..\dist\windows\test-worker\_internal\*"

  ; Copy all other files (exclude root config and _internal to avoid duplication)
  DetailPrint "Copying program files..."
  SetOutPath "$INSTDIR"
  File /r /x "config" /x "_internal" /x "worker.log" "..\dist\windows\test-worker\*"

  ; Replace config file with user input
  Call ReplaceConfigFile

  ; Create shortcuts
  CreateDirectory "$SMPROGRAMS\${PRODUCT_NAME}"
  CreateShortCut "$SMPROGRAMS\${PRODUCT_NAME}\${PRODUCT_NAME}.lnk" "$INSTDIR\test-worker.exe"
  CreateShortCut "$SMPROGRAMS\${PRODUCT_NAME}\Uninstall ${PRODUCT_NAME}.lnk" "$INSTDIR\uninst.exe"

  ; Desktop shortcut
  CreateShortCut "$DESKTOP\${PRODUCT_NAME}.lnk" "$INSTDIR\test-worker.exe"

  ; 开机自启：仅全新安装处理，升级安装跳过
  StrCmp $IsUpgrade "1" skip_autostart
    StrCmp $AutoStart ${BST_CHECKED} 0 autostart_off
      ClearErrors
      WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Run" "test-worker" '"$INSTDIR\test-worker.exe"'
      IfErrors 0 autostart_done
        DetailPrint "Warning: 写入开机自启注册表失败，已跳过"
    autostart_off:
    autostart_done:
  skip_autostart:

  ; Write uninstaller
  WriteUninstaller "$INSTDIR\uninst.exe"

  ; Write registry keys
  WriteRegStr HKLM "${PRODUCT_DIR_REGKEY}" "" "$INSTDIR\test-worker.exe"
  WriteRegStr HKLM "${PRODUCT_UNINST_KEY}" "DisplayName" "${PRODUCT_NAME}"
  WriteRegStr HKLM "${PRODUCT_UNINST_KEY}" "UninstallString" "$INSTDIR\uninst.exe"
  WriteRegStr HKLM "${PRODUCT_UNINST_KEY}" "DisplayVersion" "${PRODUCT_VERSION}"
  WriteRegStr HKLM "${PRODUCT_UNINST_KEY}" "Publisher" "${PRODUCT_PUBLISHER}"
SectionEnd

; ============================================
; Uninstall Section
; ============================================
Section Uninstall
  ; 卸载时也使用 64 位注册表视图，与安装阶段保持一致
  SetRegView 64

  ; Kill processes (use nsExec to hide console window)
  StrCpy $1 '"taskkill" /f /im test-worker.exe'
  nsExec::Exec $1
  Pop $0

  ; 保留原有设备服务清理：ios、adb、ffmpeg 仍按安装目录过滤
  Call un.KillDeviceServiceProcesses

  ; HDC 只能清理 Worker 自己启动并登记的实例，不能按进程名或路径批量杀用户的 HDC
  Call un.KillOwnedHdcProcesses

  ; 清理开机自启注册表项
  DeleteRegValue HKLM "Software\Microsoft\Windows\CurrentVersion\Run" "test-worker"

  ; Delete shortcuts
  Delete "$DESKTOP\${PRODUCT_NAME}.lnk"
  Delete "$SMPROGRAMS\${PRODUCT_NAME}\*"
  RMDir "$SMPROGRAMS\${PRODUCT_NAME}"

  ; Delete all directories
  RMDir /r "$INSTDIR\config"
  RMDir /r "$INSTDIR\logs"
  RMDir /r "$INSTDIR\data"
  RMDir /r "$INSTDIR\temp"
  RMDir /r "$INSTDIR\playwright"

  ; Delete all files in install directory
  RMDir /r "$INSTDIR"

  ; Delete registry keys
  DeleteRegKey HKLM "${PRODUCT_UNINST_KEY}"
  DeleteRegKey HKLM "${PRODUCT_DIR_REGKEY}"
SectionEnd

; ============================================
; Functions
; ============================================

; Process cleanup and Playwright directory removal
Function KillProcessesAndCleanup
  ; 1. Kill main process (use nsExec to hide console window even if process not found)
  StrCpy $1 '"taskkill" /f /im test-worker.exe'
  nsExec::Exec $1
  Pop $0

  ; Kill window-class-finder (tools utility)
  StrCpy $1 '"taskkill" /f /im window-class-finder.exe'
  nsExec::Exec $1
  Pop $0

  ; 3. 保留原有 ios、adb、ffmpeg 清理逻辑
  Call KillDeviceServiceProcesses

  ; 4. 按项目归属记录回收 HDC
  Call KillOwnedHdcProcesses

  ; 5. Delete playwright directory (avoid upgrade incompatibility)
  IfFileExists "$INSTDIR\playwright\*.*" 0 NoPlaywright
    RMDir /r "$INSTDIR\playwright"
  NoPlaywright:

  ; 6. Delete data directory (clear worker.db and artifacts on upgrade install)
  IfFileExists "$INSTDIR\data\*.*" 0 NoData
    DetailPrint "Removing old data directory (worker.db and artifacts)..."
    ; Wait for killed processes to release SQLite file handles (WAL locks)
    Sleep 1000
    ; Explicitly delete SQLite files first (RMDir /r fails silently on locked files)
    Delete "$INSTDIR\data\worker.db"
    Delete "$INSTDIR\data\worker.db-wal"
    Delete "$INSTDIR\data\worker.db-shm"
    RMDir /r "$INSTDIR\data"
    ; Retry once if still present (slow handle release)
    IfFileExists "$INSTDIR\data\*.*" 0 NoData
      Sleep 1000
      RMDir /r "$INSTDIR\data"
  NoData:
FunctionEnd

; 保持原有设备服务清理行为，只处理安装目录中的 ios、adb、ffmpeg。
; HDC 不在这里处理，避免误杀非本项目启动的 HDC。
Function KillDeviceServiceProcesses
  StrCpy $2 "$INSTDIR"
  StrCpy $3 "$2\"
  DetailPrint "Killing device service processes..."
  StrCpy $1 "powershell -NoProfile -ExecutionPolicy Bypass -Command $\""
  StrCpy $1 "$1$$p = Get-Process -Name ios,adb,ffmpeg -ErrorAction SilentlyContinue; "
  StrCpy $1 "$1foreach ($$x in $$p) { "
  StrCpy $1 "$1  if ($$x.Path.StartsWith('$3', 1) -or $$x.Path.StartsWith('$2\\', 1)) { "
  StrCpy $1 "$1    $$x.Kill() "
  StrCpy $1 "$1  } "
  StrCpy $1 "$1}$\""
  nsExec::ExecToStack $1
  Pop $0
  Pop $0
FunctionEnd

; 只终止 data\hdc_processes.json 中由本项目启动的 HDC。
; 每个 PID 同时校验可执行文件路径和启动时间，避免 PID 重用或外部 HDC 被误杀。
Function KillOwnedHdcProcesses
  StrCpy $2 "$INSTDIR\data\hdc_processes.json"
  IfFileExists "$2" 0 done_owned_hdc
  DetailPrint "Killing Worker-owned HDC processes..."
  StrCpy $1 "powershell -NoProfile -ExecutionPolicy Bypass -Command $\""
  StrCpy $1 "$1$$path = '$2'; "
  StrCpy $1 "$1try { $$records = @(Get-Content -Raw -LiteralPath $$path | ConvertFrom-Json) } catch { $$records = @() }; "
  StrCpy $1 "$1foreach ($$record in $$records) { "
  StrCpy $1 "$1  $$p = Get-Process -Id ([int]$$record.pid) -ErrorAction SilentlyContinue; "
  StrCpy $1 "$1  if ($$null -eq $$p) { continue }; "
  StrCpy $1 "$1  try { $$samePath = ([IO.Path]::GetFullPath($$p.Path) -ieq [IO.Path]::GetFullPath([string]$$record.exe_path)) } catch { $$samePath = $$false }; "
  StrCpy $1 "$1  try { $$expected = [DateTimeOffset]::FromUnixTimeSeconds([int64][double]$$record.create_time).UtcDateTime; $$sameStart = ([Math]::Abs(($$p.StartTime.ToUniversalTime() - $$expected).TotalSeconds) -le 2) } catch { $$sameStart = $$false }; "
  StrCpy $1 "$1  if ($$samePath -and $$sameStart) { try { $$p.Kill() } catch {} }; "
  StrCpy $1 "$1}; Remove-Item -LiteralPath $$path -Force -ErrorAction SilentlyContinue$\""
  nsExec::ExecToStack $1
  Pop $0
  Pop $0
  done_owned_hdc:
FunctionEnd

; 卸载段只能调用 un. 前缀函数，保留与安装升级一致的设备服务清理范围。
Function un.KillDeviceServiceProcesses
  StrCpy $2 "$INSTDIR"
  StrCpy $3 "$2\"
  DetailPrint "Killing device service processes..."
  StrCpy $1 "powershell -NoProfile -ExecutionPolicy Bypass -Command $\""
  StrCpy $1 "$1$$p = Get-Process -Name ios,adb,ffmpeg -ErrorAction SilentlyContinue; "
  StrCpy $1 "$1foreach ($$x in $$p) { "
  StrCpy $1 "$1  if ($$x.Path.StartsWith('$3', 1) -or $$x.Path.StartsWith('$2\\', 1)) { "
  StrCpy $1 "$1    $$x.Kill() "
  StrCpy $1 "$1  } "
  StrCpy $1 "$1}$\""
  nsExec::ExecToStack $1
  Pop $0
  Pop $0
FunctionEnd

; 仅清理由本 Worker 登记的 HDC，避免误杀用户自行启动的进程。
Function un.KillOwnedHdcProcesses
  StrCpy $2 "$INSTDIR\data\hdc_processes.json"
  IfFileExists "$2" 0 un_done_owned_hdc
  DetailPrint "Killing Worker-owned HDC processes..."
  StrCpy $1 "powershell -NoProfile -ExecutionPolicy Bypass -Command $\""
  StrCpy $1 "$1$$path = '$2'; "
  StrCpy $1 "$1try { $$records = @(Get-Content -Raw -LiteralPath $$path | ConvertFrom-Json) } catch { $$records = @() }; "
  StrCpy $1 "$1foreach ($$record in $$records) { "
  StrCpy $1 "$1  $$p = Get-Process -Id ([int]$$record.pid) -ErrorAction SilentlyContinue; "
  StrCpy $1 "$1  if ($$null -eq $$p) { continue }; "
  StrCpy $1 "$1  try { $$samePath = ([IO.Path]::GetFullPath($$p.Path) -ieq [IO.Path]::GetFullPath([string]$$record.exe_path)) } catch { $$samePath = $$false }; "
  StrCpy $1 "$1  try { $$expected = [DateTimeOffset]::FromUnixTimeSeconds([int64][double]$$record.create_time).UtcDateTime; $$sameStart = ([Math]::Abs(($$p.StartTime.ToUniversalTime() - $$expected).TotalSeconds) -le 2) } catch { $$sameStart = $$false }; "
  StrCpy $1 "$1  if ($$samePath -and $$sameStart) { try { $$p.Kill() } catch {} }; "
  StrCpy $1 "$1}; Remove-Item -LiteralPath $$path -Force -ErrorAction SilentlyContinue$\""
  nsExec::ExecToStack $1
  Pop $0
  Pop $0
  un_done_owned_hdc:
FunctionEnd

; Auto IP detection - registry only (no PowerShell fallback to avoid UI freeze)
Function GetLocalIP
  ; 网卡配置存放在 64 位注册表视图中
  SetRegView 64

  ; Output: $R0 = best IP address, or "0.0.0.0" if not found
  ; Strategy: registry enumeration (milliseconds, no PowerShell)
  ; Priority by IP segment: 10.x > 192.168.x > 172.16-31.x > others

  Push $R1  ; Subkey index
  Push $R2  ; Current IP
  Push $R3  ; 10.x IP
  Push $R4  ; 192.168.x IP
  Push $R5  ; 172.x IP
  Push $R6  ; Other IP

  StrCpy $R3 ""
  StrCpy $R4 ""
  StrCpy $R5 ""
  StrCpy $R6 ""

  ; Enumerate registry subkeys
  StrCpy $R1 0
  loop_registry:
    EnumRegKey $R2 HKLM "SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces" $R1
    StrCmp $R2 "" done_registry

    ; Try to read DhcpIPAddress
    ReadRegStr $R2 HKLM "SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces\$R2" "DhcpIPAddress"
    StrCmp $R2 "" try_static_registry
    Goto check_ip_registry

  try_static_registry:
    ReadRegStr $R2 HKLM "SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces\$R2" "IPAddress"

  check_ip_registry:
    ; Filter invalid IPs (empty, 0.0.0.0, 127.x)
    StrCmp $R2 "" next_registry
    StrCmp $R2 "0.0.0.0" next_registry
    StrCpy $R0 $R2 4
    StrCmp $R0 "127." next_registry

    ; Store by priority (only first found)
    StrCpy $R0 $R2 3
    StrCmp $R0 "10." store_10_registry
    StrCpy $R0 $R2 8
    StrCmp $R0 "192.168." store_192_registry
    StrCpy $R0 $R2 4
    StrCmp $R0 "172." store_172_registry
    StrCmp $R6 "" store_other_registry
    Goto next_registry

  store_10_registry:
    StrCmp $R3 "" 0 next_registry
    StrCpy $R3 $R2
    Goto next_registry
  store_192_registry:
    StrCmp $R4 "" 0 next_registry
    StrCpy $R4 $R2
    Goto next_registry
  store_172_registry:
    StrCmp $R5 "" 0 next_registry
    StrCpy $R5 $R2
    Goto next_registry
  store_other_registry:
    StrCpy $R6 $R2

  next_registry:
    IntOp $R1 $R1 + 1
    Goto loop_registry

  done_registry:
    ; Return by priority if found valid IP
    StrCmp $R3 "" 0 return_10_registry
    StrCmp $R4 "" 0 return_192_registry
    StrCmp $R5 "" 0 return_172_registry
    StrCmp $R6 "" 0 return_other_registry

    ; Registry failed - return default 0.0.0.0 (user must fill manually)
    StrCpy $R0 "0.0.0.0"
    Goto cleanup_registry

  return_10_registry:
    StrCpy $R0 $R3
    Goto cleanup_registry
  return_192_registry:
    StrCpy $R0 $R4
    Goto cleanup_registry
  return_172_registry:
    StrCpy $R0 $R5
    Goto cleanup_registry
  return_other_registry:
    StrCpy $R0 $R6
    Goto cleanup_registry

  cleanup_registry:
    Pop $R6
    Pop $R5
    Pop $R4
    Pop $R3
    Pop $R2
    Pop $R1
FunctionEnd

; Upgrade detection
Function IsUpgradeInstall
  ; Check if upgrade install (config\worker.yaml exists)
  IfFileExists "$INSTDIR\config\worker.yaml" 0 not_upgrade
    StrCpy $IsUpgrade "1"
    Goto done
  not_upgrade:
    StrCpy $IsUpgrade "0"
  done:
FunctionEnd
; Config page creation
Function ConfigPageCreate
  ; Check if upgrade install
  Call IsUpgradeInstall
  StrCmp $IsUpgrade "1" skip_page

  ; Create custom page
  !insertmacro MUI_HEADER_TEXT "Configure Worker Parameters" "Please fill in the following configuration"

  nsDialogs::Create 1018
  Pop $0

  ; Row 1: IP and Port on same line
  ${NSD_CreateLabel} 0 0 140 12u "Worker IP Address:"
  ${NSD_CreateText} 0 18 280 12u "$IpInput"
  Pop $IpInput

  ${NSD_CreateLabel} 300 0 80 12u "Port:"
  ${NSD_CreateText} 300 18 80 12u "$PortInput"
  Pop $PortInput

  ; Row 2: Namespace
  ${NSD_CreateLabel} 0 40 100% 12u "Namespace:"
  ${NSD_CreateText} 0 58 200 12u "$NamespaceInput"
  Pop $NamespaceInput

  ; Row 3: Platform API address
  ${NSD_CreateLabel} 0 80 100% 12u "Platform API Address:"
  ${NSD_CreateText} 0 98 350 12u "$PlatformApiInput"
  Pop $PlatformApiInput

  ; Row 4: OCR service address
  ${NSD_CreateLabel} 0 120 100% 12u "OCR Service Address:"
  ${NSD_CreateText} 0 138 350 12u "$OcrServiceInput"
  Pop $OcrServiceInput

  nsDialogs::Show

  skip_page:
FunctionEnd

; Config page leave
Function ConfigPageLeave
  ; 升级安装跳过配置页，没有可读取的控件。
  Call IsUpgradeInstall
  StrCmp $IsUpgrade "1" done

  ; 静默安装没有 nsDialogs 控件，保留 .onInit 中解析出的参数。
  IfSilent silent_install

  ; Get user input
  ${NSD_GetText} $IpInput $IpInput
  ${NSD_GetText} $PortInput $PortInput
  ${NSD_GetText} $NamespaceInput $NamespaceInput
  ${NSD_GetText} $PlatformApiInput $PlatformApiInput
  ${NSD_GetText} $OcrServiceInput $OcrServiceInput

  done:
  silent_install:
FunctionEnd

; 选项页创建：设备发现 + 开机自启
Function OptionsPageCreate
  ; 升级安装跳过（与配置页同模式）
  Call IsUpgradeInstall
  StrCmp $IsUpgrade "1" skip_options

  !insertmacro MUI_HEADER_TEXT "Worker Options" "Select discovery and startup options"

  nsDialogs::Create 1018
  Pop $0

  ; Device Discovery（从配置页迁移，拆两行更宽松）
  ${NSD_CreateLabel} 0 0 100% 12u "Device Discovery:"
  ${NSD_CreateCheckbox} 0 18 80 12u "Android"
  Pop $DiscoverAndroid
  ${NSD_CreateCheckbox} 100 18 60 12u "iOS"
  Pop $DiscoverIos
  ${NSD_CreateCheckbox} 0 40 90 12u "Harmony Mobile"
  Pop $DiscoverHarmonyMobile
  ${NSD_CreateCheckbox} 110 40 80 12u "Harmony PC"
  Pop $DiscoverHarmonyPc

  ; 分隔线
  ${NSD_CreateHLine} 0 70 100% 1u ""
  Pop $0

  ; Startup
  ${NSD_CreateLabel} 0 88 100% 12u "Startup:"
  ${NSD_CreateCheckbox} 0 106 140 12u "开机自动启动"
  Pop $AutoStart

  ; 默认勾上（控件创建后同步状态）
  ${NSD_SetState} $AutoStart ${BST_CHECKED}

  nsDialogs::Show

  skip_options:
FunctionEnd

; 选项页离开：读取勾选状态
Function OptionsPageLeave
  Call IsUpgradeInstall
  StrCmp $IsUpgrade "1" done

  ; 静默安装没有控件，保留 .onInit 默认值
  IfSilent silent_install

  ${NSD_GetState} $DiscoverAndroid $DiscoverAndroid
  ${NSD_GetState} $DiscoverIos $DiscoverIos
  ${NSD_GetState} $DiscoverHarmonyMobile $DiscoverHarmonyMobile
  ${NSD_GetState} $DiscoverHarmonyPc $DiscoverHarmonyPc
  ${NSD_GetState} $AutoStart $AutoStart

  done:
  silent_install:
FunctionEnd

; Config file replacement
Function ReplaceConfigFile
  ; Only execute for new install
  StrCmp $IsUpgrade "1" done

  ; Check if template file exists
  IfFileExists "$INSTDIR\_internal\config\worker.yaml" 0 no_template

  ; Copy template to user config directory first
  CopyFiles "$INSTDIR\_internal\config\worker.yaml" "$INSTDIR\config\worker.yaml"

  ; Store config path
  StrCpy $9 "$INSTDIR\config\worker.yaml"

  ; Build PowerShell script with variable substitution
  DetailPrint "Updating configuration file..."

  ; IP replacement - build command in segments
  ; Use nsExec::Exec to completely hide console window
  ; Use double-quoted NSIS string, PowerShell uses single quotes for -replace arguments
  StrCpy $1 "$\"powershell$\" -NoProfile -ExecutionPolicy Bypass -Command $\""
  StrCpy $1 "$1(Get-Content '$9') -replace '(?m)^\s*ip:.*$$', '  ip: $IpInput' | Set-Content '$9' -Encoding UTF8$\""
  nsExec::Exec $1
  Pop $0

  ; Port replacement
  StrCpy $1 "$\"powershell$\" -NoProfile -ExecutionPolicy Bypass -Command $\""
  StrCpy $1 "$1(Get-Content '$9') -replace '(?m)^\s*port:.*$$', '  port: $PortInput' | Set-Content '$9' -Encoding UTF8$\""
  nsExec::Exec $1
  Pop $0

  ; Namespace replacement
  StrCpy $1 "$\"powershell$\" -NoProfile -ExecutionPolicy Bypass -Command $\""
  StrCpy $1 "$1(Get-Content '$9') -replace '(?m)^\s*namespace:.*$$', '  namespace: $NamespaceInput' | Set-Content '$9' -Encoding UTF8$\""
  nsExec::Exec $1
  Pop $0

  ; 平台 API 和 OCR 服务都在安装页收集，必须写入用户配置。
  StrCpy $1 "$\"powershell$\" -NoProfile -ExecutionPolicy Bypass -Command $\""
  StrCpy $1 "$1(Get-Content '$9') -replace '(?m)^\s*platform_api:.*$$', '  platform_api: $PlatformApiInput' | Set-Content '$9' -Encoding UTF8$\""
  nsExec::Exec $1
  Pop $0

  StrCpy $1 "$\"powershell$\" -NoProfile -ExecutionPolicy Bypass -Command $\""
  StrCpy $1 "$1(Get-Content '$9') -replace '(?m)^\s*ocr_service:.*$$', '  ocr_service: $OcrServiceInput' | Set-Content '$9' -Encoding UTF8$\""
  nsExec::Exec $1
  Pop $0

  ; Device discovery - Android
  StrCmp $DiscoverAndroid ${BST_CHECKED} 0 android_unchecked
    StrCpy $1 "$\"powershell$\" -NoProfile -ExecutionPolicy Bypass -Command $\""
    StrCpy $1 "$1(Get-Content '$9') -replace '(?m)^\s*discover_android_devices:.*$$', '  discover_android_devices: true' | Set-Content '$9' -Encoding UTF8$\""
    nsExec::Exec $1
    Pop $0
    Goto skip_android
  android_unchecked:
    StrCpy $1 "$\"powershell$\" -NoProfile -ExecutionPolicy Bypass -Command $\""
    StrCpy $1 "$1(Get-Content '$9') -replace '(?m)^\s*discover_android_devices:.*$$', '  discover_android_devices: false' | Set-Content '$9' -Encoding UTF8$\""
    nsExec::Exec $1
    Pop $0
  skip_android:

  ; Device discovery - iOS
  StrCmp $DiscoverIos ${BST_CHECKED} 0 ios_unchecked
    StrCpy $1 "$\"powershell$\" -NoProfile -ExecutionPolicy Bypass -Command $\""
    StrCpy $1 "$1(Get-Content '$9') -replace '(?m)^\s*discover_ios_devices:.*$$', '  discover_ios_devices: true' | Set-Content '$9' -Encoding UTF8$\""
    nsExec::Exec $1
    Pop $0
    Goto skip_ios
  ios_unchecked:
    StrCpy $1 "$\"powershell$\" -NoProfile -ExecutionPolicy Bypass -Command $\""
    StrCpy $1 "$1(Get-Content '$9') -replace '(?m)^\s*discover_ios_devices:.*$$', '  discover_ios_devices: false' | Set-Content '$9' -Encoding UTF8$\""
    nsExec::Exec $1
    Pop $0
  skip_ios:

  ; Device discovery - Harmony Mobile
  StrCmp $DiscoverHarmonyMobile ${BST_CHECKED} 0 harmony_mobile_unchecked
    StrCpy $1 "$\"powershell$\" -NoProfile -ExecutionPolicy Bypass -Command $\""
    StrCpy $1 "$1(Get-Content '$9') -replace '(?m)^\s*discover_harmony_mobile_devices:.*$$', '  discover_harmony_mobile_devices: true' | Set-Content '$9' -Encoding UTF8$\""
    nsExec::Exec $1
    Pop $0
    Goto skip_harmony_mobile
  harmony_mobile_unchecked:
    StrCpy $1 "$\"powershell$\" -NoProfile -ExecutionPolicy Bypass -Command $\""
    StrCpy $1 "$1(Get-Content '$9') -replace '(?m)^\s*discover_harmony_mobile_devices:.*$$', '  discover_harmony_mobile_devices: false' | Set-Content '$9' -Encoding UTF8$\""
    nsExec::Exec $1
    Pop $0
  skip_harmony_mobile:

  ; Device discovery - Harmony PC
  StrCmp $DiscoverHarmonyPc ${BST_CHECKED} 0 harmony_pc_unchecked
    StrCpy $1 "$\"powershell$\" -NoProfile -ExecutionPolicy Bypass -Command $\""
    StrCpy $1 "$1(Get-Content '$9') -replace '(?m)^\s*discover_harmony_pc_devices:.*$$', '  discover_harmony_pc_devices: true' | Set-Content '$9' -Encoding UTF8$\""
    nsExec::Exec $1
    Pop $0
    Goto skip_harmony_pc
  harmony_pc_unchecked:
    StrCpy $1 "$\"powershell$\" -NoProfile -ExecutionPolicy Bypass -Command $\""
    StrCpy $1 "$1(Get-Content '$9') -replace '(?m)^\s*discover_harmony_pc_devices:.*$$', '  discover_harmony_pc_devices: false' | Set-Content '$9' -Encoding UTF8$\""
    nsExec::Exec $1
    Pop $0
  skip_harmony_pc:

  Goto done

  no_template:
    MessageBox MB_OK "Warning: Config template not found"

  done:
FunctionEnd

; Initialize function (handle command line parameters)
Function .onInit
  ; SetRegView 只能在 Section 或 Function 中使用；初始化阶段也切换到 64 位视图。
  SetRegView 64

  ; 默认值用于静默安装，也作为可视化安装页面的初始值。
  StrCpy $IpInput ""
  StrCpy $PortInput "8088"
  StrCpy $NamespaceInput "meeting_public"
  StrCpy $PlatformApiInput "${PLATFORM_API}"
  StrCpy $OcrServiceInput "${OCR_SERVICE}"
  StrCpy $DiscoverAndroid ${BST_UNCHECKED}
  StrCpy $DiscoverIos ${BST_UNCHECKED}
  StrCpy $DiscoverHarmonyMobile ${BST_UNCHECKED}
  StrCpy $DiscoverHarmonyPc ${BST_UNCHECKED}
  StrCpy $AutoStart ${BST_CHECKED}

  ; Get command line parameters
  ${GetParameters} $0

  ; Parse parameters (optional: support /IP= /PORT= etc)
  ; StrCmp 语法为 StrCmp str1 str2 jump_if_equal jump_if_not_equal。
  ; 之前写成 "0 +2" 导致：未传该命令行参数时（$1 为空，属于相等分支）反而落到下一行执行 StrCpy，
  ; 用空字符串覆盖了 .onInit 刚设置好的默认值；真正传参时（不相等）又跳过 StrCpy 丢弃传入值。
  ; 正常安装（不带命令行参数）Port/Namespace/PlatformApi/OcrService 的默认值就是这样被清空的。
  ; IP 之所以看起来没问题，是因为后面还有 GetLocalIP 自动探测兜底覆盖了被清空的值。
  ; 修正为 "+2 0"：未传参（相等）跳过 StrCpy 保留默认值；传参（不相等）落到下一行写入命令行值。
  ${GetOptions} $0 "/IP=" $1
  StrCmp $1 "" +2 0
    StrCpy $IpInput $1

  ${GetOptions} $0 "/PORT=" $1
  StrCmp $1 "" +2 0
    StrCpy $PortInput $1

  ${GetOptions} $0 "/NAMESPACE=" $1
  StrCmp $1 "" +2 0
    StrCpy $NamespaceInput $1

  ${GetOptions} $0 "/PLATFORM_API=" $1
  StrCmp $1 "" +2 0
    StrCpy $PlatformApiInput $1

  ${GetOptions} $0 "/OCR_SERVICE=" $1
  StrCmp $1 "" +2 0
    StrCpy $OcrServiceInput $1

  ; Auto detect IP for all installs (not just silent)
  ; Do this in .onInit so detection happens before UI shows, avoiding UI freeze
  ; Store result in DetectedIP (not IpInput, which becomes control handle later)
  StrCmp $DetectedIP "" 0 done
    Call GetLocalIP
    StrCpy $DetectedIP $R0
    StrCmp $IpInput "" 0 done
      StrCpy $IpInput $DetectedIP
  done:
FunctionEnd

; Install success function (silent install auto launch)
Function .onInstSuccess
  ; Auto launch program for silent install
  IfSilent 0 done
    ; Use Explorer to launch, avoid UAC elevation issue
    Exec '"$WINDIR\explorer.exe" "$INSTDIR\test-worker.exe"'
  done:
FunctionEnd

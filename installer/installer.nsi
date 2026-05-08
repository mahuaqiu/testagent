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

; 命令行参数解析辅助函数
!include "FileFunc.nsh"
!insertmacro GetParameters
!insertmacro GetOption

; nsDialogs 用于自定义页面
!include "nsDialogs.nsh"

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

; ============================================
; 安装 Section
; ============================================
Section "MainSection" SEC01
  ; 创建目录
  CreateDirectory "$INSTDIR\config"
  CreateDirectory "$INSTDIR\_internal\config"
  CreateDirectory "$INSTDIR\temp"
  CreateDirectory "$INSTDIR\data"

  ; 复制文件（排除根目录 config）
  SetOutPath "$INSTDIR"
  File /r /x "config" "..\dist\windows\test-worker\*"

  ; 配置文件替换
  Call ReplaceConfigFile

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

; ============================================
; 卸载 Section
; ============================================
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

; ============================================
; 函数定义
; ============================================

; 进程清理和 Playwright 目录删除
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

; 自动 IP 检测
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

; 升级检测
Function IsUpgradeInstall
  ; 检查是否是升级安装（config\worker.yaml 存在）
  IfFileExists "$INSTDIR\config\worker.yaml" 0 not_upgrade
    StrCpy $IsUpgrade "1"
    Goto done
  not_upgrade:
    StrCpy $IsUpgrade "0"
  done:
FunctionEnd

; 配置页面创建
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

; 配置页面离开
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

; 配置文件替换
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

; 初始化函数（处理命令行参数）
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

; 安装成功后函数（静默安装自动启动）
Function .onInstSuccess
  ; 静默安装时自动启动程序
  IfSilent 0 done
    ; 使用 Explorer 启动，避免 UAC 提升问题
    Exec '"$WINDIR\explorer.exe" "$INSTDIR\test-worker.exe"'
  done:
FunctionEnd
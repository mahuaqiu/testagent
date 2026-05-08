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
Var IpInput
Var PortInput
Var NamespaceInput
Var PlatformApiInput
Var OcrServiceInput
Var DiscoverAndroid
Var DiscoverIos
Var DesktopCheckbox
Var IsUpgrade

; Page order
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
Page custom ConfigPageCreate ConfigPageLeave
!define MUI_PAGE_CUSTOMFUNCTION_PRE KillProcessesAndCleanup
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
  ; Create directories
  CreateDirectory "$INSTDIR\config"
  CreateDirectory "$INSTDIR\_internal\config"
  CreateDirectory "$INSTDIR\temp"
  CreateDirectory "$INSTDIR\data"

  ; Copy files (exclude root config directory)
  SetOutPath "$INSTDIR"
  File /r /x "config" "..\dist\windows\test-worker\*"

  ; Replace config file with user input
  Call ReplaceConfigFile

  ; Create shortcuts
  CreateDirectory "$SMPROGRAMS\${PRODUCT_NAME}"
  CreateShortCut "$SMPROGRAMS\${PRODUCT_NAME}\${PRODUCT_NAME}.lnk" "$INSTDIR\test-worker.exe"
  CreateShortCut "$SMPROGRAMS\${PRODUCT_NAME}\Uninstall ${PRODUCT_NAME}.lnk" "$INSTDIR\uninst.exe"

  ; Desktop shortcut (based on checkbox)
  ${If} $DesktopCheckbox == ${BST_CHECKED}
    CreateShortCut "$DESKTOP\${PRODUCT_NAME}.lnk" "$INSTDIR\test-worker.exe"
  ${EndIf}

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
  ; Kill processes
  ExecWait '"taskkill" /f /im test-worker.exe' $0

  ; PowerShell: kill ios, adb, ffmpeg by install path
  ; Use $$ for literal $ in PowerShell (NSIS $$ = literal $)
  ; Use nsExec plugin to hide console window
  StrCpy $2 "$INSTDIR"
  StrCpy $3 "$2\"
  StrCpy $1 'powershell -NoProfile -ExecutionPolicy Bypass -Command "'
  StrCpy $1 '$1$$p = Get-Process -Name ios,adb,ffmpeg -ErrorAction SilentlyContinue; '
  StrCpy $1 '$1foreach ($$x in $$p) { '
  StrCpy $1 '$1  if ($$x.Path -like "$3*" -or $$x.Path -like "$2\*") { '
  StrCpy $1 '$1    $$x.Kill() '
  StrCpy $1 '$1  } '
  StrCpy $1 '$1}"'
  nsExec::Exec $1

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
  ; 1. Kill main process (global kill, name is unique)
  ExecWait '"taskkill" /f /im test-worker.exe' $0

  ; 2. Prepare path variables (ensure trailing slash to avoid matching other paths)
  StrCpy $2 "$INSTDIR"
  StrCpy $3 "$2\"  ; Add trailing separator

  ; 3. PowerShell: kill ios, adb, ffmpeg by install path
  ; Build command string in segments, use $$ for literal $ in PowerShell
  ; Use nsExec plugin to hide console window
  StrCpy $1 'powershell -NoProfile -ExecutionPolicy Bypass -Command "'
  StrCpy $1 '$1$$p = Get-Process -Name ios,adb,ffmpeg -ErrorAction SilentlyContinue; '
  StrCpy $1 '$1foreach ($$x in $$p) { '
  StrCpy $1 '$1  if ($$x.Path -like "$3*" -or $$x.Path -like "$2\*") { '
  StrCpy $1 '$1    $$x.Kill() '
  StrCpy $1 '$1  } '
  StrCpy $1 '$1}"'
  nsExec::Exec $1

  ; 4. Delete playwright directory (avoid upgrade incompatibility)
  IfFileExists "$INSTDIR\playwright\*.*" 0 NoPlaywright
    RMDir /r "$INSTDIR\playwright"
  NoPlaywright:
FunctionEnd

; Auto IP detection
Function GetLocalIP
  ; Output: $R0 = best IP address
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
  loop:
    EnumRegKey $R2 HKLM "SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces" $R1
    StrCmp $R2 "" done

    ; Try to read DhcpIPAddress
    ReadRegStr $R2 HKLM "SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces\$R2" "DhcpIPAddress"
    StrCmp $R2 "" try_static
    Goto check_ip

  try_static:
    ReadRegStr $R2 HKLM "SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces\$R2" "IPAddress"

  check_ip:
    ; Filter invalid IPs (empty, 0.0.0.0, 127.x)
    StrCmp $R2 "" next
    StrCmp $R2 "0.0.0.0" next
    StrCpy $R0 $R2 4
    StrCmp $R0 "127." next

    ; Store by priority (only first found)
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
    ; Return by priority
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

  ; IP address
  ${NSD_CreateLabel} 0 0 100% 12u "Worker IP Address:"
  ${NSD_CreateText} 0 18 300 12u ""
  Pop $IpInput
  Call GetLocalIP
  ${NSD_SetText} $IpInput $R0

  ; Port
  ${NSD_CreateLabel} 0 44 100% 12u "Worker Port:"
  ${NSD_CreateText} 0 62 100 12u "8088"
  Pop $PortInput

  ; Namespace
  ${NSD_CreateLabel} 0 88 100% 12u "Namespace:"
  ${NSD_CreateText} 0 106 200 12u "meeting_public"
  Pop $NamespaceInput

  ; Platform API address
  ${NSD_CreateLabel} 0 132 100% 12u "Platform API Address:"
  ${NSD_CreateText} 0 150 350 12u "${PLATFORM_API}"
  Pop $PlatformApiInput

  ; OCR service address
  ${NSD_CreateLabel} 0 176 100% 12u "OCR Service Address:"
  ${NSD_CreateText} 0 194 350 12u "${OCR_SERVICE}"
  Pop $OcrServiceInput

  ; Device discovery options
  ${NSD_CreateLabel} 0 220 100% 12u "Device Discovery Options:"
  ${NSD_CreateCheckbox} 0 238 80 12u "Android"
  Pop $DiscoverAndroid
  ${NSD_CreateCheckbox} 90 238 80 12u "iOS"
  Pop $DiscoverIos

  ; Desktop shortcut
  ${NSD_CreateCheckbox} 0 264 100% 12u "Create Desktop Shortcut"
  Pop $DesktopCheckbox
  ${NSD_SetState} $DesktopCheckbox ${BST_CHECKED}

  nsDialogs::Show

  skip_page:
FunctionEnd

; Config page leave
Function ConfigPageLeave
  ; Get user input
  ${NSD_GetText} $IpInput $IpInput
  ${NSD_GetText} $PortInput $PortInput
  ${NSD_GetText} $NamespaceInput $NamespaceInput
  ${NSD_GetText} $PlatformApiInput $PlatformApiInput
  ${NSD_GetText} $OcrServiceInput $OcrServiceInput
  ${NSD_GetState} $DiscoverAndroid $DiscoverAndroid
  ${NSD_GetState} $DiscoverIos $DiscoverIos
  ${NSD_GetState} $DesktopCheckbox $DesktopCheckbox
FunctionEnd

; Config file replacement
Function ReplaceConfigFile
  ; Only execute for new install
  StrCmp $IsUpgrade "1" done

  ; Copy template to user config directory
  CopyFiles "$INSTDIR\_internal\config\worker.yaml" "$INSTDIR\config\worker.yaml"

  ; Replace user input values line by line
  FileOpen $4 "$INSTDIR\config\worker.yaml" r
  FileOpen $5 "$INSTDIR\config\worker.yaml.new" w

  Loop:
    FileRead $4 $6
    StrCmp $6 "" Close

    ; Replace ip: null
    StrCmp $6 "  ip: null$\r$\n" 0 NotIpLine
      StrCpy $6 "  ip: \"$IpInput\"$\r$\n"
    NotIpLine:

    ; Replace port
    StrCmp $6 "  port: 8088$\r$\n" 0 NotPortLine
      StrCpy $6 "  port: $PortInput$\r$\n"
    NotPortLine:

    ; Replace namespace
    StrCmp $6 "  namespace: meeting_public$\r$\n" 0 NotNamespaceLine
      StrCpy $6 "  namespace: $NamespaceInput$\r$\n"
    NotNamespaceLine:

    ; Replace platform_api
    StrCmp $6 '  platform_api: "${PLATFORM_API}"$\r$\n' 0 NotPlatformApiLine
      StrCpy $6 '  platform_api: "$PlatformApiInput"$\r$\n'
    NotPlatformApiLine:

    ; Replace ocr_service
    StrCmp $6 '  ocr_service: "${OCR_SERVICE}"$\r$\n' 0 NotOcrServiceLine
      StrCpy $6 '  ocr_service: "$OcrServiceInput"$\r$\n'
    NotOcrServiceLine:

    ; Replace discover_android_devices
    StrCmp $6 "  discover_android_devices: false$\r$\n" 0 NotAndroidLine
      ${If} $DiscoverAndroid == ${BST_CHECKED}
        StrCpy $6 "  discover_android_devices: true$\r$\n"
      ${EndIf}
    NotAndroidLine:

    ; Replace discover_ios_devices
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

    ; Replace original file
    Delete "$INSTDIR\config\worker.yaml"
    Rename "$INSTDIR\config\worker.yaml.new" "$INSTDIR\config\worker.yaml"

  done:
FunctionEnd

; Initialize function (handle command line parameters)
Function .onInit
  ; Get command line parameters
  ${GetParameters} $0

  ; Parse parameters (optional: support /IP= /PORT= etc)
  ${GetOptions} $0 "/IP=" $1
  StrCmp $1 "" 0 +2
    StrCpy $IpInput $1

  ${GetOptions} $0 "/PORT=" $1
  StrCmp $1 "" 0 +2
    StrCpy $PortInput $1

  ${GetOptions} $0 "/NAMESPACE=" $1
  StrCmp $1 "" 0 +2
    StrCpy $NamespaceInput $1

  ${GetOptions} $0 "/PLATFORM_API=" $1
  StrCmp $1 "" 0 +2
    StrCpy $PlatformApiInput $1

  ${GetOptions} $0 "/OCR_SERVICE=" $1
  StrCmp $1 "" 0 +2
    StrCpy $OcrServiceInput $1

  ; If silent install and no IP provided, auto detect
  IfSilent 0 done
    StrCmp $IpInput "" 0 done
    Call GetLocalIP
    StrCpy $IpInput $R0
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
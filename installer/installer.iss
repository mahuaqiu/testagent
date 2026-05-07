; installer/installer.iss
; Test Worker 安装脚本
; Inno Setup 6.x

#define Version "2.0.0"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName=Test Worker
AppVersion={#Version}
AppPublisher=Test Worker Team
DefaultDirName=C:\Program Files\Test Worker
DefaultGroupName=Test Worker
OutputDir=..\dist
OutputBaseFilename=test-worker-installer
Compression=lzma2/max
SolidCompression=yes
; Permission settings: allow normal user to install (no admin required for upgrade)
; If installing to Program Files, will prompt for admin
; If installing to user directory, normal user can install
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

; UI settings
WizardStyle=modern
WizardSizePercent=100

; Silent install support
Uninstallable=yes
CreateUninstallRegKey=yes

; Chinese UI
[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

; Custom window title (show version)
[Messages]
SetupWindowTitle=Test Worker {#Version} Setup


[Files]
; Copy all files except root config directory (preserve user config during upgrade)
; Note: Excludes "config" matches directory name, not path - so _internal\config is also excluded
; We handle _internal separately below
Source: "..\dist\windows\test-worker\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs; Excludes: "config"
; Copy _internal directory (includes _internal\config template)
Source: "..\dist\windows\test-worker\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs


[Dirs]
Name: "{app}\config"; Permissions: users-modify
Name: "{app}\_internal\config"; Permissions: users-modify
Name: "{app}\temp"; Permissions: users-modify
Name: "{app}\data"; Permissions: users-modify


[Icons]
Name: "{group}\Test Worker"; Filename: "{app}\test-worker.exe"
Name: "{group}\Uninstall Test Worker"; Filename: "{app}\unins000.exe"
Name: "{autodesktop}\Test Worker"; Filename: "{app}\test-worker.exe"; Tasks: desktopicon


[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加选项:"


[Run]
; Interactive install: user checks to start, UAC prompt for admin
; skipifsilent: skip in silent mode (handled by CurStepChanged)
; shellexec: use ShellExecute (Verb required)
; runas: run with admin (UAC prompt)
Filename: "{app}\test-worker.exe"; Description: "启动 Test Worker"; Flags: nowait postinstall skipifsilent shellexec; Verb: runas


[UninstallRun]
Filename: "taskkill"; Parameters: "/f /im test-worker.exe"; Flags: runhidden


[UninstallDelete]
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\data"
Type: filesandordirs; Name: "{app}\temp"


[Code]
var
  ConfigPage: TInputQueryWizardPage;
  IpLabel, PortLabel, NamespaceLabel, PlatformApiLabel, OcrServiceLabel: TLabel;
  IpEdit, PortEdit, NamespaceEdit, PlatformApiEdit, OcrServiceEdit: TNewEdit;
  DiscoverAndroidCheckbox, DiscoverIosCheckbox: TNewCheckBox;
  CmdIp, CmdPort, CmdNamespace, CmdPlatformApi, CmdOcrService: String;

// Kill processes from tools directory (only those running from install dir)
procedure KillToolsProcesses;
var
  ResultCode: Integer;
  PowerShellScript: String;
begin
  // Method 1: Use taskkill to kill common tools processes directly
  Log('Killing tools processes with taskkill...');
  Exec('taskkill.exe', '/f /im ios.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec('taskkill.exe', '/f /im adb.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec('taskkill.exe', '/f /im ffmpeg.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Log('taskkill done.');

  // Method 2: Use PowerShell to find and kill any remaining processes from install dir
  PowerShellScript :=
    '$appDir = ''' + ExpandConstant('{app}') + ''';' +
    'Get-Process | Where-Object { $_.Path -and $_.Path.StartsWith($appDir, [System.StringComparison]::OrdinalIgnoreCase) } | Stop-Process -Force;';

  Log('Cleaning up remaining processes from install directory...');
  Exec('powershell.exe', '-Command "' + PowerShellScript + '"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Log('Tools processes cleanup done.');
end;

// Delete playwright directory to avoid upgrade issues
procedure DeletePlaywrightDir;
var
  PlaywrightDir: String;
begin
  PlaywrightDir := ExpandConstant('{app}\playwright');
  if DirExists(PlaywrightDir) then
  begin
    Log('Deleting playwright directory: ' + PlaywrightDir);
    DelTree(PlaywrightDir, True, True, True);
    Log('Playwright directory deleted.');
  end;
end;

function GetCmdParam(Name: String): String;
var
  I: Integer;
begin
  Result := '';
  for I := 1 to ParamCount do
  begin
    if Pos('/' + Name + '=', ParamStr(I)) = 1 then
    begin
      Result := Copy(ParamStr(I), Length('/' + Name + '=') + 1, MaxInt);
      Break;
    end;
  end;
end;

// Get local IP address from registry, prefer 10.xx and 192.xx ranges
function GetLocalIP: String;
var
  SubKeyNames: TArrayOfString;
  I: Integer;
  IPValue: String;
  Enabled: String;
  IP_10: String;      // 10.x.x.x range
  IP_192: String;     // 192.168.x.x range
  IP_172: String;     // 172.16-31.x.x range
  IP_Other: String;   // Other valid IP
begin
  // Initialize all IP variables
  IP_10 := '';
  IP_192 := '';
  IP_172 := '';
  IP_Other := '';

  // Find all network adapters
  if RegGetSubkeyNames(HKEY_LOCAL_MACHINE, 'SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces', SubKeyNames) then
  begin
    for I := 0 to GetArrayLength(SubKeyNames) - 1 do
    begin
      IPValue := '';

      // Try DHCP IP first
      if RegQueryStringValue(HKEY_LOCAL_MACHINE, 'SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces\' + SubKeyNames[I], 'DhcpIPAddress', IPValue) then
      begin
        // DHCP IP read success
      end
      else
      begin
        // Try static IP
        RegQueryStringValue(HKEY_LOCAL_MACHINE, 'SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces\' + SubKeyNames[I], 'IPAddress', IPValue);
      end;

      // Check if IP is valid (exclude empty, 0.0.0.0 and 127.x.x.x)
      if (IPValue <> '') and (IPValue <> '0.0.0.0') and (Copy(IPValue, 1, 4) <> '127.') then
      begin
        // Store by priority (only first found)
        if (IP_10 = '') and (Copy(IPValue, 1, 3) = '10.') then
          IP_10 := IPValue
        else if (IP_192 = '') and (Copy(IPValue, 1, 8) = '192.168.') then
          IP_192 := IPValue
        else if (IP_172 = '') and (Copy(IPValue, 1, 4) = '172.') then
          IP_172 := IPValue
        else if (IP_Other = '') then
          IP_Other := IPValue;
      end;
    end;
  end;

  // Return by priority: 10.xx > 192.168.xx > 172.xx > other > 127.0.0.1
  if IP_10 <> '' then
    Result := IP_10
  else if IP_192 <> '' then
    Result := IP_192
  else if IP_172 <> '' then
    Result := IP_172
  else if IP_Other <> '' then
    Result := IP_Other
  else
    Result := '127.0.0.1';
end;

function IsUpgradeInstall: Boolean;
begin
  // Check if root config/worker.yaml exists (upgrade vs new install)
  Result := FileExists(ExpandConstant('{app}\config\worker.yaml'));
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  if PageID = ConfigPage.ID then
    Result := IsUpgradeInstall();
end;

procedure InitializeWizard;
begin
  CmdIp := GetCmdParam('IP');
  CmdPort := GetCmdParam('PORT');
  CmdNamespace := GetCmdParam('NAMESPACE');
  CmdPlatformApi := GetCmdParam('PLATFORM_API');
  CmdOcrService := GetCmdParam('OCR_SERVICE');

  ConfigPage := CreateInputQueryPage(wpSelectDir,
    '配置 Worker 参数', '请填写以下配置',
    '这些将写入 config/worker.yaml');

  // IP address
  IpLabel := TLabel.Create(ConfigPage);
  IpLabel.Parent := ConfigPage.Surface;
  IpLabel.Caption := 'Worker IP 地址:';
  IpLabel.Left := ScaleX(0);
  IpLabel.Top := ScaleY(0);
  IpLabel.Width := ScaleX(150);

  IpEdit := TNewEdit.Create(ConfigPage);
  IpEdit.Parent := ConfigPage.Surface;
  IpEdit.Left := ScaleX(0);
  IpEdit.Top := ScaleY(18);
  IpEdit.Width := ScaleX(300);
  if CmdIp <> '' then
    IpEdit.Text := CmdIp
  else
    IpEdit.Text := GetLocalIP();

  // Port
  PortLabel := TLabel.Create(ConfigPage);
  PortLabel.Parent := ConfigPage.Surface;
  PortLabel.Caption := 'Worker 端口:';
  PortLabel.Left := ScaleX(0);
  PortLabel.Top := ScaleY(44);
  PortLabel.Width := ScaleX(150);

  PortEdit := TNewEdit.Create(ConfigPage);
  PortEdit.Parent := ConfigPage.Surface;
  PortEdit.Left := ScaleX(0);
  PortEdit.Top := ScaleY(62);
  PortEdit.Width := ScaleX(100);
  if CmdPort <> '' then
    PortEdit.Text := CmdPort
  else
    PortEdit.Text := '8088';

  // Namespace
  NamespaceLabel := TLabel.Create(ConfigPage);
  NamespaceLabel.Parent := ConfigPage.Surface;
  NamespaceLabel.Caption := '命名空间:';
  NamespaceLabel.Left := ScaleX(0);
  NamespaceLabel.Top := ScaleY(88);
  NamespaceLabel.Width := ScaleX(150);

  NamespaceEdit := TNewEdit.Create(ConfigPage);
  NamespaceEdit.Parent := ConfigPage.Surface;
  NamespaceEdit.Left := ScaleX(0);
  NamespaceEdit.Top := ScaleY(106);
  NamespaceEdit.Width := ScaleX(200);
  if CmdNamespace <> '' then
    NamespaceEdit.Text := CmdNamespace
  else
    NamespaceEdit.Text := 'meeting_public';

  // Platform API
  PlatformApiLabel := TLabel.Create(ConfigPage);
  PlatformApiLabel.Parent := ConfigPage.Surface;
  PlatformApiLabel.Caption := '平台 API 地址:';
  PlatformApiLabel.Left := ScaleX(0);
  PlatformApiLabel.Top := ScaleY(132);
  PlatformApiLabel.Width := ScaleX(150);

  PlatformApiEdit := TNewEdit.Create(ConfigPage);
  PlatformApiEdit.Parent := ConfigPage.Surface;
  PlatformApiEdit.Left := ScaleX(0);
  PlatformApiEdit.Top := ScaleY(150);
  PlatformApiEdit.Width := ScaleX(350);
  if CmdPlatformApi <> '' then
    PlatformApiEdit.Text := CmdPlatformApi
  else
    PlatformApiEdit.Text := 'http://192.168.0.102:8000';

  // OCR service
  OcrServiceLabel := TLabel.Create(ConfigPage);
  OcrServiceLabel.Parent := ConfigPage.Surface;
  OcrServiceLabel.Caption := 'OCR 服务地址:';
  OcrServiceLabel.Left := ScaleX(0);
  OcrServiceLabel.Top := ScaleY(176);
  OcrServiceLabel.Width := ScaleX(150);

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

procedure CurStepChanged(CurStep: TSetupStep);
var
  ConfigFile: String;
  TemplateFile: String;
  ConfigLines: TArrayOfString;
  LineIndex: Integer;
  LineContent: String;
  ResultCode: Integer;
begin
  // Pre-install cleanup: kill processes and delete playwright
  if CurStep = ssInstall then
  begin
    Log('Pre-install cleanup starting...');
    KillToolsProcesses;
    DeletePlaywrightDir;
    Log('Pre-install cleanup completed.');
  end;

  ConfigFile := ExpandConstant('{app}\config\worker.yaml');
  TemplateFile := ExpandConstant('{app}\_internal\config\worker.yaml');

  if CurStep = ssPostInstall then
  begin
    // New install: copy template config, then replace user input values
    if not IsUpgradeInstall then
    begin
      // Copy template file to user config directory
      if FileExists(TemplateFile) then
      begin
        FileCopy(TemplateFile, ConfigFile, False);
      end;

      // Read and replace user input values (line by line)
      if FileExists(ConfigFile) then
      begin
        LoadStringsFromFile(ConfigFile, ConfigLines);

        for LineIndex := 0 to GetArrayLength(ConfigLines) - 1 do
        begin
          LineContent := ConfigLines[LineIndex];

          // Replace IP address (template uses null as default IP)
          if Pos('ip: null', LineContent) > 0 then
            LineContent := '  ip: "' + IpEdit.Text + '"                          # Specify IP address, null means auto-detect';

          // Replace port
          if Pos('port: 8088', LineContent) > 0 then
            LineContent := '  port: ' + PortEdit.Text + '                        # HTTP service port';

          // Replace namespace
          if Pos('namespace: meeting_public', LineContent) > 0 then
            LineContent := '  namespace: ' + NamespaceEdit.Text + '         # Namespace for categorizing Workers';

          // Replace platform API URL
          if Pos('platform_api: "http://192.168.0.102:8000"', LineContent) > 0 then
            LineContent := '  platform_api: "' + PlatformApiEdit.Text + '"  # Platform API URL';

          // Replace OCR service URL
          if Pos('ocr_service: "http://192.168.0.102:9021"', LineContent) > 0 then
            LineContent := '  ocr_service: "' + OcrServiceEdit.Text + '"   # OCR service URL';

          // Replace discover_android_devices
          if Pos('discover_android_devices:', LineContent) > 0 then
          begin
            if DiscoverAndroidCheckbox.Checked then
              LineContent := '  discover_android_devices: true   # 是否发现 Android 设备（关闭则跳过所有 Android 相关逻辑）'
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
        end;

        // Write back config file
        SaveStringsToFile(ConfigFile, ConfigLines, False);
      end;
    end;

    // Auto-start in silent mode (no UAC, run as current user for unattended upgrade)
    if WizardSilent then
      ShellExec('', ExpandConstant('{app}\test-worker.exe'), '', '', SW_HIDE, ewNoWait, ResultCode);
  end;
end;
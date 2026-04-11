; installer/installer.iss
; Test Worker 安装脚本
; Inno Setup 6.x

#define Version "2.0.0"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}}
AppName=Test Worker
AppVersion={#Version}
AppPublisher=Test Worker Team
DefaultDirName=C:\Program Files\Test Worker
DefaultGroupName=Test Worker
OutputDir=..\dist
OutputBaseFilename=test-worker-installer
Compression=lzma2/max
SolidCompression=yes
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog

; 界面设置
WizardStyle=modern
WizardSizePercent=100

; 静默安装支持
Uninstallable=yes
CreateUninstallRegKey=yes

; 中文界面
[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"


[Files]
; Worker 主程序和依赖（排除配置文件，配置文件单独处理）
Source: "..\dist\windows\test-worker\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs; Excludes: "_internal\config\worker.yaml"

; 配置文件 - 总是覆盖（确保配置文件格式正确）
Source: "..\dist\windows\test-worker\_internal\config\worker.yaml"; DestDir: "{app}\_internal\config"; Flags: ignoreversion


[Dirs]
Name: "{app}\_internal\config"; Permissions: users-modify
Name: "{app}\temp"; Permissions: users-modify
Name: "{app}\data"; Permissions: users-modify


[Registry]
Root: HKLM; Subkey: "Software\Test Worker"; ValueType: string; ValueName: "InstallPath"; ValueData: "{app}"


[Icons]
Name: "{group}\Test Worker"; Filename: "{app}\test-worker.exe"
Name: "{group}\卸载 Test Worker"; Filename: "{app}\unins000.exe"
Name: "{autodesktop}\Test Worker"; Filename: "{app}\test-worker.exe"; Tasks: desktopicon


[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加选项:"


[Run]
Filename: "{app}\test-worker.exe"; Description: "启动 Test Worker"; Flags: nowait postinstall skipifsilent


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
  CmdIp, CmdPort, CmdNamespace, CmdPlatformApi, CmdOcrService: String;

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

// 通过注册表获取本机 IP 地址，优先选择 10.xx 和 192.xx 网段
function GetLocalIP: String;
var
  SubKeyNames: TArrayOfString;
  I: Integer;
  IPValue: String;
  Enabled: String;
  IP_10: String;      // 10.x.x.x 网段
  IP_192: String;     // 192.168.x.x 网段
  IP_172: String;     // 172.16-31.x.x 网段
  IP_Other: String;   // 其他有效 IP
begin
  // 初始化所有 IP 变量
  IP_10 := '';
  IP_192 := '';
  IP_172 := '';
  IP_Other := '';

  // 查找所有网络适配器
  if RegGetSubkeyNames(HKEY_LOCAL_MACHINE, 'SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces', SubKeyNames) then
  begin
    for I := 0 to GetArrayLength(SubKeyNames) - 1 do
    begin
      IPValue := '';

      // 尝试读取 DHCP IP（优先）
      if RegQueryStringValue(HKEY_LOCAL_MACHINE, 'SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces\' + SubKeyNames[I], 'DhcpIPAddress', IPValue) then
      begin
        // DHCP IP 读取成功
      end
      else
      begin
        // 尝试读取静态 IP
        RegQueryStringValue(HKEY_LOCAL_MACHINE, 'SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces\' + SubKeyNames[I], 'IPAddress', IPValue);
      end;

      // 检查 IP 是否有效（排除空值、0.0.0.0 和 127.x.x.x）
      if (IPValue <> '') and (IPValue <> '0.0.0.0') and (Copy(IPValue, 1, 4) <> '127.') then
      begin
        // 按优先级分类存储（只存储第一个找到的）
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

  // 按优先级返回：10.xx > 192.168.xx > 172.xx > 其他 > 127.0.0.1
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
  Result := RegValueExists(HKEY_LOCAL_MACHINE,
    'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting("AppId")}_is1',
    'UninstallString');
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
    '配置 Worker 参数', '请填写以下配置信息',
    '这些配置将写入 config/worker.yaml 文件');

  // IP 地址
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

  // 端口
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

  // 命名空间
  NamespaceLabel := TLabel.Create(ConfigPage);
  NamespaceLabel.Parent := ConfigPage.Surface;
  NamespaceLabel.Caption := '命名空间 (Namespace):';
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

  // 平台 API
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

  // OCR 服务
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
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ConfigFile: String;
  ConfigContent: AnsiString;
  ResultCode: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    // Auto-start in silent mode
    if WizardSilent then
      ShellExec('', ExpandConstant('{app}\test-worker.exe'), '', '', SW_HIDE, ewNoWait, ResultCode);

    // Write config file with user input values (UTF-8 encoding, English comments)
    ConfigFile := ExpandConstant('{app}\_internal\config\worker.yaml');

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

    // Delete existing file and write new content with UTF-8 encoding
    DeleteFile(ConfigFile);
    SaveStringToFile(ConfigFile, ConfigContent, True);
  end;
end;
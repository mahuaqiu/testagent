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
; Worker 主程序和依赖
Source: "..\dist\windows\test-worker\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs

; 配置目录（在 _internal 下）- 升级时不覆盖（保留用户配置）
Source: "..\dist\windows\test-worker\_internal\config\*"; DestDir: "{app}\_internal\config"; Flags: onlyifdoesntexist recursesubdirs


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

// 通过注册表获取本机 IP 地址
function GetLocalIP: String;
var
  SubKeyNames: TArrayOfString;
  I, J: Integer;
  IPValue: String;
  Enabled: String;
begin
  Result := '127.0.0.1';

  // 查找启用的网络适配器
  if RegGetSubkeyNames(HKEY_LOCAL_MACHINE, 'SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces', SubKeyNames) then
  begin
    for I := 0 to GetArrayLength(SubKeyNames) - 1 do
    begin
      // 检查适配器是否启用
      if RegQueryStringValue(HKEY_LOCAL_MACHINE, 'SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces\' + SubKeyNames[I], 'EnableDHCP', Enabled) then
      begin
        if Enabled = '1' then
        begin
          // DHCP 启用，读取 DHCP IP
          if RegQueryStringValue(HKEY_LOCAL_MACHINE, 'SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces\' + SubKeyNames[I], 'DhcpIPAddress', IPValue) then
          begin
            if (IPValue <> '') and (IPValue <> '0.0.0.0') then
            begin
              Result := IPValue;
              Exit;
            end;
          end;
        end
        else if Enabled = '0' then
        begin
          // 静态 IP
          if RegQueryStringValue(HKEY_LOCAL_MACHINE, 'SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces\' + SubKeyNames[I], 'IPAddress', IPValue) then
          begin
            if (IPValue <> '') and (IPValue <> '0.0.0.0') then
            begin
              Result := IPValue;
              Exit;
            end;
          end;
        end;
      end;
    end;
  end;
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
  ConfigContent: String;
begin
  if CurStep = ssPostInstall then
  begin
    // 静默安装模式下自动启动
    if WizardSilent then
      ShellExec('', ExpandConstant('{app}\test-worker.exe'), '', '', SW_HIDE, ewNoWait, 0);

    // 原有配置写入逻辑
    if not IsUpgradeInstall() then
    begin
      ConfigFile := ExpandConstant('{app}\_internal\config\worker.yaml');
      ConfigContent :=
        '# Worker 配置文件（安装时生成）' + #13#10 +
        '' + #13#10 +
        'worker:' + #13#10 +
        '  id: null' + #13#10 +
        '  ip: "' + IpEdit.Text + '"' + #13#10 +
        '  port: ' + PortEdit.Text + #13#10 +
        '  namespace: "' + NamespaceEdit.Text + '"' + #13#10 +
        '  device_check_interval: 300' + #13#10 +
        '' + #13#10 +
        'external_services:' + #13#10 +
        '  platform_api: "' + PlatformApiEdit.Text + '"' + #13#10 +
        '  ocr_service: "' + OcrServiceEdit.Text + '"' + #13#10 +
        '' + #13#10 +
        '# 其他配置请参考完整配置文件模板' + #13#10;
      SaveStringToFile(ConfigFile, ConfigContent, False);
    end;
  end;
end;
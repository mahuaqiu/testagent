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
OutputDir=dist
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


[Files]
; Worker 主程序和依赖
Source: "dist\test-worker\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs

; 配置目录 - 升级时不覆盖（保留用户配置）
Source: "dist\test-worker\config\*"; DestDir: "{app}\config"; Flags: onlyifdoesntexist recursesubdirs


[Dirs]
Name: "{app}\config"; Permissions: users-modify
Name: "{app}\temp"; Permissions: users-modify
Name: "{app}\data"; Permissions: users-modify


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

function GetLocalIP: String;
var
  WSAData: TWSAData;
  HostName: String;
  HostEnt: PHostEnt;
  IPAddr: PInAddr;
begin
  Result := '127.0.0.1';
  try
    WSAStartup(MakeWord(1, 1), WSAData);
    SetLength(HostName, 255);
    GetHostName(PChar(HostName), 255);
    HostEnt := GetHostByName(PChar(HostName));
    if HostEnt <> nil then
    begin
      IPAddr := PInAddr(HostEnt^.h_addr_list^[0]);
      Result := inet_ntoa(IPAddr^);
    end;
    WSACleanup;
  except
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

  IpEdit := TNewEdit.Create(ConfigPage);
  IpEdit.Parent := ConfigPage.Surface;
  IpEdit.Left := ScaleX(0);
  IpEdit.Top := ScaleY(10);
  IpEdit.Width := ScaleX(300);
  if CmdIp <> '' then
    IpEdit.Text := CmdIp
  else
    IpEdit.Text := GetLocalIP();

  PortEdit := TNewEdit.Create(ConfigPage);
  PortEdit.Parent := ConfigPage.Surface;
  PortEdit.Left := ScaleX(0);
  PortEdit.Top := ScaleY(40);
  PortEdit.Width := ScaleX(100);
  if CmdPort <> '' then
    PortEdit.Text := CmdPort
  else
    PortEdit.Text := '8088';

  NamespaceEdit := TNewEdit.Create(ConfigPage);
  NamespaceEdit.Parent := ConfigPage.Surface;
  NamespaceEdit.Left := ScaleX(0);
  NamespaceEdit.Top := ScaleY(70);
  NamespaceEdit.Width := ScaleX(200);
  if CmdNamespace <> '' then
    NamespaceEdit.Text := CmdNamespace
  else
    NamespaceEdit.Text := 'meeting_public';

  PlatformApiEdit := TNewEdit.Create(ConfigPage);
  PlatformApiEdit.Parent := ConfigPage.Surface;
  PlatformApiEdit.Left := ScaleX(0);
  PlatformApiEdit.Top := ScaleY(100);
  PlatformApiEdit.Width := ScaleX(350);
  if CmdPlatformApi <> '' then
    PlatformApiEdit.Text := CmdPlatformApi
  else
    PlatformApiEdit.Text := 'http://192.168.0.102:8000';

  OcrServiceEdit := TNewEdit.Create(ConfigPage);
  OcrServiceEdit.Parent := ConfigPage.Surface;
  OcrServiceEdit.Left := ScaleX(0);
  OcrServiceEdit.Top := ScaleY(130);
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
    if not IsUpgradeInstall() then
    begin
      ConfigFile := ExpandConstant('{app}\config\worker.yaml');
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
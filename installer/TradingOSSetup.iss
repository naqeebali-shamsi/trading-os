; Trading OS — Inno Setup installer script
; Build: iscc installer\TradingOSSetup.iss
; Requires: Inno Setup 6+, TradingOS.exe built via installer\build_release.ps1

#define MyAppName "Trading OS"
#define MyAppVersion "1.4.1"
#define MyAppPublisher "QTπ"
#define MyAppCopyright "Copyright (C) QTπ"
#define MyAppURL "http://127.0.0.1:8765/ui"
#define MyAppExeName "TradingOS.exe"

[Setup]
AppId={{B4E8F2A1-9C3D-4F5E-8A1B-2D3C4E5F6A7B}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppCopyright={#MyAppCopyright}
VersionInfoCompany={#MyAppPublisher}
VersionInfoCopyright={#MyAppCopyright}
VersionInfoProductName={#MyAppName}
VersionInfoVersion={#MyAppVersion}
DefaultDirName={autopf}\TradingOS
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=output
OutputBaseFilename=TradingOS-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Runtime Python packages — code only; exclude dev logs, traces, and runtime state
Source: "..\bridge\*"; DestDir: "{app}\bridge"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__\*,*.pyc"
Source: "..\config\*"; DestDir: "{app}\config"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "secrets.yaml"
Source: "..\consciousness\*"; DestDir: "{app}\consciousness"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "traces\*,__pycache__\*,*.pyc"
Source: "..\cortex\*"; DestDir: "{app}\cortex"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__\*,*.pyc"
Source: "..\immune\*"; DestDir: "{app}\immune"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__\*,*.pyc"
Source: "..\kernel\*"; DestDir: "{app}\kernel"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__\*,*.pyc"
Source: "..\memory\*"; DestDir: "{app}\memory"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "*.jsonl,__pycache__\*,*.pyc"
Source: "..\muscle\*"; DestDir: "{app}\muscle"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: ".order_lifecycle_state.json,..order_lifecycle_state.json.*.tmp,*.tmp,__pycache__\*,*.pyc"
Source: "..\nervous\*"; DestDir: "{app}\nervous"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "bus.jsonl,topics\*,__pycache__\*,*.pyc"
Source: "..\ops\*"; DestDir: "{app}\ops"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__\*,*.pyc"
Source: "..\sensory\*"; DestDir: "{app}\sensory"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__\*,*.pyc"
Source: "..\swarm\*"; DestDir: "{app}\swarm"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__\*,*.pyc"
Source: "..\telemetry\*"; DestDir: "{app}\telemetry"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__\*,*.pyc"
Source: "..\introspect\*"; DestDir: "{app}\introspect"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__\*,*.pyc"
Source: "..\intel\*"; DestDir: "{app}\intel"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "*.jsonl,__pycache__\*,*.pyc"
Source: "..\calendar\*"; DestDir: "{app}\calendar"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__\*,*.pyc"
Source: "..\tracks\*"; DestDir: "{app}\tracks"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__\*,*.pyc"
Source: "..\installer\*.py"; DestDir: "{app}\installer"; Flags: ignoreversion
Source: "..\installer\*.ps1"; DestDir: "{app}\installer"; Flags: ignoreversion; Excludes: "build_release.ps1,fetch_python_runtime.ps1,download_wheelhouse.ps1"
Source: "..\ConfigureTradingOS.cmd"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\installer\ConfigureTradingOS.cmd"; DestDir: "{app}\installer"; Flags: ignoreversion
Source: "..\installer\*.example"; DestDir: "{app}\installer"; Flags: ignoreversion
Source: "..\installer\*.spec"; DestDir: "{app}\installer"; Flags: ignoreversion
Source: "..\paths.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\runtime_safety.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\runtime_controls.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\trading_profile.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\data_lake.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\requirements.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\requirements-optional.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "staging\runtime\python\*"; DestDir: "{app}\runtime\python"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__\*,*.pyc,*.pyo,Doc\*,NEWS.txt,LICENSE.txt,Lib\test\*,Lib\idlelib\*,tcl\*,Lib\site-packages\PyInstaller\*,Lib\site-packages\pyinstaller*\*,Lib\site-packages\_pyinstaller*\*,Lib\site-packages\pytest\*,Lib\site-packages\_pytest\*,Scripts\pyi-*"
Source: "wheelhouse\*.whl"; DestDir: "{app}\wheelhouse"; Flags: ignoreversion; Excludes: "*cp311*.whl"
Source: "..\STOP_TRADING.example"; DestDir: "{app}"; Flags: ignoreversion
; Desktop launchers
Source: "dist\TradingOS.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\TradingOS-Stop.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Stop {#MyAppName}"; Filename: "{app}\TradingOS-Stop.exe"
Name: "{group}\Dashboard"; Filename: "{#MyAppURL}"
Name: "{group}\Configure {#MyAppName}"; Filename: "{app}\ConfigureTradingOS.cmd"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Dirs]
Name: "{app}"; Permissions: users-modify

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent unchecked; Check: WizardConfigured

[Code]
function WizardConfigured(): Boolean;
begin
  Result := FileExists(ExpandConstant('{app}\.install-complete'));
end;

function RunConfigWizard(): Boolean;
var
  ResultCode: Integer;
  Params: String;
begin
  Params := '-ExecutionPolicy Bypass -NoProfile -STA -WindowStyle Normal -File "' +
    ExpandConstant('{app}\installer\install_wizard.ps1') + '" -InstallRoot "' +
    ExpandConstant('{app}') + '" -SetupBridge -Mandatory';
  Result := Exec(
    ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    Params,
    ExpandConstant('{app}'),
    SW_SHOW,
    ewWaitUntilTerminated,
    ResultCode);
  if not Result then
  begin
    MsgBox('Could not start the Trading OS configuration wizard.', mbError, MB_OK);
    Exit;
  end;
  Result := (ResultCode = 0);
end;

procedure InitializeWizard();
begin
  WizardForm.WelcomeLabel2.Caption :=
    'Trading OS installs in two phases:' + #13#10 +
    '1. Copy program files (this wizard)' + #13#10 +
    '2. Configure API key, Python environment, and MT5 bridge' + #13#10#13#10 +
    'A setup window opens automatically after files are copied. ' +
    'Trading OS will not launch until that finishes.';
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpFinished then
  begin
    if WizardConfigured() then
      WizardForm.FinishedLabel.Caption :=
        'Trading OS is installed and configured.' + #13#10#13#10 +
        'Launch Trading OS from the desktop shortcut or Start Menu. ' +
        'For LIVE trading, attach FileBridgeEA_Windows in MetaTrader 5.'
    else
      WizardForm.FinishedLabel.Caption :=
        'Files were copied but configuration did not complete.' + #13#10 +
        'Run Configure Trading OS from the Start Menu before launching.';
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Retry: Boolean;
begin
  if CurStep = ssPostInstall then
  begin
    Retry := True;
    while Retry do
    begin
      if RunConfigWizard() then
        Retry := False
      else if MsgBox(
        'Trading OS is not configured yet. You must complete the configuration wizard (API key or observe-only mode) before Trading OS will run.' + #13#10#13#10 +
        'Open the configuration wizard again now?',
        mbConfirmation, MB_RETRYCANCEL) = IDRETRY then
        Retry := True
      else
        Abort;
    end;
  end;
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
end;

[UninstallDelete]
; Remove runtime PID only — keep config.env, secrets, and other user data in ProgramData\TradingOS
Type: files; Name: "{commonappdata}\TradingOS\supervisor.pid"

[Registry]
Root: HKCU; Subkey: "Software\QTπ\TradingOS"; ValueType: string; ValueName: "InstallPath"; ValueData: "{app}"; Flags: uninsdeletekey

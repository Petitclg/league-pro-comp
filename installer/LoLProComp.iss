; Inno Setup 6 script: a per-user installer (no administrator rights) around the one-file LoLProComp.exe.
; Built by `build.ps1 -Installer`, which runs:
;   ISCC /DAppVersion=1.2.0 /DSourceExe=C:\path\to\LoLProComp.exe /OC:\path\to\outdir installer\LoLProComp.iss
; Updates still come from the app itself (updater.py swaps the exe in place): the install folder is per-user and writable.

#define AppName "LoL Pro Comp"
#define AppExeName "LoLProComp.exe"
#define AppPublisher "Gaspard Petitclair"
#define AppURL "https://github.com/Petitclg/league-pro-comp"
; Port the desktop launcher prefers (desktop.PREFERRED_PORT): used to ask a running instance to quit.
#define AppPort "8765"

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceExe
  #define SourceExe "..\backend\dist\LoLProComp.exe"
#endif

[Setup]
AppId={{989BC7C7-A019-46FB-B6DC-0431E5486AFD}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\LoLProComp
DisableProgramGroupPage=yes
OutputDir=..\backend\dist
OutputBaseFilename=LoLProComp-Setup
SetupIconFile=..\backend\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}
WizardStyle=modern
Compression=lzma2
SolidCompression=yes
; The running app holds LoLProComp.exe: PrepareToInstall asks it to quit, the Restart Manager closes what is left.
CloseApplications=force
CloseApplicationsFilter=*.exe
RestartApplications=no

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
french.DeleteData=Supprimer aussi les données de {#AppName} (parties importées, réglages, journal) ?%n%n%1%n%nChoisissez Non pour les garder : une réinstallation les retrouvera.
english.DeleteData=Also delete the {#AppName} data (imported games, settings, log)?%n%n%1%n%nChoose No to keep them for a later reinstall.
french.AppStillRunning={#AppName} est encore ouvert (un import est peut-être en cours).%n%nOK le ferme et poursuit la désinstallation ; Annuler l'interrompt.
english.AppStillRunning={#AppName} is still running (an import may be in progress).%n%nOK closes it and continues uninstalling; Cancel stops here.

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceExe}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Left behind by an interrupted self-update (desktop.update_at_startup / updater.install).
Type: files; Name: "{app}\{#AppExeName}.old"
Type: files; Name: "{app}\{#AppExeName}.new"

[Code]
const
  DataFolder = 'LoLProComp';  { config.DATA_DIR of the packaged app: %LOCALAPPDATA%\LoLProComp }

{ Processes started from the installed executable (the one-file bootloader and its child). }
function QueryAppProcesses(): Variant;
var
  Locator, Service: Variant;
  Path: String;
begin
  Path := ExpandConstant('{app}\{#AppExeName}');
  StringChangeEx(Path, '\', '\\', True);
  StringChangeEx(Path, '''', '\''', True);
  Locator := CreateOleObject('WbemScripting.SWbemLocator');
  Service := Locator.ConnectServer('.', 'root\CIMV2');
  Result := Service.ExecQuery('SELECT * FROM Win32_Process WHERE ExecutablePath = ''' + Path + '''');
end;

function AppProcessCount(): Integer;
var
  Processes: Variant;
begin
  try
    Processes := QueryAppProcesses();
    Result := Processes.Count;
  except
    Result := 0;
  end;
end;

function WaitForAppExit(TimeoutMs: Integer): Boolean;
var
  Waited: Integer;
begin
  Waited := 0;
  while (AppProcessCount() > 0) and (Waited < TimeoutMs) do
  begin
    Sleep(250);
    Waited := Waited + 250;
  end;
  Result := AppProcessCount() = 0;
end;

function AppRequest(const Verb, Path: String; var Body: String): Integer;
var
  Request: Variant;
begin
  Body := '';
  try
    Request := CreateOleObject('WinHttp.WinHttpRequest.5.1');
    Request.SetTimeouts(1000, 1000, 2000, 3000);
    Request.Open(Verb, 'http://127.0.0.1:{#AppPort}' + Path, False);
    Request.SetRequestHeader('X-Requested-With', '{#AppName}');
    Request.Send('');
    Result := Request.Status;
    Body := Request.ResponseText;
  except
    Result := 0;
  end;
end;

{ Ask the installed app to quit the way a newer launch does (POST /api/quit): it closes its window and
  stops cleanly, but refuses while an import runs. True once no installed process is left. }
function AskAppToQuit(): Boolean;
var
  Body: String;
begin
  Result := AppProcessCount() = 0;
  if Result then
    Exit;
  if (AppRequest('GET', '/api/health', Body) = 200) and (Pos('{#AppName}', Body) > 0) then
    if AppRequest('POST', '/api/quit', Body) = 200 then
      Result := WaitForAppExit(15000);
end;

procedure TerminateApp();
var
  Processes, Proc: Variant;
  I: Integer;
begin
  try
    Processes := QueryAppProcesses();
    for I := 0 to Processes.Count - 1 do
    begin
      try
        Proc := Processes.ItemIndex(I);
        Proc.Terminate();
      except
      end;
    end;
  except
  end;
  WaitForAppExit(5000);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  AskAppToQuit();  { whatever still runs is then closed by the Restart Manager (CloseApplications) }
  Result := '';
end;

function InitializeUninstall(): Boolean;
begin
  Result := True;
  if AskAppToQuit() then
    Exit;
  if not UninstallSilent() then
    if MsgBox(CustomMessage('AppStillRunning'), mbConfirmation, MB_OKCANCEL) <> IDOK then
    begin
      Result := False;
      Exit;
    end;
  TerminateApp();
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep <> usPostUninstall then
    Exit;
  DataDir := ExpandConstant('{localappdata}\' + DataFolder);
  { A silent uninstall keeps the data: deleting it must be an explicit choice. }
  if UninstallSilent() or not DirExists(DataDir) then
    Exit;
  if MsgBox(FmtMessage(CustomMessage('DeleteData'), [DataDir]), mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
    DelTree(DataDir, True, True, True);
end;

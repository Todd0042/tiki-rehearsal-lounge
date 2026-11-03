; Inno Setup Script for Tiki Rehearsal Lounge
; Generates standalone turnkey installer: TikiRehearsalLoungeSetup-v1.0.0.exe

#define MyAppName "Tiki Rehearsal Lounge"
#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif
#define MyAppPublisher "Todd"
#define MyAppURL "https://github.com/Todd0042/tiki-rehearsal-lounge"
#define MyAppExeName "TikiRehearsalLounge.exe"

[Setup]
; Unique AppId for upgrade detection
AppId={{A4B7D6E2-81C5-4A39-9A0B-48F06E662F81}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=dist_installer
OutputBaseFilename=TikiRehearsalLoungeSetup-v{#MyAppVersion}
SetupIconFile=tiki-icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=auto
CloseApplications=force
RestartApplications=no
LicenseFile=LICENSE.txt

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

#if FileExists("C:\TikiRehearsalBuild\dist\TikiRehearsalLounge\TikiRehearsalLounge.exe")
#define SourceDir "C:\TikiRehearsalBuild\dist\TikiRehearsalLounge"
#else
#define SourceDir "dist\TikiRehearsalLounge"
#endif

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "tiki-icon.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "README_WINDOWS.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "LICENSE.txt"; DestDir: "{app}"; Flags: ignoreversion

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\tiki-icon.ico"; AppUserModelID: "Todd.TikiRehearsalLounge.1.0"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\tiki-icon.ico"; Tasks: desktopicon; AppUserModelID: "Todd.TikiRehearsalLounge.1.0"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Code]
procedure KillRehearsalProcesses();
var
  ResultCode: Integer;
begin
  Exec('taskkill.exe', '/F /T /IM TikiRehearsalLounge.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec('taskkill.exe', '/F /T /IM mpv.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  KillRehearsalProcesses();
  Sleep(200);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  KillRehearsalProcesses();
  Sleep(200);
end;

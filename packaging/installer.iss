; Inno Setup script: per-user install, no admin needed.  iscc packaging\installer.iss  (after PyInstaller)
; Installs to %LOCALAPPDATA%\Programs\GrokDesktop. Chat history/settings live in %APPDATA%\GrokDesktop and are kept on uninstall.
#ifndef AppVersion
#define AppVersion "0.2.3"
#endif
[Setup]
AppId={{58D82AE6-4380-42E8-8ABD-C2C9125D7BC4}
AppName=Grok Desktop
AppVersion={#AppVersion}
AppPublisher=Zander11111
DefaultDirName={localappdata}\Programs\GrokDesktop
DefaultGroupName=Grok Desktop
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=GrokDesktop-Setup-{#AppVersion}
SetupIconFile=icon.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
UninstallDisplayIcon={app}\GrokDesktop.exe
UninstallDisplayName=Grok Desktop

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "startup"; Description: "Start Grok Desktop in the tray when I sign in to Windows"; GroupDescription: "Startup:"; Flags: unchecked

[Files]
Source: "..\dist\GrokDesktop\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\Grok Desktop"; Filename: "{app}\GrokDesktop.exe"
Name: "{userdesktop}\Grok Desktop"; Filename: "{app}\GrokDesktop.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "GrokDesktop"; \
  ValueData: """{app}\GrokDesktop.exe"" --minimized"; Tasks: startup; Flags: uninsdeletevalue

[Run]
Filename: "{app}\GrokDesktop.exe"; Description: "Start Grok Desktop"; Flags: nowait postinstall skipifsilent

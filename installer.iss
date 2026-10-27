; Inno Setup script. build.ps1 passes /DAppVersion=x.y.z
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{8F3C2A4E-6B1D-4C8E-9A2F-5D7E1B3C9A10}
AppName=Dynamic Island
AppVersion={#AppVersion}
AppPublisher=mihoshiho
AppPublisherURL=https://github.com/mihoshiho/dynamic-island
; per-user install: no admin prompt, so the island can update itself silently
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\DynamicIsland
DisableDirPage=yes
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename=DynamicIsland-Setup-{#AppVersion}
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\DynamicIsland.exe
UninstallDisplayName=Dynamic Island
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=force
RestartApplications=no

[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"; Flags: unchecked

[InstallDelete]
; files dropped by a newer version must not linger
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "dist\DynamicIsland\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{autoprograms}\Dynamic Island"; Filename: "{app}\DynamicIsland.exe"
Name: "{autodesktop}\Dynamic Island"; Filename: "{app}\DynamicIsland.exe"; Tasks: desktopicon

[Registry]
; the app writes this "Start with Windows" value itself; remove it on uninstall
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "DynamicIsland"; Flags: uninsdeletevalue

[Run]
; runs after a normal install (checkbox) and after a silent auto-update
Filename: "{app}\DynamicIsland.exe"; Description: "Start Dynamic Island"; Flags: nowait postinstall

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/IM DynamicIsland.exe /F"; Flags: runhidden; RunOnceId: "StopIsland"

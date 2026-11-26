; The LuxTunes installer for Windows (Inno Setup 6). Built by build.py, which
; passes AppVersion, AppFolder (PyInstaller's LuxTunes folder), FFmpegFolder,
; IconFile and LicenseFile.
;
; Installs for you alone (no administrator needed), or for everyone if you
; choose. Your library (Music\LuxTunes) and settings (AppData\Local\luxtunes)
; are yours, not the program's: uninstalling leaves them alone.

#define AppName "LuxTunes"

[Setup]
AppId={{15FB1481-A071-4333-A226-9E66CDFE182A}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=metric-ambient
AppPublisherURL=https://github.com/metric-ambient/luxtunes
AppSupportURL=https://github.com/metric-ambient/luxtunes/issues
AppUpdatesURL=https://github.com/metric-ambient/luxtunes/releases
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
LicenseFile={#LicenseFile}
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\LuxTunes.exe
OutputBaseFilename=LuxTunes-{#AppVersion}-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; The previous version's program files (not your library or settings).
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#AppFolder}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#FFmpegFolder}\ffmpeg.exe"; DestDir: "{app}\ffmpeg"; Flags: ignoreversion
Source: "{#FFmpegFolder}\ffprobe.exe"; DestDir: "{app}\ffmpeg"; Flags: ignoreversion
Source: "{#FFmpegFolder}\LICENSE"; DestDir: "{app}\ffmpeg"; DestName: "LICENSE.txt"; Flags: ignoreversion skipifsourcedoesntexist
Source: "{#LicenseFile}"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\LuxTunes.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\LuxTunes.exe"; Tasks: desktopicon

[Run]
; Also after an update (LuxTunes runs the installer with /SILENT), so it opens again.
Filename: "{app}\LuxTunes.exe"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall

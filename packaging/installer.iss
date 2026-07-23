; Inno Setup script -- wraps the PyInstaller folder build into a Windows
; setup program (Stitch-Hero-Setup-<version>.exe).
;
; Build locally:
;   cd packaging && pyinstaller --noconfirm leather-drafting.spec
;   ISCC /DMyAppVersion=0.2.0 installer.iss
; CI passes the version + source dir in as /D defines (see the workflow).

#define MyAppName "Stitch Hero"
#define MyAppPublisher "heathmanc"
#define MyAppURL "https://github.com/heathmanc/leather-drafting"
#define MyAppExeName "Stitch Hero.exe"

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
; PyInstaller output folder (relative to this script, or absolute from CI)
#ifndef MySourceDir
  #define MySourceDir "dist\Stitch Hero"
#endif

[Setup]
; A stable AppId keeps upgrades/uninstalls tied to one product across versions.
AppId={{7E2C9A64-3B1D-4E2A-9C7F-2A6B1F0E4D55}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#MyAppExeName}
OutputDir=installer
OutputBaseFilename=Stitch-Hero-Setup-{#MyAppVersion}
SetupIconFile=icons\StitchHero.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; 64-bit app (PySide6 wheels are x64)
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; per-user install by default -> no admin prompt, "downloadable for all"
PrivilegesRequiredOverridesAllowed=dialog
PrivilegesRequired=lowest

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#MySourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

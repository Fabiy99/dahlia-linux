; Inno Setup script for the Dahlia desktop app (app/packaging/windows).
; Per-user install, no administrator rights: the app updates itself by
; running the next installer with /VERYSILENT (lib/src/installer.dart).
;
; iscc /DVersion=1.4.0 /DSource=..\..\build\windows\x64\runner\Release dahlia.iss

#ifndef Version
  #error Version is required (/DVersion=x.y.z)
#endif
#ifndef Source
  #define Source "..\..\build\windows\x64\runner\Release"
#endif

[Setup]
AppId={{6B0F7C2E-4E0B-4D7A-9C55-3B1D2D7A1E61}
AppName=Dahlia
AppVersion={#Version}
AppPublisher=Dahlia Linux
AppPublisherURL=https://github.com/Fabiy99/dahlia-linux
DefaultDirName={localappdata}\Programs\Dahlia
DefaultGroupName=Dahlia
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir=output
OutputBaseFilename=Dahlia-{#Version}-windows-x64-setup
SetupIconFile=..\..\windows\runner\resources\app_icon.ico
UninstallDisplayIcon={app}\dahlia.exe
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=force
RestartApplications=no
WizardStyle=modern

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "de"; MessagesFile: "compiler:Languages\German.isl"

[Files]
Source: "{#Source}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; Leftovers of an older version (renamed plugin DLLs and the like).
Type: filesandordirs; Name: "{app}\data"

[Icons]
Name: "{userprograms}\Dahlia"; Filename: "{app}\dahlia.exe"
Name: "{userdesktop}\Dahlia"; Filename: "{app}\dahlia.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Run]
; Also after a silent update: the app starts again by itself.
Filename: "{app}\dahlia.exe"; Flags: nowait postinstall

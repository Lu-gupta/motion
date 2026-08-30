; Inno Setup script — compile with Inno Setup 6 (iscc installer\MotionGestureApp.iss)
; Prerequisite: build the app first:
;   .venv\Scripts\pyinstaller.exe MotionGestureApp.spec --noconfirm

#define AppName "Motion Gesture App"
#define AppVersion "0.1.0"
#define AppExe "MotionGestureApp.exe"

[Setup]
AppId={{8E7B2C64-52A1-4E7B-9C93-MGA010000001}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Motion Gesture App
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
UninstallDisplayIcon={app}\{#AppExe}
OutputDir=..\dist
OutputBaseFilename=MotionGestureApp-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest

[Files]
Source: "..\dist\MotionGestureApp\*"; DestDir: "{app}"; \
    Flags: recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; \
    Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; \
    GroupDescription: "Additional icons:"

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; \
    Flags: nowait postinstall skipifsilent

; SPDX-License-Identifier: MPL-2.0
#define MyAppName "Maritime Ship Simulator V2"
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#ifndef MyAppSourceDir
  #define MyAppSourceDir "..\\dist-v2\\Ship Simulator V2"
#endif
#ifndef MyOutputBaseFilename
  #define MyOutputBaseFilename "Maritime-Ship-Simulator-V2-setup"
#endif
#ifndef MyOutputDir
  #define MyOutputDir "..\\release-v2\\windows"
#endif

[Setup]
AppId={{86E9ABAD-A5AA-4E84-A5C0-E6998E855083}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=Maritime Ship Simulator
DefaultDirName={localappdata}\Programs\Maritime Ship Simulator V2
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
UsePreviousAppDir=yes
CloseApplications=yes
CloseApplicationsFilter=Ship Simulator V2.exe
RestartApplications=no
SetupLogging=yes
OutputDir={#MyOutputDir}
OutputBaseFilename={#MyOutputBaseFilename}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\assets\app_icon.ico
UninstallDisplayIcon={app}\Ship Simulator V2.exe
LicenseFile=..\FREEWARE_LICENSE.txt

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#MyAppSourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\Maritime Ship Simulator V2"; Filename: "{app}\Ship Simulator V2.exe"
Name: "{autodesktop}\Maritime Ship Simulator V2"; Filename: "{app}\Ship Simulator V2.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Ship Simulator V2.exe"; Description: "{cm:LaunchProgram,Maritime Ship Simulator V2}"; Flags: nowait postinstall skipifsilent

#ifndef MyAppVersion
  #error MyAppVersion must be provided with /DMyAppVersion=x.y.z
#endif
#ifndef PayloadRoot
  #error PayloadRoot must be provided with /DPayloadRoot=D:\tw\payload
#endif
#ifndef OutputDir
  #error OutputDir must be provided with /DOutputDir=D:\tw\out
#endif

#define MyAppName "Taiji Agent"
#define MyAppPublisher "Taiji Agent"
#define MyAppExeName "TaijiAgent.exe"

[Setup]
AppId={{B2C40D2B-8F6D-4E30-9D6D-8E0C9FC2E824}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\Taiji Agent
DefaultGroupName=Taiji Agent
DisableProgramGroupPage=yes
PrivilegesRequired=admin
SetupArchitecture=x64
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
MinVersion=10.0
OutputDir={#OutputDir}
OutputBaseFilename=TaijiAgent-Setup-{#MyAppVersion}-win-x64
Compression=lzma2/fast
SolidCompression=no
WizardStyle=modern
SetupLogging=yes
CloseApplications=yes
RestartApplications=no
UninstallDisplayIcon={app}\{#MyAppExeName}
VersionInfoVersion={#MyAppVersion}
VersionInfoProductName={#MyAppName}
VersionInfoCompany={#MyAppPublisher}

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Files]
Source: "{#PayloadRoot}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Taiji Agent"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{group}\Taiji Agent 诊断"; Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\tools\diagnose.ps1"""; WorkingDir: "{app}"
Name: "{autodesktop}\Taiji Agent"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 Taiji Agent"; Flags: nowait postinstall skipifsilent runasoriginaluser

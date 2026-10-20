#ifndef AppVersion
  #define AppVersion "0.6.0"
#endif
[Setup]
AppId={{19E4F5E1-43D5-4C13-9890-071C070D2F60}
AppName=VOID LEADS
AppVersion={#AppVersion}
AppPublisher=VOID LEADS
DefaultDirName={localappdata}\Programs\VOID LEADS
DefaultGroupName=VOID LEADS
UninstallDisplayIcon={app}\VOID-Leads.exe
SetupIconFile=..\assets\void-leads.ico
OutputDir=..\release
OutputBaseFilename=VOID-Leads-Setup
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
UsePreviousAppDir=yes
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Ярлыки:"; Flags: checkedonce

[Files]
Source: "..\dist\VOID-Leads.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\VOID-Updater.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\VOID LEADS"; Filename: "{app}\VOID-Leads.exe"
Name: "{autodesktop}\VOID LEADS"; Filename: "{app}\VOID-Leads.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\VOID-Leads.exe"; Description: "Запустить VOID LEADS"; Flags: nowait postinstall skipifsilent

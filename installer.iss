; Inno Setup script - build with:  iscc /DAppVersion=1.0.0 installer.iss   (after pyinstaller)
#ifndef AppVersion
#define AppVersion "1.0.0"
#endif
[Setup]
AppId={{B7C1D1F4-5A52-4B6D-9C57-3F1E8A0A7D11}
AppName=Serial Monitor
AppVersion={#AppVersion}
AppPublisher=Serial Monitor
DefaultDirName={autopf}\Serial Monitor
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename=SerialMonitor-Setup-{#AppVersion}
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\SerialMonitor.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; per-user install by default (no admin prompt); the user can switch to all-users in the dialog
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
Source: "dist\SerialMonitor\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
#ifexist "redist\MicrosoftEdgeWebview2Setup.exe"
Source: "redist\MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall
#endif

[Icons]
Name: "{autoprograms}\Serial Monitor"; Filename: "{app}\SerialMonitor.exe"
Name: "{autodesktop}\Serial Monitor"; Filename: "{app}\SerialMonitor.exe"; Tasks: desktopicon

[Run]
#ifexist "redist\MicrosoftEdgeWebview2Setup.exe"
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; StatusMsg: "Installing Microsoft Edge WebView2 runtime..."; Check: NeedsWebView2; Flags: waituntilterminated
#endif
Filename: "{app}\SerialMonitor.exe"; Description: "Launch Serial Monitor"; Flags: nowait postinstall skipifsilent

[Code]
const WV2 = 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
function HasWV2(Root: Integer): Boolean;
var v: String;
begin
  Result := RegQueryStringValue(Root, WV2, 'pv', v) and (v <> '') and (v <> '0.0.0.0');
end;
function NeedsWebView2: Boolean;
begin
  Result := not (HasWV2(HKLM32) or HasWV2(HKCU));
end;

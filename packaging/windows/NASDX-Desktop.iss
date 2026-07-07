; NASDX Desktop installer wrapper.
;
; Build the portable folder first:
;   powershell -ExecutionPolicy Bypass -File packaging\windows\build_portable.ps1
;
; Then compile this script with Inno Setup:
;   iscc packaging\windows\NASDX-Desktop.iss
;
; The installer wraps the already-tested portable folder. It must not collect
; local user config, generated reports, history databases, logs, cache folders,
; wheelhouse files, or build outputs from the repository.
; User-facing desktop instructions are packaged at docs\WINDOWS_DESKTOP.md.
; WebView2 is useful for the optional pywebview native window; NASDX still
; falls back to the user's browser when the WebView path is unavailable.

#ifndef MyAppName
  #define MyAppName "NASDX Desktop"
#endif
#ifndef MyAppVersion
  #define MyAppVersion "0.1.0"
#endif
#ifndef MyAppPublisher
  #define MyAppPublisher "NASDX"
#endif
#ifndef MyAppExeName
  #define MyAppExeName "启动NASDX桌面.bat"
#endif
#ifndef PortableDir
  #define PortableDir "..\..\dist\NASDX-Desktop"
#endif
#ifndef InstallerOutputDir
  #define InstallerOutputDir "..\..\dist\installer"
#endif

[Setup]
AppId={{8F0CFB80-6560-4327-A2D4-3D53AB4FC6A0}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\NASDX Desktop
DefaultGroupName=NASDX Desktop
DisableProgramGroupPage=yes
OutputDir={#InstallerOutputDir}
OutputBaseFilename=NASDX-Desktop-Setup
Compression=lzma2
SolidCompression=yes
SetupLogging=yes
WizardStyle=modern
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "{#PortableDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "reports\*,stock_data_*.json,nasdx_history.db,config.toml,.env,*.log,*_log*.txt,pip_*.txt,__pycache__\*,.git\*,dist\*,build\*,wheelhouse\*,models\signal_confidence.json"

[Icons]
Name: "{group}\NASDX Desktop"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\NASDX Desktop"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch NASDX Desktop"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent

; Do not delete local user runtime state on uninstall. The launcher reads user
; config from %APPDATA%\NASDX\config.toml or NASDX_CONFIG_FILE, and reports or
; history can be kept outside the installed application folder.
[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
function InitializeSetup(): Boolean;
begin
  if not DirExists(ExpandConstant('{#PortableDir}')) then
  begin
    MsgBox(
      'Portable package not found. Run packaging\windows\build_portable.ps1 before compiling the installer.',
      mbError,
      MB_OK
    );
    Result := False;
  end
  else
    Result := True;
end;

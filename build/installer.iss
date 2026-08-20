; Instalador de Claude Usage Monitor (Inno Setup 6).
; Se compila desde build\build.ps1, o a mano con:  ISCC.exe build\installer.iss

#define AppName        "Claude Usage Monitor"
#define AppVersion     "1.0.0"
#define AppExe         "ClaudeUsageMonitor.exe"
#define AppPublisher   "Uso personal"

[Setup]
AppId={{7F1D0C4E-2B6A-4E1F-9C3D-8A5E4B2C1D90}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\ClaudeUsageMonitor
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; lowest = se instala en la carpeta del usuario sin pedir permisos de administrador
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename=ClaudeUsageMonitor-Setup
SetupIconFile=..\assets\app.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "autostart"; Description: "Iniciar automáticamente con Windows"; GroupDescription: "Opciones:"
Name: "desktopicon"; Description: "Crear un acceso directo en el escritorio"; GroupDescription: "Opciones:"; Flags: unchecked

[Files]
Source: "..\dist\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Desinstalar {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; Arranque automatico para el usuario actual
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
    ValueType: string; ValueName: "ClaudeUsageMonitor"; ValueData: """{app}\{#AppExe}"""; \
    Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\{#AppExe}"; Description: "Abrir {#AppName} ahora"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Sesion guardada, ajustes y registro de la app
Type: filesandordirs; Name: "{userappdata}\ClaudeUsageMonitor"

[Code]
// El .exe se queda en la bandeja: hay que cerrarlo antes de desinstalar o
// actualizar, o el fichero estaria en uso.
function CerrarApp(): Boolean;
var
  Codigo: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/IM {#AppExe} /F', '',
       SW_HIDE, ewWaitUntilTerminated, Codigo);
  Result := True;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  CerrarApp();
  Result := '';
end;

function InitializeUninstall(): Boolean;
begin
  Result := CerrarApp();
end;

; Instalador del CLIENTE (para repartir): sin Python, sin modelos y sin claves de API.
; Pide la dirección del servidor (wss://…), el usuario y la clave y escribe config.json.
; Compilar con Inno Setup 6 después de build_client.bat:   iscc installer-cliente.iss
; (GitHub Actions lo compila solo: Artifacts → TraductorCliente-Setup)

#define AppVer "1.0"

[Setup]
AppName=Traductor en Vivo (cliente)
AppVersion={#AppVer}
AppPublisher=James
AppId={{6F1C2A1E-7B9D-4C55-9E0B-2B7C1D4A8F31}
; por usuario y sin permisos de administrador: el overlay guarda sus ajustes junto al .exe
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\TraductorCliente
DefaultGroupName=Traductor en Vivo
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\traductor.ico
SetupIconFile=traductor.ico
OutputBaseFilename=TraductorCliente-Setup
OutputDir=Output
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "Crear acceso directo en el escritorio"; GroupDescription: "Accesos directos:"

[Files]
Source: "dist\TraductorCliente\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "traductor.ico"; DestDir: "{app}"

[Icons]
Name: "{autodesktop}\Traductor en Vivo"; Filename: "{app}\TraductorCliente.exe"; IconFilename: "{app}\traductor.ico"; Tasks: desktopicon
Name: "{group}\Traductor en Vivo"; Filename: "{app}\TraductorCliente.exe"; IconFilename: "{app}\traductor.ico"
Name: "{group}\Cambiar servidor o token"; Filename: "notepad.exe"; Parameters: """{app}\config.json"""

[Run]
Filename: "{app}\TraductorCliente.exe"; Description: "Abrir Traductor en Vivo"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: files; Name: "{app}\config.json"

[Code]
var
  ConnPage: TInputQueryWizardPage;

function ConfigPath: String;
begin
  Result := ExpandConstant('{app}\config.json');
end;

procedure InitializeWizard;
begin
  ConnPage := CreateInputQueryPage(wpSelectDir,
    'Conexión con el servidor',
    'Datos que te dio quien administra el servidor',
    'Pega la dirección (empieza por wss://) y escribe tu usuario y tu clave. ' +
    'Si ya lo tenías instalado, déjalos vacíos para conservar los actuales.');
  ConnPage.Add('Servidor:', False);
  ConnPage.Add('Usuario:', False);
  ConnPage.Add('Clave:', True);
end;

{ quien pega la dirección completa sobre un "wss://" ya escrito deja "wss://wss://…" }
function CleanServer(S: String): String;
begin
  S := Trim(S);
  while Pos('wss://wss://', Lowercase(S)) = 1 do Delete(S, 1, 6);
  Result := S;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Server, User, Pass: String;
begin
  Result := True;
  if CurPageID <> ConnPage.ID then Exit;
  Server := CleanServer(ConnPage.Values[0]);
  User := Trim(ConnPage.Values[1]);
  Pass := ConnPage.Values[2];
  if (Server = '') and (User = '') and (Pass = '') and FileExists(ConfigPath) then Exit;
  if (Pos('wss://', Lowercase(Server)) <> 1) or (Length(Server) < 10) then begin
    MsgBox('La dirección del servidor debe empezar por wss://', mbError, MB_OK);
    Result := False;
  end else if User = '' then begin
    MsgBox('Escribe tu usuario.', mbError, MB_OK);
    Result := False;
  end else if Length(Pass) < 8 then begin
    MsgBox('La clave tiene al menos 8 caracteres: revísala.', mbError, MB_OK);
    Result := False;
  end;
end;

function JsonEscape(S: String): String;
begin
  StringChangeEx(S, '\', '\\', True);
  StringChangeEx(S, '"', '\"', True);
  Result := S;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Server, User, Pass: String;
begin
  if CurStep <> ssPostInstall then Exit;
  Server := CleanServer(ConnPage.Values[0]);
  User := Trim(ConnPage.Values[1]);
  Pass := ConnPage.Values[2];
  if User = '' then Exit;                        { actualización: se conserva el config.json actual }
  SaveStringToFile(ConfigPath,
    '{' + #13#10 +
    '  "server": "' + JsonEscape(Server) + '",' + #13#10 +
    '  "user": "' + JsonEscape(User) + '",' + #13#10 +
    '  "password": "' + JsonEscape(Pass) + '",' + #13#10 +
    '  "tone": "gamer",' + #13#10 +
    '  "dub": false' + #13#10 +
    '}' + #13#10, False);
end;

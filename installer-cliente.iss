; Instalador del CLIENTE (para repartir): sin Python, sin modelos y sin claves de API.
; Pide la dirección del servidor (wss://…) y el token personal y escribe config.json.
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
    'Pega la dirección (empieza por wss://) y tu token personal. ' +
    'Si ya lo tenías instalado, déjalos vacíos para conservar los actuales.');
  ConnPage.Add('Servidor:', False);
  ConnPage.Add('Token:', True);
  ConnPage.Values[0] := 'wss://';
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Server, Token: String;
begin
  Result := True;
  if CurPageID <> ConnPage.ID then Exit;
  Server := Trim(ConnPage.Values[0]);
  Token := Trim(ConnPage.Values[1]);
  if ((Server = '') or (Server = 'wss://')) and (Token = '') and FileExists(ConfigPath) then Exit;
  if (Pos('wss://', Lowercase(Server)) <> 1) or (Length(Server) < 10) then begin
    MsgBox('La dirección del servidor debe empezar por wss://', mbError, MB_OK);
    Result := False;
  end else if Length(Token) < 16 then begin
    MsgBox('El token parece incompleto: cópialo entero.', mbError, MB_OK);
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
  Server, Token: String;
begin
  if CurStep <> ssPostInstall then Exit;
  Server := Trim(ConnPage.Values[0]);
  Token := Trim(ConnPage.Values[1]);
  if Token = '' then Exit;                       { actualización: se conserva el config.json actual }
  SaveStringToFile(ConfigPath,
    '{' + #13#10 +
    '  "server": "' + JsonEscape(Server) + '",' + #13#10 +
    '  "token": "' + JsonEscape(Token) + '",' + #13#10 +
    '  "tone": "gamer",' + #13#10 +
    '  "dub": false' + #13#10 +
    '}' + #13#10, False);
end;

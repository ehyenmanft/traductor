; Instalador de Traductor en Vivo — compilar con Inno Setup 6
; https://jrsoftware.org/isdl.php   (abrir este archivo y pulsar Compile,
; o por consola:  iscc installer.iss)

[Setup]
AppName=Traductor en Vivo
AppVersion=1.0
AppPublisher=James
DefaultDirName={autopf}\TraductorEnVivo
DefaultGroupName=Traductor en Vivo
UninstallDisplayIcon={app}\traductor.ico
SetupIconFile=traductor.ico
OutputBaseFilename=TraductorEnVivo-Setup
OutputDir=Output
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequiredOverridesAllowed=dialog
WizardStyle=modern

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "Crear acceso directo en el escritorio"; \
    GroupDescription: "Accesos directos:"

[Files]
Source: "dist\TraductorEnVivo\*"; DestDir: "{app}"; \
    Flags: recursesubdirs createallsubdirs
Source: "traductor.ico"; DestDir: "{app}"
; config con placeholder solo si no existe (no pisa la key en actualizaciones)
Source: "config.example.json"; DestDir: "{app}"; DestName: "config.json"; \
    Flags: onlyifdoesntexist

[Icons]
Name: "{autodesktop}\Traductor en Vivo"; Filename: "{app}\TraductorEnVivo.exe"; \
    IconFilename: "{app}\traductor.ico"; Tasks: desktopicon
Name: "{group}\Traductor en Vivo"; Filename: "{app}\TraductorEnVivo.exe"; \
    IconFilename: "{app}\traductor.ico"
Name: "{group}\Editar configuración (API keys)"; \
    Filename: "notepad.exe"; Parameters: """{app}\config.json"""

[Run]
Filename: "notepad.exe"; Parameters: """{app}\config.json"""; \
    Description: "Abrir config.json para pegar tu API key de Deepgram"; \
    Flags: postinstall skipifsilent unchecked
Filename: "{app}\TraductorEnVivo.exe"; Description: "Ejecutar Traductor en Vivo"; \
    Flags: nowait postinstall skipifsilent

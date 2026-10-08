@echo off
setlocal EnableExtensions
title Compilar TraductorCliente.exe
cd /d "%~dp0"
rem Alternativa a la compilacion automatica de GitHub Actions (necesita Python en ESTA PC solo para compilar).

if not exist "venv-cliente\Scripts\python.exe" (
    echo [SETUP] Creando entorno virtual...
    python -m venv venv-cliente || (pause & exit /b 1)
)
set "VPY=venv-cliente\Scripts\python.exe"
"%VPY%" -m pip install --upgrade pip --quiet
"%VPY%" -m pip install -r requirements-client.txt pyinstaller --quiet || (pause & exit /b 1)

echo [BUILD] Empaquetando el cliente (rapido: no incluye modelos)...
"%VPY%" -m PyInstaller --noconfirm --clean --onedir --windowed ^
    --name TraductorCliente --icon traductor.ico ^
    --hidden-import style_dialog ^
    --exclude-module faster_whisper --exclude-module ctranslate2 ^
    --exclude-module onnxruntime --exclude-module av --exclude-module websockets ^
    client_main.py || (echo [ERROR] Fallo el empaquetado & pause & exit /b 1)

copy /y traductor.ico dist\TraductorCliente\ >nul
copy /y config.client.example.json dist\TraductorCliente\ >nul
echo.
echo [OK] dist\TraductorCliente\TraductorCliente.exe
echo Copia config.client.example.json como config.json junto al .exe y rellena token y ssh.
pause

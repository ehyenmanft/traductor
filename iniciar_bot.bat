@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title Bot Traductor de Videos (Telegram)

echo ==================================================
echo   Bot de Telegram - Traductor de videos
echo ==================================================

rem --- 1. Python ---
set "PY=py -3"
%PY% --version >nul 2>&1 || set "PY=python"
%PY% --version >nul 2>&1 || (
    echo [ERROR] No se encontro Python 3.10+. Instalalo desde https://www.python.org/downloads/
    echo         y marca "Add Python to PATH". Luego vuelve a ejecutar este archivo.
    pause & exit /b 1
)

rem --- 2. ffmpeg ---
where ffmpeg >nul 2>&1 || (
    echo [INFO] ffmpeg no esta instalado. Instalando con winget...
    winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
    echo.
    echo [IMPORTANTE] Cierra esta ventana y vuelve a ejecutar iniciar_bot.bat
    echo              para que Windows reconozca ffmpeg.
    pause & exit /b 0
)

rem --- 3. config.json ---
if not exist config.json (
    echo [ERROR] Falta config.json. Copia config.example.json a config.json y completalo.
    pause & exit /b 1
)
findstr /c:"TOKEN_DE_BOTFATHER" config.json >nul && (
    echo [ERROR] Abre config.json y reemplaza TOKEN_DE_BOTFATHER por el token de @BotFather.
    notepad config.json
    pause & exit /b 1
)

rem --- 4. Entorno virtual + dependencias (solo la primera vez tarda) ---
if not exist .venv-bot\Scripts\python.exe (
    echo [INFO] Creando entorno virtual...
    %PY% -m venv .venv-bot || (pause & exit /b 1)
)
echo [INFO] Verificando dependencias...
.venv-bot\Scripts\python.exe -m pip install -q -r requirements-bot.txt || (pause & exit /b 1)

rem --- 5. Ejecutar (se reinicia solo si se cae) ---
:loop
echo [INFO] Bot en marcha. Ctrl+C para detener.
.venv-bot\Scripts\python.exe telegram_bot.py
echo [AVISO] El bot se detuvo. Reiniciando en 5 s... (cierra la ventana para salir)
timeout /t 5 >nul
goto loop

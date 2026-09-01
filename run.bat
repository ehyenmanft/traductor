@echo off
setlocal EnableExtensions
title Traductor de voz en vivo - Overlay
cd /d "%~dp0"

echo ============================================
echo   Traductor de voz en vivo - Overlay
echo ============================================
echo.

:: ---------- 1. Verificar Python ----------
where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python no esta instalado o no esta en el PATH.
    echo Descargalo desde https://www.python.org/downloads/
    echo y marca "Add Python to PATH" durante la instalacion.
    pause
    exit /b 1
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo [OK] Python %PYVER% detectado.

:: ---------- 2. Crear entorno virtual si no existe ----------
if not exist "venv\Scripts\python.exe" (
    echo [SETUP] Creando entorno virtual...
    python -m venv venv
    if errorlevel 1 (
        echo [ERROR] No se pudo crear el entorno virtual.
        pause
        exit /b 1
    )
) else (
    echo [OK] Entorno virtual encontrado.
)

set "VPY=venv\Scripts\python.exe"

:: ---------- 3. Instalar dependencias solo si hace falta ----------
:: Marca de instalacion: se regenera si cambia requirements.txt
set NEED_INSTALL=0
if not exist "venv\.deps_ok" set NEED_INSTALL=1
if exist "venv\.deps_ok" (
    fc /b requirements.txt "venv\.deps_ok" >nul 2>&1 || set NEED_INSTALL=1
)

if "%NEED_INSTALL%"=="1" (
    echo [SETUP] Instalando dependencias, esto puede tardar unos minutos...
    "%VPY%" -m pip install --upgrade pip --quiet
    "%VPY%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Fallo la instalacion de dependencias.
        pause
        exit /b 1
    )
    copy /y requirements.txt "venv\.deps_ok" >nul
    echo [OK] Dependencias instaladas.
) else (
    echo [OK] Dependencias al dia.
)

:: ---------- 4. Detectar GPU NVIDIA para elegir device ----------
set "DEVICE=cpu"
where nvidia-smi >nul 2>&1
if not errorlevel 1 (
    nvidia-smi >nul 2>&1
    if not errorlevel 1 set "DEVICE=auto"
)
echo [OK] Dispositivo de inferencia: %DEVICE%

:: En CPU el modelo depende de la RAM: tiny para equipos con poca memoria.
:: Puedes forzar otro: run.bat --model small
set "MODEL=small"
if "%DEVICE%"=="cpu" (
    set "MODEL=base"
    for /f %%m in ('powershell -noprofile -command "[math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory/1GB)"') do set RAM_GB=%%m
)
if "%DEVICE%"=="cpu" if defined RAM_GB (
    echo [OK] RAM detectada: %RAM_GB% GB
    if %RAM_GB% LEQ 5 set "MODEL=tiny"
)
echo [OK] Modelo Whisper: %MODEL%

:: Librerias CUDA runtime via pip (una sola vez, solo si hay GPU)
if "%DEVICE%"=="auto" if not exist "venv\.cuda_ok" (
    echo [SETUP] Instalando librerias CUDA para la GPU...
    "%VPY%" -m pip install nvidia-cublas-cu12 nvidia-cudnn-cu12 --quiet
    if not errorlevel 1 (
        echo ok> "venv\.cuda_ok"
        echo [OK] Librerias CUDA instaladas.
    ) else (
        echo [AVISO] No se pudieron instalar las librerias CUDA, se usara CPU.
    )
)

:: ---------- 5. Lanzar la app ----------
:: Argumentos extra se pasan directo: run.bat --target en --model medium
echo [RUN] Iniciando overlay...
echo       F6 = Modo Gaming HUD Subtitle ^| F7 = Opacidad ^| F8 = Click-through
echo       F9 = Mostrar/Ocultar ^| F10 = Compacto ^| Icono en Bandeja del Sistema
echo.
"%VPY%" main.py --device %DEVICE% --model %MODEL% %*


if errorlevel 1 (
    echo.
    echo [ERROR] La aplicacion termino con errores. Revisa el mensaje de arriba.
    pause
)

endlocal

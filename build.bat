@echo off
setlocal EnableExtensions
title Compilar TraductorEnVivo.exe
cd /d "%~dp0"

echo ============================================
echo   Compilando TraductorEnVivo.exe
echo ============================================

if not exist "traductor.ico" (
    echo [ERROR] Falta traductor.ico en esta carpeta.
    pause & exit /b 1
)

if not exist "venv\Scripts\python.exe" (
    echo [SETUP] Creando entorno virtual...
    python -m venv venv || (pause & exit /b 1)
)
set "VPY=venv\Scripts\python.exe"

echo [SETUP] Instalando dependencias + PyInstaller...
"%VPY%" -m pip install --upgrade pip --quiet
"%VPY%" -m pip install -r requirements.txt pyinstaller --quiet || (pause & exit /b 1)

echo [BUILD] Empaquetando (esto tarda varios minutos)...
"%VPY%" -m PyInstaller --noconfirm --clean --onedir ^
    --name TraductorEnVivo ^
    --icon traductor.ico ^
    --collect-all faster_whisper ^
    main.py
if errorlevel 1 (
    echo [ERROR] Fallo el empaquetado. Revisa el mensaje de arriba.
    pause & exit /b 1
)

echo.
echo [OK] Listo: dist\TraductorEnVivo\TraductorEnVivo.exe
echo Siguiente paso: compilar installer.iss con Inno Setup.
pause

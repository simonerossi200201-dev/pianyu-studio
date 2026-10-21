@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 片语工作室 (Cutora LTX Studio v3.0)
set "PYTHONUTF8=1"
set "HF_HUB_OFFLINE=1"
set "TRANSFORMERS_OFFLINE=1"
set "STUDIO_ROOT=%~dp0"
if "%STUDIO_ROOT:~-1%"=="\" set "STUDIO_ROOT=%STUDIO_ROOT:~0,-1%"
set "PATH=%STUDIO_ROOT%\runtime\bin;%STUDIO_ROOT%\runtime\ffmpeg;%PATH%"

if exist "%STUDIO_ROOT%\runtime\bin\portable_venv_python.exe" (
    copy /y "%STUDIO_ROOT%\runtime\bin\portable_venv_python.exe" "%STUDIO_ROOT%\runtime\venv\Scripts\python.exe" >nul 2>&1
)
if exist "%STUDIO_ROOT%\runtime\bin\portable_venv_pythonw.exe" (
    copy /y "%STUDIO_ROOT%\runtime\bin\portable_venv_pythonw.exe" "%STUDIO_ROOT%\runtime\venv\Scripts\pythonw.exe" >nul 2>&1
)

if exist "%STUDIO_ROOT%\runtime\venv\Scripts\pythonw.exe" (
    start "" "%STUDIO_ROOT%\runtime\venv\Scripts\pythonw.exe" "%STUDIO_ROOT%\studio_launcher.py" %*
    exit /b 0
)
if exist "%STUDIO_ROOT%\runtime\python\pythonw.exe" (
    start "" "%STUDIO_ROOT%\runtime\python\pythonw.exe" "%STUDIO_ROOT%\studio_launcher.py" %*
    exit /b 0
)
python "%STUDIO_ROOT%\studio_launcher.py" %*

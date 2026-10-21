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

REM 自动将 venv 路径指向当前解压目录下的便携 Python，确保任何电脑任意盘符解压即用
if exist "%STUDIO_ROOT%\runtime\python\python.exe" (
    (
        echo home = %STUDIO_ROOT%\runtime\python
        echo include-system-site-packages = false
        echo version = 3.10.11
    ) > "%STUDIO_ROOT%\runtime\venv\pyvenv.cfg"
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

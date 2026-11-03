@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 片语剪辑台 CUTORA (独立运行模式)
set "PYTHONUTF8=1"
set "HF_HUB_OFFLINE=1"
set "TRANSFORMERS_OFFLINE=1"
set "STUDIO_ROOT=%~dp0"
if "%STUDIO_ROOT:~-1%"=="\" set "STUDIO_ROOT=%STUDIO_ROOT:~0,-1%"
set "PATH=%STUDIO_ROOT%\runtime\bin;%STUDIO_ROOT%\runtime\ffmpeg;%PATH%"

if exist "%STUDIO_ROOT%\runtime\venv\Scripts\python.exe" (
    if exist "%STUDIO_ROOT%\runtime\bin\portable_venv_python.exe" (
        copy /y "%STUDIO_ROOT%\runtime\bin\portable_venv_python.exe" "%STUDIO_ROOT%\runtime\venv\Scripts\python.exe" >nul 2>&1
    )
    "%STUDIO_ROOT%\runtime\venv\Scripts\python.exe" "%STUDIO_ROOT%\core\standalone_bootstrap.py" --app cutora %*
    exit /b %ERRORLEVEL%
)
if exist "%STUDIO_ROOT%\runtime\python\python.exe" (
    "%STUDIO_ROOT%\runtime\python\python.exe" "%STUDIO_ROOT%\core\standalone_bootstrap.py" --app cutora %*
    exit /b %ERRORLEVEL%
)
python "%STUDIO_ROOT%\core\standalone_bootstrap.py" --app cutora %*

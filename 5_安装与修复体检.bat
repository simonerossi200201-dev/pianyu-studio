@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 片语工作室 — 安装、右键注册与环境体检
set "PYTHONUTF8=1"
set "STUDIO_ROOT=%~dp0"
if "%STUDIO_ROOT:~-1%"=="\" set "STUDIO_ROOT=%STUDIO_ROOT:~0,-1%"
set "PATH=%STUDIO_ROOT%\runtime\bin;%STUDIO_ROOT%\runtime\ffmpeg;%PATH%"

if exist "%STUDIO_ROOT%\runtime\venv\Scripts\python.exe" (
    if exist "%STUDIO_ROOT%\runtime\bin\portable_venv_python.exe" (
        copy /y "%STUDIO_ROOT%\runtime\bin\portable_venv_python.exe" "%STUDIO_ROOT%\runtime\venv\Scripts\python.exe" >nul 2>&1
    )
    "%STUDIO_ROOT%\runtime\venv\Scripts\python.exe" "%STUDIO_ROOT%\core\standalone_bootstrap.py" --app install %*
) else if exist "%STUDIO_ROOT%\runtime\python\python.exe" (
    "%STUDIO_ROOT%\runtime\python\python.exe" "%STUDIO_ROOT%\core\standalone_bootstrap.py" --app install %*
) else (
    python "%STUDIO_ROOT%\core\standalone_bootstrap.py" --app install %*
)
pause

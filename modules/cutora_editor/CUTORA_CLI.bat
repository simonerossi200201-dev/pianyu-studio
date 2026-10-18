@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
cd /d "%~dp0"
title CUTORA CLI
color 0B

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
set "VENV_ACTIVATE=%ROOT%\runtime\venv\Scripts\activate.bat"
set "FFMPEG_DIR=%ROOT%\runtime\ffmpeg"
set "BUNDLED_PY=%ROOT%\runtime\python\python.exe"

if exist "%BUNDLED_PY%" (
    "%BUNDLED_PY%" -c "import sys; sys.path.insert(0, r'%ROOT%'); from cutora.core.portable import rewrite_pyvenv_cfg; rewrite_pyvenv_cfg(r'%ROOT%')"
    if errorlevel 1 (
        color 0C
        echo [ERROR] Failed to rewrite runtime\venv\pyvenv.cfg
        pause
        exit /b 1
    )
)

if not exist "%VENV_ACTIVATE%" (
    color 0C
    echo [ERROR] Venv missing. Please run CUTORA_Install.bat first.
    pause
    exit /b 1
)

set "PATH=%FFMPEG_DIR%;%PATH%"
call "%VENV_ACTIVATE%"
cd /d "%ROOT%"

python -m cutora.batch.cli %*
exit /b %errorlevel%

@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

set "PY=%ROOT%\runtime\venv\Scripts\python.exe"
if not exist "%PY%" set "PY=%ROOT%\runtime\python\python.exe"
if not exist "%PY%" (
    echo [ERROR] Python not found. Double-click CUTORA.vbs or CUTORA.bat first.
    pause
    exit /b 1
)
if not exist "%ROOT%\CUTORA.vbs" (
    echo [ERROR] CUTORA.vbs not found
    pause
    exit /b 1
)
if not exist "%ROOT%\resources\brand\app.ico" (
    echo [ERROR] app.ico not found. Expected resources\brand\app.ico.
    pause
    exit /b 1
)

"%PY%" -c "import sys; sys.path.insert(0, r'%ROOT%'); from cutora.core.bootstrap import create_desktop_shortcut; print(create_desktop_shortcut(r'%ROOT%'))"
if errorlevel 1 (
    echo [ERROR] failed to create shortcut
    pause
    exit /b 1
)
exit /b 0

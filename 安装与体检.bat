@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "STUDIO_ROOT=%~dp0"
if "%STUDIO_ROOT:~-1%"=="\" set "STUDIO_ROOT=%STUDIO_ROOT:~0,-1%"
if exist "%STUDIO_ROOT%\runtime\bin\portable_venv_python.exe" (
    copy /y "%STUDIO_ROOT%\runtime\bin\portable_venv_python.exe" "%STUDIO_ROOT%\runtime\venv\Scripts\python.exe" >nul 2>&1
)
if exist "%STUDIO_ROOT%\runtime\bin\portable_venv_pythonw.exe" (
    copy /y "%STUDIO_ROOT%\runtime\bin\portable_venv_pythonw.exe" "%STUDIO_ROOT%\runtime\venv\Scripts\pythonw.exe" >nul 2>&1
)
if exist "%STUDIO_ROOT%\runtime\venv\Scripts\python.exe" (
    "%STUDIO_ROOT%\runtime\venv\Scripts\python.exe" "%STUDIO_ROOT%\install_studio.py" %*
) else if exist "%STUDIO_ROOT%\runtime\python\python.exe" (
    "%STUDIO_ROOT%\runtime\python\python.exe" "%STUDIO_ROOT%\install_studio.py" %*
) else (
    python "%STUDIO_ROOT%\install_studio.py" %*
)
pause

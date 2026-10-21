@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "STUDIO_ROOT=%~dp0"
if "%STUDIO_ROOT:~-1%"=="\" set "STUDIO_ROOT=%STUDIO_ROOT:~0,-1%"
if exist "%STUDIO_ROOT%\runtime\bin\portable_venv_pythonw.exe" (
    copy /y "%STUDIO_ROOT%\runtime\bin\portable_venv_pythonw.exe" "%STUDIO_ROOT%\runtime\venv\Scripts\pythonw.exe" >nul 2>&1
)
start "" "%STUDIO_ROOT%\runtime\venv\Scripts\pythonw.exe" "%STUDIO_ROOT%\modules\table_tool\app\表格工具.py" %*

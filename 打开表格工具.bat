@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "STUDIO_ROOT=%~dp0"
if "%STUDIO_ROOT:~-1%"=="\" set "STUDIO_ROOT=%STUDIO_ROOT:~0,-1%"
if exist "%STUDIO_ROOT%\runtime\python\python.exe" (
    (
        echo home = %STUDIO_ROOT%\runtime\python
        echo include-system-site-packages = false
        echo version = 3.10.11
    ) > "%STUDIO_ROOT%\runtime\venv\pyvenv.cfg"
)
start "" "%STUDIO_ROOT%\runtime\venv\Scripts\pythonw.exe" "%STUDIO_ROOT%\modules\table_tool\app\表格工具.py" %*

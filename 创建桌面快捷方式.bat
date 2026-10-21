@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "STUDIO_ROOT=%~dp0"
if "%STUDIO_ROOT:~-1%"=="\" set "STUDIO_ROOT=%STUDIO_ROOT:~0,-1%"
if exist "%STUDIO_ROOT%\runtime\python\python.exe" (
    (
        echo home = %STUDIO_ROOT%\runtime\python
        echo include-system-site-packages = false
        echo version = 3.10.11
    ) > "%STUDIO_ROOT%\runtime\venv\pyvenv.cfg"
)
if exist "%STUDIO_ROOT%\runtime\venv\Scripts\python.exe" (
    "%STUDIO_ROOT%\runtime\venv\Scripts\python.exe" -m core.shortcut
) else (
    python -m core.shortcut
)
echo.
echo ✅ 桌面与开始菜单快捷方式已创建完成！
pause

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
if exist "%STUDIO_ROOT%\runtime\comfyui\python312\python.exe" (
    if exist "%STUDIO_ROOT%\runtime\comfyui\venv" (
        (
            echo home = %STUDIO_ROOT%\runtime\comfyui\python312
            echo implementation = CPython
            echo version_info = 3.12.7
            echo include-system-site-packages = false
        ) > "%STUDIO_ROOT%\runtime\comfyui\venv\pyvenv.cfg"
    )
)
if exist "%STUDIO_ROOT%\runtime\venv\Scripts\python.exe" (
    "%STUDIO_ROOT%\runtime\venv\Scripts\python.exe" "%STUDIO_ROOT%\install_studio.py" %*
) else if exist "%STUDIO_ROOT%\runtime\python\python.exe" (
    "%STUDIO_ROOT%\runtime\python\python.exe" "%STUDIO_ROOT%\install_studio.py" %*
) else (
    python "%STUDIO_ROOT%\install_studio.py" %*
)
pause

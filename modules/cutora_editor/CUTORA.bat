@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
title 片语 Studio / CUTORA

REM Success never pauses. Errors pause. bootstrap exits 0 so its window closes.
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
set "MODE=%~1"
set "BUNDLED=%ROOT%\runtime\python\python.exe"
set "VENV=%ROOT%\runtime\venv"
set "VENV_PY=%VENV%\Scripts\python.exe"
set "VENV_PYW=%VENV%\Scripts\pythonw.exe"
set "LOCK=%ROOT%\requirements.lock"
set "FFMPEG_DIR=%ROOT%\runtime\ffmpeg"

if not exist "%ROOT%\cutora\__main__.py" (
    color 0C
    echo [FATAL] cutora package missing.
    pause
    exit /b 1
)

if exist "%BUNDLED%" set "PY_KIND=exe"
if exist "%BUNDLED%" set "PY_EXE=%BUNDLED%"
if exist "%BUNDLED%" goto :have_python

py -3.10 -c "import sys; raise SystemExit(0 if sys.version_info[:2]==(3,10) and sys.maxsize>2**32 else 1)" >nul 2>&1
if not errorlevel 1 set "PY_KIND=py"
if not errorlevel 1 goto :have_python

python -c "import sys; raise SystemExit(0 if sys.version_info[:2]==(3,10) and sys.maxsize>2**32 else 1)" >nul 2>&1
if not errorlevel 1 set "PY_KIND=exe"
if not errorlevel 1 set "PY_EXE=python"
if not errorlevel 1 goto :have_python

color 0C
echo.
echo 未找到可用的 Python。
echo.
echo 片语 Studio 需要 Python 3.10.11（64 位）。
echo.
echo 请打开这个页面，下载 Windows installer 64-bit：
echo   https://www.python.org/downloads/release/python-31011/
echo.
echo 安装时勾选 "Add python.exe to PATH"。
echo 装完之后，重新双击这个图标即可。
echo.
pause
exit /b 1

:have_python
if exist "%VENV_PY%" goto :venv_ready
echo [Step 1] Creating runtime\venv ...
if "%PY_KIND%"=="py" goto :venv_via_py
"%PY_EXE%" -m venv "%VENV%"
goto :venv_created
:venv_via_py
py -3.10 -m venv "%VENV%"
:venv_created
if errorlevel 1 (
    color 0C
    echo [ERROR] venv creation failed.
    pause
    exit /b 1
)

:venv_ready
if not exist "%BUNDLED%" goto :after_rewrite
"%BUNDLED%" -c "import sys; sys.path.insert(0, r'%ROOT%'); from cutora.core.portable import rewrite_pyvenv_cfg; rewrite_pyvenv_cfg(r'%ROOT%')"
if errorlevel 1 (
    color 0C
    echo [ERROR] Failed to rewrite runtime\venv\pyvenv.cfg
    pause
    exit /b 1
)

:after_rewrite
if /I "%MODE%"=="repair" goto :do_install
"%VENV_PY%" -c "import sys; sys.path.insert(0, r'%ROOT%'); from cutora.core.bootstrap import is_env_ready; raise SystemExit(0 if is_env_ready(r'%ROOT%') else 1)"
if errorlevel 1 goto :do_install
goto :launch

:do_install
echo.
echo 依赖不完整，请运行 CUTORA_Install.bat
echo.
pause
exit /b 2

:launch
set "PATH=%FFMPEG_DIR%;%PATH%"
if not exist "%VENV_PYW%" (
    color 0C
    echo [ERROR] pythonw.exe missing: %VENV_PYW%
    pause
    exit /b 1
)

start "" /D "%ROOT%" "%VENV_PYW%" -m cutora
if errorlevel 1 (
    color 0C
    echo [ERROR] failed to start the application.
    pause
    exit /b 1
)

"%VENV_PY%" -c "import sys; sys.path.insert(0, r'%ROOT%'); from cutora.core.bootstrap import maybe_create_desktop_shortcut; maybe_create_desktop_shortcut(r'%ROOT%')"
if errorlevel 1 echo [WARN] desktop shortcut was not created. Run CUTORA_CreateShortcut.bat

exit /b 0

@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
title 片语 Studio / CUTORA Install

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
set "BUNDLED=%ROOT%\runtime\python\python.exe"
set "VENV=%ROOT%\runtime\venv"
set "VENV_PY=%VENV%\Scripts\python.exe"
set "LOCK=%ROOT%\requirements.lock"

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
echo.
echo [Install] 依赖未就绪，开始安装 ...
call "%VENV%\Scripts\activate.bat"
if errorlevel 1 (
    color 0C
    echo [ERROR] failed to activate runtime\venv
    pause
    exit /b 1
)

echo [Step 2] Upgrading pip / setuptools / wheel ...
python -m pip install --upgrade pip setuptools wheel -i https://pypi.org/simple
if errorlevel 1 (
    color 0C
    echo [ERROR] pip upgrade failed.
    pause
    exit /b 1
)

if not exist "%LOCK%" (
    color 0C
    echo [ERROR] requirements.lock missing.
    pause
    exit /b 1
)

echo [Step 3] Installing locked dependencies with hashes ...
python -m pip install --require-hashes -r "%LOCK%" -i https://pypi.org/simple --extra-index-url https://download.pytorch.org/whl/cu128
if errorlevel 1 (
    color 0C
    echo [ERROR] hashed install failed.
    pause
    exit /b 1
)

echo [Step 4] Installing local whisperX (no deps) ...
set "SETUPTOOLS_SCM_PRETEND_VERSION=3.1.1"
python -m pip install "%ROOT%\vendor\whisperX" --no-deps --no-build-isolation
if errorlevel 1 (
    color 0C
    echo [ERROR] whisperX install failed.
    pause
    exit /b 1
)

echo [Step 5] Verifying imports ...
python -c "import torch, whisperx, faster_whisper, PySide6, cv2; print(torch.cuda.is_available(), torch.cuda.get_device_capability())"
if errorlevel 1 (
    color 0C
    echo [ERROR] import verification failed.
    pause
    exit /b 1
)

echo [Step 6] Writing ready marker ...
python -c "import sys; sys.path.insert(0, r'%ROOT%'); from cutora.core.bootstrap import write_ready_marker; write_ready_marker(r'%ROOT%')"
if errorlevel 1 (
    color 0C
    echo [ERROR] failed to write runtime\venv\.cutora_ready
    pause
    exit /b 1
)

echo.
echo ========================================================
echo           INSTALLATION SUCCESSFUL
echo ========================================================
echo 接下来双击 CUTORA.bat 打开片语 Studio。
echo.
pause
exit /b 0

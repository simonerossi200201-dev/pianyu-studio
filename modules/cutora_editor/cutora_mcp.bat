@echo off
setlocal
cd /d "%~dp0"
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
set "BUNDLED_PY=%ROOT%\runtime\python\python.exe"
if exist "%BUNDLED_PY%" (
  "%BUNDLED_PY%" -c "import sys; sys.path.insert(0, r'%ROOT%'); from cutora.core.portable import rewrite_pyvenv_cfg; rewrite_pyvenv_cfg(r'%ROOT%')"
  if errorlevel 1 (
    echo [ERROR] Failed to rewrite runtime\venv\pyvenv.cfg 1>&2
    exit /b 1
  )
)
set "VENV_PY=%ROOT%\runtime\venv\Scripts\python.exe"
if not exist "%VENV_PY%" (
  echo [ERROR] Venv missing. Please run CUTORA_Install.bat first. 1>&2
  exit /b 1
)
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONPATH=%ROOT%"
"%VENV_PY%" -m cutora.api.mcp_server
exit /b %errorlevel%

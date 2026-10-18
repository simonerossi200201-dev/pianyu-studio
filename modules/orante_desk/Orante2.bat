@echo off
rem =====================================================================
rem  Orante 2.0 launcher (Windows).  Keep this file PURE ASCII.
rem  1) uses .venv next to this file (creates it on first run)
rem  2) installs requirements.txt once
rem  3) pythonw -m orante  (python.exe if --browser)
rem  --install-only : install deps then exit (used by the first-run install path)
rem  Blank window / won't start? run tools\13-Orante-Debug.bat
rem  First-run shortcut: after deps, call tools\Install-Shortcuts.bat --quiet
rem  Kill-switch: remove that call in :maybe_shortcut to go back to two-step.
rem =====================================================================
title Orante
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "PY=.venv\Scripts\python.exe"
set "PYW=.venv\Scripts\pythonw.exe"
set "INSTALL_ONLY=0"
set "BROWSER=0"
set "JUST_VENV=0"
for %%A in (%*) do (
  if /i "%%~A"=="--install-only" set "INSTALL_ONLY=1"
  if /i "%%~A"=="--browser" set "BROWSER=1"
)
if exist "%PY%" goto :deps
echo [1/3] Creating virtual environment...
where py >nul 2>nul && (py -3 -m venv .venv) || (python -m venv .venv)
if not exist "%PY%" goto :err_py
set "JUST_VENV=1"
:deps
echo [2/3] Checking dependencies...
"%PY%" -c "import fastapi, uvicorn, playwright, PIL, webview, imageio_ffmpeg" 2>nul
if not errorlevel 1 goto :after_deps
echo       installing (first run only, may take a minute)...
"%PY%" -m pip install -q -r requirements.txt
if errorlevel 1 goto :err_dep
:after_deps
call :maybe_shortcut
if "%INSTALL_ONLY%"=="1" (
  echo Installed.
  goto :eof
)
:run
echo [3/3] Starting Orante...
if "%BROWSER%"=="1" (
  "%PY%" -m orante %*
  if errorlevel 1 pause
  goto :eof
)
if exist "%PYW%" (
  start "" "%PYW%" -m orante %*
) else (
  "%PY%" -m orante %*
  if errorlevel 1 pause
)
goto :eof

rem Kill-switch: delete the next call line to restore two-step install.
:maybe_shortcut
if "%JUST_VENV%"=="1" goto :do_shortcut
if exist "%USERPROFILE%\Desktop\Orante.lnk" goto :eof
if exist "%USERPROFILE%\OneDrive\Desktop\Orante.lnk" goto :eof
:do_shortcut
if not exist "tools\Install-Shortcuts.bat" goto :eof
pushd "%CD%"
call tools\Install-Shortcuts.bat --quiet
set "SC_EC=%ERRORLEVEL%"
popd
goto :eof
:err_py
echo Python 3.10+ not found. Install it from python.org (tick "Add to PATH") and run again.
pause
exit /b 1
:err_dep
echo Could not install dependencies. Check your network and run again.
pause
exit /b 1

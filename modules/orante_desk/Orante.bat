@echo off
rem =====================================================================
rem  Orante control desk - launcher
rem
rem  IMPORTANT - this file must stay PURE ASCII, including paths.
rem  cmd.exe reads a .bat by byte offset. Non-ASCII text plus chcp 65001
rem  shifts every offset after that point and cmd starts executing garbage.
rem  That is why Orante keeps its OWN .venv here instead of pointing at
rem  the pipeline venv (whose path contains Chinese characters).
rem  All Chinese text lives in app\server.py, which prints UTF-8.
rem =====================================================================
chcp 65001 >nul
title Orante
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "PY=.venv\Scripts\python.exe"

if exist "%PY%" goto :deps
echo [1/3] Creating virtual environment...
python -m venv .venv
if errorlevel 1 goto :err_py

:deps
echo [2/3] Checking dependencies...
"%PY%" -c "import fastapi, uvicorn" 2>nul
if not errorlevel 1 goto :run
echo       installing fastapi + uvicorn (first run only)...
"%PY%" -m pip install fastapi uvicorn -q
if errorlevel 1 goto :err_dep

:run
echo [3/3] Starting server on http://127.0.0.1:9400
echo       Close this window to stop it.
echo.
"%PY%" app\server.py
echo.
echo Server stopped.
pause
goto :eof

:err_py
echo Python 3 not found. Install Python 3 and try again.
pause
exit /b 1

:err_dep
echo Could not install fastapi/uvicorn. Check your network and try again.
pause
exit /b 1

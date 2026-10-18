@echo off
rem CUTORA: run every test file in its own process (a native crash only
rem takes down that one file). Summary goes to docs\plan\queue\_log\_pytest_each.log
rem Pure ASCII on purpose.
title CUTORA tests one by one
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PY=%~dp0runtime\venv\Scripts\python.exe"
if not exist "%PY%" (
  echo  venv python not found: %PY%
  pause
  exit /b 1
)
"%PY%" tools\pytest_each.py
echo.
echo ================================================================
echo  Done. Tell Claude "tests done" - the log is
echo  docs\plan\queue\_log\_pytest_each.log
echo ================================================================
pause

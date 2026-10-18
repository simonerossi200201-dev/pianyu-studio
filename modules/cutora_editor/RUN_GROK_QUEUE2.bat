@echo off
rem CUTORA dev helper v2 - launcher only.  All logic lives in tools\queue_runner.py
rem so it can be edited safely while a run is in flight (cmd reads .bat files by
rem byte offset - editing THIS file mid-run corrupts the run).
rem Keep this file pure ASCII and do not grow it.
title CUTORA Grok worker v2
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PY=%~dp0runtime\venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" -u "tools\queue_runner.py" %*
echo.
pause

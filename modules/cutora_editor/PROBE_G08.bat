@echo off
title CUTORA stream diff
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PY=%~dp0runtime\venv\Scripts\python.exe"
if not exist "docs\plan\queue\_log" mkdir "docs\plan\queue\_log"
set "OUT=docs\plan\queue\_log\_stream_diff.log"
"%PY%" tools\_diag\stream_diff.py g08_fx_static > "%OUT%" 2>&1
type "%OUT%"
echo.
echo  log: %OUT%
pause

@echo off
rem Prove which decoder is correct before re-recording any golden baseline.
title CUTORA decode proof
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PY=%~dp0runtime\venv\Scripts\python.exe"
if not exist "docs\plan\queue\_log" mkdir "docs\plan\queue\_log"
set "OUT=docs\plan\queue\_log\_decode_proof.log"
"%PY%" tools\decode_proof.py > "%OUT%" 2>&1
type "%OUT%"
echo.
echo  log: %OUT%
pause

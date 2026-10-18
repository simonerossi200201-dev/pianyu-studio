@echo off
rem Priority queue self-test (spec 19). Uses a temp DB, does not touch data\orante.db.
title Orante priority queue test
cd /d "%~dp0.."
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo Orante .venv not found. Run Orante2.bat once first. & pause & exit /b 1
)
"%PY%" tools\prio_test.py > "data\logs\prio-test.log" 2>&1
type "data\logs\prio-test.log"
pause

@echo off
rem Multimodal API pool self-test (spec 20). Temp DB, no network, does not touch data\orante.db.
title Orante AI pool test
cd /d "%~dp0.."
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo Orante .venv not found. Run Orante2.bat once first. & pause & exit /b 1
)
"%PY%" tools\aipool_test.py > "data\logs\aipool-test.log" 2>&1
type "data\logs\aipool-test.log"
pause

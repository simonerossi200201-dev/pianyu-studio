@echo off
rem Collect-integrity gates (spec 21). Tests 1+2+3 against T0013 on disk.
title Orante verify / gapfill test
cd /d "%~dp0.."
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo Orante .venv not found. Run Orante2.bat once first. & pause & exit /b 1
)
if not exist "data\logs" mkdir "data\logs"
"%PY%" tools\verify_test.py > "data\logs\verify-test.log" 2>&1
type "data\logs\verify-test.log"
pause

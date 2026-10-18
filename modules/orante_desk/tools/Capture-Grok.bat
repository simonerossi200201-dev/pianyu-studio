@echo off
rem Grok imagine capture. Usage: Capture-Grok.bat [probe|auto|watch] [more args]
title Grok capture
cd /d "%~dp0.."
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo Orante .venv not found. & pause & exit /b 1
)
if "%~1"=="" (
  "%PY%" tools\grok_capture.py probe > "data\logs\grok-capture.log" 2>&1
) else (
  "%PY%" tools\grok_capture.py %* > "data\logs\grok-capture.log" 2>&1
)
type "data\logs\grok-capture.log"
pause

@echo off
rem 规格 57 C 节：解析最新的 flow_capture_*.jsonl
title Parse Flow capture
cd /d "%~dp0.."
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo Orante .venv not found. & pause & exit /b 1
)
"%PY%" tools\flow_capture_parse.py %*
echo.
pause

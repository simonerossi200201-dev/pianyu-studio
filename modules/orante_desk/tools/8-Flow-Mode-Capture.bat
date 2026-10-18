@echo off
rem Capture eb1hJf payloads for the two composer modes (Frames vs Ingredients).
rem Usage: 8-Flow-Mode-Capture.bat [account name]   default TRE-1
title Flow mode capture
cd /d "%~dp0.."
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" ( echo Orante .venv not found. Run Orante2.bat once first. & pause & exit /b 1 )
set "ACC=%~1"
if "%ACC%"=="" set "ACC=TRE-1"
"%PY%" tools\flow_mode_capture.py "%ACC%"
pause

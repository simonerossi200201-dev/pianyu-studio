@echo off
rem Grok imagine: drop a reference image, type the prompt, submit, record the API calls.
title Grok capture - auto
cd /d "%~dp0.."
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
"%~dp0..\.venv\Scripts\python.exe" tools\grok_capture.py auto --dry --secs 30 > "data\logs\grok-capture.log" 2>&1
type "data\logs\grok-capture.log"
pause

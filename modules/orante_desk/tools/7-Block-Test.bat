@echo off
rem Blocked-image detection regression test (0908). No network, no browser.
title Orante block-detect test
cd /d "%~dp0.."
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" ( echo Orante .venv not found. & pause & exit /b 1 )
"%PY%" tools\blockre_test.py > "data\logs\block-test.log" 2>&1
type "data\logs\block-test.log"
pause

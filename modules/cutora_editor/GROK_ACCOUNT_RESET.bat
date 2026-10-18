@echo off
rem Wipe every trace of the old Grok account, then sign in once.
rem Logic lives in tools\grok_account_reset.py so it can be edited safely.
rem Keep this file pure ASCII.
title Grok account reset
cd /d "%~dp0"
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PY=%~dp0runtime\venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" -u "tools\grok_account_reset.py"
echo.
pause

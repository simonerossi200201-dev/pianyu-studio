@echo off
title Test Flow poll
cd /d "%~dp0.."
set "PY="
for /d %%D in ("%~dp0..\..\*") do (
  if exist "%%D\.venv\Scripts\python.exe" if exist "%%D\core\grok_pipe.py" set "PY=%%D\.venv\Scripts\python.exe"
)
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
"%PY%" tools\test_flow_api.py TIK-L1 poll 112a4088-ea10-4cec-9ad1-d02d80bfa3a5 > data\logs\test-flow-poll.log 2>&1
type data\logs\test-flow-poll.log
pause

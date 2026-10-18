@echo off
title Test Flow API
cd /d "%~dp0.."
set "PY="
for /d %%D in ("%~dp0..\..\*") do (
  if exist "%%D\.venv\Scripts\python.exe" if exist "%%D\core\grok_pipe.py" set "PY=%%D\.venv\Scripts\python.exe"
)
if "%PY%"=="" ( echo pipeline .venv not found & pause & exit /b 1 )
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
"%PY%" -c "import sys; sys.path.insert(0,'.'); from orante import db, accounts; db.init(); print(accounts.import_legacy_lanes()); print([a['name'] for a in accounts.list_all()])"
"%PY%" tools\test_flow_api.py TIK-L1 "auto" "@data\tmp_prompt.txt" 4 > data\logs\test-flow.log 2>&1
type data\logs\test-flow.log
pause

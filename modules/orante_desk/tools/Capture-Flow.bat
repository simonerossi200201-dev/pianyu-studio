@echo off
title Flow capture
cd /d "%~dp0"
set "PY="
for /d %%D in ("%~dp0..\..\*") do (
  if exist "%%D\.venv\Scripts\python.exe" if exist "%%D\core\grok_pipe.py" set "PY=%%D\.venv\Scripts\python.exe"
)
if "%PY%"=="" (
  echo Could not find the pipeline .venv next to Orante. & pause & exit /b 1
)
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "LANE=%~1"
if "%LANE%"=="" set "LANE=TIK-L1"
"%PY%" flow_capture.py %LANE%
pause

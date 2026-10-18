@echo off
rem Hand the queue over to the v2 runner WITHOUT disturbing the run in flight.
rem Safe to click while RUN_GROK_QUEUE.bat is still working: this just waits for
rem that window's lock file to disappear, then picks the queue up where it left
rem off - with skip-and-continue instead of halt-the-night.
rem Keep this file pure ASCII.
title CUTORA queue handover to v2
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PY=%~dp0runtime\venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
set "LOCK=docs\plan\queue\_done\_RUNNING.lock"

echo.
echo  Waiting for the current queue run to finish or halt.
echo  Leave this window open. Do not close the other one - let it end by itself.
echo  lock: %LOCK%
echo.
:wait
if not exist "%LOCK%" goto go
timeout /t 30 /nobreak >nul
goto wait

:go
echo.
echo  The old run has released the lock. Taking over with the v2 runner.
echo.
"%PY%" -u "tools\queue_runner.py"
echo.
pause

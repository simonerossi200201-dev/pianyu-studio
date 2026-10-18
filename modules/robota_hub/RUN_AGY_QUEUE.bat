@echo off
rem Robota: run the CUTORA task queue with Antigravity CLI (agy) instead of Grok.
rem Launcher only - all logic lives in robota\agy_queue.py. Keep this file pure ASCII.
rem Usage:  RUN_AGY_QUEUE.bat [--model claude-opus-5-5-high] [--timeout-min 180] [--dry-run] [--root D:\0_Dev\CUTORA]
rem Stop :  create an empty file D:\0_Dev\CUTORA\docs\plan\queue\_done\_HALT
title Robota agy worker
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PATH=%LOCALAPPDATA%\agy\bin;%PATH%"
"%~dp0runtime\venv\Scripts\python.exe" -u "%~dp0robota\agy_queue.py" %*
echo.
pause

@echo off
title CUTORA audio bisect
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
echo.
echo  Rendering g08 at five commits, 30-60s each. Expect 3-5 minutes.
echo  Lines appear as each one finishes.
echo.
"%~dp0runtime\venv\Scripts\python.exe" -u tools\_diag\bisect_audio.py
echo.
echo  log: docs\plan\queue\_log\_bisect_audio.log
pause

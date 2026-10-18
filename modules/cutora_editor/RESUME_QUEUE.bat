@echo off
rem Verify the Q30 golden-audio ruling, then resume the queue from Q19.
title CUTORA verify + resume
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PY=%~dp0runtime\venv\Scripts\python.exe"
if not exist "docs\plan\queue\_log" mkdir "docs\plan\queue\_log"
set "OUT=docs\plan\queue\_log\_q30_verify.log"

echo.
echo  [1/3] Q30 + migration tests
echo ----------------------------------------------------------------
"%PY%" -m pytest tests/test_t30_spatial.py -q > "%OUT%" 2>&1
set "T=%errorlevel%"
echo  tests exit %T%

echo.
echo  [2/3] golden - the audio track must be BYTE-IDENTICAL, not re-recorded
echo ----------------------------------------------------------------
echo --- golden --- >> "%OUT%"
"%PY%" tools\golden_check.py --all --repeat 1 >> "%OUT%" 2>&1
set "G=%errorlevel%"
echo  golden exit %G%

echo.
echo  [3/3] full pytest
echo ----------------------------------------------------------------
echo --- pytest --- >> "%OUT%"
"%PY%" -m pytest tests -q >> "%OUT%" 2>&1
set "P=%errorlevel%"
echo  pytest exit %P%

echo.
echo ================================================================
echo  t30=%T%   golden=%G%   pytest=%P%      (0 = pass)
echo  log: %OUT%
echo.
if not "%G%"=="0" goto bad
if not "%T%"=="0" goto bad
if not "%P%"=="0" goto bad
echo  All green - the ruling holds, golden audio unchanged.
echo  Clearing the Q30 stop marker and resuming the queue from Q19 ...
del "docs\plan\queue\_done\Q30.stop" >nul 2>&1
echo ================================================================
echo.
pause
call "%~dp0RUN_GROK_QUEUE.bat"
exit /b 0
:bad
echo  NOT green - stop marker left in place. Send %OUT% to Claude.
echo ================================================================
pause

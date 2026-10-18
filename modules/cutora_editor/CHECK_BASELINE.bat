@echo off
rem CUTORA: run the baseline check before starting the Grok queue.
rem Pure ASCII on purpose.
title CUTORA baseline check
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PY=%~dp0runtime\venv\Scripts\python.exe"
if not exist "%PY%" (
  echo  venv python not found: %PY%
  pause
  exit /b 1
)
if not exist "docs\plan\queue\_log" mkdir "docs\plan\queue\_log"
set "OUT=docs\plan\queue\_log\_baseline.log"
echo === baseline %date% %time% > "%OUT%"
"%PY%" -c "import subprocess;print(subprocess.run(['git','rev-parse','--short','HEAD'],capture_output=True,text=True).stdout.strip())" >> "%OUT%" 2>&1

echo.
echo  [1/3] golden twelve cases  (this is the one that matters most)
echo ----------------------------------------------------------------
echo --- golden --- >> "%OUT%"
"%PY%" tools\golden_check.py --all --repeat 1 >> "%OUT%" 2>&1
set "G=%errorlevel%"
echo  golden exit code %G%
echo  golden exit %G% >> "%OUT%"

echo.
echo  [2/3] UI baseline
echo ----------------------------------------------------------------
echo --- ui golden --- >> "%OUT%"
"%PY%" tools\ui_golden.py --all >> "%OUT%" 2>&1
set "U=%errorlevel%"
echo  ui exit code %U%
echo  ui exit %U% >> "%OUT%"

echo.
echo  [3/3] full pytest  (this takes a while)
echo ----------------------------------------------------------------
echo --- pytest --- >> "%OUT%"
"%PY%" -m pytest tests -q >> "%OUT%" 2>&1
set "P=%errorlevel%"
echo  pytest exit code %P%
echo  pytest exit %P% >> "%OUT%"

echo.
echo ================================================================
echo  golden=%G%   ui=%U%   pytest=%P%      (0 = pass)
echo  full log: %OUT%
echo.
if not "%G%"=="0" echo  GOLDEN FAILED - do NOT start the queue, send the log to Claude first.
if "%G%"=="0" if not "%P%"=="0" echo  golden is fine but pytest has failures - send the log to Claude.
if "%G%"=="0" if "%P%"=="0" echo  All green. Safe to run RUN_GROK_QUEUE.bat
echo ================================================================
pause

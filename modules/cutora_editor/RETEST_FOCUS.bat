@echo off
rem Re-run just the test files touched by the last fixes (one process per file).
rem Log: docs\plan\queue\_log\_retest.log   (the full-run log is left alone)
rem Pure ASCII on purpose.
title CUTORA retest focus
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PY=%~dp0runtime\venv\Scripts\python.exe"
if not exist "%PY%" (
  echo  venv python not found: %PY%
  pause
  exit /b 1
)
"%PY%" tools\pytest_each.py --name retest_focus --fresh ^
  tests\test_q145b_timeline_shortcuts.py ^
  tests\test_t34_split.py ^
  tests\test_q86_split_feather.py
echo.
echo ================================================================
echo  Done. Tell Claude "focus done" - log: docs\plan\queue\_log\_retest_focus.log
echo ================================================================
pause

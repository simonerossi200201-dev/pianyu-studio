@echo off
rem Diagnostic only: re-run golden with hardware decoding to prove whether the
rem 2026-09-19 hash drift comes from the software-decode switch. Pure ASCII.
title CUTORA golden A/B - hardware decode
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PY=%~dp0runtime\venv\Scripts\python.exe"
set "OUT=docs\plan\queue\_log\_ab_hwdecode.log"
if not exist "docs\plan\queue\_log" mkdir "docs\plan\queue\_log"
echo === A/B hardware decode %date% %time% > "%OUT%"
echo.
echo  Running golden with CUTORA_EXPORT_HW_DECODE=1 (diagnostic only).
echo  This does NOT change the product default.
echo.
set "CUTORA_EXPORT_HW_DECODE=1"
"%PY%" tools\golden_check.py --all --repeat 1 >> "%OUT%" 2>&1
set "G=%errorlevel%"
set "CUTORA_EXPORT_HW_DECODE="
echo.
echo ================================================================
echo  golden with HARDWARE decode: exit %G%   (0 = pass)
echo.
if "%G%"=="0" echo  -^> Confirmed: the drift is the decode switch, nothing else.
if not "%G%"=="0" echo  -^> Something ELSE also drifted. Send the log to Claude.
echo  log: %OUT%
echo ================================================================
pause

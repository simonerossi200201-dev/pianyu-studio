@echo off
rem ============================================================
rem  Stitch checkup - READ ONLY. Nothing is modified.
rem  Drag the "video output" folder onto this file, or just
rem  double-click and paste the path when asked.
rem
rem  NOTE: kept 100%% ASCII on purpose. cmd.exe reads .bat with
rem  the OEM codepage, so a UTF-8 .bat with Chinese in it just
rem  flashes and dies.
rem ============================================================
setlocal
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"

set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"

if "%~1"=="" (
  "%PY%" stitch_checkup.py
) else (
  "%PY%" stitch_checkup.py "%~1"
)

echo.
pause

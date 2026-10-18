@echo off
rem ============================================================
rem  Material preflight - run this BEFORE stitching.
rem
rem  Drag the material folder onto this file, or double-click
rem  and paste the path when asked.
rem
rem  Exit codes:  0 = all clear
rem               1 = FATAL, do not stitch
rem               3 = needs your confirmation
rem
rem  Add  --quarantine  to also move bad files into _invalid\
rem
rem  NOTE: kept 100%% ASCII on purpose. cmd.exe reads .bat with
rem  the OEM codepage, so a UTF-8 .bat with Chinese in it just
rem  flashes and dies.
rem ============================================================
setlocal
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
chcp 65001 >nul

set "PY=python"
if exist "runtime\venv\Scripts\python.exe" set "PY=runtime\venv\Scripts\python.exe"

if "%~1"=="" (
  "%PY%" -m cutora.batch.media_guard
) else (
  "%PY%" -m cutora.batch.media_guard "%~1" %2 %3
)

echo.
echo Exit code: %ERRORLEVEL%
pause

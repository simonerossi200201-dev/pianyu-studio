@echo off
rem Build a clean share zip in D:\0_Dev\_share. NEVER touches this folder's data\.
rem PURE ASCII. Run from anywhere; paths from %~dp0.
setlocal EnableExtensions
cd /d "%~dp0.."
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo .venv not found. Double-click Orante2.bat once, then run this again.
  pause
  exit /b 1
)
echo.
echo Building share package. Source folder is read-only.
echo.
"%PY%" tools\make_share.py %*
set "EC=%ERRORLEVEL%"
if not "%EC%"=="0" (
  echo.
  echo Share package FAILED.
  pause
  exit /b %EC%
)
echo.
exit /b 0

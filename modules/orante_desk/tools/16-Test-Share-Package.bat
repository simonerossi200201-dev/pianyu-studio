@echo off
rem Smoke-test an already-built share folder. PURE ASCII.
setlocal EnableExtensions
cd /d "%~dp0.."
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo .venv not found. Double-click Orante2.bat once, then run this again.
  pause
  exit /b 1
)
if "%~1"=="" (
  echo Usage: 16-Test-Share-Package.bat ^<share-folder^>
  pause
  exit /b 1
)
"%PY%" tools\smoke_share.py %*
set "EC=%ERRORLEVEL%"
if not "%EC%"=="0" (
  echo Smoke FAILED.
  pause
  exit /b %EC%
)
exit /b 0

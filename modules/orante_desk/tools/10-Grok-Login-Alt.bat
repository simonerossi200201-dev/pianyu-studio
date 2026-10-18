@echo off
rem One-time setup: sign the ALTERNATE Grok account into its own home dir,
rem so grok-run.bat can use it while your normal grok session keeps the default account.
title Grok alt-account login
cd /d "%~dp0.."
set "ALT="
if not exist "tools\grok-account.txt" goto nofile
for /f "usebackq tokens=* delims=" %%L in (`findstr /v /b /i "rem " "tools\grok-account.txt"`) do if not defined ALT set "ALT=%%L"
if not defined ALT goto nofile
call set "ALT=%ALT%"
if not exist "%ALT%" mkdir "%ALT%"
set "GROK_HOME=%ALT%"
echo.
echo   GROK_HOME = %GROK_HOME%
echo.
echo   1) A login flow opens. Sign in with the OTHER account (Lucy).
echo   2) Then an interactive grok starts here in D:\0_Dev\Orante.
echo      Say YES to "trust this folder", then type  /exit
echo   3) Done - grok-run.bat will pick this account up automatically.
echo.
pause
grok login
echo.
echo   --- interactive session, just to trust this folder ---
grok
echo.
echo   Setup finished.
timeout /t 15
goto :eof
:nofile
echo tools\grok-account.txt has no home-dir line. Nothing to do.
timeout /t 10

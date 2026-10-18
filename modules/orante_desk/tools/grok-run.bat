@echo off
rem Orante dev helper: run every spec listed in tools\grok-queue.txt through Grok (headless), one after another.
rem Keep this file pure ASCII. Each queue line = a path relative to the Orante root, e.g. specs\01-skeleton.md
title Grok worker
cd /d "%~dp0.."
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
rem --- optional: run under a separate grok account, which lives in its own GROK_HOME ---
rem tools\grok-account.txt : first line that does not start with "rem" = that home dir.
rem File missing / only rem lines = use the default account (C:\Users\<you>\.grok).
set "ALT="
if not exist "tools\grok-account.txt" goto altdone
for /f "usebackq tokens=* delims=" %%L in (`findstr /v /b /i "rem " "tools\grok-account.txt"`) do if not defined ALT set "ALT=%%L"
if not defined ALT goto altdone
call set "ALT=%ALT%"
if not exist "%ALT%" goto altmissing
set "GROK_HOME=%ALT%"
echo  grok account home: %ALT%
goto altdone
:altmissing
echo  WARNING: alt grok home "%ALT%" not found. Run tools\10-Grok-Login-Alt.bat first.
echo  Falling back to the default account.
:altdone
if not exist "data\logs" mkdir "data\logs"
rem --- one pass per queue line ---
rem A spec already listed in grok-done.txt is SKIPPED, so re-queueing something
rem by mistake no longer burns a full Grok run (62, 65, 76 and 77 all got run
rem twice before this guard existed).  To force a re-run on purpose - e.g. the
rem spec text was amended - put an exclamation mark in front of that queue line:
rem     !specs\76-template-suites.md
:loop
set "SPEC="
for /f "usebackq delims=" %%L in ("tools\grok-queue.txt") do if not defined SPEC set "SPEC=%%L"
if "%SPEC%"=="" goto done
set "FORCE=0"
if "%SPEC:~0,1%"=="!" set "FORCE=1"
if "%FORCE%"=="1" set "SPEC=%SPEC:~1%"
if "%SPEC%"=="" goto pop
for %%F in ("%SPEC%") do set "NAME=%%~nF"
if "%FORCE%"=="1" goto run
if not exist "tools\grok-done.txt" goto run
findstr /i /c:"%SPEC%" "tools\grok-done.txt" >nul 2>nul
if errorlevel 1 goto run
echo.
echo  SKIP - already in grok-done.txt: %SPEC%
echo  To force a re-run, prefix that queue line with an exclamation mark.
goto pop

:run
echo.
echo ================================================================
echo  [%date% %time%] Grok is working on: %SPEC%
echo  log: data\logs\grok-%NAME%.log
echo ================================================================
grok --prompt-file "%SPEC%" --permission-mode bypassPermissions --output-format plain --no-alt-screen %GROK_EXTRA% > "data\logs\grok-%NAME%.log" 2>&1
set "RC=%errorlevel%"
echo  exit code %RC%  >> "data\logs\grok-%NAME%.log"
rem A spec only counts as done when Grok exited 0 and did not hit its usage limit.
rem Otherwise it stays at the top of the queue and the worker stops (0921: 83-86 were
rem all marked done after hitting the free-tier limit without changing a single file).
findstr /i /c:"Grok Build usage limit" "data\logs\grok-%NAME%.log" >nul 2>nul
if not errorlevel 1 goto limit
if not "%RC%"=="0" goto failed
echo  done: %SPEC%  (exit %RC%)
echo %SPEC%>> "tools\grok-done.txt"
goto pop

:limit
echo.
echo  STOPPED - Grok usage limit reached while working on %SPEC%
echo  It stays in the queue and is NOT marked done. Run grok-run.bat again after the limit resets.
goto stopped
:failed
echo.
echo  STOPPED - Grok exited with code %RC% on %SPEC%
echo  It stays in the queue and is NOT marked done. Check data\logs\grok-%NAME%.log first.
:stopped
timeout /t 60
exit /b 1

:pop
more +1 "tools\grok-queue.txt" > "tools\grok-queue.tmp"
move /y "tools\grok-queue.tmp" "tools\grok-queue.txt" >nul
goto loop
:done
echo.
echo Queue empty. All done.
timeout /t 15

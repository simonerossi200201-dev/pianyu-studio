@echo off
rem CUTORA dev helper: run every task listed in tools\grok-queue.txt through Grok, one after another.
rem Keep this file pure ASCII.
title CUTORA Grok worker
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

set "QD=docs\plan\queue\_done"
set "QL=docs\plan\queue\_log"
set "LOCK=%QD%\_RUNNING.lock"

if not exist "%QL%" mkdir "%QL%"
if not exist "%QD%" mkdir "%QD%"
if not exist "tools\grok-queue.txt" (
  echo  tools\grok-queue.txt not found.
  pause
  exit /b 1
)

rem --- single-writer lock: never let two Grok agents edit the repo at once ---
if exist "%LOCK%" (
  echo.
  echo ****************************************************************
  echo  A queue run is already active ^(or crashed^).
  echo  Lock file: %LOCK%
  echo.
  echo  Do NOT start a second run and do NOT paste tasks into an
  echo  interactive Grok while this is running - concurrent edits
  echo  corrupt the tree and make every test number meaningless.
  echo.
  echo  If you are sure nothing is running, delete that file and retry.
  echo ****************************************************************
  pause
  exit /b 2
)
echo %date% %time% pid-less lock > "%LOCK%"

echo.
echo  CUTORA task queue
echo  progress : docs\plan\queue\_progress.txt
echo  logs     : docs\plan\queue\_log\
echo  to stop  : create an empty file docs\plan\queue\_done\_HALT
echo.

:loop
if exist "%QD%\_HALT" goto halted
set "SPEC="
for /f "usebackq delims=" %%L in ("tools\grok-queue.txt") do if not defined SPEC set "SPEC=%%L"
if "%SPEC%"=="" goto done
for %%F in ("%SPEC%") do set "NAME=%%~nF"
echo.
echo ================================================================
echo  [%date% %time%] Grok is working on: %SPEC%
echo  log: docs\plan\queue\_log\%NAME%.log
echo ================================================================
echo [%date% %time%] START %NAME% >> "docs\plan\queue\_progress.txt"
rem forensics: what did the tree look like before this task
echo === %NAME% start %date% %time% > "%QL%\%NAME%.log"
git rev-parse --short HEAD >> "%QL%\%NAME%.log" 2>&1
echo --- git status before --- >> "%QL%\%NAME%.log"
git status --short >> "%QL%\%NAME%.log" 2>&1
echo --- grok output --- >> "%QL%\%NAME%.log"
grok --prompt-file "%SPEC%" --permission-mode bypassPermissions --output-format plain --no-alt-screen >> "%QL%\%NAME%.log" 2>&1
echo  exit code %errorlevel% >> "%QL%\%NAME%.log"
echo --- git status after --- >> "%QL%\%NAME%.log"
git status --short >> "%QL%\%NAME%.log" 2>&1
echo  done: %SPEC%  (exit %errorlevel%)
echo [%date% %time%] END   %NAME% (exit %errorlevel%) >> "docs\plan\queue\_progress.txt"
more +1 "tools\grok-queue.txt" > "tools\grok-queue.tmp"
move /y "tools\grok-queue.tmp" "tools\grok-queue.txt" >nul
echo %SPEC% >> "tools\grok-done.txt"
if exist "%QD%\%NAME%.stop" goto stopped
goto loop

:stopped
echo.
echo ****************************************************************
echo  %NAME% wrote a .stop marker - a red line was hit (baseline changed?).
echo  Queue halted on purpose. Read: docs\plan\queue\_done\%NAME%.stop
echo ****************************************************************
echo [%date% %time%] HALTED by %NAME%.stop >> "docs\plan\queue\_progress.txt"
goto tail

:halted
echo.
echo  _HALT file found. Queue stopped.
echo [%date% %time%] HALTED by _HALT >> "docs\plan\queue\_progress.txt"
goto tail

:done
echo.
echo  Queue empty. All done.
echo [%date% %time%] QUEUE EMPTY - all done >> "docs\plan\queue\_progress.txt"

:tail
del "%LOCK%" >nul 2>&1
echo.
pause

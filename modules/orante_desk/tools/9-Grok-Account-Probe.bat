@echo off
rem Probe: does the grok CLI honor a per-invocation home dir env var?
rem Read-only. Does NOT log in, log out, or touch your current session.
title Grok account probe
cd /d "%~dp0.."
if not exist "data\logs" mkdir "data\logs"
set "LOG=data\logs\grok-account-probe.log"
echo ================================================== > "%LOG%"
echo  probe at %date% %time%                           >> "%LOG%"
echo ================================================== >> "%LOG%"
echo. >> "%LOG%"

echo [1] where is grok >> "%LOG%"
where grok >> "%LOG%" 2>&1
grok --version >> "%LOG%" 2>&1
echo. >> "%LOG%"

echo [2] default home usage >> "%LOG%"
grok du >> "%LOG%" 2>&1
echo. >> "%LOG%"

echo [3] inspect (default home) >> "%LOG%"
grok inspect >> "%LOG%" 2>&1
echo. >> "%LOG%"

echo [4] same commands with GROK_HOME redirected >> "%LOG%"
set "GROK_HOME=%CD%\data\tmp\grok-probe-home"
if not exist "%GROK_HOME%" mkdir "%GROK_HOME%"
echo     GROK_HOME=%GROK_HOME% >> "%LOG%"
grok du >> "%LOG%" 2>&1
echo. >> "%LOG%"

echo [5] same with GROK_CONFIG_DIR redirected >> "%LOG%"
set "GROK_HOME="
set "GROK_CONFIG_DIR=%CD%\data\tmp\grok-probe-home"
grok du >> "%LOG%" 2>&1
set "GROK_CONFIG_DIR="
echo. >> "%LOG%"

echo [6] env vars grok knows about >> "%LOG%"
set GROK >> "%LOG%" 2>&1
echo. >> "%LOG%"
echo  probe done >> "%LOG%"

echo.
echo  Done. Log written to: %LOG%
echo.
timeout /t 10

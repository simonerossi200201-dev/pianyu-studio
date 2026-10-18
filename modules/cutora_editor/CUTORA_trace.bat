@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "CUTORA_PERF=1"
echo CUTORA_PERF=1
echo seek log: %LOCALAPPDATA%\CUTORA\logs\seek_trace.jsonl
echo.
call "%~dp0CUTORA.bat"

@echo off
REM Visible-window entry. Same flow as CUTORA.vbs.
REM Use this when antivirus blocks .vbs, or when you need to see install progress.
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
call "%~dp0CUTORA.bat" %*
exit /b %ERRORLEVEL%

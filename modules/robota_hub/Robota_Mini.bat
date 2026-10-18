@echo off
rem Robota mini assistant window
cd /d "%~dp0"
set PYTHONPATH=%CD%
set PYTHONUTF8=1

rem Ensure Robota background server is alive
"%CD%\runtime\venv\Scripts\pythonw.exe" "%CD%\robota\launch.py" --no-browser

rem Try opening as standalone frameless app in Edge or Chrome
start "" msedge.exe --app="http://127.0.0.1:9600/mini" --window-size=390,700 2>nul
if %ERRORLEVEL% equ 0 exit /b 0

start "" chrome.exe --app="http://127.0.0.1:9600/mini" --window-size=390,700 2>nul
if %ERRORLEVEL% equ 0 exit /b 0

rem Fallback to default browser
start "" "http://127.0.0.1:9600/mini"
exit /b 0

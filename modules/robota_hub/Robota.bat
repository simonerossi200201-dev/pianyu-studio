@echo off
rem Robota console launcher. Keep pure ASCII.
rem   Robota.bat          start (if needed) and open http://127.0.0.1:9600
rem   Robota.bat stop     stop the console
cd /d "%~dp0"
set "PYTHONUTF8=1"
if /i "%~1"=="stop" goto stop
"%~dp0runtime\venv\Scripts\python.exe" "%~dp0robota\launch.py" %*
if errorlevel 1 pause
exit /b
:stop
for /f "tokens=5" %%p in ('netstat -ano ^| findstr "127.0.0.1:9600" ^| findstr LISTENING') do taskkill /pid %%p /f
exit /b

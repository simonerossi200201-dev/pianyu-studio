@echo off
rem Orante floating icon - self test. Starts the icon in demo mode (always visible).
rem Drag it, wheel to resize, right click for the menu, close with the menu item.
rem PURE ASCII on purpose: cmd.exe reads .bat files by byte offset.
cd /d "%~dp0.."
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
echo Starting the floating icon in demo mode...
"%PY%" -m orante.floaticon --demo
echo.
pause

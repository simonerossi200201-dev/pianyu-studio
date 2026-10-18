@echo off
chcp 65001 >nul
title RightClickTableTool - Install
cd /d "%~dp0"
echo.
echo  Installing RightClickTableTool ...
echo.
py -3 "%~dp0install.py"
if errorlevel 1 python "%~dp0install.py"
if errorlevel 1 (
  echo.
  echo  Python not found. Please install Python 3.10+ and tick "Add python.exe to PATH":
  echo  https://www.python.org/downloads/
  pause
  exit /b 1
)

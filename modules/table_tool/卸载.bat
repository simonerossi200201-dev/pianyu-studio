@echo off
chcp 65001 >nul
title RightClickTableTool - Uninstall
cd /d "%~dp0"
echo.
echo  This removes the program and the right-click menu entry only.
echo  Your videos and project files are NOT deleted.
echo.
pause
py -3 "%~dp0uninstall.py"
if errorlevel 1 python "%~dp0uninstall.py"

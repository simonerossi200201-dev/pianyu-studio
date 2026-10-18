@echo off
rem 规格 60：把误判成出画的近景尾帧 in_frame=False 改回 True。
rem 默认只列出。要真写：14-Fix-InFrame.bat --yes
title Fix in_frame
cd /d "%~dp0.."
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo Python not found. & pause & exit /b 1
)
"%PY%" tools\fix_inframe.py %*
echo.
pause

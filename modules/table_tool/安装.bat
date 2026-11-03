@echo off
chcp 65001 >nul
title 片语工作室 - 右键表格工具安装
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "STUDIO_ROOT=%~dp0..\.."
if exist "%STUDIO_ROOT%\runtime\bin\portable_venv_python.exe" (
    copy /y "%STUDIO_ROOT%\runtime\bin\portable_venv_python.exe" "%STUDIO_ROOT%\runtime\venv\Scripts\python.exe" >nul 2>&1
)
if exist "%STUDIO_ROOT%\runtime\bin\portable_venv_pythonw.exe" (
    copy /y "%STUDIO_ROOT%\runtime\bin\portable_venv_pythonw.exe" "%STUDIO_ROOT%\runtime\venv\Scripts\pythonw.exe" >nul 2>&1
)
if exist "%STUDIO_ROOT%\runtime\venv\Scripts\python.exe" (
    "%STUDIO_ROOT%\runtime\venv\Scripts\python.exe" "%~dp0install.py"
    goto :done
)
if exist "%STUDIO_ROOT%\runtime\python\python.exe" (
    "%STUDIO_ROOT%\runtime\python\python.exe" "%~dp0install.py"
    goto :done
)
py -3 "%~dp0install.py"
if errorlevel 1 python "%~dp0install.py"
:done
pause

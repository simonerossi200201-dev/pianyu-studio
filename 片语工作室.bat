@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 片语工作室 (Cutora LTX Studio v3.0)
set "PYTHONUTF8=1"
set "HF_HUB_OFFLINE=1"
set "TRANSFORMERS_OFFLINE=1"
set "PATH=%~dp0runtime\bin;%~dp0runtime\ffmpeg;%PATH%"

REM 优先使用自带便携 Python 重写 venv 路径，确保任何电脑解压即用
if exist "%~dp0runtime\python\python.exe" (
    "%~dp0runtime\python\python.exe" -c "import sys; sys.path.insert(0, r'%~dp0'); from core.portable import rewrite_pyvenv_cfg; rewrite_pyvenv_cfg(r'%~dp0')" >nul 2>&1
)

if exist "%~dp0runtime\venv\Scripts\pythonw.exe" (
    start "" "%~dp0runtime\venv\Scripts\pythonw.exe" "%~dp0studio_launcher.py" %*
    exit /b 0
)
if exist "%~dp0runtime\python\pythonw.exe" (
    start "" "%~dp0runtime\python\pythonw.exe" "%~dp0studio_launcher.py" %*
    exit /b 0
)
python "%~dp0studio_launcher.py" %*

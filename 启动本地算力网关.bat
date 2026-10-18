@echo off
chcp 65001 >nul
cd /d "%~dp0"
title PianYu Studio - Local AI Gateway (127.0.0.1:7870)
set "PYTHONUTF8=1"
set "HF_HUB_OFFLINE=1"
set "TRANSFORMERS_OFFLINE=1"
set "PATH=%~dp0runtime\bin;%~dp0runtime\ffmpeg;%PATH%"

if exist "%~dp0runtime\python\python.exe" (
    "%~dp0runtime\python\python.exe" -c "import sys; sys.path.insert(0, r'%~dp0'); from core.portable import rewrite_pyvenv_cfg; rewrite_pyvenv_cfg(r'%~dp0')" >nul 2>&1
)
"%~dp0runtime\venv\Scripts\python.exe" -m modules.studio_gateway.server
pause

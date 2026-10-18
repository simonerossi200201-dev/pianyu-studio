@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
if exist "%~dp0runtime\python\python.exe" (
    "%~dp0runtime\python\python.exe" -c "import sys; sys.path.insert(0, r'%~dp0'); from core.portable import rewrite_pyvenv_cfg; rewrite_pyvenv_cfg(r'%~dp0')" >nul 2>&1
)
if exist "%~dp0runtime\venv\Scripts\python.exe" (
    "%~dp0runtime\venv\Scripts\python.exe" "%~dp0install_studio.py" %*
) else (
    python "%~dp0install_studio.py" %*
)
pause

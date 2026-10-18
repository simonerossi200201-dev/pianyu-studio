@echo off
rem Robota voice gateway (127.0.0.1:9611). Uses D:\0_Dev\local_tts venvs read-only.
cd /d "%~dp0..\.."
set PYTHONPATH=%CD%
set PYTHONUTF8=1
"%CD%\runtime\venv\Scripts\python.exe" -m robota.voice.server

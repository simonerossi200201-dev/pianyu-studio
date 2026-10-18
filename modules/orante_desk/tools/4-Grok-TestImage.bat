@echo off
rem One-shot test: Grok generates one image from a reference image.
title Grok image test
cd /d "%~dp0.."
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
".venv\Scripts\python.exe" tools\test_grok_image.py %* > "data\logs\grok-testimage.log" 2>&1
type "data\logs\grok-testimage.log"
pause

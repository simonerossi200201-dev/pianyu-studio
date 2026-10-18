@echo off
cd /d "%~dp0"
(grok models & echo ---- & grok inspect) > grok-models.txt 2>&1
type grok-models.txt
timeout /t 5

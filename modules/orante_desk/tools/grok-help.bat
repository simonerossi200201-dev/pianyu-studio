@echo off
cd /d "%~dp0"
(where grok & echo ---- & grok --help & echo ---- & grok --version) > grok-help.txt 2>&1
type grok-help.txt
echo.
echo written to tools\grok-help.txt
timeout /t 8

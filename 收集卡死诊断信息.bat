@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 片语工作室 - 收集卡死诊断信息
echo 正在收集诊断信息（只读取，不会修改电脑任何设置），大约需要 30 秒...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0收集卡死诊断信息.ps1"
echo.
pause

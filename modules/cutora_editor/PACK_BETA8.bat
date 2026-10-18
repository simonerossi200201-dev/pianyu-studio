@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
title 片语 Studio 打包 7.0.0-beta8

REM 一键出内测包：tools\pack_internal.py，跳过测试闸门（沿用 beta6/beta7 的做法，
REM 已知失败测试见 README_INTERNAL.md）。仍会做：拷贝白名单 / 模型 / 自带 Python、
REM 字体与授权核对、防泄漏检查、便携 venv 自检、打 zip + sha256。
REM 输出：D:\0_Dev\CUTORA-dist\CUTORA-7.0.0-beta8-internal.zip（约 20 GB，需要 40 GB 以上空闲）

set "PY=%~dp0runtime\venv\Scripts\python.exe"
if not exist "%PY%" (
    color 0C
    echo [失败] 找不到 %PY%
    pause
    exit /b 1
)

echo.
echo 开始打包 7.0.0-beta8 ……整个过程大约 20-40 分钟，别关这个窗口。
echo.
"%PY%" tools\pack_internal.py --version 7.0.0-beta8 --skip-gates
set RC=%ERRORLEVEL%
echo.
if %RC% NEQ 0 (
    color 0C
    echo [失败] 打包没成功，日志：D:\0_Dev\CUTORA-dist\pack_7.0.0-beta8.log
) else (
    color 0A
    echo [完成] D:\0_Dev\CUTORA-dist\CUTORA-7.0.0-beta8-internal.zip
    start "" explorer.exe /select,"D:\0_Dev\CUTORA-dist\CUTORA-7.0.0-beta8-internal.zip"
)
pause
exit /b %RC%

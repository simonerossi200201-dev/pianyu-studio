@echo off
rem 规格 51 A 节：把当前 Grok 详情页的 <video> / 网络 / probe JS 原样 dump 出来。
rem 只读，不点、不关标签。下次卡住时双击这个。
title Grok post dump
cd /d "%~dp0.."
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo Orante .venv not found. & pause & exit /b 1
)
"%PY%" tools\grok_post_dump.py %*
echo.
echo  写到 tools\grok-post-dump.txt
echo.
pause

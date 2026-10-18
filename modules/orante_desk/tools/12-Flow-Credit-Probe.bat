@echo off
rem 规格 57 B 节：只生 1 张图 + 1 条 8s 视频，量 nzlxg 和响应[1]。
rem 用法：12-Flow-Credit-Probe.bat [主账号] [对照账号]
rem 默认 TIK-D1 / TIK-L1。只跑一次，报告出来再决定要不要跑第二次。
title Flow credit probe
cd /d "%~dp0.."
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
  for /d %%D in ("%~dp0..\..\*") do (
    if exist "%%D\.venv\Scripts\python.exe" if exist "%%D\core\grok_pipe.py" set "PY=%%D\.venv\Scripts\python.exe"
  )
)
if not exist "%PY%" (
  echo Python not found. & pause & exit /b 1
)
if "%~1"=="" (
  "%PY%" tools\flow_credit_probe.py
) else (
  "%PY%" tools\flow_credit_probe.py %*
)
echo.
echo  写到 data\capture\probe57\result.md
echo.
pause

@echo off
rem Orante 启动诊断：用带控制台的 python 跑，把完整报错写进 data\logs\orante-debug.txt
cd /d "%~dp0.."
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "PYTHONFAULTHANDLER=1"
if not exist "data\logs" mkdir "data\logs"
echo ==== %date% %time% ==== > "data\logs\orante-debug.txt"
echo --- 探端口 9400 --- >> "data\logs\orante-debug.txt"
".venv\Scripts\python.exe" -c "import socket,sys; s=socket.socket(); s.settimeout(1); r=s.connect_ex(('127.0.0.1',9400)); s.close(); sys.exit(0 if r==0 else 1)"
if not errorlevel 1 (
  echo PORT 9400 BUSY >> "data\logs\orante-debug.txt"
  echo.
  echo 9400 已经被占用，像是 Orante 已经在跑。请先关掉现有窗口再跑本脚本。
  echo.
  type "data\logs\orante-debug.txt"
  pause
  exit /b 1
)
echo PORT 9400 FREE >> "data\logs\orante-debug.txt"
echo --- import 自检 --- >> "data\logs\orante-debug.txt"
".venv\Scripts\python.exe" -c "import orante, orante.server, orante.services.generate, orante.services.expand, orante.services.bench, orante.services.pipeline, orante.engines.flow_api; print('IMPORT OK')" >> "data\logs\orante-debug.txt" 2>&1
echo --- 启动（浏览器模式，无 pythonw） --- >> "data\logs\orante-debug.txt"
".venv\Scripts\python.exe" -m orante --browser >> "data\logs\orante-debug.txt" 2>&1
echo EXITCODE=%errorlevel% >> "data\logs\orante-debug.txt"
echo.
echo ================= 诊断结果 =================
type "data\logs\orante-debug.txt"
echo ===========================================
pause

@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 片语工作室 - 模块化乐高插件与版本打包分发器
set "PYTHONUTF8=1"

if not "%~1"=="" (
    "%~dp0runtime\venv\Scripts\python.exe" "%~dp0打包分发.py" %*
    goto :eof
)

echo ================================================================
echo   片语工作室 (D:\0_LTX) 模块化乐高扩展包与版本打包器
echo ================================================================
echo   [1] 查看版本与乐高扩展包体积精算表 (--edition plan)
echo   [2] 打包【01_模型与环境底座包 (只需下载一次)】(--edition models-once)
echo   [3] 打包【02_代码主程序包 (日常更新只下此包)】(--edition code-only)
echo   [4] 单独打包扩展包 1: LTX-2.5 22B 视频生成包 (pack-ltx25-video.ltxpack)
echo   [5] 单独打包扩展包 2: LTX-2.5 IC-LoRA 7合1特效包 (pack-ltx25-vfx-loras.ltxpack)
echo   [6] 单独打包扩展包 3: FLUX.2 [klein] 4B 生图改图包 (pack-flux2-image.ltxpack)
echo   [7] 单独打包扩展包 4: AI 配音克隆与配乐生成包 (pack-audio-tts-bgm.ltxpack)
echo   [8] 单独打包扩展包 5: 4x 超分/追踪与多国语对齐包 (pack-pro-vision-multilang.ltxpack)
echo ================================================================
set /p choice="请输入序号 (默认 3): "
if "%choice%"=="" set choice=3

if "%choice%"=="1" "%~dp0runtime\venv\Scripts\python.exe" "%~dp0打包分发.py" --edition plan
if "%choice%"=="2" "%~dp0runtime\venv\Scripts\python.exe" "%~dp0打包分发.py" --edition models-once
if "%choice%"=="3" "%~dp0runtime\venv\Scripts\python.exe" "%~dp0打包分发.py" --edition code-only
if "%choice%"=="4" "%~dp0runtime\venv\Scripts\python.exe" "%~dp0打包分发.py" --edition pack --pack-id pack-ltx25-video
if "%choice%"=="5" "%~dp0runtime\venv\Scripts\python.exe" "%~dp0打包分发.py" --edition pack --pack-id pack-ltx25-vfx-loras
if "%choice%"=="6" "%~dp0runtime\venv\Scripts\python.exe" "%~dp0打包分发.py" --edition pack --pack-id pack-flux2-image
if "%choice%"=="7" "%~dp0runtime\venv\Scripts\python.exe" "%~dp0打包分发.py" --edition pack --pack-id pack-audio-tts-bgm
if "%choice%"=="8" "%~dp0runtime\venv\Scripts\python.exe" "%~dp0打包分发.py" --edition pack --pack-id pack-pro-vision-multilang
echo.
pause

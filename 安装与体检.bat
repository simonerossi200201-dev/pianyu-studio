@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "STUDIO_ROOT=%~dp0"
if "%STUDIO_ROOT:~-1%"=="\" set "STUDIO_ROOT=%STUDIO_ROOT:~0,-1%"

rem ── 代码包 + 模型包自动合并 ──
rem 两个压缩包里都是「片语工作室」文件夹：解压到同一位置会自动合并；
rem 如果被分别解压到了两个不同的文件夹，这里自动找到模型包里的 runtime 并合并进来（只合并一次，旧文件不覆盖新文件）。
if not exist "%STUDIO_ROOT%\runtime\venv\Scripts\python.exe" (
    echo [合并] 当前目录还没有模型与运行环境，正在自动查找《01_模型与环境底座包》解压出的文件夹...
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
      "$here = '%STUDIO_ROOT%';" ^
      "$roots = @((Split-Path $here), (Split-Path (Split-Path $here))) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -Unique;" ^
      "$cand = $null;" ^
      "foreach ($r in $roots) { $cand = Get-ChildItem -LiteralPath $r -Directory -Recurse -Depth 3 -ErrorAction SilentlyContinue | Where-Object { $_.FullName -ne $here -and (Test-Path (Join-Path $_.FullName 'runtime\venv\Scripts\python.exe')) } | Select-Object -First 1; if ($cand) { break } }" ^
      "if ($cand) { Write-Host ('[合并] 找到模型包: ' + $cand.FullName); robocopy (Join-Path $cand.FullName 'runtime') (Join-Path $here 'runtime') /E /MOVE /XO /NFL /NDL /NJH /NJS /NP | Out-Null; Write-Host '[合并] 模型与运行环境已合并完成。' } else { Write-Host '[合并] 没找到模型包。请把《01_模型与环境底座包》和本代码包解压到同一个位置后再运行本程序。' }"
)

if exist "%STUDIO_ROOT%\runtime\bin\portable_venv_python.exe" (
    copy /y "%STUDIO_ROOT%\runtime\bin\portable_venv_python.exe" "%STUDIO_ROOT%\runtime\venv\Scripts\python.exe" >nul 2>&1
)
if exist "%STUDIO_ROOT%\runtime\bin\portable_venv_pythonw.exe" (
    copy /y "%STUDIO_ROOT%\runtime\bin\portable_venv_pythonw.exe" "%STUDIO_ROOT%\runtime\venv\Scripts\pythonw.exe" >nul 2>&1
)
if exist "%STUDIO_ROOT%\runtime\venv\Scripts\python.exe" (
    "%STUDIO_ROOT%\runtime\venv\Scripts\python.exe" "%STUDIO_ROOT%\install_studio.py" %*
) else if exist "%STUDIO_ROOT%\runtime\python\python.exe" (
    "%STUDIO_ROOT%\runtime\python\python.exe" "%STUDIO_ROOT%\install_studio.py" %*
) else (
    python "%STUDIO_ROOT%\install_studio.py" %*
)
pause

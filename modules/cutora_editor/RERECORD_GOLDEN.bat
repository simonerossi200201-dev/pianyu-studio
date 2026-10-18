@echo off
rem Re-record the twelve live golden baselines after the decoder proof.
rem Signed off by Luca 2026-09-19. Pure ASCII.
title CUTORA golden re-record
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PY=%~dp0runtime\venv\Scripts\python.exe"
set "REC=tools\_diag\t11g_record_one.py"
if not exist "docs\plan\queue\_log" mkdir "docs\plan\queue\_log"
set "OUT=docs\plan\queue\_log\_rerecord.log"
echo === re-record %date% %time% > "%OUT%"

echo.
echo  Re-recording twelve golden baselines.
echo  Reason: the old ones were recorded through a decoder that deviates from
echo  the H.264 spec on 40%% of pixels. Proof: docs\plan\queue\_log\_decode_proof.log
echo.
for %%C in (g08_fx_static g10_fx_anim g11_vertical_basic_fx g12_vertical_fx_path g13_horizontal_it_fx g14_stitch_fx g15_scatter_insert_fx g16_parallel_fx g17_cli_fx g18_linebreak_latin_fx g19-2_stitch_timeline g19b-2_stitch_crossfade) do (
  echo  recording %%C ...
  echo --- %%C --- >> "%OUT%"
  "%PY%" "%REC%" %%C >> "%OUT%" 2>&1
  if errorlevel 1 (
    echo.
    echo  ****  %%C FAILED to record - stopping. See %OUT%
    pause
    exit /b 1
  )
)

echo.
echo  All twelve recorded. Verifying with a clean golden run ...
echo --- verify --- >> "%OUT%"
"%PY%" tools\golden_check.py --all --repeat 1 --allow-dirty >> "%OUT%" 2>&1
set "G=%errorlevel%"
echo.
echo ================================================================
echo  verify golden exit %G%   (0 = pass)
if "%G%"=="0" echo  -^> Baselines re-recorded and verified. Send the log to Claude.
if not "%G%"=="0" echo  -^> Verification FAILED. Do not commit. Send the log to Claude.
echo  log: %OUT%
echo ================================================================
pause

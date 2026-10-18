$log = "D:\0_LLM\logs\refine_tests.log"
function Log([string]$m) { Add-Content -Path $log -Value ("{0} {1}" -f (Get-Date -Format "o"), $m) -Encoding UTF8 }
$py = "D:\0_LLM\ComfyUI\venv\Scripts\python.exe"; $d = "D:\0_LLM\bench\ltx25"
Log "waiting for IC-LoRA"
while (-not (Test-Path "D:\0_LLM\logs\upscale_dl.done")) { if (Test-Path "D:\0_LLM\logs\upscale_dl.fail") { if ((Get-Item "D:\0_LLM\logs\upscale_dl.fail").LastWriteTime -gt (Get-Date "2026-10-11 16:30")) { Log "download failed"; exit 1 } }; Start-Sleep 20 }
Log ("gpu " + (nvidia-smi --query-gpu=clocks.sm,power.draw,pstate --format=csv,noheader) + " plan " + ((powercfg /getactivescheme) -join " "))
Log "RUN 5s"; $o = & $py -u "$d\run_api.py" "$d\refine_api_5s.json" refine_5s 2>&1 | Out-String; Log ("RESULT " + $o.Trim())
$ok = $o -match '"status": "success"'
$w = 0; if ($o -match '"wall_s": ([\d\.]+)') { $w = [double]$Matches[1] }
if ($ok -and ($w * 3) -lt 3600) { Log "RUN full"; $o2 = & $py -u "$d\run_api.py" "$d\refine_api_full.json" refine_full 2>&1 | Out-String; Log ("RESULT " + $o2.Trim()) } else { Log "skip full (ok=$ok wall5s=$w)" }
Log "ALL DONE"; Set-Content "D:\0_LLM\logs\refine_tests.done" "ok"

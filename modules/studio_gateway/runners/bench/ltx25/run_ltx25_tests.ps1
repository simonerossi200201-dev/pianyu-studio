# Waits for verified LTX-2.5 weights, then runs the local test generations through ComfyUI on 127.0.0.1:8188.
$log = "D:\0_LLM\logs\ltx25_tests.log"
function Log([string]$m) { Add-Content -Path $log -Value ("{0} {1}" -f (Get-Date -Format "o"), $m) -Encoding UTF8 }
$py = "D:\0_LLM\ComfyUI\venv\Scripts\python.exe"; $run = "D:\0_LLM\bench\ltx25\ltx25_run.py"; $pd = "D:\0_LLM\bench\ltx25"
Log "waiting for weights"
while (-not (Test-Path "D:\0_LLM\logs\ltx25_gated.done")) {
    if (Test-Path "D:\0_LLM\logs\ltx25_gated.fail") { Log "download failed, abort"; exit 1 }
    Start-Sleep -Seconds 30
}
# ComfyUI must be up (restart on the user's usual flags if not)
try { Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8188/system_stats -TimeoutSec 5 | Out-Null } catch {
    Start-Process cmd.exe -ArgumentList '/c ""D:\0_LLM\ComfyUI\venv\Scripts\python.exe" -u "D:\0_LLM\ComfyUI\main.py" --listen 127.0.0.1 --port 8188 --disable-pinned-memory --reserve-vram 0.8 --preview-method none --use-ck-attention 1>>"D:\0_LLM\logs\comfyui.log" 2>&1"' -WindowStyle Hidden
    Start-Sleep -Seconds 60
}
Log ("gpu-idle " + (nvidia-smi --query-gpu=clocks.sm,clocks.max.sm,power.draw,power.limit,pstate --format=csv,noheader))
Log ("cpu " + ((Get-CimInstance Win32_Processor | % { "$($_.Name) cur=$($_.CurrentClockSpeed)MHz max=$($_.MaxClockSpeed)MHz" }) -join "; "))
function T($argsList) { Log ("RUN " + ($argsList -join " ")); $o = & $py -u $run @argsList 2>&1 | Out-String; Log ("RESULT " + $o.Trim()); return $o }
$r1 = T @("--mode","i2v","--w","576","--h","1024","--frames","361","--name","i2v_576x1024_15s","--prompt_file","$pd\prompt_i2v_speech.txt")
if ($r1 -notmatch '"status": "success"') {
    $r1b = T @("--mode","i2v","--w","576","--h","1024","--frames","361","--tile","256","--tframes","32","--name","i2v_576x1024_15s_tiled","--prompt_file","$pd\prompt_i2v_speech.txt")
    if ($r1b -notmatch '"status": "success"') { T @("--mode","i2v","--w","576","--h","1024","--frames","241","--tile","256","--tframes","32","--name","i2v_576x1024_10s","--prompt_file","$pd\prompt_i2v_speech.txt") | Out-Null }
}
T @("--mode","i2v","--w","640","--h","1152","--frames","361","--name","i2v_640x1152_15s","--prompt_file","$pd\prompt_i2v_speech.txt") | Out-Null
T @("--mode","t2v","--w","576","--h","1024","--frames","361","--name","t2v_576x1024_15s","--prompt_file","$pd\prompt_t2v_speech.txt") | Out-Null
Log ("cpu-end " + ((Get-CimInstance Win32_Processor | % { "cur=$($_.CurrentClockSpeed)MHz" }) -join "; "))
Log "ALL DONE"; Set-Content "D:\0_LLM\logs\ltx25_tests.done" "ok"

$log='D:\0_LLM\logs\inpaint_tune.log'; function L($m){ "$(Get-Date -Format o) $m" | Add-Content $log }
$py='D:\0_LLM\ComfyUI\venv\Scripts\python.exe'
function Up(){ try{ $null=Invoke-WebRequest http://127.0.0.1:8188/system_stats -UseBasicParsing -TimeoutSec 5; $true }catch{ $false } }
function StartComfy(){ L 'start comfy'; Start-Process -WindowStyle Minimized -FilePath $py -ArgumentList '-u','main.py','--listen','127.0.0.1','--port','8188','--disable-pinned-memory' -WorkingDirectory D:\0_LLM\ComfyUI -RedirectStandardOutput D:\0_LLM\logs\comfy_tune.out -RedirectStandardError D:\0_LLM\logs\comfy_tune.err; for($i=0;$i -lt 60;$i++){ Start-Sleep 5; if(Up){break} } }
foreach($k in 'A','B','C','E','D','F'){
  if(Test-Path "D:\0_LLM\bench\inpaint\tune\done_$k"){ continue }
  if(-not (Up)){ StartComfy }
  L "run $k"; $r = & $py D:\0_LLM\bench\inpaint\run_api.py "D:\0_LLM\bench\inpaint\tune\api_$k.json" "tune_$k" 2>&1 | Out-String; L ($r.Trim() -replace "`r?`n",' | ').Substring(0,[math]::Min(1500,$r.Trim().Length))
  New-Item "D:\0_LLM\bench\inpaint\tune\done_$k" -ItemType File -Force | Out-Null
}
L "ALL DONE"

$R='D:\0_LLM\Outputs\restore_real'; $IN='D:\0_LLM\ComfyUI\input'; $log="$R\rr.log"; function L($m){ "$(Get-Date -Format T) $m" | Add-Content $log -Encoding UTF8 }
$FF=(Get-ChildItem D:\0_LLM\ComfyUI\venv\Lib\site-packages\imageio_ffmpeg\binaries\ffmpeg*.exe)[0].FullName
$AG='D:\0_LLM\tools\autograde\venv\Scripts\python.exe'
# SDR->HDR variant on first 97 frames of restored clip (GPU)
& $FF -y -loglevel error -i "$R\restored_576.mp4" -frames:v 97 -c:v libx264 -crf 12 -pix_fmt yuv420p -c:a aac "$IN\hdr_in.mp4"
$j=Get-Content D:\0_LLM\bench\ltx_vfx\api_sdr2hdr.json -Raw -Encoding UTF8 | ConvertFrom-Json; $j.save.inputs.filename_prefix='restore_real/sdr2hdr'; $j.hlg.inputs.filename_prefix='restore_real/sdr2hdr_hlg'
[IO.File]::WriteAllText("$R\api_sdr2hdr_rr.json", ($j | ConvertTo-Json -Depth 20))
$t=Get-Date; L "sdr2hdr start vram=$((nvidia-smi --query-gpu=memory.used --format=csv,noheader).Trim())"
$o=& D:\0_LLM\ComfyUI\venv\Scripts\python.exe D:\0_LLM\bench\inpaint\run_api.py "$R\api_sdr2hdr_rr.json" sdr2hdr_rr 2>&1; L "sdr2hdr $(($o|Select -Last 1).ToString().Substring(0,[Math]::Min(400,($o|Select -Last 1).ToString().Length)))"
try{ $null=Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8188/free -Body '{"unload_models":true,"free_memory":true}' -ContentType 'application/json' -TimeoutSec 30 }catch{}
L "SDR2HDR_DONE"



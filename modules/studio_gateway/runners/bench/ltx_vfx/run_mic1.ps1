$log='D:\0_LLM\logs\ltx_vfx_test.log'; function L($m){ "$(Get-Date -Format o) $m" | Add-Content $log -Encoding UTF8 }
$IN='D:\0_LLM\ComfyUI\input'; $O='D:\0_LLM\Outputs\ltx_vfx_test'; $py='D:\0_LLM\ComfyUI\venv\Scripts\python.exe'
$FF=(Get-ChildItem D:\0_LLM\ComfyUI\venv\Lib\site-packages\imageio_ffmpeg\binaries\ffmpeg*.exe)[0].FullName
for($w=0;$w -lt 540;$w++){ $u=[int]((nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits).Trim()); if($u -lt 6000){break}; if($w % 30 -eq 0){ L "wait VRAM used=$u" }; Start-Sleep 10 }
L "run mic_cp08 vram_before=$u"
& $py D:\0_LLM\bench\inpaint\run_api.py D:\0_LLM\bench\ltx_vfx\api_mic_cp08.json vfx_mic_cp08 2>&1 | % { L $_ }
try{ $null=Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8188/free -Body '{"unload_models":true,"free_memory":true}' -ContentType 'application/json' -TimeoutSec 30 }catch{}
$out=Get-ChildItem "D:\0_LLM\ComfyUI\output\ltx_vfx\mic_cp08*.mp4" | sort LastWriteTime | select -Last 1
& $FF -y -loglevel error -i "$IN\vfx_clean_in.mp4" -i $out.FullName -filter_complex "[0:v]scale=576:1024,setsar=1[a];[1:v]scale=576:1024,setsar=1[b];[a][b]hstack=inputs=2[v]" -map "[v]" -an -c:v libx264 -crf 18 -pix_fmt yuv420p "$O\sbs_mic_cleanplate.mp4"
Copy-Item "$O\sbs_mic_cleanplate.mp4" C:\Users\Timco\ltx_xfer\ -Force; L 'MIC_CP_DONE'

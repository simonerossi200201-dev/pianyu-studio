$log='D:\0_LLM\logs\inpaint_test.log'; function L($m){ "$(Get-Date -Format o) $m" | Add-Content $log }
while(-not (((Get-Content D:\0_LLM\logs\inpaint_lora.log)[-1] -match 'DONE|MISMATCH'))){ Start-Sleep 20 }
if((Get-Content D:\0_LLM\logs\inpaint_lora.log)[-1] -match 'MISMATCH'){ L "LoRA hash mismatch"; exit 1 }
try{ $null=Invoke-WebRequest http://127.0.0.1:8188/system_stats -UseBasicParsing -TimeoutSec 5 }catch{ L 'start comfy'; Start-Process -WindowStyle Minimized -FilePath D:\0_LLM\ComfyUI\venv\Scripts\python.exe -ArgumentList '-u','main.py','--listen','127.0.0.1','--port','8188','--disable-pinned-memory' -WorkingDirectory D:\0_LLM\ComfyUI -RedirectStandardOutput D:\0_LLM\logs\comfy_inpaint.out -RedirectStandardError D:\0_LLM\logs\comfy_inpaint.err; for($i=0;$i -lt 60;$i++){ Start-Sleep 5; try{ $null=Invoke-WebRequest http://127.0.0.1:8188/system_stats -UseBasicParsing -TimeoutSec 5; break }catch{} } }
L "start inpaint"; $py='D:\0_LLM\ComfyUI\venv\Scripts\python.exe'
& $py D:\0_LLM\bench\inpaint\run_api.py D:\0_LLM\bench\inpaint\inpaint_api_5s.json inpaint_mouth_5s 2>&1 | % { L $_ }
$out = Get-ChildItem D:\0_LLM\ComfyUI\output\inpaint_test\mouth_closed*.mp4 -EA SilentlyContinue | sort LastWriteTime | select -Last 1
if($out){ $FF=(Get-ChildItem D:\0_LLM\ComfyUI\venv\Lib\site-packages\imageio_ffmpeg\binaries\ffmpeg*.exe)[0].FullName
  & $FF -y -loglevel error -i D:\0_LLM\ComfyUI\input\inpaint_src_5s.mp4 -i $out.FullName -filter_complex "[0:v]scale=576:1024,setsar=1[a];[1:v]scale=576:1024,setsar=1[b];[a][b]hstack=inputs=2[v]" -map "[v]" -map 1:a? -c:v libx264 -crf 18 -pix_fmt yuv420p -c:a aac D:\0_LLM\Outputs\inpaint_sbs_5s.mp4
  Copy-Item $out.FullName D:\0_LLM\Outputs\inpaint_mouth_closed_5s.mp4 -Force; L "sbs done $($out.FullName)" } else { L "NO OUTPUT" }
L "ALL DONE"



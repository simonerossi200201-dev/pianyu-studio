$ErrorActionPreference='Continue'
$log='D:\0_LLM\logs\ltx_vfx_test.log'; function L($m){ "$(Get-Date -Format o) $m" | Add-Content $log -Encoding UTF8 }
$B='D:\0_LLM\bench\ltx_vfx'; $IN='D:\0_LLM\ComfyUI\input'; $O='D:\0_LLM\Outputs\ltx_vfx_test'; New-Item -ItemType Directory -Force $O | Out-Null
New-Item -ItemType Directory -Force D:\0_LLM\Outputs\ltx25_test | Out-Null
$py='D:\0_LLM\ComfyUI\venv\Scripts\python.exe'
$FF=(Get-ChildItem D:\0_LLM\ComfyUI\venv\Lib\site-packages\imageio_ffmpeg\binaries\ffmpeg*.exe)[0].FullName
L 'START'
$src='D:\0_LLM\ComfyUI\output\ltx25_test\i2v_576x1024_15s_00001_.mp4'
$base="$IN\vfx_clean_in.mp4"
& $FF -y -loglevel error -ss 2 -i $src -f lavfi -i anullsrc=r=44100:cl=stereo -map 0:v -map 1:a -vf "scale=576:1024,fps=25,setsar=1" -frames:v 49 -t 1.96 -c:v libx264 -crf 14 -pix_fmt yuv420p -c:a aac -shortest $base
& $FF -y -loglevel error -i $base -vf "scale=192:342,scale=576:1024:flags=bilinear,noise=alls=22:allf=t,eq=contrast=0.85:saturation=0.75" -c:v libx264 -crf 38 -pix_fmt yuv420p -c:a copy "$IN\vfx_restore_in.mp4"
& $FF -y -loglevel error -i $base -vf "gblur=sigma=4" -c:v libx264 -crf 16 -pix_fmt yuv420p -c:a copy "$IN\vfx_deblur_in.mp4"
& $FF -y -loglevel error -i $base -vf "hue=s=0" -c:v libx264 -crf 16 -pix_fmt yuv420p -c:a copy "$IN\vfx_color_in.mp4"
L 'inputs made'
function Up(){ try{ $null=Invoke-WebRequest http://127.0.0.1:8188/system_stats -UseBasicParsing -TimeoutSec 5; return $true }catch{ return $false } }
if(-not (Up)){ L 'start comfy'; Start-Process -WindowStyle Minimized -FilePath $py -ArgumentList '-u','main.py','--listen','127.0.0.1','--port','8188','--disable-pinned-memory' -WorkingDirectory D:\0_LLM\ComfyUI -RedirectStandardOutput D:\0_LLM\logs\comfy_vfx.out -RedirectStandardError D:\0_LLM\logs\comfy_vfx.err; for($i=0;$i -lt 90;$i++){ Start-Sleep 5; if(Up){break} } }
L "comfy up=$(Up)"
$map=@{restore='vfx_restore_in.mp4';deblur='vfx_deblur_in.mp4';colorize='vfx_color_in.mp4';cleanplate='vfx_clean_in.mp4'}
foreach($t in 'restore','deblur','colorize','cleanplate'){
  # wait for VRAM: other processes (excluding ComfyUI itself) using < 2.5GB beyond comfy idle; simple rule: total used < 6000 MiB, max 90 min
  for($w=0;$w -lt 540;$w++){ $u=[int]((nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits).Trim()); if($u -lt 6000){break}; if($w % 30 -eq 0){ L "wait VRAM used=$u" }; Start-Sleep 10 }
  L "run $t vram_before=$u"
  & $py D:\0_LLM\bench\inpaint\run_api.py "$B\api_$t.json" "vfx_$t" 2>&1 | % { L $_ }
  $out=Get-ChildItem "D:\0_LLM\ComfyUI\output\ltx_vfx\$t*.mp4" -EA SilentlyContinue | sort LastWriteTime | select -Last 1
  try{ $null=Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8188/free -Body '{"unload_models":true,"free_memory":true}' -ContentType 'application/json' -TimeoutSec 30; Start-Sleep 15 }catch{}
  if($out){ & $FF -y -loglevel error -i "$IN\$($map[$t])" -i $out.FullName -filter_complex "[0:v]scale=576:1024,setsar=1[a];[1:v]scale=576:1024,setsar=1[b];[a][b]hstack=inputs=2[v]" -map "[v]" -an -c:v libx264 -crf 18 -pix_fmt yuv420p "$O\sbs_$t.mp4"; Copy-Item $out.FullName "$O\out_$t.mp4" -Force; Copy-Item "$O\sbs_$t.mp4" C:\Users\Timco\ltx_xfer\ -Force; L "sbs ok $t" } else { L "NO OUTPUT $t" }
}
L 'ALL_VFX_TESTS_DONE'

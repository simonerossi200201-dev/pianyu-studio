# Waits for LTX-2.3 downloads + free GPU/ComfyUI queue, then runs one official Relight test and builds a side-by-side.
$env:PYTHONUTF8='1'; $log='D:\0_LLM\logs\relight_test.log'; function L($m){ "$(Get-Date -Format o) $m" | Add-Content $log }
$py='D:\0_LLM\ComfyUI\venv\Scripts\python.exe'; $b='D:\0_LLM\bench\ltx_vfx'
L "waiting for downloads"
while(-not ((Test-Path D:\0_LLM\logs\hf_gated_ltx23.log) -and ((Get-Content D:\0_LLM\logs\hf_gated_ltx23.log)[-1] -match 'ALL_OK'))){ Start-Sleep 120 }
L "downloads OK; waiting for free GPU"
$free=0
while($free -lt 3){
  $q = curl.exe -s http://127.0.0.1:8188/queue
  $idle = $q -match '"queue_running": \[\]' -and $q -match '"queue_pending": \[\]'
  $mem = [int](nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits)
  if($idle -and $mem -lt 4000){ $free++ } else { $free=0 }
  Start-Sleep 60 }
L "GPU free; running relight 704x1280 121f"
& $py "$b\run_relight.py" --video relight_car_in.mp4 --width 704 --height 1280 --frames 121 2>&1 | % { L $_ }
if((Get-Content $log)[-1..-3] -match 'STATUS error|Traceback'){ L "retry 97 frames"; & $py "$b\run_relight.py" --video relight_car_in.mp4 --width 704 --height 1280 --frames 97 --prefix ltx_vfx/relight_official97 2>&1 | % { L $_ } }
$o='D:\0_LLM\ComfyUI\output\ltx_vfx'; $res=Get-ChildItem $o -Filter 'relight_official*_0*.mp4' | ? Name -notmatch '_ref_' | sort LastWriteTime | select -Last 1
$ref=Get-ChildItem $o -Filter 'relight_official*_ref_*.mp4' | sort LastWriteTime | select -Last 1
if($res -and $ref){
  New-Item -ItemType Directory -Force D:\0_LLM\Outputs\ltx_vfx_test | Out-Null
  $ff = & $py -c "import imageio_ffmpeg;print(imageio_ffmpeg.get_ffmpeg_exe())"
  & $ff -y -loglevel error -i $ref.FullName -i $res.FullName -filter_complex "[0:v]drawtext=text='input + ball':x=20:y=20:fontsize=36:fontcolor=white:box=1:boxcolor=black@0.5[a];[1:v]drawtext=text='Relight LTX-2.3':x=20:y=20:fontsize=36:fontcolor=white:box=1:boxcolor=black@0.5[b];[a][b]hstack=inputs=2" -c:v libx264 -crf 18 -pix_fmt yuv420p D:\0_LLM\Outputs\ltx_vfx_test\sbs_relight.mp4 2>&1 | % { L $_ }
  if(-not (Test-Path D:\0_LLM\Outputs\ltx_vfx_test\sbs_relight.mp4)){ & $ff -y -loglevel error -i $ref.FullName -i $res.FullName -filter_complex "hstack=inputs=2" -c:v libx264 -crf 18 -pix_fmt yuv420p D:\0_LLM\Outputs\ltx_vfx_test\sbs_relight.mp4 2>&1 | % { L $_ } }
  L "SBS D:\0_LLM\Outputs\ltx_vfx_test\sbs_relight.mp4"
}
L "RELIGHT_TEST_DONE"

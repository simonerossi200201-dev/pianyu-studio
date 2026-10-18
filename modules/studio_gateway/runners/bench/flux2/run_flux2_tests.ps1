$ErrorActionPreference='Continue'; $py='D:\0_LLM\ComfyUI\venv\Scripts\python.exe'; $r='D:\0_LLM\bench\flux2\flux2_run.py'; $log='D:\0_LLM\logs\flux2_tests.log'
function L($m){ "$(Get-Date -Format o) $m" | Add-Content $log }
while(-not (Select-String D:\0_LLM\logs\flux2_klein.log -Pattern 'linked .*qwen_3_4b_fp8_mixed|link-exists .*qwen_3_4b_fp8_mixed' -Quiet)){ if($false){ L "DOWNLOAD FAILED"; exit 1 }; Start-Sleep 30 }
L "weights ready"
$ok=$false; try{ $null=Invoke-WebRequest http://127.0.0.1:8188/system_stats -UseBasicParsing -TimeoutSec 5; $ok=$true }catch{}
if(-not $ok){ L "start comfyui"; Start-Process -WindowStyle Minimized -FilePath $py -ArgumentList 'main.py','--listen','127.0.0.1','--port','8188' -WorkingDirectory D:\0_LLM\ComfyUI -RedirectStandardOutput D:\0_LLM\logs\comfy_flux2.out -RedirectStandardError D:\0_LLM\logs\comfy_flux2.err
  for($i=0;$i -lt 60;$i++){ Start-Sleep 5; try{ $null=Invoke-WebRequest http://127.0.0.1:8188/system_stats -UseBasicParsing -TimeoutSec 5; break }catch{} } }
$tests=@(
 @('woman_car','A natural smartphone selfie photo of a friendly middle-aged woman with glasses sitting in the driver seat of a parked car, smiling softly, eyes fully open and bright, looking at the camera, casual white shirt, soft daylight through the car windows, realistic skin texture, candid everyday look'),
 @('church','A sunlit church interior with warm golden light streaming through tall windows, wooden pews, stone columns, peaceful and quiet, no people, photorealistic, soft dust in the light beams'),
 @('bible','An open Bible on a rustic wooden table beside a lit candle, soft warm morning light from a window, shallow depth of field, the page text is small and out of focus so nothing is legible, cozy peaceful mood, photorealistic'),
 @('sign_psalm','A wooden sign standing in a lush green garden with flowers, the sign has carved painted white letters reading exactly "The Lord is my shepherd" and below it a smaller line reading "Psalm 23:1", soft morning sunlight, photorealistic, sharp focus on the sign'))
foreach($t in $tests){ L "run $($t[0])"; & $py $r --prompt $t[1] --w 1088 --h 1920 --seed 42 --out-name $t[0] 2>&1 | Add-Content $log }
$src = Get-ChildItem D:\0_LLM\ComfyUI\output -Recurse -Filter 'woman_car*.png' | sort LastWriteTime | select -Last 1
if($src){ Copy-Item $src.FullName D:\0_LLM\ComfyUI\input\flux2_woman_car.png -Force; L "run edit"; & $py $r --prompt 'Change her shirt color to light blue. Keep everything else identical: same woman, same face, glasses, pose, car and lighting.' --seed 42 --edit-image flux2_woman_car.png --out-name edit_blue_shirt 2>&1 | Add-Content $log }
L "ALL DONE"


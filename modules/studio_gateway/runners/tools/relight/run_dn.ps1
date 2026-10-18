$env:PYTHONUTF8="1"; $r="D:\0_LLM\tools\relight"; Set-Location $r
$py="D:\0_LLM\ComfyUI\venv\Scripts\python.exe"; $ff="D:\0_Dev\CUTORA\runtime\ffmpeg\ffmpeg.exe"
$v="D:\0_LLM\Outputs\restore_real\v6"; $faces="D:\0_LLM\Outputs\restore_real\v5\faces.npz"; $in="$v\v6h_fill_exp.mp4"
$d="D:\0_LLM\Outputs\restore_real\denoise"; $x="C:\Users\Timco\ltx_xfer\denoise"; New-Item -ItemType Directory -Force $d,$x | Out-Null
function Log($m){ "$(Get-Date -Format HH:mm:ss) $m" | Add-Content D:\0_LLM\logs\denoise_batch.log }
Log "start"
& $py denoise.py $in "$d\v6h_dn_light.mp4" --radius 2 --strength 0.6 --thr 2.0 --chroma 0.5 --luma-nl 0 --face-npz $faces --face-strength 0.5 *> D:\0_LLM\logs\denoise_light.log
Log "light done"
& $py denoise.py $in "$d\v6h_dn_medium.mp4" --radius 2 --strength 1.0 --thr 2.5 --chroma 0.8 --luma-nl 2.5 --face-npz $faces --face-strength 0.5 *> D:\0_LLM\logs\denoise_medium.log
Log "medium done"
& $ff -v error -y -i $in -i "$d\v6h_dn_medium.mp4" -filter_complex "[0:v][1:v]hstack" -map "1:a?" -c:v libx264 -crf 16 -c:a copy "$d\v6h_vs_dn_medium_sbs.mp4"
foreach($t in 2,5,8,12){ & $py crops.py "$d\v6h_vs_dn_medium_sbs.mp4" $faces $t 30 1080 "$x\med_t$t" }
foreach($t in 2,8){ & $ff -v error -y -ss $t -i $in -ss $t -i "$d\v6h_dn_medium.mp4" -ss $t -i "$d\v6h_dn_light.mp4" -frames:v 1 -filter_complex "[0:v]crop=360:360:620:60[a];[1:v]crop=360:360:620:60[b];[2:v]crop=360:360:620:60[c];[a][b][c]hstack=3" "$x\wall100_t$t.jpg" }
Copy-Item D:\0_LLM\logs\denoise_light.log,D:\0_LLM\logs\denoise_medium.log $x -Force
Log "ALL DONE"

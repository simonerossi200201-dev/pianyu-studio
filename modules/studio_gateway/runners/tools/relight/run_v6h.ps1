$env:PYTHONUTF8="1"; $r="D:\0_LLM\tools\relight"; Set-Location $r
$py="D:\0_LLM\ComfyUI\venv\Scripts\python.exe"; $mp="D:\0_LLM\tools\venv_mediapipe\Scripts\python.exe"; $ff="D:\0_Dev\CUTORA\runtime\ffmpeg\ffmpeg.exe"
$v="D:\0_LLM\Outputs\restore_real\v6"; $src="D:\0_LLM\Outputs\relight\src_v3_final_1080x1440.mp4"; $faces="D:\0_LLM\Outputs\restore_real\v5\faces.npz"
$c="D:\0_LLM\Outputs\restore_real\catch"; $x="C:\Users\Timco\ltx_xfer\catch"; New-Item -ItemType Directory -Force $c,$x | Out-Null
function Log($m){ "$(Get-Date -Format HH:mm:ss) $m" | Add-Content D:\0_LLM\logs\relight_v6h_batch.log }
Log "start"
& $py relight.py $src -o $v --name v6h --strength 0 --window 0 --face-key 0 --face-npz $faces --deglare 0.001 --blacklevel 0.2 --lens 0.6 --hair 1.0 --lips 1.0 *> D:\0_LLM\logs\relight_v6h.log
& $py fill.py "$v\v6h.mp4" $faces "$v\v6h_fill_exp.mp4" --k 0.3 --rmax 1.5 --gain 1.17 *> D:\0_LLM\logs\relight_v6h_fill.log
& $py flicker.py $src "$v\v6h_fill_exp.mp4" $faces *> "$v\flicker_v3_vs_v6h_final.txt"
& $ff -v error -y -i $src -i "$v\v6h_fill_exp.mp4" -filter_complex "[0:v][1:v]hstack" -map "1:a?" -c:v libx264 -crf 18 -c:a copy "$v\v3_vs_v6h_sbs.mp4"
foreach($t in 2,8){ & $py crops.py "$v\v3_vs_v6h_sbs.mp4" $faces $t 30 1080 "$x\v3_vs_v6h_t$t" }
Copy-Item "$v\flicker_v3_vs_v6h_final.txt" $x -Force
foreach($t in 2,7,11){ & $ff -v error -y -ss $t -i $src -ss $t -i "$v\v6h_fill_exp.mp4" -frames:v 1 -filter_complex "[0:v]crop=500:500:580:0[l];[1:v]crop=500:500:580:0[rr];[l][rr]hstack" "$x\v6h_wall_t$t.jpg" }
Log "ALL DONE"


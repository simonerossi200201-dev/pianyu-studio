$ff="D:\0_Dev\CUTORA\runtime\ffmpeg\ffmpeg.exe"; $py="D:\0_LLM\ComfyUI\venv\Scripts\python.exe"; $env:PYTHONUTF8="1"
$v="D:\0_LLM\Outputs\restore_real\v6"; $fin="D:\0_LLM\Outputs\restore_real\final"; New-Item -ItemType Directory -Force $fin | Out-Null
$faces="D:\0_LLM\Outputs\restore_real\v5\faces.npz"; $src3="D:\0_LLM\Outputs\relight\src_v3_final_1080x1440.mp4"
$orig="C:\Users\Timco\Downloads\AQPHBUkMKKRnnDyV5vZZFIUhfE-x8ekZHQDu34kTYtd6dW2mghmOwKXXkz6xKbrPP0HRdfvj897KcDBghUpYbSnm8mxFaovXKSLW2wNmXA.mp4"
$res="$v\v6h_fill_exp.mp4"
$d=[double](& "D:\0_Dev\CUTORA\runtime\ffmpeg\ffprobe.exe" -v error -show_entries format=duration -of csv=p=0 $res)
$L1="$([string][char]0x539F+[string][char]0x7247)"; $L2="$([string][char]0x4FEE+[string][char]0x590D+[string][char]0x540E)"; $NM="$([string][char]0x5BF9+[string][char]0x6BD4+[string][char]0x005F+[string][char]0x539F+[string][char]0x7247+[string][char]0x005F+[string][char]0x0076+[string][char]0x0073+[string][char]0x005F+[string][char]0x4FEE+[string][char]0x590D+[string][char]0x540E)"
$fs="[0:v]fps=30,scale=-2:1440:flags=lanczos,setsar=1,drawtext=fontfile='C\:/Windows/Fonts/msyh.ttc':text='$L1':x=30:y=30:fontsize=56:fontcolor=white:box=1:boxcolor=black@0.45:boxborderw=12[l];[1:v]fps=30,setsar=1,drawtext=fontfile='C\:/Windows/Fonts/msyh.ttc':text='$L2':x=30:y=30:fontsize=56:fontcolor=white:box=1:boxcolor=black@0.45:boxborderw=12[r];[l][r]hstack=inputs=2[v]"
[IO.File]::WriteAllText("$fin\filter.txt",$fs,(New-Object Text.UTF8Encoding $false))
$out="$fin\$NM.mp4"
& $ff -v error -y -ss 20 -t $d -i $orig -i $res -filter_complex_script "$fin\filter.txt" -map "[v]" -map "1:a" -c:v libx264 -preset slow -crf 16 -profile:v high -pix_fmt yuv420p -c:a aac -b:a 192k -shortest -movflags +faststart $out
& $ff -v error -y -i $out -vf "scale=-2:720:flags=lanczos" -c:v libx264 -crf 23 -preset medium -c:a aac -b:a 128k -movflags +faststart "$fin\${NM}_preview720.mp4"
& "D:\0_Dev\CUTORA\runtime\ffmpeg\ffprobe.exe" -v error -show_entries stream=codec_type,width,height,duration,start_time -of compact $out
foreach($t in 1,7,13){ & $ff -v error -y -ss $t -i $out -frames:v 1 -vf "scale=-2:720" "$fin\check_t$t.jpg" }
foreach($t in 2,5,9,12){ & $ff -v error -y -ss $t -i $out -frames:v 1 -vf "crop=1080:420:1068:380" "C:\Users\Timco\ltx_xfer\final\eyes_t$t.jpg" }
Copy-Item "$fin\*.jpg" C:\Users\Timco\ltx_xfer\final -Force; Copy-Item "$fin\${NM}_preview720.mp4" C:\Users\Timco\ltx_xfer\final\preview720.mp4 -Force; Copy-Item $out C:\Users\Timco\ltx_xfer\final\full.mp4 -Force
& D:\0_LLM\ComfyUI\venv\Scripts\python.exe D:\0_LLM\tools\relight\avsync.py $orig $out
"FINAL DONE"

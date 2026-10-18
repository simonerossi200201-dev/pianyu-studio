$env:PYTHONUTF8="1"
$py="D:\0_LLM\ComfyUI\venv\Scripts\python.exe"; $src="D:\0_LLM\Outputs\relight\src_restored_576.mp4"
$jobs=@(
 @("A_left_4500","--light","left","--temp","4500","--strength","0.7"),
 @("B_right_3200_warm","--light","right","--temp","3200","--strength","0.7","--rim","0.6"),
 @("C_topleft_5600_soft","--light","top-left","--temp","5600","--strength","0.5","--softness","0.8","--ambient","0.6")
)
foreach($j in $jobs){
  $n=$j[0]; $rest=$j[1..($j.Length-1)]
  & $py relight.py $src --name $n --seconds 15 --debug @rest *> "D:\0_LLM\logs\relight_$n.log"
}
"ALL DONE" | Out-File D:\0_LLM\logs\relight_tests.done

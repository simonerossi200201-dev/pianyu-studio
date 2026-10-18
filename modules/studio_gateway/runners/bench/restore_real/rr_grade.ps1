$R='D:\0_LLM\Outputs\restore_real'; $log="$R\rr.log"; function L($m){ "$(Get-Date -Format T) $m" | Add-Content $log -Encoding UTF8 }
$AG='D:\0_LLM\tools\autograde\venv\Scripts\python.exe'
$t=Get-Date; & $AG D:\0_LLM\tools\autograde\autograde.py "$R\restored_576.mp4" -o "$R\graded_default.mp4" *> "$R\autograde_default.log"; L "grade default s=$([int]((Get-Date)-$t).TotalSeconds)"
$t=Get-Date; & $AG D:\0_LLM\tools\autograde\autograde.py "$R\restored_576.mp4" -o "$R\graded_regions.mp4" --regions *> "$R\autograde_regions.log"; L "grade regions s=$([int]((Get-Date)-$t).TotalSeconds)"
L "GRADE_DONE"

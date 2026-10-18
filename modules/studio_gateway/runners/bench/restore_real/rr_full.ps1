$log="D:\0_LLM\logs\ltx_vfx_test.log"
Start-Sleep 30
while(-not ((Get-Content $log -Tail 1) -match "MIC_INPAINT_DONE")){ Start-Sleep 15 }
& D:\0_LLM\ComfyUI\venv\Scripts\python.exe D:\0_LLM\bench\restore_real\rr.py full "R1.0;D1.0" *> D:\0_LLM\logs\rr_full.out

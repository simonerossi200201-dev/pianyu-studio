$log='D:\0_LLM\logs\rmbg.log'; function L($m){ "$(Get-Date -Format o) $m" | Add-Content $log }
$tokFile="$env:USERPROFILE\.cache\huggingface\token"
function Tok(){ if(Test-Path $tokFile){(Get-Content $tokFile -Raw).Trim()} else {(Get-ChildItem -Force -File $env:USERPROFILE | ? Name -match '^hf_[A-Za-z0-9]{30,}$' | select -First 1).Name} }
function Cfg($lines){ $p=New-Object Diagnostics.ProcessStartInfo 'curl.exe','-K -'; $p.UseShellExecute=$false; $p.RedirectStandardInput=$true; $p.RedirectStandardOutput=$true; $p.RedirectStandardError=$true; $pr=[Diagnostics.Process]::Start($p); $pr.StandardInput.WriteLine("header = `"Authorization: Bearer $(Tok)`""); foreach($l in $lines){$pr.StandardInput.WriteLine($l)}; $pr.StandardInput.Close(); $o=$pr.StandardOutput.ReadToEnd(); $null=$pr.StandardError.ReadToEnd(); $pr.WaitForExit(); @($pr.ExitCode,$o) }
# 1) venv: copy venv_demucs (torch 2.8 cu128), add official wheels
$v='D:\0_LLM\tools\venv_rmbg'
if(-not (Test-Path "$v\Scripts\python.exe")){ L "copy venv"; robocopy D:\0_LLM\audio\venv_demucs $v /E /R:1 /W:2 /NP /NFL /NDL /NJH /NJS | Out-Null; & D:\0_LLM\audio\python\cpython-3.11\python.exe -m venv --upgrade $v }
$wd='D:\0_LLM\tools\wheels'
$tv='https://download.pytorch.org/whl/cu128/torchvision-0.23.0%2Bcu128-cp311-cp311-win_amd64.whl'
if(-not (Test-Path "$wd\torchvision-0.23.0+cu128-cp311-cp311-win_amd64.whl")){ curl.exe -sL --fail --retry 5 -o "$wd\torchvision-0.23.0+cu128-cp311-cp311-win_amd64.whl" $tv; L "torchvision dl exit=$LASTEXITCODE" }
foreach($p in 'timm','kornia','kornia-rs'){ $j=curl.exe -s "https://pypi.org/pypi/$p/json" | ConvertFrom-Json; $w=$j.urls | ? { $_.filename -match 'py3-none-any\.whl$|cp311-cp311-win_amd64\.whl$|abi3-win_amd64\.whl$' } | select -First 1; $o="$wd\$($w.filename)"; curl.exe -sL --fail -o $o $w.url; L "$($w.filename) $(if((Get-FileHash $o).Hash.ToLower() -eq $w.digests.sha256){'sha OK'}else{'MISMATCH'})" }
& "$v\Scripts\python.exe" -m pip install --no-index --find-links $wd torchvision timm kornia 2>&1 | select -Last 2 | % { L $_ }
# 2) wait for gated access
$deadline=(Get-Date).AddHours(12)
while($true){ $r=Cfg @('url = "https://huggingface.co/briaai/RMBG-2.0/resolve/main/config.json"','head','location','silent','output = "NUL"','write-out = "%{http_code}"'); if($r[1] -match '200'){ L "access OK"; break }; L "GATED_NO_ACCESS http=$($r[1]) -> https://huggingface.co/briaai/RMBG-2.0"; if((Get-Date) -gt $deadline){ L "GIVE UP"; exit 3 }; Start-Sleep 900 }
# 3) download with SHA check
$md='D:\0_LLM\tools\rmbg\models\RMBG-2.0'; New-Item -ItemType Directory -Force $md | Out-Null
$tree=(Cfg @('url = "https://huggingface.co/api/models/briaai/RMBG-2.0/tree/main"','silent'))[1] | ConvertFrom-Json
foreach($f in 'config.json','BiRefNet_config.py','birefnet.py','preprocessor_config.json','model.safetensors','LICENSE','README.md'){
  $e=$tree | ? path -eq $f; if(-not $e){ continue }; $o="$md\$f"
  $r=Cfg @("url = `"https://huggingface.co/briaai/RMBG-2.0/resolve/main/$f`"",'location','fail','silent','retry = 10','retry-all-errors','speed-limit = 50000','speed-time = 60','continue-at = -',"output = `"$($o.Replace('\','/'))`"")
  $sz=(Get-Item $o).Length; if($e.lfs){ $h=(Get-FileHash $o).Hash.ToLower(); L "$f size=$sz $(if($h -eq $e.lfs.oid){'sha OK '+$h}else{'MISMATCH'})" } else { L "$f size=$sz $(if($sz -eq $e.size){'size OK'}else{'SIZE MISMATCH'})" } }
# 4) test
& "$v\Scripts\python.exe" D:\0_LLM\tools\rmbg\rmbg.py D:\0_LLM\tools\rmbg\test_woman_car.png D:\0_LLM\Outputs\rmbg 2>&1 | select -Last 4 | % { L $_ }
L "ALL DONE"

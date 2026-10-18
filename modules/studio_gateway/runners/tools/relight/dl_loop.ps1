# Resumable download of Intel/dpt-hybrid-midas weights (public, Apache-2.0, no token),
# official HF repo pinned to commit 11eaf7a; verified against the LFS sha256.
$d="D:\0_LLM\tools\relight\models\dpt-hybrid-midas"
$f="$d\pytorch_model.bin"; $part="$f.part"
$u="https://huggingface.co/Intel/dpt-hybrid-midas/resolve/11eaf7a1cf4bd70740697dbc216f98980c0aeb03/pytorch_model.bin"
$sha="b6c4d44f9d96ca3fa76dd3bbb153989a60b4ad5526559f3c598562a368d687ec"; $size=489648389
for($i=0;$i -lt 300;$i++){
  if((Test-Path $part) -and (Get-Item $part).Length -ge $size){ break }
  curl.exe -sS -L --speed-limit 1000 --speed-time 60 -C - -o $part $u
  Start-Sleep 10
}
$h=(Get-FileHash $part -Algorithm SHA256).Hash.ToLower()
if($h -eq $sha){ Move-Item $part $f -Force; "verified $(Get-Date)" } else { "SHA MISMATCH $h"; Remove-Item $part }

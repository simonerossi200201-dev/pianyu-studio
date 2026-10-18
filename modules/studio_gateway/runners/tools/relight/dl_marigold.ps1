# Resumable, sha256-verified download of prs-eth/marigold-normals-v1-1 (ETH Zurich, OpenRAIL++-M),
# official HF repo pinned to commit 09cfdd2, fp16 safetensors only. Public repo, no token.
$base="https://huggingface.co/prs-eth/marigold-normals-v1-1/resolve/09cfdd258cb281fa006cf1afcd2284376d16687d"
$root="D:\0_LLM\tools\relight\models\marigold-normals-v1-1"
$files=@(
 @("model_index.json",""),@("README.md",""),@("scheduler/scheduler_config.json",""),
 @("text_encoder/config.json",""),@("tokenizer/merges.txt",""),@("tokenizer/special_tokens_map.json",""),
 @("tokenizer/tokenizer_config.json",""),@("tokenizer/vocab.json",""),@("unet/config.json",""),@("vae/config.json",""),
 @("vae/diffusion_pytorch_model.fp16.safetensors","3e4c08995484ee61270175e9e7a072b66a6e4eeb5f0c266667fe1f45b90daf9a"),
 @("text_encoder/model.fp16.safetensors","bc1827c465450322616f06dea41596eac7d493f4e95904dcb51f0fc745c4e13f"),
 @("unet/diffusion_pytorch_model.fp16.safetensors","e90ff52ea6b56275a633cab0138ef8448fb5cffc9c1f283fb13520233b46d947")
)
foreach($f in $files){
  $rel=$f[0]; $sha=$f[1]; $dst=Join-Path $root ($rel -replace '/','\'); $part="$dst.part"
  New-Item -ItemType Directory -Force (Split-Path $dst) | Out-Null
  if(Test-Path $dst){ continue }
  for($i=0;$i -lt 500;$i++){
    curl.exe -sS -L --fail --speed-limit 1000 --speed-time 60 -C - -o $part "$base/$rel"
    if($LASTEXITCODE -eq 0){ break }
    Start-Sleep 10
  }
  if($sha -ne ""){
    $h=(Get-FileHash $part -Algorithm SHA256).Hash.ToLower()
    if($h -ne $sha){ "SHA MISMATCH $rel"; Remove-Item $part; continue }
  }
  Move-Item $part $dst -Force; "ok $rel $(Get-Date)"
}
"ALL DONE $(Get-Date)"

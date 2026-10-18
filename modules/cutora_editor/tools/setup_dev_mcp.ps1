# Create build/mcp-sdk-venv and install requirements-dev.lock (hashed).
# Never touches runtime/venv. CUTORA_Install.bat does not call this.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if (-not $Root) { $Root = Get-Location }
$Py = "C:\Program Files\Python310\python.exe"
$Venv = Join-Path $Root "build\mcp-sdk-venv"
$Lock = Join-Path $Root "requirements-dev.lock"
if (-not (Test-Path $Py)) {
    Write-Error "Python 3.10.11 not found at $Py"
    exit 1
}
if (-not (Test-Path $Lock)) {
    Write-Error "missing $Lock"
    exit 1
}
New-Item -ItemType Directory -Force -Path (Join-Path $Root "build") | Out-Null
if (-not (Test-Path (Join-Path $Venv "Scripts\python.exe"))) {
    Write-Host "Creating $Venv"
    & $Py -m venv $Venv
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
$VenvPy = Join-Path $Venv "Scripts\python.exe"
Write-Host "Installing hashed dev lock into mcp-sdk-venv (not product venv)"
& $VenvPy -m pip install --upgrade pip -i https://pypi.org/simple
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $VenvPy -m pip install --require-hashes -r $Lock -i https://pypi.org/simple
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "OK" $VenvPy
& $VenvPy -c "import mcp; print('mcp', getattr(mcp, '__version__', 'ok'))"
exit $LASTEXITCODE

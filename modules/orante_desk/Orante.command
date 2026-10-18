#!/bin/bash
# Orante 2.0 launcher (macOS). Double-click in Finder.
cd "$(dirname "$0")"
export PYTHONUTF8=1
if [ ! -x .venv/bin/python ]; then
  echo "[1/3] Creating virtual environment..."; python3 -m venv .venv || { echo "Need Python 3.10+ (brew install python)"; read -r; exit 1; }
fi
PY=.venv/bin/python
echo "[2/3] Checking dependencies..."
$PY -c "import fastapi, uvicorn, playwright, PIL, webview" 2>/dev/null || $PY -m pip install -q -r requirements.txt
echo "[3/3] Starting Orante..."
$PY -m orante "$@"

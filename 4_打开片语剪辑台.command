#!/bin/bash
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
xattr -cr "$DIR" 2>/dev/null || true
export PYTHONUTF8=1
export PATH="$DIR/runtime/bin:$DIR/runtime/ffmpeg/bin:$DIR/runtime/ffmpeg:/opt/homebrew/bin:/usr/local/bin:$PATH"

PY_EXE=""
for P in "$DIR/runtime/venv/bin/python3" "$DIR/runtime/python/bin/python3" /opt/homebrew/bin/python3.11 /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
    if command -v "$P" >/dev/null 2>&1 || [ -x "$P" ]; then
        PY_EXE="$P"
        break
    fi
done

exec "$PY_EXE" "$DIR/core/standalone_bootstrap.py" --app cutora "$@"

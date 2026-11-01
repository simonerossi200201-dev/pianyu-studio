#!/bin/bash
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
chmod +x "$DIR/启动片语工作室.command" 2>/dev/null || true
if [ -x "$DIR/runtime/venv/bin/python3" ]; then
    "$DIR/runtime/venv/bin/python3" "$DIR/install_studio.py" "$@"
elif [ -x "$DIR/runtime/python/bin/python3" ]; then
    "$DIR/runtime/python/bin/python3" "$DIR/install_studio.py" "$@"
else
    python3 "$DIR/install_studio.py" "$@"
fi

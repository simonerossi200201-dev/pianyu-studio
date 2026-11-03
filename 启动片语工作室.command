#!/bin/bash
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

xattr -cr "$DIR" 2>/dev/null || true
export PYTHONUTF8=1
export PATH="$DIR/runtime/bin:$DIR/runtime/ffmpeg/bin:$DIR/runtime/ffmpeg:/opt/homebrew/bin:/usr/local/bin:$PATH"

ARGS=()
for arg in "$@"; do
    case "$arg" in
        -psn_*) ;;
        *) ARGS+=("$arg") ;;
    esac
done

if [ -x "$DIR/runtime/venv/bin/python3" ]; then
    exec "$DIR/runtime/venv/bin/python3" "$DIR/studio_launcher.py" "${ARGS[@]}"
fi
exec bash "$DIR/1_启动片语工作室.command" "${ARGS[@]}"

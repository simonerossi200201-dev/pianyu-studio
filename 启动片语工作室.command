#!/bin/bash
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
if [ -x "$DIR/runtime/venv/bin/python3" ]; then
    exec "$DIR/runtime/venv/bin/python3" "$DIR/studio_launcher.py" "$@"
elif [ -x "$DIR/runtime/python/bin/python3" ]; then
    exec "$DIR/runtime/python/bin/python3" "$DIR/studio_launcher.py" "$@"
else
    exec python3 "$DIR/studio_launcher.py" "$@"
fi

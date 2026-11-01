#!/bin/bash
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

xattr -cr "$DIR" 2>/dev/null || true

if [ -x "$DIR/runtime/venv/bin/python3" ]; then
    exec "$DIR/runtime/venv/bin/python3" "$DIR/studio_launcher.py" "$@"
elif [ -x "$DIR/runtime/python/bin/python3" ]; then
    exec "$DIR/runtime/python/bin/python3" "$DIR/studio_launcher.py" "$@"
else
    echo "首次在 macOS 启动，正在自动引导运行《安装与体检.command》..."
    bash "$DIR/安装与体检.command"
    exec "$DIR/runtime/venv/bin/python3" "$DIR/studio_launcher.py" "$@"
fi

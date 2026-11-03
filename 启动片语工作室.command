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

# 每次启动前执行轻量级核心 Preflight，若虚拟环境未创建、目录被移动或缺少 pandas/PySide6 等核心包则自动调用《安装与体检.command》修复
if [ ! -x "$DIR/runtime/venv/bin/python3" ] || ! "$DIR/runtime/venv/bin/python3" "$DIR/core/preflight.py" --core >/dev/null 2>&1; then
    echo "检测到 macOS 运行环境尚未完成初始化或缺少核心组件，正在自动运行《安装与体检.command》修复..."
    bash "$DIR/安装与体检.command"
fi

exec "$DIR/runtime/venv/bin/python3" "$DIR/studio_launcher.py" "${ARGS[@]}"

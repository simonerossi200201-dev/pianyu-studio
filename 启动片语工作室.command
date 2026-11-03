#!/bin/bash
set -e

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

# 若 runtime/venv 尚未初始化，或核心依赖 (PySide6 / PyYAML / pandas / transformers / tokenizers) 缺失，自动调用《安装与体检.command》自愈
if [ ! -x "$DIR/runtime/venv/bin/python3" ] || ! "$DIR/runtime/venv/bin/python3" -c "import PySide6, yaml, pandas, transformers, tokenizers" >/dev/null 2>&1; then
    echo "检测到 macOS 运行环境尚未完成初始化或缺少核心组件，正在自动运行《安装与体检.command》..."
    bash "$DIR/安装与体检.command"
fi

exec "$DIR/runtime/venv/bin/python3" "$DIR/studio_launcher.py" "${ARGS[@]}"

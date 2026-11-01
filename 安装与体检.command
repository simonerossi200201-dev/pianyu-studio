#!/bin/bash
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

echo "================================================================"
echo "  《片语工作室》(Pianyu Studio v3.0) — macOS 全自动安装与体检"
echo "================================================================"

# 1. 移除 macOS Gatekeeper 隔离属性并赋予执行权限
xattr -cr "$DIR" 2>/dev/null || true
chmod +x "$DIR/启动片语工作室.command" "$DIR/安装与体检.command" 2>/dev/null || true
chmod -R +x "$DIR/runtime/python/bin" "$DIR/runtime/ffmpeg/bin" "$DIR/runtime/bin" 2>/dev/null || true

# 2. 自动处理 macOS Finder 解压同名文件夹产生「片语工作室 2」或未解压 01_模型包 的情况
if [ ! -d "$DIR/runtime/models/asr_small" ]; then
    PARENT_DIR="$(dirname "$DIR")"
    # 情况 A: 用户在 Finder 中分别双击了两个 zip，产生了 "片语工作室" 和 "片语工作室 2"
    for SIBLING in "$PARENT_DIR/片语工作室" "$PARENT_DIR/片语工作室 2" "$PARENT_DIR/片语工作室 3"; do
        if [ "$SIBLING" != "$DIR" ] && [ -d "$SIBLING/runtime/models" ]; then
            echo "[1/4] 检测到 Finder 分离解压目录 ($SIBLING)，正在自动合并 AI 模型与运行环境..."
            mkdir -p "$DIR/runtime"
            rsync -a "$SIBLING/runtime/" "$DIR/runtime/"
            chmod -R +x "$DIR/runtime/python/bin" "$DIR/runtime/ffmpeg/bin" "$DIR/runtime/bin" 2>/dev/null || true
            echo "  -> AI 模型库合并完成！"
            break
        fi
    done
fi

if [ ! -d "$DIR/runtime/models/asr_small" ]; then
    PARENT_DIR="$(dirname "$DIR")"
    # 情况 B: 同级目录或 ~/Downloads 下存在 01_【模型与环境底座包...】.zip，自动流式解压合并到当前目录
    MODEL_ZIP=""
    for CANDIDATE in "$PARENT_DIR"/01_*模型*.zip "$HOME/Downloads"/01_*模型*.zip; do
        if [ -f "$CANDIDATE" ]; then
            MODEL_ZIP="$CANDIDATE"
            break
        fi
    done
    if [ -n "$MODEL_ZIP" ]; then
        echo "[1/4] 发现模型底座包: $MODEL_ZIP"
        echo "  -> 正在使用 macOS 原生 ditto 引擎自动合并模型与运行环境..."
        TMP_EXT="$PARENT_DIR/.pianyu_extract_$$"
        mkdir -p "$TMP_EXT"
        ditto -x -k "$MODEL_ZIP" "$TMP_EXT"
        if [ -d "$TMP_EXT/片语工作室/runtime" ]; then
            mkdir -p "$DIR/runtime"
            rsync -a "$TMP_EXT/片语工作室/runtime/" "$DIR/runtime/"
        elif [ -d "$TMP_EXT/runtime" ]; then
            mkdir -p "$DIR/runtime"
            rsync -a "$TMP_EXT/runtime/" "$DIR/runtime/"
        fi
        rm -rf "$TMP_EXT"
        chmod -R +x "$DIR/runtime/python/bin" "$DIR/runtime/ffmpeg/bin" "$DIR/runtime/bin" 2>/dev/null || true
        echo "  -> AI 模型与运行环境合并完成！"
    fi
fi

# 3. 检测内置 macOS Apple Silicon (ARM64) 原生 FFmpeg (支持 VideoToolbox 硬编解码)
if [ ! -x "$DIR/runtime/ffmpeg/bin/ffmpeg" ]; then
    if command -v ffmpeg >/dev/null 2>&1; then
        mkdir -p "$DIR/runtime/ffmpeg/bin"
        ln -sf "$(command -v ffmpeg)" "$DIR/runtime/ffmpeg/bin/ffmpeg"
        command -v ffprobe >/dev/null 2>&1 && ln -sf "$(command -v ffprobe)" "$DIR/runtime/ffmpeg/bin/ffprobe"
    fi
fi

# 4. 检测并初始化 macOS Apple Silicon 原生 Python 虚拟环境 (runtime/venv/bin/python3)
if [ ! -x "$DIR/runtime/venv/bin/python3" ]; then
    echo "[2/4] 正在初始化 macOS Apple Silicon 原生 Python 3.11 虚拟环境 (runtime/venv)..."
    if [ -d "$DIR/runtime/venv/Scripts" ] && [ ! -d "$DIR/runtime/venv/bin" ]; then
        rm -rf "$DIR/runtime/venv"
    fi

    BASE_PY=""
    if [ -x "$DIR/runtime/python/bin/python3.11" ]; then
        BASE_PY="$DIR/runtime/python/bin/python3.11"
    elif [ -x "$DIR/runtime/python/bin/python3" ]; then
        BASE_PY="$DIR/runtime/python/bin/python3"
    else
        for P in /opt/homebrew/bin/python3.11 /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
            if command -v "$P" >/dev/null 2>&1; then
                BASE_PY="$(command -v "$P")"
                break
            fi
        done
    fi

    if [ -x "$DIR/runtime/bin/uv" ] && [ -n "$BASE_PY" ]; then
        "$DIR/runtime/bin/uv" venv "$DIR/runtime/venv" --python "$BASE_PY" --seed
        echo "[3/4] 正在通过内置 uv 极速安装 Apple Silicon AI 与界面依赖库..."
        "$DIR/runtime/bin/uv" pip install --python "$DIR/runtime/venv/bin/python3" \
            PySide6 fastapi uvicorn httpx pydantic openpyxl cryptography \
            numpy Pillow opencv-python-headless psutil soundfile scipy \
            faster-whisper torch torchvision torchaudio transformers \
            mediapipe demucs playwright || true
        if [ "$(uname -m)" = "arm64" ]; then
            "$DIR/runtime/bin/uv" pip install --python "$DIR/runtime/venv/bin/python3" mlx-whisper || true
        fi
    else
        "$BASE_PY" -m venv "$DIR/runtime/venv"
        "$DIR/runtime/venv/bin/python3" -m pip install --upgrade pip wheel setuptools
        "$DIR/runtime/venv/bin/python3" -m pip install \
            PySide6 fastapi uvicorn httpx pydantic openpyxl cryptography \
            numpy Pillow opencv-python-headless psutil soundfile scipy \
            faster-whisper torch torchvision torchaudio transformers \
            mediapipe demucs playwright || true
        if [ "$(uname -m)" = "arm64" ]; then
            "$DIR/runtime/venv/bin/python3" -m pip install mlx-whisper || true
        fi
    fi
fi

# 5. 执行全自动安装、Finder 右键菜单注册、桌面 .app 创建与黑匣子体检
echo "[4/4] 正在执行《片语工作室》硬件自适应体检与桌面/Finder 集成..."
export PATH="$DIR/runtime/bin:$DIR/runtime/ffmpeg/bin:$PATH"
"$DIR/runtime/venv/bin/python3" "$DIR/install_studio.py" "$@"

echo ""
echo "✅ macOS 安装与体检已完成！您现在可以直接双击桌面的《片语工作室.app》或本目录的《启动片语工作室.command》启动。"

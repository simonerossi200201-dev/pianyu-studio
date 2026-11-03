#!/bin/bash
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

mkdir -p "$DIR/data/logs" 2>/dev/null || true
INSTALL_LOG="$DIR/data/logs/macos_install.log"
echo "=== [$(date '+%Y-%m-%d %H:%M:%S')] macOS 安装与体检启动 ===" >> "$INSTALL_LOG"

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
    for SIBLING in "$PARENT_DIR/片语工作室" "$PARENT_DIR/片语工作室 2" "$PARENT_DIR/片语工作室 3"; do
        if [ "$SIBLING" != "$DIR" ] && [ -d "$SIBLING/runtime/models" ]; then
            echo "[1/5] 检测到 Finder 分离解压目录 ($SIBLING)，正在自动合并 AI 模型与运行环境..."
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
    MODEL_ZIP=""
    for CANDIDATE in "$PARENT_DIR"/01_*模型*.zip "$HOME/Downloads"/01_*模型*.zip; do
        if [ -f "$CANDIDATE" ]; then
            MODEL_ZIP="$CANDIDATE"
            break
        fi
    done
    if [ -n "$MODEL_ZIP" ]; then
        echo "[1/5] 发现模型底座包: $MODEL_ZIP"
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

# 3. 统一内置 macOS Apple Silicon (ARM64) FFmpeg / FFprobe 路径
mkdir -p "$DIR/runtime/ffmpeg/bin" "$DIR/runtime/bin"
if [ -x "$DIR/runtime/ffmpeg/bin/ffmpeg" ] && [ ! -x "$DIR/runtime/bin/ffmpeg" ]; then
    ln -sf "$DIR/runtime/ffmpeg/bin/ffmpeg" "$DIR/runtime/bin/ffmpeg" 2>/dev/null || cp -f "$DIR/runtime/ffmpeg/bin/ffmpeg" "$DIR/runtime/bin/ffmpeg"
fi
if [ -x "$DIR/runtime/ffmpeg/bin/ffprobe" ] && [ ! -x "$DIR/runtime/bin/ffprobe" ]; then
    ln -sf "$DIR/runtime/ffmpeg/bin/ffprobe" "$DIR/runtime/bin/ffprobe" 2>/dev/null || cp -f "$DIR/runtime/ffmpeg/bin/ffprobe" "$DIR/runtime/bin/ffprobe"
fi
if [ -x "$DIR/runtime/bin/ffmpeg" ] && [ ! -x "$DIR/runtime/ffmpeg/bin/ffmpeg" ]; then
    ln -sf "$DIR/runtime/bin/ffmpeg" "$DIR/runtime/ffmpeg/bin/ffmpeg" 2>/dev/null || cp -f "$DIR/runtime/bin/ffmpeg" "$DIR/runtime/ffmpeg/bin/ffmpeg"
fi
if [ -x "$DIR/runtime/bin/ffprobe" ] && [ ! -x "$DIR/runtime/ffmpeg/bin/ffprobe" ]; then
    ln -sf "$DIR/runtime/bin/ffprobe" "$DIR/runtime/ffmpeg/bin/ffprobe" 2>/dev/null || cp -f "$DIR/runtime/bin/ffprobe" "$DIR/runtime/ffmpeg/bin/ffprobe"
fi
if [ ! -x "$DIR/runtime/ffmpeg/bin/ffmpeg" ] && command -v ffmpeg >/dev/null 2>&1; then
    ln -sf "$(command -v ffmpeg)" "$DIR/runtime/ffmpeg/bin/ffmpeg"
    command -v ffprobe >/dev/null 2>&1 && ln -sf "$(command -v ffprobe)" "$DIR/runtime/ffmpeg/bin/ffprobe"
fi

# 4. 定位 ARM64 基础 Python 3.11 解释器并确保 runtime/venv 可执行
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

if [ -z "$BASE_PY" ]; then
    echo "❌ 错误：未找到可用的 Python 3 解释器，请确认 runtime/python 完整或已安装 Python 3.11。" | tee -a "$INSTALL_LOG"
    exit 1
fi

# 若移动了目录导致 runtime/venv/bin/python3 断链，先修复软链或重建 venv
if [ -L "$DIR/runtime/venv/bin/python3" ] && [ ! -e "$DIR/runtime/venv/bin/python3" ]; then
    echo "检测到安装目录发生移动，正在自动重定位 runtime/venv 解释器链接..." | tee -a "$INSTALL_LOG"
    ln -sf "$BASE_PY" "$DIR/runtime/venv/bin/python"
    ln -sf "$BASE_PY" "$DIR/runtime/venv/bin/python3"
    ln -sf "$BASE_PY" "$DIR/runtime/venv/bin/python3.11"
fi

if [ ! -x "$DIR/runtime/venv/bin/python3" ]; then
    echo "[2/5] 正在初始化 macOS Apple Silicon 原生 Python 虚拟环境 (runtime/venv)..." | tee -a "$INSTALL_LOG"
    if [ -x "$DIR/runtime/bin/uv" ]; then
        "$DIR/runtime/bin/uv" venv "$DIR/runtime/venv" --python "$BASE_PY" --seed >> "$INSTALL_LOG" 2>&1
    else
        "$BASE_PY" -m venv "$DIR/runtime/venv" >> "$INSTALL_LOG" 2>&1
    fi
fi

VENV_PY="$DIR/runtime/venv/bin/python3"
export PYTHONUTF8=1
export PATH="$DIR/runtime/bin:$DIR/runtime/ffmpeg/bin:$DIR/runtime/ffmpeg:$PATH"

# 5. 每次执行体检均运行独立 Preflight（不以解释器文件存在代替健康度检查）
echo "[3/5] 正在执行核心依赖与媒体环境预检 (Preflight)..."
if ! "$VENV_PY" "$DIR/core/preflight.py" --core >> "$INSTALL_LOG" 2>&1; then
    echo "  -> 检测到缺失核心依赖（如 pandas / PySide6 / PyYAML 等），正在根据 requirements-macos-arm64.lock 自动安装补齐..." | tee -a "$INSTALL_LOG"
    if [ -x "$DIR/runtime/bin/uv" ]; then
        "$DIR/runtime/bin/uv" pip install --python "$VENV_PY" -r "$DIR/requirements-macos-arm64.lock" 2>&1 | tee -a "$INSTALL_LOG"
    else
        "$VENV_PY" -m pip install --upgrade pip wheel setuptools 2>&1 | tee -a "$INSTALL_LOG"
        "$VENV_PY" -m pip install -r "$DIR/requirements-macos-arm64.lock" 2>&1 | tee -a "$INSTALL_LOG"
    fi
    # 安装后再次严格执行核心 Preflight，不通过则明确报错退出（严禁吞错报成功）
    if ! "$VENV_PY" "$DIR/core/preflight.py" --core 2>&1 | tee -a "$INSTALL_LOG"; then
        echo ""
        echo "❌ 核心依赖预检仍未通过，请查看详细安装日志: $INSTALL_LOG"
        exit 1
    fi
else
    "$VENV_PY" "$DIR/core/preflight.py" --core
fi

# 6. 可选加速与扩展模块独立探测安装（失败仅记录降级，绝不污染核心面板环境）
echo "[4/5] 正在检查可选扩展模块 (pywebview / playwright / mlx-whisper / mediapipe / demucs)..."
for OPT_PKG in pywebview playwright mediapipe demucs; do
    if ! "$VENV_PY" -c "import ${OPT_PKG/-/_}" >/dev/null 2>&1; then
        if [ -x "$DIR/runtime/bin/uv" ]; then
            "$DIR/runtime/bin/uv" pip install --python "$VENV_PY" "$OPT_PKG" >> "$INSTALL_LOG" 2>&1 || true
        else
            "$VENV_PY" -m pip install "$OPT_PKG" >> "$INSTALL_LOG" 2>&1 || true
        fi
    fi
done
if [ "$(uname -m)" = "arm64" ] && ! "$VENV_PY" -c "import mlx_whisper" >/dev/null 2>&1; then
    if [ -x "$DIR/runtime/bin/uv" ]; then
        "$DIR/runtime/bin/uv" pip install --python "$VENV_PY" mlx-whisper >> "$INSTALL_LOG" 2>&1 || true
    else
        "$VENV_PY" -m pip install mlx-whisper >> "$INSTALL_LOG" 2>&1 || true
    fi
fi
"$VENV_PY" "$DIR/core/preflight.py" --optional-report | tee -a "$INSTALL_LOG"

# 7. 执行全自动安装、Finder 右键菜单注册、桌面 .app 创建与黑匣子体检
echo "[5/5] 正在执行《片语工作室》硬件自适应体检与桌面/Finder 集成..."
"$VENV_PY" "$DIR/install_studio.py" "$@" 2>&1 | tee -a "$INSTALL_LOG"

echo ""
echo "✅ macOS 安装与核心预检全部通过！您现在可以直接双击桌面的《片语工作室.app》或本目录的《启动片语工作室.command》启动。"

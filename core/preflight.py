# -*- coding: utf-8 -*-
"""片语工作室独立启动前环境体检器 (core/preflight.py)。

特点：
1. 顶层仅依赖 Python 标准库，即使缺失 pandas / PySide6 / torch 也能正常执行并输出结构化诊断；
2. 区分四个层级：
   - 桌面核心 (Desktop Core): PySide6, PyYAML, pandas, openpyxl, cryptography
   - 媒体核心 (Media Core): numpy, Pillow, cv2, soundfile, scipy, ffmpeg, ffprobe
   - AI 基础 (AI Foundation): ctranslate2, faster_whisper, torch, torchvision, torchaudio, transformers, tokenizers, safetensors
   - 可选模块 (Optional Modules): pywebview, mlx_whisper, mediapipe, demucs, cutora_rust_core
3. 支持 `--core`（核心体检，缺一即返回非零退出码）、`--optional-report`（可选模块与降级说明）、`--json`。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

LTX_ROOT = Path(__file__).resolve().parent.parent

DESKTOP_CORE_PKGS = [
    ("PySide6", "PySide6"),
    ("yaml", "PyYAML"),
    ("pandas", "pandas"),
    ("fastapi", "fastapi"),
    ("uvicorn", "uvicorn"),
    ("httpx", "httpx"),
    ("pydantic", "pydantic"),
    ("openpyxl", "openpyxl"),
    ("cryptography", "cryptography"),
]

MEDIA_CORE_PKGS = [
    ("numpy", "numpy"),
    ("PIL", "Pillow"),
    ("cv2", "opencv-python-headless"),
    ("soundfile", "soundfile"),
    ("scipy", "scipy"),
    ("psutil", "psutil"),
]

AI_CORE_PKGS = [
    ("ctranslate2", "ctranslate2"),
    ("faster_whisper", "faster-whisper"),
    ("torch", "torch"),
    ("torchvision", "torchvision"),
    ("torchaudio", "torchaudio"),
    ("transformers", "transformers"),
    ("tokenizers", "tokenizers"),
    ("safetensors", "safetensors"),
]

OPTIONAL_PKGS = [
    ("webview", "pywebview", "Orante 独立桌面窗口外壳（未安装时自动降级为系统默认浏览器打开）"),
    ("mlx_whisper", "mlx-whisper", "Apple Silicon Metal 原生 Whisper 加速（未安装或沙箱受限时自动使用 CTranslate2 ARM64 CPU int8）"),
    ("mediapipe", "mediapipe", "人脸关键点检测辅助模块（未安装时使用 OpenCV/基础检测后备）"),
    ("demucs", "demucs", "AI 人声/伴奏分离扩展模块（按需使用）"),
    ("cutora_rust_core", "cutora_rust_core", "Rust SIMD 原生加速扩展（未安装时自动使用标准 Python/NumPy 后备引擎）"),
]


def _check_spec(mod_name: str) -> tuple[bool, str]:
    try:
        spec = importlib.util.find_spec(mod_name)
        if spec is None:
            return False, f"Module '{mod_name}' not found"
        return True, getattr(spec, "origin", "") or "found"
    except Exception as exc:
        return False, str(exc)


def _resolve_media_bin(name: str) -> tuple[bool, str]:
    exe = f"{name}.exe" if os.name == "nt" else name
    candidates = [
        LTX_ROOT / "runtime" / "bin" / exe,
        LTX_ROOT / "runtime" / "ffmpeg" / "bin" / exe,
        LTX_ROOT / "runtime" / "ffmpeg" / exe,
    ]
    for c in candidates:
        if c.is_file() and (os.name == "nt" or os.access(c, os.X_OK)):
            return True, str(c)
    found = shutil.which(name)
    if found:
        return True, found
    return False, f"{name} not found in runtime/bin, runtime/ffmpeg/bin, or PATH"


def run_preflight() -> dict[str, Any]:
    py_ver = platform.python_version()
    machine = platform.machine().lower()
    ver_parts = sys.version_info[:2]
    py_ok = ver_parts >= (3, 10)

    missing_core: list[dict[str, str]] = []
    passed_core: list[str] = []

    for tier_label, pkg_list in (
        ("desktop_core", DESKTOP_CORE_PKGS),
        ("media_core", MEDIA_CORE_PKGS),
        ("ai_core", AI_CORE_PKGS),
    ):
        for mod_name, pip_name in pkg_list:
            ok, info = _check_spec(mod_name)
            if ok:
                passed_core.append(pip_name)
            else:
                missing_core.append({
                    "tier": tier_label,
                    "import_name": mod_name,
                    "pip_name": pip_name,
                    "error": info,
                })

    ffmpeg_ok, ffmpeg_path = _resolve_media_bin("ffmpeg")
    ffprobe_ok, ffprobe_path = _resolve_media_bin("ffprobe")
    if not ffmpeg_ok:
        missing_core.append({"tier": "media_core", "import_name": "ffmpeg", "pip_name": "ffmpeg", "error": ffmpeg_path})
    if not ffprobe_ok:
        missing_core.append({"tier": "media_core", "import_name": "ffprobe", "pip_name": "ffprobe", "error": ffprobe_path})

    # Essential base models check
    models_root = LTX_ROOT / "runtime" / "models"
    essential_models = {
        "asr_small": (models_root / "asr_small" / "model.bin").is_file() or (models_root / "asr_small").is_dir(),
        "align_wav2vec2": (models_root / "align_wav2vec2").is_dir(),
    }

    optional_status: list[dict[str, Any]] = []
    for mod_name, pip_name, desc in OPTIONAL_PKGS:
        if mod_name == "cutora_rust_core":
            ok1, info1 = _check_spec("cutora_rust_core")
            ok2, info2 = _check_spec("cutora_core")
            ok = ok1 or ok2
            info = info1 if ok1 else info2
        else:
            ok, info = _check_spec(mod_name)
        optional_status.append({
            "import_name": mod_name,
            "pip_name": pip_name,
            "available": ok,
            "detail": info if ok else "未安装（使用标准后备方案）",
            "fallback_note": desc,
        })

    core_ok = py_ok and len(missing_core) == 0
    repair_cmd = "bash 安装与体检.command" if sys.platform == "darwin" else "安装与体检.bat"

    return {
        "ok": core_ok,
        "platform": sys.platform,
        "machine": machine,
        "python_executable": sys.executable,
        "python_version": py_ver,
        "python_ok": py_ok,
        "ffmpeg": {"ok": ffmpeg_ok, "path": ffmpeg_path},
        "ffprobe": {"ok": ffprobe_ok, "path": ffprobe_path},
        "essential_models": essential_models,
        "passed_core_count": len(passed_core),
        "missing_core": missing_core,
        "optional_modules": optional_status,
        "repair_hint": f"请运行根目录下的 `{repair_cmd}` 自动补齐缺失组件。" if not core_ok else "",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="片语工作室环境预检工具 (Preflight)")
    parser.add_argument("--core", action="store_true", help="检查桌面、媒体与 AI 核心依赖，缺失则返回退出码 1")
    parser.add_argument("--optional-report", action="store_true", help="输出可选模块状态与降级说明（不因可选模块缺失而失败）")
    parser.add_argument("--json", action="store_true", help="以 JSON 格式输出完整报告")
    args = parser.parse_args(argv)

    rep = run_preflight()
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0 if (rep["ok"] or args.optional_report) else 1

    if args.optional_report:
        print("  [可选加速与扩展模块状态]:")
        for item in rep["optional_modules"]:
            badge = "🟢 已启用" if item["available"] else "⚪ 标准后备"
            print(f"    · {badge} {item['pip_name']}: {item['fallback_note']}")
        return 0

    if rep["ok"]:
        print(
            f"  [Preflight 通过] Python {rep['python_version']} ({rep['machine']}) · "
            f"{rep['passed_core_count']} 项核心依赖与 FFmpeg 全部就绪。"
        )
        return 0

    print("=" * 64)
    print("  ❌ [Preflight 未通过] 检测到核心运行组件缺失：")
    for m in rep["missing_core"]:
        print(f"    - [{m['tier']}] {m['pip_name']} ({m['import_name']}): {m['error']}")
    print(f"  修复方式: {rep['repair_hint']}")
    print("=" * 64)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

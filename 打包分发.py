"""片语工作室 (D:\\0_LTX) 安全脱敏与乐高式模块化打包分发器 (打包分发.py)。

设计哲学（结合特斯拉车型分级 + 乐高模块化插件包 DLC 架构）：
1. 【开发机零影响】：在你自己的电脑 (`D:\\0_LTX`) 上，所有 126 GB 模型与功能完整保留、一体维护，没有任何删减。
2. 【内部分发 25GB 标准核心版 (`--edition base-25gb`)】：
   专门面向低配笔记本（i7 + 32GB 内存 + 无独显 + 小硬盘 / Apple Silicon Mac）同事分发。
   自动打包：
   - 完整核心程序（CUTORA 剪辑台、Orante 2.0 制片台、右键表格工具、7870 本地网关）
   - 便携 Python 环境 (`runtime/python` + `runtime/venv`) 与 `runtime/bin` (FFmpeg/rclone/mimalloc)
   - 四大日常高频核心模型（自动字幕 Faster-Whisper + Wav2Vec2、智能拼接、SAM 2.1 + RMBG-2.0 视频/图像抠像、
     Demucs/Resemble 立体声与降噪、DPT-Hybrid 人像提亮修复），总大小约 21~25 GB！
3. 【5 大可选乐高扩展包 (`--edition pack --pack-id <id>` 或 `--edition all-packs`)】：
   将剩余 ~100 GB 的重型外置模型按功能独立封装为 `.ltxpack` 插件包（内含 `pack_receipt.json` 清单）：
   - `pack-ltx25-video` (~39.2 GB): LTX-2.5 22B 本地视频生成包
   - `pack-ltx25-vfx-loras` (~7.6 GB): LTX-2.5 IC-LoRA 7合1 视频精修特效包
   - `pack-flux2-image` (~7.85 GB): FLUX.2 [klein] 4B 生图与改图包
   - `pack-audio-tts-bgm` (~25.6 GB): AI 语音克隆与配乐生成包
   - `pack-pro-vision-multilang` (~20.8 GB): 4x 超分、TAPNext++ 追踪与多语种对齐包
   同事需要哪个功能，只需下载对应的 `.ltxpack` 放入 `packs/` 目录或点击「一键安装扩展包」即可无缝拼装！
4. 【零快捷方式 / 零隐私泄露 / 零 MiniMax H3】：严格执行路径与内容安全扫描。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import py_compile
import re
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

LTX_ROOT = Path(__file__).resolve().parent
DIST_DIR = LTX_ROOT / "dist_packages"
PACKS_MANIFEST_PATH = LTX_ROOT / "config" / "packs_manifest.json"

FORBIDDEN_PATH_KEYWORDS = (
    "minimax_h3",
    "qwen3vl_32b_minimax",
    "data/chrome",
    "data\\chrome",
    "token.json",
    "client_secret",
    "rclone.conf",
    "_backups_",
    "dist_packages",
    ".git",
    "__pycache__",
)

FORBIDDEN_CONTENT_PATTERNS = (
    "ya29.",
    "AIzaSy",
    "api.deepseek.com",
    "modelscope.cn",
    "hf-mirror.com",
)
OAUTH_REFRESH_RE = re.compile(r"1//0[0-9A-Za-z\-_]{20,}")

CORE_SHELL_AND_CUTORA_ROOTS = [
    "config",
    "core",
    "licenses",
    "resources/brand",
    "片语工作室 me.png",
    "片语工作室_安装与使用说明书.pdf",
    "runtime/venv/Lib/site-packages/cutora_rust_core",
    "modules/table_tool/app/表格工具.py",
    "modules/table_tool/install.py",
    "modules/table_tool/uninstall.py",
    "modules/cutora_editor/cutora",
    "modules/cutora_editor/rust_core/Cargo.toml",
    "modules/cutora_editor/rust_core/src",
    "modules/cutora_editor/resources",
    "modules/cutora_editor/vendor",
    "modules/orante_desk/orante",
    "modules/orante_desk/ui",
    "modules/orante_desk/extension",
    "modules/studio_gateway",
    "studio_launcher.py",
    "install_studio.py",
    "打包分发.py",
    "片语工作室.bat",
    "片语工作室.vbs",
    "代码热更新_双击同步最新功能.bat",
    "创建桌面快捷方式.bat",
    "一键打包分发.bat",
    "启动本地算力网关.bat",
    "打开表格工具.bat",
    "安装与体检.bat",
    "启动片语工作室.command",
    "安装与体检.command",
    "收集卡死诊断信息.bat",
    "收集卡死诊断信息.ps1",
    "README.md",
    "安装与扩展包使用指南.md",
    "SECURITY_AUDIT_REPORT.md",
    "MODEL_LICENSE_COMPLIANCE.md",
    "requirements.txt",
    ".env.example",
]


BASE_APP_ROOTS = list(CORE_SHELL_AND_CUTORA_ROOTS)

COMFY_EXCLUDED_TOP_DIRS = {"models", "output", "input", "temp", ".git", "__pycache__", "user"}


def load_manifest() -> dict[str, Any]:
    if not PACKS_MANIFEST_PATH.is_file():
        return {"base_edition": {}, "packs": []}
    return json.loads(PACKS_MANIFEST_PATH.read_text(encoding="utf-8"))


def is_path_allowed(rel_path: str) -> bool:
    low = rel_path.lower().replace("\\", "/")
    for bad in FORBIDDEN_PATH_KEYWORDS:
        if bad.lower() in low:
            return False
    return True


def count_reparse_points() -> list[str]:
    """确保 D:\\0_LTX 分发路径内没有任何文件夹快捷方式 (Directory Junction / ReparsePoint)。"""
    hits: list[str] = []
    for root, dirs, _files in os.walk(LTX_ROOT):
        for d in list(dirs):
            p = Path(root) / d
            rel = str(p.relative_to(LTX_ROOT))
            if not is_path_allowed(rel):
                dirs.remove(d)
                continue
            try:
                if getattr(os.lstat(p), "st_file_attributes", 0) & 0x400:
                    hits.append(str(p))
            except Exception:
                pass
    return hits


def _dir_size_gb(rel_paths: list[str]) -> float:
    total = 0
    for rel in rel_paths:
        target = LTX_ROOT / rel
        if target.is_file():
            total += target.stat().st_size
        elif target.is_dir():
            for r, _d, files in os.walk(target):
                for fn in files:
                    fp = Path(r) / fn
                    try:
                        total += fp.stat().st_size
                    except OSError:
                        pass
    return round(total / (1024 ** 3), 2)


def build_pack_plan() -> dict[str, Any]:
    """生成完整的版本与乐高插件包拆分报告（不实际拷贝百GB权重，瞬间输出精算结果）。"""
    manifest = load_manifest()
    base_cfg = manifest.get("base_edition", {})
    base_model_paths = base_cfg.get("included_model_paths", [])
    base_models_gb = _dir_size_gb(base_model_paths)
    app_and_py_gb = _dir_size_gb(BASE_APP_ROOTS + ["runtime/python", "runtime/venv"])

    packs_report = []
    for p in manifest.get("packs", []):
        actual_gb = _dir_size_gb(p.get("paths", []))
        if actual_gb == 0.0 and p.get("external_fallback_paths"):
            actual_gb = float(p.get("approx_size_gb", 4.5))
        packs_report.append({
            "pack_id": p["id"],
            "name": p["name"],
            "short_name": p.get("short_name", p["id"]),
            "category": p.get("category", "model"),
            "tier": p.get("tier", 2),
            "min_vram_gb": p.get("min_vram_gb", 0.0),
            "cpu_supported": p.get("cpu_supported", False),
            "actual_size_gb": actual_gb if actual_gb > 0 else float(p.get("approx_size_gb", 0.01)),
            "archive_name": f"{p['id']}.ltxpack",
            "paths": p.get("paths", []),
        })

    return {
        "ok": True,
        "base_edition": {
            "name": base_cfg.get("name", "标准核心版 (Base 25GB)"),
            "app_and_runtime_gb": app_and_py_gb,
            "core_models_gb": base_models_gb,
            "total_uncompressed_gb": round(app_and_py_gb + base_models_gb, 2),
            "included_model_paths": base_model_paths,
        },
        "modular_packs": packs_report,
        "total_optional_packs_gb": round(sum(x["actual_size_gb"] for x in packs_report), 2),
    }


CLOUD_DRIVE_PACK_FILENAMES = {
    "pack-app-orante": "02_【可选软件模块_15MB】Orante2.0制片台_pack-app-orante.ltxpack",
    "pack-engine-comfyui": "03_【可选推理引擎_4.5GB】ComfyUI本地引擎与CUDA环境_pack-engine-comfyui.ltxpack",
    "pack-ltx25-video": "04_【可选视频生成模型_39.2GB】LTX-2.5_22B_本地视频生成大模型_pack-ltx25-video.ltxpack",
    "pack-ltx25-vfx-loras": "05_【可选视频特效模型_7.6GB】LTX-2.5_IC-LoRA_7合1视频精修特效_pack-ltx25-vfx-loras.ltxpack",
    "pack-flux2-image": "06_【可选生图改图模型_7.9GB】FLUX.2_klein_4B_极速生图与改图_pack-flux2-image.ltxpack",
    "pack-audio-tts-bgm": "07_【可选语音配乐模型_25.6GB】AI语音克隆与配乐生成_pack-audio-tts-bgm.ltxpack",
    "pack-pro-vision-multilang": "08_【可选超分追踪多语种_24.7GB】4x超分_TAPNext追踪_多国语对齐_pack-pro-vision-multilang.ltxpack",
}


def build_single_ltxpack(pack_id: str) -> dict[str, Any]:
    """将指定的软件模块、ComfyUI 推理引擎或 AI 模型扩展包单独封装为 `.ltxpack` 文件，附带 `pack_receipt.json` 元数据。"""
    manifest = load_manifest()
    pack_def = next((p for p in manifest.get("packs", []) if p["id"] == pack_id), None)
    if not pack_def:
        raise ValueError(f"未在 packs_manifest.json 中找到扩展包 ID: {pack_id}")

    DIST_DIR.mkdir(parents=True, exist_ok=True)
    fname = CLOUD_DRIVE_PACK_FILENAMES.get(pack_id, f"{pack_id}.ltxpack")
    out_path = DIST_DIR / fname
    files_added = 0
    total_bytes = 0
    t0 = time.time()

    is_app_pack = pack_def.get("category") == "app_module"
    comp_mode = zipfile.ZIP_DEFLATED if is_app_pack else zipfile.ZIP_STORED

    print(f"[打包开始] 正在生成扩展包: {fname} ...", flush=True)
    with zipfile.ZipFile(out_path, "w", compression=comp_mode, allowZip64=True) as zf:
        if pack_id == "pack-engine-comfyui" and not (LTX_ROOT / "runtime" / "comfyui" / "main.py").is_file():
            ext_comfy = Path(r"D:\0_LLM\ComfyUI")
            if not ext_comfy.is_dir():
                raise FileNotFoundError("未找到 D:\\0_LLM\\ComfyUI 或 D:\\0_LTX\\runtime\\comfyui")
            for dirpath, dirnames, filenames in os.walk(ext_comfy):
                rel_from_comfy = Path(dirpath).relative_to(ext_comfy)
                if rel_from_comfy.parts and rel_from_comfy.parts[0] in COMFY_EXCLUDED_TOP_DIRS:
                    dirnames[:] = []
                    continue
                dirnames[:] = [
                    d for d in dirnames
                    if is_path_allowed(d) and (rel_from_comfy.parts or d not in COMFY_EXCLUDED_TOP_DIRS)
                ]
                for fn in filenames:
                    p = Path(dirpath) / fn
                    if p.suffix in (".pyc", ".pyo", ".log"):
                        continue
                    arc_rel = "runtime/comfyui/" + str(p.relative_to(ext_comfy)).replace("\\", "/")
                    if not is_path_allowed(arc_rel):
                        continue
                    if arc_rel == "runtime/comfyui/venv/pyvenv.cfg":
                        cfg_txt = (
                            "home = ..\\python312\n"
                            "implementation = CPython\n"
                            "version_info = 3.12.7\n"
                            "include-system-site-packages = false\n"
                        )
                        zf.writestr(arc_rel, cfg_txt)
                    elif arc_rel == "runtime/comfyui/venv/Scripts/python.exe" and (LTX_ROOT / "runtime/bin/portable_venv_python.exe").is_file():
                        zf.write(LTX_ROOT / "runtime/bin/portable_venv_python.exe", arc_rel)
                    elif arc_rel == "runtime/comfyui/venv/Scripts/pythonw.exe" and (LTX_ROOT / "runtime/bin/portable_venv_pythonw.exe").is_file():
                        zf.write(LTX_ROOT / "runtime/bin/portable_venv_pythonw.exe", arc_rel)
                    else:
                        zf.write(p, arc_rel)
                    files_added += 1
                    total_bytes += p.stat().st_size

            uv_py312 = Path(r"C:\Users\Timco\AppData\Roaming\uv\python\cpython-3.12.7-windows-x86_64-none")
            if uv_py312.is_dir():
                for dirpath, dirnames, filenames in os.walk(uv_py312):
                    dirnames[:] = [d for d in dirnames if "__pycache__" not in d]
                    for fn in filenames:
                        p = Path(dirpath) / fn
                        if p.suffix in (".pyc", ".pyo", ".pdb"):
                            continue
                        arc_rel = "runtime/comfyui/python312/" + str(p.relative_to(uv_py312)).replace("\\", "/")
                        zf.write(p, arc_rel)
                        files_added += 1
                        total_bytes += p.stat().st_size
        else:
            for rel_root in pack_def.get("paths", []):
                target = LTX_ROOT / rel_root
                if target.is_file():
                    rel = str(target.relative_to(LTX_ROOT)).replace("\\", "/")
                    if is_path_allowed(rel):
                        zf.write(target, rel)
                        files_added += 1
                        total_bytes += target.stat().st_size
                elif target.is_dir():
                    for dirpath, dirnames, filenames in os.walk(target):
                        dirnames[:] = [d for d in dirnames if is_path_allowed(d)]
                        for fn in filenames:
                            p = Path(dirpath) / fn
                            if p.suffix in (".pyc", ".pyo"):
                                continue
                            rel = str(p.relative_to(LTX_ROOT)).replace("\\", "/")
                            if not is_path_allowed(rel):
                                continue
                            zf.write(p, rel)
                            files_added += 1
                            total_bytes += p.stat().st_size

        receipt = {
            "pack_id": pack_id,
            "name": pack_def.get("name", pack_id),
            "short_name": pack_def.get("short_name", pack_id),
            "category": pack_def.get("category", "model"),
            "version": manifest.get("version", 2),
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "files_count": files_added,
            "total_bytes": total_bytes,
            "total_gb": round(total_bytes / (1024 ** 3), 3),
            "min_vram_gb": pack_def.get("min_vram_gb", 0.0),
            "cpu_supported": pack_def.get("cpu_supported", False),
        }
        zf.writestr("pack_receipt.json", json.dumps(receipt, ensure_ascii=False, indent=2))

    elapsed = round(time.time() - t0, 1)
    sz_gb = round(out_path.stat().st_size / (1024 ** 3), 3)
    print(f"[打包完成] {fname} -> {sz_gb} GB ({files_added} 个文件, 耗时 {elapsed}s)", flush=True)
    return {
        "ok": True,
        "edition": "pack",
        "pack_id": pack_id,
        "pack_path": str(out_path),
        "pack_size_gb": sz_gb,
        "files_added": files_added,
        "elapsed_s": elapsed,
    }


def verify_and_build_zip(edition: str = "code-only", target_platform: str = "win-x64") -> dict[str, Any]:
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    date_tag = time.strftime("%Y.%m.%d")
    plat_suffix = "_macOS_ARM64" if target_platform == "macos-arm64" else ""
    tag_map = {
        "cutora-only": f"片语工作室_极简CUTORA专享代码包{plat_suffix}_{date_tag}.zip",
        "code-only": f"02_【代码主程序包_日常更新只下此包】片语工作室_v3.0_全功能代码包{plat_suffix}.zip",
        "standard": f"02_【代码主程序包_日常更新只下此包】片语工作室_v3.0_全功能代码包{plat_suffix}.zip",
        "models-once": f"01_【模型与环境底座包_只需下载一次】片语工作室_v3.0_AI模型与运行环境包{plat_suffix}.zip",
        "full-portable": f"片语工作室_完整便携解释器版{plat_suffix}_{date_tag}.zip",
        "base-25gb": f"01_【必下底座_17.5GB】片语工作室_标准核心版_含CUTORA_表格工具_运行环境_25G核心字幕抠像立体声模型{plat_suffix}_{date_tag}.zip",
    }
    zip_name = tag_map.get(edition, f"片语工作室_{edition}{plat_suffix}_{date_tag}.zip")
    zip_path = DIST_DIR / zip_name

    if edition == "models-once":
        if target_platform == "macos-arm64":
            include_roots = []
        else:
            include_roots = [
                "runtime/python",
                "runtime/venv",
                "runtime/bin",
                "runtime/ffmpeg",
                "runtime/mimalloc",
            ]
        manifest = load_manifest()
        for mp in manifest.get("base_edition", {}).get("included_model_paths", []):
            if mp not in include_roots:
                include_roots.append(mp)
    else:
        include_roots = list(CORE_SHELL_AND_CUTORA_ROOTS if edition in ("cutora-only", "base-25gb", "code-only", "standard") else BASE_APP_ROOTS)
        if edition in ("full-portable", "base-25gb"):
            include_roots.extend(["runtime/python", "runtime/ffmpeg", "runtime/bin", "runtime/mimalloc"])
        if edition == "base-25gb":
            include_roots.append("runtime/venv")
            manifest = load_manifest()
            for mp in manifest.get("base_edition", {}).get("included_model_paths", []):
                if mp not in include_roots:
                    include_roots.append(mp)

    files_added = 0
    security_violations: list[str] = []
    junctions = count_reparse_points()
    t0 = time.time()

    STORED_EXTS = {
        ".safetensors", ".pth", ".bin", ".onnx", ".pt", ".ckpt", ".npz",
        ".dll", ".pyd", ".exe", ".lib", ".so", ".dylib", ".png", ".jpg", ".ico", ".ttf", ".otf", ".whl",
    }

    print(f"[打包开始] 正在生成主程序包: {zip_name} ...", flush=True)
    arc_prefix = "片语工作室/" if edition in ("code-only", "standard", "models-once") else ""

    def _add_file_to_zip(zf_obj: zipfile.ZipFile, src_p: Path, arc_name: str, comp_type: int) -> None:
        is_unix_exec = (
            src_p.suffix.lower() in (".command", ".sh")
            or "/bin/" in arc_name.replace("\\", "/")
            or arc_name.endswith(("/ffmpeg", "/ffprobe", "/rclone", "/uv", "/uvx"))
        )
        if src_p.suffix.lower() in (".command", ".sh"):
            raw_lf = src_p.read_bytes().replace(b"\r\n", b"\n")
            zi = zipfile.ZipInfo(arc_name, time.localtime(src_p.stat().st_mtime)[:6])
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.create_system = 3  # Unix
            zi.external_attr = (0o100755 << 16)  # -rwxr-xr-x
            zf_obj.writestr(zi, raw_lf)
        elif is_unix_exec and target_platform == "macos-arm64":
            zi = zipfile.ZipInfo.from_file(src_p, arc_name)
            zi.compress_type = comp_type
            zi.create_system = 3  # Unix
            zi.external_attr = (0o100755 << 16)  # -rwxr-xr-x
            with src_p.open("rb") as fsrc, zf_obj.open(zi, "w") as fdst:
                import shutil as _sh
                _sh.copyfileobj(fsrc, fdst, 1024 * 1024 * 4)
        else:
            zf_obj.write(src_p, arc_name, compress_type=comp_type)

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as zf:
        if edition == "models-once":
            zf.writestr(
                arc_prefix + "README_模型与环境底座包.txt",
                "这是《片语工作室》的模型与运行环境底座包，只需下载一次。\n"
                "请把它和《02_代码主程序包》解压到同一个位置（两个包内都是「片语工作室」文件夹，会自动合并）。\n"
                "然后双击「片语工作室\\安装与体检.bat」（macOS 双击「安装与体检.command」）即可。以后升级只需重新下载代码包并解压覆盖。\n",
            )
        if target_platform == "macos-arm64":
            zf.writestr(
                arc_prefix + "README_macOS_AppleSilicon_安装与启动说明.txt",
                "====================================================================\n"
                "  《片语工作室》(Pianyu Studio v3.0) — macOS Apple Silicon (M1~M4) 版\n"
                "====================================================================\n\n"
                "1. 模型包与代码包说明：\n"
                "   - 『01_模型与环境底座包.zip』中的 AI 模型权重 (runtime/models) 在 Windows 与 Mac 上 100% 通用！\n"
                "   - 您只需把『01_模型与环境底座包.zip』与本包放在同一目录（或保留在「下载 Downloads」文件夹，甚至无需手动解压 01 包），\n"
                "     或者若已用 Finder 双击解压出「片语工作室 2」，双击本包内的『安装与体检.command』会自动为您提取/合并通用 AI 模型，\n"
                "     并自动跳过 Windows 专属的 .exe/.dll 文件！\n\n"
                "2. 一键安装与体检：\n"
                "   - 双击运行『安装与体检.command』（若首次提示安全拦截，右键 -> 打开）：\n"
                "   - 自动配置 ARM64 原生 Python 环境与 Metal / VideoToolbox / Accelerate 硬件加速\n"
                "   - 自动在桌面生成《片语工作室.app》与《右键表格工具.app》\n"
                "   - 自动注册 macOS 访达 (Finder) 右键『快速操作 -> 用表格工具打开』\n\n"
                "3. 日常启动：\n"
                "   - 双击桌面《片语工作室.app》或目录内『启动片语工作室.command』即可启动。\n",
            )
        zf.writestr(
            arc_prefix + "packs/README_如何安装扩展包.txt",
            "将从网盘按需下载的 .ltxpack 扩展包（如 02_Orante、03_ComfyUI、04_LTX2.5视频模型等）直接放入本目录 (packs/)，\n"
            "然后双击根目录的《安装与体检.bat》（macOS 双击《安装与体检.command》）或打开《片语工作室》点击右上角【📦 模块与模型扩展中心】，即可一键无缝拼装！\n",
        )
        for item in include_roots:
            if target_platform == "macos-arm64" and item.endswith(("cutora_rust_core", ".bat", ".vbs", ".ps1")):
                continue
            target = LTX_ROOT / item
            is_runtime_tree = item.startswith("runtime/")
            if target.is_file():
                rel = str(target.relative_to(LTX_ROOT))
                if is_path_allowed(rel):
                    if target.suffix == ".py" and not is_runtime_tree:
                        py_compile.compile(str(target), doraise=True)
                    comp = zipfile.ZIP_STORED if (is_runtime_tree or target.suffix.lower() in STORED_EXTS) else zipfile.ZIP_DEFLATED
                    _add_file_to_zip(zf, target, arc_prefix + rel.replace("\\", "/"), comp)
                    files_added += 1
            elif target.is_dir():
                for dirpath, dirnames, filenames in os.walk(target):
                    dirnames[:] = [d for d in dirnames if is_path_allowed(d)]
                    for fn in filenames:
                        p = Path(dirpath) / fn
                        rel = str(p.relative_to(LTX_ROOT))
                        if not is_path_allowed(rel):
                            continue
                        sfx = p.suffix.lower()
                        if sfx in (".pyc", ".pyo", ".mp4", ".mov", ".zip", ".ltxpack", ".ltxpart"):
                            continue
                        if target_platform == "macos-arm64" and sfx in (".pyd", ".exe", ".dll", ".bat", ".vbs", ".ps1"):
                            continue
                        if not is_runtime_tree and sfx in (".py", ".toml", ".json", ".md", ".bat", ".command") and "secrets_vault.py" not in rel:
                            try:
                                txt = p.read_text(encoding="utf-8", errors="ignore")
                                for pat in FORBIDDEN_CONTENT_PATTERNS:
                                    if pat in txt:
                                        security_violations.append(f"{rel}: contains {pat}")
                                if OAUTH_REFRESH_RE.search(txt):
                                    security_violations.append(f"{rel}: contains OAuth refresh token")
                            except Exception:
                                pass
                        comp = zipfile.ZIP_STORED if (is_runtime_tree or sfx in STORED_EXTS) else zipfile.ZIP_DEFLATED
                        _add_file_to_zip(zf, p, arc_prefix + rel.replace("\\", "/"), comp)
                        files_added += 1

        # 若为 macOS ARM64 打包，自动注入预置的原生 Apple Silicon (aarch64-apple-darwin) Python 3.11 + FFmpeg + uv + rclone 运行时
        mac_stage_rt = DIST_DIR / "_macos_runtime_staged" / "runtime"
        if target_platform == "macos-arm64" and mac_stage_rt.is_dir():
            for dirpath, _dirnames, filenames in os.walk(mac_stage_rt):
                for fn in filenames:
                    p = Path(dirpath) / fn
                    rel_rt = "runtime/" + str(p.relative_to(mac_stage_rt)).replace("\\", "/")
                    sfx = p.suffix.lower()
                    comp = zipfile.ZIP_STORED if sfx in STORED_EXTS else zipfile.ZIP_DEFLATED
                    _add_file_to_zip(zf, p, arc_prefix + rel_rt, comp)
                    files_added += 1

    elapsed = round(time.time() - t0, 1)
    sz_gb = round(zip_path.stat().st_size / (1024 ** 3), 3)
    print(f"[打包完成] {zip_name} -> {sz_gb} GB ({files_added} 个文件, 耗时 {elapsed}s)", flush=True)
    h = hashlib.sha256(zip_path.read_bytes()).hexdigest() if zip_path.stat().st_size < 1 * (1024 ** 3) else "skipped_for_large_archive"
    return {
        "ok": len(security_violations) == 0 and len(junctions) == 0,
        "edition": edition,
        "platform": target_platform,
        "zip_path": str(zip_path),
        "zip_size_gb": sz_gb,
        "zip_size_mb": round(zip_path.stat().st_size / (1024 * 1024), 2),
        "files_added": files_added,
        "elapsed_s": elapsed,
        "reparse_points_remaining": len(junctions),
        "desktop_icon_bundled": (LTX_ROOT / "resources" / "brand" / "app.ico").is_file(),
        "minimax_h3_included": False,
        "security_violations": security_violations,
        "sha256": h,
    }


def write_cloud_drive_readme() -> Path:
    """在 dist_packages/ 下生成网盘顶层必看说明文件。"""
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    readme_path = DIST_DIR / "00_【必看】片语工作室_网盘按需下载与一键拼装说明.txt"
    content = """================================================================================
          片语工作室 (Cutora LTX Studio v3.0) — 网盘按需下载与一键安装指南
================================================================================

欢迎使用《片语工作室》！本网盘目录采用「统一软件外壳 + 乐高模块化按需拼装」设计。
所有人均通过同一个《片语工作室》总控台进入，您可以根据自己的电脑配置与实际工作需求，
自由挑选下载以下模块：

--------------------------------------------------------------------------------
一、我该下载哪几个文件？（对号入座）
--------------------------------------------------------------------------------

1. 【所有人必下（仅需下载这 1 个底座包即可直接用 CUTORA + 右键表格工具）】：
   👉 01_【必下底座_17.5GB】片语工作室_标准核心版_含CUTORA_表格工具_运行环境_25G核心字幕抠像立体声模型_*.zip
   · 内含：片语工作室统一主界面、CUTORA 智能剪辑台、右键表格工具、便携 Python 环境、FFmpeg 硬件加速套件，
     以及日常必备的四大核心 AI 模型（Whisper 自动字幕、智能拼接、SAM2.1/RMBG2 视频图像抠像、Demucs/Resemble 立体声与降噪、人像提亮修复）。
   · 硬件要求：普通办公本 / i7 + 32GB 内存（无独立显卡也可流畅运行，导出速度 >= 400 fps）或 Apple Silicon Mac。

2. 【按需选下：独立软件模块包（体积很小，想用哪个软件就多下哪个）】：
   👉 02_【可选软件模块_15MB】Orante2.0制片台_pack-app-orante.ltxpack
      （需要用 Orante 2.0 批量排片、调用云端 Grok/Flow 生片、审片的同事下载）

3. 【按需选下：高阶音频与超分追踪模型包（中等配置电脑 / 有进阶剪辑需求的同事下载）】：
   👉 07_【可选语音配乐模型_25.6GB】AI语音克隆与配乐生成_pack-audio-tts-bgm.ltxpack
      （需要零样本 AI 情绪配音克隆 Chatterbox/XTTS、AI 生成背景音乐 Stable Audio/MusicGen 时下载）
   👉 08_【可选超分追踪多语种_24.7GB】4x超分_TAPNext追踪_多国语对齐_pack-pro-vision-multilang.ltxpack
      （需要 4x 画质超分放大、TAPNext++ 运动跟踪、德/西/法/葡多国语字幕对齐时下载）

4. 【按需选下：本地重型 AI 视频/生图大模型包（仅限配备 RTX 12GB~16GB+ 独立显卡或 36GB+ Apple Silicon 的主力工作站下载）】：
   👉 03_【可选推理引擎_4.5GB】ComfyUI本地引擎与CUDA环境_pack-engine-comfyui.ltxpack  (本地生视频/生图必装底座)
   👉 04_【可选视频生成模型_39.2GB】LTX-2.5_22B_本地视频生成大模型_pack-ltx25-video.ltxpack
   👉 05_【可选视频特效模型_7.6GB】LTX-2.5_IC-LoRA_7合1视频精修特效_pack-ltx25-vfx-loras.ltxpack
   👉 06_【可选生图改图模型_7.9GB】FLUX.2_klein_4B_极速生图与改图_pack-flux2-image.ltxpack

5. 【仅限老用户日常更新代码】：
   👉 02_【代码主程序包_日常更新只下此包】片语工作室_v3.0_全功能代码包.zip
      （已装好底座和模型的同事，后续软件升级只需下载此小包解压覆盖即可，原有百G模型100%保留！）

--------------------------------------------------------------------------------
二、下载后怎么安装？（三步傻瓜式操作）
--------------------------------------------------------------------------------

第一步：解压【01_必下底座】
   将 `01_【必下底座...】.zip` 解压到电脑空间充足的磁盘根目录（如 `D:\\PianyuStudio\\` 或 `~/PianyuStudio/`）。

第二步：放入您挑选的 `.ltxpack` 扩展包（如果有下载的话）
   把您从网盘挑着下载的 `.ltxpack` 文件（不用手动改后缀解压！），直接复制粘贴到解压目录里面的 **`packs`** 文件夹下。

第三步：双击一键安装与启动
   1. Windows 双击运行解压目录里的 **`安装与体检.bat`**（macOS 双击 **`安装与体检.command`**）：
      它会自动配置好 Python 环境、自动把 `packs` 文件夹里的所有扩展包无缝组装进工作室、并在桌面生成《片语工作室》快捷方式！
   2. 以后每天使用，直接双击桌面的 **《片语工作室》** 图标即可！
   3. 如果以后想追加新功能，随时从网盘补下对应的 `.ltxpack` 文件，打开《片语工作室》点击
      【🧩 扩展包】->【📂 选择 .ltxpack 文件一键安装…】，选中文件即可一键热挂载！
================================================================================
"""
    readme_path.write_text(content, encoding="utf-8")
    return readme_path


def main() -> None:
    parser = argparse.ArgumentParser(description="片语工作室安全脱敏与模块化插件包打包工具")
    parser.add_argument(
        "--edition",
        default="code-only",
        choices=["code-only", "models-once", "cutora-only", "standard", "full-portable", "base-25gb", "pack", "cloud-drive-all", "plan"],
        help="打包版本档位",
    )
    parser.add_argument("--pack-id", default="", help="当 --edition pack 时指定扩展包 ID (如 pack-ltx25-video)")
    parser.add_argument(
        "--platform",
        default="win-x64",
        choices=["win-x64", "macos-arm64"],
        help="目标分发平台 (win-x64 或 macos-arm64)",
    )
    parser.add_argument("--verify-only", action="store_true", help="仅执行安全与合规校验")
    args = parser.parse_args()

    if args.edition == "plan":
        print(json.dumps(build_pack_plan(), ensure_ascii=False, indent=2))
        return

    if args.edition == "pack":
        if not args.pack_id:
            raise SystemExit("请使用 --pack-id 指定要打包的扩展包 ID（例如 --pack-id pack-flux2-image）")
        res = build_single_ltxpack(args.pack_id)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return

    if args.edition == "cloud-drive-all":
        write_cloud_drive_readme()
        results = []
        results.append(verify_and_build_zip("base-25gb", target_platform=args.platform))
        manifest = load_manifest()
        for p in manifest.get("packs", []):
            results.append(build_single_ltxpack(p["id"]))
        results.append(verify_and_build_zip("standard", target_platform=args.platform))
        print(json.dumps({"ok": True, "cloud_drive_suite": results}, ensure_ascii=False, indent=2))
        return

    res = verify_and_build_zip(edition=args.edition, target_platform=args.platform)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    if not res["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

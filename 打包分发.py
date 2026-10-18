"""片语工作室 (D:\\0_LTX) 安全脱敏与乐高式模块化打包分发器 (打包分发.py)。

设计哲学（结合特斯拉车型分级 + 乐高模块化插件包 DLC 架构）：
1. 【开发机零影响】：在你自己的电脑 (`D:\\0_LTX`) 上，所有 126 GB 模型与功能完整保留、一体维护，没有任何删减。
2. 【内部分发 25GB 标准核心版 (`--edition base-25gb`)】：
   专门面向低配笔记本（i7 + 32GB 内存 + 无独显 + 小硬盘）同事分发。
   自动打包：
   - 完整四翼程序（CUTORA 剪辑台、Orante 2.0 制片台、右键表格工具、Robota 巡检代理、7870 本地网关）
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
    "runtime/bin",
    "runtime/mimalloc",
    "modules/table_tool/app/表格工具.py",
    "modules/table_tool/install.py",
    "modules/table_tool/uninstall.py",
    "modules/cutora_editor/cutora",
    "modules/cutora_editor/resources/brand",
    "modules/cutora_editor/resources/theme",
    "modules/cutora_editor/resources/data",
    "modules/studio_gateway",
    "studio_launcher.py",
    "install_studio.py",
    "片语工作室.bat",
    "片语工作室.vbs",
    "创建桌面快捷方式.bat",
    "一键打包分发.bat",
    "启动本地算力网关.bat",
    "打开表格工具.bat",
    "安装与体检.bat",
    "README.md",
    "安装与扩展包使用指南.md",
    "SECURITY_AUDIT_REPORT.md",
    "MODEL_LICENSE_COMPLIANCE.md",
    "requirements.txt",
    ".env.example",
]


BASE_APP_ROOTS = CORE_SHELL_AND_CUTORA_ROOTS + [
    "modules/orante_desk/orante",
    "modules/orante_desk/ui",
    "modules/orante_desk/extension",
    "modules/robota_hub/robota",
    "modules/robota_hub/config",
    "robota_assistant_window.py",
]

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
    """确保 D:\\0_LTX 内没有任何文件夹快捷方式 (Directory Junction / ReparsePoint)。"""
    hits: list[str] = []
    for root, dirs, _files in os.walk(LTX_ROOT):
        for d in list(dirs):
            p = Path(root) / d
            try:
                if os.lstat(p).st_file_attributes & 0x400:
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
    "pack-app-robota": "03_【可选软件模块_7MB】Robota协同操作代理_pack-app-robota.ltxpack",
    "pack-engine-comfyui": "04_【可选推理引擎_4.5GB】ComfyUI本地引擎与CUDA环境_pack-engine-comfyui.ltxpack",
    "pack-ltx25-video": "05_【可选视频生成模型_39.2GB】LTX-2.5_22B_本地视频生成大模型_pack-ltx25-video.ltxpack",
    "pack-ltx25-vfx-loras": "06_【可选视频特效模型_7.6GB】LTX-2.5_IC-LoRA_7合1视频精修特效_pack-ltx25-vfx-loras.ltxpack",
    "pack-flux2-image": "07_【可选生图改图模型_7.9GB】FLUX.2_klein_4B_极速生图与改图_pack-flux2-image.ltxpack",
    "pack-audio-tts-bgm": "08_【可选语音配乐模型_25.6GB】AI语音克隆与配乐生成_pack-audio-tts-bgm.ltxpack",
    "pack-pro-vision-multilang": "09_【可选超分追踪多语种_24.7GB】4x超分_TAPNext追踪_多国语对齐_pack-pro-vision-multilang.ltxpack",
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
            # 自动从开发机 D:\0_LLM\ComfyUI 抽取纯净引擎底座 (不含 models/output/input/temp) 封装进 runtime/comfyui/
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
                        # 自动重写 pyvenv.cfg 使其指向内置的 python312
                        cfg_txt = (
                            "home = D:\\0_LTX\\runtime\\comfyui\\python312\n"
                            "implementation = CPython\n"
                            "version_info = 3.12.7\n"
                            "include-system-site-packages = false\n"
                        )
                        zf.writestr(arc_rel, cfg_txt)
                    else:
                        zf.write(p, arc_rel)
                    files_added += 1
                    total_bytes += p.stat().st_size

            # 同时内置便携式 Python 3.12.7 基底，确保 ComfyUI 虚拟环境在任何电脑上开箱即用
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


def verify_and_build_zip(edition: str = "standard") -> dict[str, Any]:
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    date_tag = time.strftime("%Y.%m.%d")
    tag_map = {
        "cutora-only": f"片语工作室_极简CUTORA专享代码包_{date_tag}.zip",
        "standard": f"10_【日常代码热更新包_563MB】片语工作室_四翼全代码热更新包_{date_tag}.zip",
        "full-portable": f"片语工作室_完整便携解释器版_{date_tag}.zip",
        "base-25gb": f"01_【必下底座_17.5GB】片语工作室_标准核心版_含CUTORA_表格工具_运行环境_25G核心字幕抠像立体声模型_{date_tag}.zip",
    }
    zip_name = tag_map.get(edition, f"片语工作室_{edition}_{date_tag}.zip")
    zip_path = DIST_DIR / zip_name

    # 注意：base-25gb 底座默认包含统一工作室外壳 + CUTORA + 右键表格工具 + 完整运行环境 + 25GB 基础模型；
    # Orante 与 Robota 作为 02 / 03 号独立扩展包按需选择（如果用户下载了 02/03 放入 packs/ 会自动一键拼装）
    include_roots = list(CORE_SHELL_AND_CUTORA_ROOTS if edition in ("cutora-only", "base-25gb") else BASE_APP_ROOTS)
    if edition in ("full-portable", "base-25gb"):
        include_roots.append("runtime/python")
        include_roots.append("runtime/ffmpeg")
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
        ".dll", ".pyd", ".exe", ".lib", ".so", ".png", ".jpg", ".ico", ".ttf", ".otf", ".whl",
    }

    print(f"[打包开始] 正在生成主程序包: {zip_name} ...", flush=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as zf:
        zf.writestr(
            "packs/README_如何安装扩展包.txt",
            "将从网盘按需下载的 .ltxpack 扩展包（如 02_Orante、03_Robota、04_ComfyUI、05_LTX2.5视频模型等）直接放入本目录 (packs/)，\n"
            "然后双击根目录的《安装与体检.bat》或打开《片语工作室》点击右上角【📦 模块与模型扩展中心】，即可一键无缝拼装！\n",
        )
        for item in include_roots:
            target = LTX_ROOT / item
            is_runtime_tree = item.startswith("runtime/")
            if target.is_file():
                rel = str(target.relative_to(LTX_ROOT))
                if is_path_allowed(rel):
                    if target.suffix == ".py" and not is_runtime_tree:
                        py_compile.compile(str(target), doraise=True)
                    comp = zipfile.ZIP_STORED if (is_runtime_tree or target.suffix.lower() in STORED_EXTS) else zipfile.ZIP_DEFLATED
                    zf.write(target, rel, compress_type=comp)
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
                        if not is_runtime_tree and sfx in (".py", ".toml", ".json", ".md", ".bat") and "secrets_vault.py" not in rel:
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
                        zf.write(p, rel, compress_type=comp)
                        files_added += 1

    elapsed = round(time.time() - t0, 1)
    sz_gb = round(zip_path.stat().st_size / (1024 ** 3), 3)
    print(f"[打包完成] {zip_name} -> {sz_gb} GB ({files_added} 个文件, 耗时 {elapsed}s)", flush=True)
    h = hashlib.sha256(zip_path.read_bytes()).hexdigest() if zip_path.stat().st_size < 1 * (1024 ** 3) else "skipped_for_large_archive"
    return {
        "ok": len(security_violations) == 0 and len(junctions) == 0,
        "edition": edition,
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
   · 硬件要求：普通办公本 / i7 + 32GB 内存（无独立显卡也可流畅运行，导出速度 >= 400 fps）。

2. 【按需选下：独立软件模块包（体积很小，想用哪个软件就多下哪个）】：
   👉 02_【可选软件模块_15MB】Orante2.0制片台_pack-app-orante.ltxpack
      （需要用 Orante 2.0 批量排片、调用云端 Grok/Flow 生片、审片的同事下载）
   👉 03_【可选软件模块_7MB】Robota协同操作代理_pack-app-robota.ltxpack
      （需要用 Robota 自动建夹、网盘拉取、自动重命名与缺片闭环巡检的同事下载）

3. 【按需选下：高阶音频与超分追踪模型包（中等配置电脑 / 有进阶剪辑需求的同事下载）】：
   👉 08_【可选语音配乐模型_25.6GB】AI语音克隆与配乐生成_pack-audio-tts-bgm.ltxpack
      （需要零样本 AI 情绪配音克隆 Chatterbox/XTTS、AI 生成背景音乐 Stable Audio/MusicGen 时下载）
   👉 09_【可选超分追踪多语种_24.7GB】4x超分_TAPNext追踪_多国语对齐_pack-pro-vision-multilang.ltxpack
      （需要 4x 画质超分放大、TAPNext++ 运动跟踪、德/西/法/葡多国语字幕对齐时下载）

4. 【按需选下：本地重型 AI 视频/生图大模型包（仅限配备 RTX 12GB~16GB+ 独立显卡的主力工作站下载）】：
   👉 04_【可选推理引擎_4.5GB】ComfyUI本地引擎与CUDA环境_pack-engine-comfyui.ltxpack  (本地生视频/生图必装底座)
   👉 05_【可选视频生成模型_39.2GB】LTX-2.5_22B_本地视频生成大模型_pack-ltx25-video.ltxpack
   👉 06_【可选视频特效模型_7.6GB】LTX-2.5_IC-LoRA_7合1视频精修特效_pack-ltx25-vfx-loras.ltxpack
   👉 07_【可选生图改图模型_7.9GB】FLUX.2_klein_4B_极速生图与改图_pack-flux2-image.ltxpack

5. 【仅限老用户日常更新代码（563MB）】：
   👉 10_【日常代码热更新包_563MB】片语工作室_四翼全代码热更新包_*.zip
      （已装好底座和模型的同事，后续软件升级只需下载此小包解压覆盖即可，原有百G模型100%保留！）

--------------------------------------------------------------------------------
二、下载后怎么安装？（三步傻瓜式操作）
--------------------------------------------------------------------------------

第一步：解压【01_必下底座】
   将 `01_【必下底座...】.zip` 解压到电脑空间充足的磁盘根目录（如 `D:\\PianyuStudio\\` 或 `D:\\0_LTX\\`）。

第二步：放入您挑选的 `.ltxpack` 扩展包（如果有下载的话）
   把您从网盘挑着下载的 `.ltxpack` 文件（不用手动改后缀解压！），直接复制粘贴到解压目录里面的 **`packs`** 文件夹下。

第三步：双击一键安装与启动
   1. 双击运行解压目录里的 **`安装与体检.bat`**：
      它会自动配置好 Python 环境、自动把 `packs` 文件夹里的所有扩展包无缝组装进工作室、并在桌面生成《片语工作室》快捷方式！
   2. 以后每天使用，直接双击桌面的 **《片语工作室》** 图标即可！
   3. 如果以后后悔了、想追加新功能，随时从网盘补下对应的 `.ltxpack` 文件，打开《片语工作室》点击右上角
      【📦 模块与模型扩展中心】->【📂 从本地导入 .ltxpack 扩展包...】，选中文件即可一键热挂载！
================================================================================
"""
    readme_path.write_text(content, encoding="utf-8")
    return readme_path


def main() -> None:
    parser = argparse.ArgumentParser(description="片语工作室安全脱敏与模块化插件包打包工具")
    parser.add_argument(
        "--edition",
        default="standard",
        choices=["cutora-only", "standard", "full-portable", "base-25gb", "pack", "cloud-drive-all", "plan"],
        help="打包版本档位",
    )
    parser.add_argument("--pack-id", default="", help="当 --edition pack 时指定扩展包 ID (如 pack-ltx25-video)")
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
        results.append(verify_and_build_zip("base-25gb"))
        manifest = load_manifest()
        for p in manifest.get("packs", []):
            results.append(build_single_ltxpack(p["id"]))
        results.append(verify_and_build_zip("standard"))
        print(json.dumps({"ok": True, "cloud_drive_suite": results}, ensure_ascii=False, indent=2))
        return

    res = verify_and_build_zip(edition=args.edition)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    if not res["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()


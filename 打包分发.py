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


def build_single_ltxpack(pack_id: str) -> dict[str, Any]:
    """将指定的软件模块、ComfyUI 推理引擎或 AI 模型扩展包单独封装为 `.ltxpack` 文件，附带 `pack_receipt.json` 元数据。"""
    manifest = load_manifest()
    pack_def = next((p for p in manifest.get("packs", []) if p["id"] == pack_id), None)
    if not pack_def:
        raise ValueError(f"未在 packs_manifest.json 中找到扩展包 ID: {pack_id}")

    DIST_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DIST_DIR / f"{pack_id}.ltxpack"
    files_added = 0
    total_bytes = 0

    is_app_pack = pack_def.get("category") == "app_module"
    comp_mode = zipfile.ZIP_DEFLATED if is_app_pack else zipfile.ZIP_STORED

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

    return {
        "ok": True,
        "edition": "pack",
        "pack_id": pack_id,
        "pack_path": str(out_path),
        "pack_size_gb": round(out_path.stat().st_size / (1024 ** 3), 3),
        "files_added": files_added,
    }


def verify_and_build_zip(edition: str = "standard") -> dict[str, Any]:
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    date_tag = time.strftime("%Y.%m.%d")
    tag_map = {
        "cutora-only": "极简CUTORA专享版_统一工作室外壳_Orante与Robota按需装",
        "standard": "标准代码底座版_32GB_CPU",
        "full-portable": "完整便携解释器版",
        "base-25gb": "标准核心版_25GB_内置字幕抠像立体声",
    }
    tag_name = tag_map.get(edition, edition)
    zip_path = DIST_DIR / f"片语工作室_{tag_name}_{date_tag}.zip"

    include_roots = list(CORE_SHELL_AND_CUTORA_ROOTS if edition == "cutora-only" else BASE_APP_ROOTS)
    if edition in ("full-portable", "base-25gb"):
        include_roots.append("runtime/python")
    if edition == "base-25gb":
        include_roots.append("runtime/venv")
        manifest = load_manifest()
        include_roots.extend(manifest.get("base_edition", {}).get("included_model_paths", []))


    files_added = 0
    security_violations: list[str] = []
    junctions = count_reparse_points()

    STORED_EXTS = {
        ".safetensors", ".pth", ".bin", ".onnx", ".pt", ".ckpt", ".npz",
        ".dll", ".pyd", ".exe", ".lib", ".so", ".png", ".jpg", ".ico", ".ttf", ".otf", ".whl",
    }

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=3, allowZip64=True) as zf:
        # 在打包归档中自动创建空的 packs/ 目录说明，方便用户直接拖入 .ltxpack
        zf.writestr(
            "packs/README_如何安装扩展包.txt",
            "将按需下载的 .ltxpack 扩展包放入本目录 (packs/)，打开《片语工作室》点击【🧩 扩展包】即可一键无缝安装！\n",
        )
        for item in include_roots:
            target = LTX_ROOT / item
            is_runtime_tree = item.startswith("runtime/")
            if target.is_file():
                rel = str(target.relative_to(LTX_ROOT))
                if is_path_allowed(rel):
                    if target.suffix == ".py" and not is_runtime_tree:
                        py_compile.compile(str(target), doraise=True)
                    comp = zipfile.ZIP_STORED if target.suffix.lower() in STORED_EXTS else zipfile.ZIP_DEFLATED
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
                        comp = zipfile.ZIP_STORED if sfx in STORED_EXTS else zipfile.ZIP_DEFLATED
                        zf.write(p, rel, compress_type=comp)
                        files_added += 1


    h = hashlib.sha256(zip_path.read_bytes()).hexdigest() if zip_path.stat().st_size < 2 * (1024 ** 3) else "skipped_for_large_archive"
    return {
        "ok": len(security_violations) == 0 and len(junctions) == 0,
        "edition": edition,
        "zip_path": str(zip_path),
        "zip_size_mb": round(zip_path.stat().st_size / (1024 * 1024), 2),
        "files_added": files_added,
        "reparse_points_remaining": len(junctions),
        "desktop_icon_bundled": (LTX_ROOT / "resources" / "brand" / "app.ico").is_file(),
        "minimax_h3_included": False,
        "security_violations": security_violations,
        "sha256": h,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="片语工作室安全脱敏与模块化插件包打包工具")
    parser.add_argument(
        "--edition",
        default="standard",
        choices=["cutora-only", "standard", "full-portable", "base-25gb", "pack", "plan"],
        help="打包版本档位: cutora-only(统一工作室外壳+CUTORA+右键表格工具) / standard(四翼完整代码底座) / full-portable(含Python解释器) / base-25gb(25GB标准核心版) / pack(单独打包指定.ltxpack扩展包) / plan(查看完整拆分精算表)",
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

    res = verify_and_build_zip(edition=args.edition)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    if not res["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

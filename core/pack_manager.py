"""片语工作室 (D:\\0_LTX) 乐高式全栈模块化插件包管理器 (PackManager)。

设计目标（借鉴 DaVinci Resolve / Unreal Engine Feature Packs / Steam DLC 行业最高标准）：
1. 【统一外壳 · 一体工程】：无论下载任何档位（极简 CUTORA 版 / 25GB 标准核心版 / 126GB 旗舰完整版），
   外框总控台 (`studio_launcher.py`)、视觉设计、底层总线 (`core`) 与轻量级『右键表格工具』100% 保持一致，
   所有人统一从『片语工作室』入口进入。
2. 【大模块到小模块全可拆可装】：
   - 软件模块包：`pack-app-orante` (Orante 2.0 制片台)、`pack-app-robota` (Robota 协同操作代理)
   - 推理引擎包：`pack-engine-comfyui` (便携式 ComfyUI 推理引擎底座 + CUDA 运行环境，免外部依赖)
   - AI 模型扩展包：
     * `pack-ltx25-video` (LTX-2.5 22B 视频生成包)
     * `pack-ltx25-vfx-loras` (LTX-2.5 IC-LoRA 7合1 特效包)
     * `pack-flux2-image` (FLUX.2 [klein] 4B 生图改图包)
     * `pack-audio-tts-bgm` (AI 语音克隆与配乐生成包)
     * `pack-pro-vision-multilang` (4x 超分、TAPNext++ 追踪与多国语对齐包)
3. 【一键无缝拼装】：当用户点击未安装的软件模块或 AI 功能时，界面友好提示下载对应 `.ltxpack` 扩展包，
   支持放入 `D:\\0_LTX\\packs` 自动识别或点击「选择扩展包一键安装」，经 Zip Slip 防穿越校验后原子级热挂载！
"""

from __future__ import annotations

import json
import os
import shutil
import time
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from . import LTX_ROOT
from .hardware_guard import HardwareProfile, probe_hardware
from .portable import rewrite_pyvenv_cfg

PACKS_MANIFEST_PATH = LTX_ROOT / "config" / "packs_manifest.json"
PACKS_INBOX_DIR = LTX_ROOT / "packs"
INSTALLED_RECEIPTS_DIR = LTX_ROOT / "data" / "installed_packs"

ALLOWED_EXTRACT_PREFIXES = (
    "runtime/models/",
    "runtime/comfyui/",
    "modules/orante_desk/",
    "modules/robota_hub/",
)
ALLOWED_EXTRACT_EXACT = {
    "pack_receipt.json",
    "robota_assistant_window.py",
}


@dataclass
class PackStatus:
    id: str
    name: str
    short_name: str
    category: str
    tier: int
    min_vram_gb: float
    cpu_supported: bool
    approx_size_gb: float
    installed_size_gb: float
    installed: bool
    hardware_compatible: bool
    status_badge: str
    description: str
    cloud_fallback: str
    inbox_archive_found: str  # 若 packs/ 目录下已放入待安装的 .ltxpack/.zip 则显示文件名
    model_ids: list[str]
    paths: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_packs_manifest() -> dict[str, Any]:
    if not PACKS_MANIFEST_PATH.is_file():
        return {"version": 2, "base_edition": {}, "packs": []}
    try:
        return json.loads(PACKS_MANIFEST_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 2, "base_edition": {}, "packs": []}


def _dir_or_file_size_bytes(p: Path) -> int:
    if not p.exists():
        return 0
    if p.is_file():
        try:
            return p.stat().st_size
        except OSError:
            return 0
    total = 0
    for root, _dirs, files in os.walk(p):
        for fn in files:
            fp = Path(root) / fn
            try:
                total += fp.stat().st_size
            except OSError:
                pass
    return total


def is_pack_installed(pack_def: dict[str, Any], root: Path = LTX_ROOT) -> tuple[bool, float]:
    """根据 required_markers (及开发机 external_fallback_paths) 判断软件或模型扩展包是否已安装。"""
    markers = pack_def.get("required_markers") or pack_def.get("paths") or []
    if not markers:
        return False, 0.0

    all_markers_ok = True
    for m in markers:
        target = root / m
        if not target.exists():
            all_markers_ok = False
            break
        if target.is_dir():
            has_real_file = any(
                f.is_file() and f.stat().st_size > 128
                for f in target.rglob("*")
            )
            if not has_real_file:
                all_markers_ok = False
                break
        elif target.is_file():
            if target.stat().st_size <= 64:
                all_markers_ok = False
                break

    if not all_markers_ok:
        # 检查是否有开发机外部回退路径（如 D:/0_LLM/ComfyUI/main.py）
        ext_fallbacks = pack_def.get("external_fallback_paths") or []
        if ext_fallbacks and all(Path(p).exists() for p in ext_fallbacks):
            return True, float(pack_def.get("approx_size_gb", 4.5))
        return False, 0.0

    total_bytes = 0
    for rel in pack_def.get("paths", []):
        total_bytes += _dir_or_file_size_bytes(root / rel)
    return True, round(total_bytes / (1024 ** 3), 2)


def is_module_installed(pack_id: str, root: Path = LTX_ROOT) -> bool:
    """快捷检查某个模块包 (如 pack-app-orante / pack-app-robota / pack-engine-comfyui) 是否已安装。"""
    manifest = load_packs_manifest()
    for p in manifest.get("packs", []):
        if p.get("id") == pack_id:
            installed, _ = is_pack_installed(p, root=root)
            return installed
    return True


def find_inbox_archive(pack_id: str, inbox_dir: Path = PACKS_INBOX_DIR) -> Path | None:
    """扫描 D:\\0_LTX\\packs 或 dist_packages 目录中是否存在对应的待安装压缩包。"""
    search_dirs = [inbox_dir, LTX_ROOT / "dist_packages"]
    for d in search_dirs:
        if not d.is_dir():
            continue
        for ext in (".ltxpack", ".zip"):
            for cand in sorted(d.glob(f"*{pack_id}*{ext}"), key=lambda x: x.stat().st_mtime, reverse=True):
                if cand.is_file():
                    return cand
    return None


def get_pack_for_model(model_id: str) -> dict[str, Any] | None:
    manifest = load_packs_manifest()
    for pack in manifest.get("packs", []):
        if model_id in pack.get("model_ids", []) or pack.get("id") == model_id:
            return pack
    return None


def evaluate_all_packs(profile: HardwareProfile | None = None, root: Path = LTX_ROOT) -> dict[str, Any]:
    """返回标准核心底座信息与全部可选乐高扩展包（软件模块 + 推理引擎 + AI模型）的实时安装/硬件兼容状态。"""
    prof = profile or probe_hardware()
    manifest = load_packs_manifest()
    base_info = dict(manifest.get("base_edition", {}))

    base_bytes = 0
    for rel in base_info.get("included_model_paths", []):
        base_bytes += _dir_or_file_size_bytes(root / rel)
    base_info["installed_models_size_gb"] = round(base_bytes / (1024 ** 3), 2)
    base_info["installed"] = True

    packs_out: list[dict[str, Any]] = []
    for pdef in manifest.get("packs", []):
        pid = pdef["id"]
        installed, inst_gb = is_pack_installed(pdef, root=root)
        min_vram = float(pdef.get("min_vram_gb", 0.0))
        cpu_ok = bool(pdef.get("cpu_supported", False))
        hw_ok = cpu_ok or (prof.has_nvidia_gpu and prof.vram_total_gb >= min_vram)

        inbox_arch = find_inbox_archive(pid)
        inbox_name = inbox_arch.name if inbox_arch else ""

        if installed and hw_ok:
            badge = "🟢 已安装 · 随时可用"
        elif installed and not hw_ok:
            badge = f"🟡 已安装 · 需 {min_vram}GB 独显"
        elif inbox_name:
            badge = "📦 已就绪待装 (可一键安装)"
        elif not hw_ok:
            badge = f"☁️ 未安装 (建议用云端 / 需 {min_vram}GB 显卡)"
        else:
            badge = "🧩 可选扩展包 (按需一键安装)"

        ps = PackStatus(
            id=pid,
            name=pdef.get("name", pid),
            short_name=pdef.get("short_name", pid),
            category=pdef.get("category", "general"),
            tier=int(pdef.get("tier", 2)),
            min_vram_gb=min_vram,
            cpu_supported=cpu_ok,
            approx_size_gb=float(pdef.get("approx_size_gb", 0.0)),
            installed_size_gb=inst_gb if installed else 0.0,
            installed=installed,
            hardware_compatible=hw_ok,
            status_badge=badge,
            description=pdef.get("description", ""),
            cloud_fallback=pdef.get("cloud_fallback", ""),
            inbox_archive_found=inbox_name,
            model_ids=list(pdef.get("model_ids", [])),
            paths=list(pdef.get("paths", [])),
        )
        packs_out.append(ps.to_dict())

    installed_count = sum(1 for p in packs_out if p["installed"])
    return {
        "base_edition": base_info,
        "packs": packs_out,
        "summary": {
            "total_packs": len(packs_out),
            "installed_packs": installed_count,
            "optional_uninstalled": len(packs_out) - installed_count,
            "edition_mode": "旗舰完整版 (Full Workstation)" if installed_count == len(packs_out) else f"乐高模块化版 (已装 {installed_count}/{len(packs_out)} 扩展包)",
        },
    }


def _is_safe_extract_path(base_dir: Path, target_rel: str) -> bool:
    """严格防御 Zip Slip 路径穿越漏洞与非法文件注入。"""
    norm = target_rel.replace("\\", "/").lstrip("/")
    if not norm or ".." in norm.split("/"):
        return False
    if norm not in ALLOWED_EXTRACT_EXACT and not any(norm.startswith(pref) for pref in ALLOWED_EXTRACT_PREFIXES):
        return False
    resolved = (base_dir / norm).resolve()
    try:
        resolved.relative_to(base_dir.resolve())
        return True
    except ValueError:
        return False


def install_pack_archive(
    archive_path: str | Path,
    root: Path = LTX_ROOT,
    progress_cb: Callable[[int, str], None] | None = None,
) -> dict[str, Any]:
    """一键安全安装 `.ltxpack` 或 `.zip` 扩展包（支持软件模块包、ComfyUI 引擎包与 AI 模型权重包）。"""
    arch = Path(archive_path)
    if not arch.is_file():
        return {"ok": False, "error": f"未找到扩展包文件: {arch}"}

    if not zipfile.is_zipfile(arch):
        return {"ok": False, "error": f"无效的扩展包格式 (非标准 ZIP/LTXPACK 归档): {arch.name}"}

    t0 = time.time()
    extracted_files = 0
    extracted_bytes = 0
    pack_id = "unknown"

    with zipfile.ZipFile(arch, "r") as zf:
        names = zf.namelist()
        if "pack_receipt.json" in names:
            try:
                receipt = json.loads(zf.read("pack_receipt.json").decode("utf-8"))
                pack_id = receipt.get("pack_id", pack_id)
            except Exception:
                receipt = {}
        else:
            receipt = {}

        entries = [n for n in names if not n.endswith("/") and n != "pack_receipt.json"]
        if not entries:
            return {"ok": False, "error": "扩展包内没有找到任何有效模块或模型文件"}

        # 1. 全量安全预检 (防止 Zip Slip)
        for entry in entries:
            if not _is_safe_extract_path(root, entry):
                return {
                    "ok": False,
                    "error": f"安全拦截：扩展包内存在越权路径 `{entry}`",
                }

        # 2. 原子流式解压
        total_entries = len(entries)
        for idx, entry in enumerate(entries, 1):
            norm = entry.replace("\\", "/").lstrip("/")
            dest = root / norm
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp_dest = dest.with_suffix(dest.suffix + ".ltxpart")
            if progress_cb:
                progress_cb(int(idx * 100 / max(1, total_entries)), f"正在安装 ({idx}/{total_entries}): {dest.name}")
            with zf.open(entry, "r") as src_f, open(tmp_dest, "wb") as dst_f:
                shutil.copyfileobj(src_f, dst_f, length=8 * 1024 * 1024)
            os.replace(tmp_dest, dest)
            extracted_files += 1
            extracted_bytes += dest.stat().st_size

    # 若安装了便携式 ComfyUI 引擎包，自动修正其 pyvenv.cfg 指向当前机器的 runtime/python
    comfy_venv_cfg = root / "runtime" / "comfyui" / "venv" / "pyvenv.cfg"
    if comfy_venv_cfg.is_file():
        rewrite_pyvenv_cfg(root)

    INSTALLED_RECEIPTS_DIR.mkdir(parents=True, exist_ok=True)
    receipt_out = INSTALLED_RECEIPTS_DIR / f"{pack_id}_{int(time.time())}.json"
    receipt_data = {
        "ok": True,
        "pack_id": pack_id,
        "archive": str(arch),
        "installed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "files_installed": extracted_files,
        "installed_size_gb": round(extracted_bytes / (1024 ** 3), 3),
        "elapsed_s": round(time.time() - t0, 2),
    }
    receipt_out.write_text(json.dumps(receipt_data, ensure_ascii=False, indent=2), encoding="utf-8")
    return receipt_data


def scan_and_auto_install_inbox(
    inbox_dir: Path = PACKS_INBOX_DIR,
    root: Path = LTX_ROOT,
    progress_cb: Callable[[int, str], None] | None = None,
) -> list[dict[str, Any]]:
    """扫描 `D:\\0_LTX\\packs` 目录下的待装 `.ltxpack` 包，若对应扩展包尚未安装则自动一键安装。"""
    results: list[dict[str, Any]] = []
    if not inbox_dir.is_dir():
        return results
    manifest = load_packs_manifest()
    for pdef in manifest.get("packs", []):
        installed, _ = is_pack_installed(pdef, root=root)
        if installed:
            continue
        arch = find_inbox_archive(pdef["id"], inbox_dir=inbox_dir)
        if arch:
            res = install_pack_archive(arch, root=root, progress_cb=progress_cb)
            results.append(res)
    return results

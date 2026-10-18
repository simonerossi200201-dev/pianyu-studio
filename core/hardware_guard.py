"""三级硬件自适应网关 (HardwareGuard) — 保障 32GB 内存 + CPU 电脑流畅运行，智能管理 16GB GPU。

遵循 CUTORA 铁律：绝不在此模块中 `import torch`（避免无故占用 2~3 GB 系统内存提交页）。
全部通过 Windows Kernel32 API (`GlobalMemoryStatusEx`) 与无窗 `nvidia-smi` 探测。
"""

from __future__ import annotations

import ctypes
import os
import subprocess
from dataclasses import asdict, dataclass
from typing import Any

from . import load_models_registry, load_studio_config

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


@dataclass
class HardwareProfile:
    cpu_cores: int
    ram_total_gb: float
    ram_avail_gb: float
    ram_load_pct: int
    has_nvidia_gpu: bool
    gpu_name: str
    vram_total_gb: float
    vram_free_gb: float
    vram_used_gb: float
    driver_version: str
    recommended_tier: int  # 1 = 纯CPU极速档, 2 = 轻量GPU/32GB-CPU降级档, 3 = 16GB重型GPU全开档

    @property
    def gpu_available(self) -> bool:
        return self.has_nvidia_gpu

    @property
    def gpu_vram_total_gb(self) -> float:
        return self.vram_total_gb

    @property
    def gpu_vram_free_gb(self) -> float:
        return self.vram_free_gb

    @property
    def tier_label(self) -> str:
        if self.recommended_tier >= 3:
            return "Tier 3 (16GB重型GPU全开模式)"
        if self.recommended_tier == 2:
            return "Tier 2 (8GB中量级GPU模式)"
        return "Tier 1 (32GB内存+CPU安全模式)"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["gpu_available"] = self.gpu_available
        d["gpu_vram_total_gb"] = self.gpu_vram_total_gb
        d["gpu_vram_free_gb"] = self.gpu_vram_free_gb
        d["tier"] = self.tier_label
        d["tier_label"] = self.tier_label
        return d


@dataclass
class CapabilityVerdict:
    model_id: str
    name: str
    tier: int
    can_run: bool
    execution_device: str  # "cuda" | "cpu" | "blocked" | "pending_fiber"
    badge: str
    user_message: str
    cloud_fallback_available: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def query_system_memory() -> tuple[float, float, int]:
    """返回 (总物理内存GB, 可用物理内存GB, 内存占用百分比)。"""
    if os.name == "nt":
        stat = _MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            total_gb = round(stat.ullTotalPhys / (1024 ** 3), 2)
            avail_gb = round(stat.ullAvailPhys / (1024 ** 3), 2)
            return total_gb, avail_gb, int(stat.dwMemoryLoad)
    return 32.0, 16.0, 50


def query_nvidia_gpu() -> dict[str, Any]:
    """通过 nvidia-smi 无窗探测显卡状态，绝不初始化 CUDA Context。"""
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.free,memory.used,driver_version",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=3.0,
            creationflags=CREATE_NO_WINDOW,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            first_line = proc.stdout.strip().splitlines()[0]
            parts = [p.strip() for p in first_line.split(",")]
            if len(parts) >= 5:
                total_mb = float(parts[1])
                free_mb = float(parts[2])
                used_mb = float(parts[3])
                return {
                    "has_gpu": True,
                    "name": parts[0],
                    "vram_total_gb": round(total_mb / 1024.0, 2),
                    "vram_free_gb": round(free_mb / 1024.0, 2),
                    "vram_used_gb": round(used_mb / 1024.0, 2),
                    "driver": parts[4],
                }
    except Exception:
        pass
    return {
        "has_gpu": False,
        "name": "CPU-Only (未检测到 NVIDIA 独立显卡)",
        "vram_total_gb": 0.0,
        "vram_free_gb": 0.0,
        "vram_used_gb": 0.0,
        "driver": "",
    }


def probe_hardware() -> HardwareProfile:
    """获取当前机器完整硬件画像。"""
    ram_total, ram_avail, ram_pct = query_system_memory()
    gpu = query_nvidia_gpu()
    cores = os.cpu_count() or 8

    if gpu["has_gpu"] and gpu["vram_total_gb"] >= 12.0:
        tier = 3
    elif gpu["has_gpu"] or ram_total >= 28.0:
        tier = 2
    else:
        tier = 1

    return HardwareProfile(
        cpu_cores=cores,
        ram_total_gb=ram_total,
        ram_avail_gb=ram_avail,
        ram_load_pct=ram_pct,
        has_nvidia_gpu=gpu["has_gpu"],
        gpu_name=gpu["name"],
        vram_total_gb=gpu["vram_total_gb"],
        vram_free_gb=gpu["vram_free_gb"],
        vram_used_gb=gpu["vram_used_gb"],
        driver_version=gpu["driver"],
        recommended_tier=tier,
    )


def evaluate_model_capability(model_id: str, profile: HardwareProfile | None = None) -> CapabilityVerdict:
    """判断指定模型/功能在当前电脑（如 32GB+CPU 或 16GB GPU）上的运行方式与友好提示。"""
    prof = profile or probe_hardware()
    reg = load_models_registry()
    item = next((m for m in reg.get("models", []) if m["id"] == model_id), None)

    if not item:
        return CapabilityVerdict(
            model_id=model_id,
            name=model_id,
            tier=1,
            can_run=True,
            execution_device="cpu",
            badge="🟢 CPU/GPU 通用",
            user_message="标准组件，可直接运行。",
            cloud_fallback_available=False,
        )

    status = item.get("status", "ready")
    name = item.get("name", model_id)
    tier = int(item.get("tier", 1))
    min_vram = float(item.get("min_vram_gb", 0.0))
    cpu_ok = bool(item.get("cpu_supported", True))

    if status == "pending_fiber_download":
        fallback = item.get("fallback_active", "")
        return CapabilityVerdict(
            model_id=model_id,
            name=name,
            tier=tier,
            can_run=False,
            execution_device="pending_fiber",
            badge="⏳ 待光纤就绪下载",
            user_message=f"该组件已记账预留槽位，等待家里光纤安装后一键运行脚本拉取。当前已自动由【{fallback}】无缝承接！",
            cloud_fallback_available=True,
        )

    # 检查若该模型归属于某个可选乐高扩展包 (Plugin Pack)，且当前精简版电脑尚未安装该扩展包
    try:
        from .pack_manager import get_pack_for_model, is_pack_installed
        pack_def = get_pack_for_model(model_id)
        if pack_def is not None:
            installed, _ = is_pack_installed(pack_def)
            if not installed:
                pack_title = pack_def.get("short_name") or pack_def.get("name") or pack_def["id"]
                fallback_desc = pack_def.get("cloud_fallback") or "云端引擎"
                return CapabilityVerdict(
                    model_id=model_id,
                    name=name,
                    tier=tier,
                    can_run=False,
                    execution_device="pack_missing",
                    badge=f"🧩 需安装【{pack_title}】",
                    user_message=(
                        f"当前使用的是 25GB 轻量化标准版，尚未安装可选扩展包【{pack_title} ({pack_def.get('approx_size_gb', '')}GB)】。"
                        f"如需在本地使用该功能，请将 `{pack_def['id']}.ltxpack` 放入 `packs/` 目录一键安装，或直接使用 {fallback_desc}。"
                    ),
                    cloud_fallback_available=True,
                )
    except Exception:
        pass

    # Tier 1: 原生支持 CPU 极速运行
    if tier == 1:
        dev = "cuda" if (prof.has_nvidia_gpu and prof.vram_free_gb >= 3.0) else "cpu"
        return CapabilityVerdict(
            model_id=model_id,
            name=name,
            tier=1,
            can_run=True,
            execution_device=dev,
            badge="🟢 32GB+CPU 原生流畅",
            user_message="支持在 32GB 内存 + CPU 电脑上全速运行。" if dev == "cpu" else f"已启用 {prof.gpu_name} 加速运行。",
            cloud_fallback_available=False,
        )

    # Tier 2: 有 GPU 用 GPU，无 GPU 或显存不足时自动让 CPU 坚持运行
    if tier == 2 or cpu_ok:
        if prof.has_nvidia_gpu and prof.vram_total_gb >= min_vram:
            return CapabilityVerdict(
                model_id=model_id,
                name=name,
                tier=tier,
                can_run=True,
                execution_device="cuda",
                badge="🟢 GPU 硬件加速",
                user_message=f"检测到 {prof.gpu_name} ({prof.vram_total_gb}GB)，使用显卡高速推理。",
                cloud_fallback_available=False,
            )
        return CapabilityVerdict(
            model_id=model_id,
            name=name,
            tier=tier,
            can_run=True,
            execution_device="cpu",
            badge="🟡 CPU 兼容模式 (自动降级)",
            user_message=(
                f"当前未检测到 >= {min_vram}GB 显存的独立显卡，已自动切换至 32GB 内存 + CPU 模式坚持运行；"
                "耗时会比显卡略长，后台处理期间不影响您继续其他操作。"
            ),
            cloud_fallback_available=False,
        )

    # Tier 3: 必须依赖大显存显卡 (如 LTX-2.5 22B / FLUX.2 klein / IC-LoRA)
    if prof.has_nvidia_gpu and prof.vram_total_gb >= min_vram:
        return CapabilityVerdict(
            model_id=model_id,
            name=name,
            tier=3,
            can_run=True,
            execution_device="cuda",
            badge=f"⚡ 16GB GPU 独享算力 ({min_vram}GB+)",
            user_message=f"将通过 VRAMBroker 自动清空其他显存占用，独享 {prof.gpu_name} 全速生成。",
            cloud_fallback_available=True,
        )

    return CapabilityVerdict(
        model_id=model_id,
        name=name,
        tier=3,
        can_run=False,
        execution_device="blocked",
        badge=f"🔴 需 {min_vram}GB+ NVIDIA 显卡",
        user_message=(
            f"【硬件提示】{name} 需要至少 {min_vram}GB 显存的 NVIDIA 独立显卡（当前：{prof.gpu_name}，"
            f"总显存 {prof.vram_total_gb}GB）。为保护您的电脑不卡死，已自动禁用本地运行；"
            "您可以一键切换至 Orante 云端引擎（Grok / Google Flow）完成该任务！"
        ),
        cloud_fallback_available=True,
    )


def evaluate_all_models() -> dict[str, Any]:
    """返回硬件画像与全部注册模型的可用性矩阵。"""
    prof = probe_hardware()
    reg = load_models_registry()
    verdicts = [evaluate_model_capability(m["id"], prof).to_dict() for m in reg.get("models", [])]
    return {
        "hardware": prof.to_dict(),
        "models": verdicts,
    }


def check_ram_watermark() -> dict[str, Any]:
    """32GB 内存安全水位检查（供并行导出与批量拼接守护）。"""
    cfg = load_studio_config().get("hardware", {})
    pause_gb = float(cfg.get("ram_pause_gb", 2.0))
    shed_gb = float(cfg.get("ram_shed_gb", 1.5))
    total_gb, avail_gb, load_pct = query_system_memory()
    if avail_gb < shed_gb:
        action = "shed_worker"
    elif avail_gb < pause_gb:
        action = "degrade_to_cpu"
    else:
        action = "normal"
    return {
        "ram_total_gb": total_gb,
        "ram_avail_gb": avail_gb,
        "ram_load_pct": load_pct,
        "action": action,
    }


class HardwareGuard:
    """面向对象封装接口，供启动器、安装程序与 AI 工坊统一调用。"""

    def probe_system(self) -> HardwareProfile:
        return probe_hardware()

    def evaluate_all_models(self) -> list[dict[str, Any]]:
        return evaluate_all_models()["models"]


_GUARD_SINGLETON = HardwareGuard()


def get_hardware_guard() -> HardwareGuard:
    return _GUARD_SINGLETON

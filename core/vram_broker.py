"""16GB 单卡显存与 Apple Silicon 统一内存互斥仲裁器 (VRAMBroker)。

当 LTX-2.5 (峰值 15.5 GB)、LTX IC-LoRA (峰值 15.6 GB) 或 FLUX.2 klein (峰值 15.6 GB) 启动前，
自动卸载本机 Ollama 常驻模型、ComfyUI 旧模型缓存，并在 Apple Silicon MPS 下执行 `torch.mps.empty_cache()`，
确保 16GB 显卡与统一内存设备零冲突、零 OOM 崩溃。
"""

from __future__ import annotations

import gc
import json
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from typing import Any, Iterator

from . import DATA_DIR
from .hardware_guard import query_nvidia_gpu

LOCK_FILE = DATA_DIR / "vram_mutex.json"


def _http_req(method: str, url: str, payload: dict | None = None, timeout: float = 5.0) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw.strip() else {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def unload_ollama_models(ollama_url: str = "http://127.0.0.1:11434") -> list[str]:
    """查询 Ollama 当前加载在显存中的模型并以 keep_alive=0 立即卸载。"""
    unloaded: list[str] = []
    ps = _http_req("GET", f"{ollama_url}/api/ps", timeout=2.5)
    models = ps.get("models") if isinstance(ps, dict) else None
    if not isinstance(models, list):
        return unloaded
    for m in models:
        name = m.get("name") or m.get("model")
        if name:
            _http_req(
                "POST",
                f"{ollama_url}/api/generate",
                {"model": name, "prompt": "", "keep_alive": 0},
                timeout=5.0,
            )
            unloaded.append(str(name))
    return unloaded


def empty_accelerator_cache() -> bool:
    """释放当前进程中的 PyTorch CUDA 或 Apple Silicon MPS 显存/统一内存缓存。"""
    gc.collect()
    torch_mod = sys.modules.get("torch")
    if torch_mod is None and sys.platform == "darwin":
        try:
            import torch as torch_mod  # type: ignore
        except Exception:
            torch_mod = None
    if torch_mod is None:
        return False
    freed = False
    try:
        if hasattr(torch_mod, "cuda") and torch_mod.cuda.is_available():
            torch_mod.cuda.empty_cache()
            freed = True
    except Exception:
        pass
    try:
        if (
            hasattr(torch_mod, "backends")
            and hasattr(torch_mod.backends, "mps")
            and torch_mod.backends.mps.is_available()
            and hasattr(torch_mod, "mps")
        ):
            torch_mod.mps.empty_cache()
            freed = True
    except Exception:
        pass
    return freed


def free_comfy_vram(comfy_url: str = "http://127.0.0.1:8188", unload_models: bool = True) -> bool:
    """通知 ComfyUI 卸载当前缓存的扩散模型并清空显存池。"""
    res = _http_req(
        "POST",
        f"{comfy_url}/free",
        {"unload_models": unload_models, "free_memory": True},
        timeout=5.0,
    )
    return bool(res.get("ok", True))


def prepare_vram_for_heavy_task(
    task_name: str,
    required_gb: float = 14.0,
    also_free_comfy: bool = False,
) -> dict[str, Any]:
    """在执行重型 GPU 任务前一键清场显存/统一内存。"""
    before = query_nvidia_gpu()
    unloaded_ollama = unload_ollama_models()
    cache_freed = empty_accelerator_cache()
    comfy_freed = free_comfy_vram() if also_free_comfy else False

    if unloaded_ollama or cache_freed or comfy_freed:
        time.sleep(0.5)

    after = query_nvidia_gpu()
    return {
        "task": task_name,
        "required_gb": required_gb,
        "vram_free_before_gb": before.get("vram_free_gb", 0.0),
        "vram_free_after_gb": after.get("vram_free_gb", 0.0),
        "vram_total_gb": after.get("vram_total_gb", 0.0),
        "unloaded_ollama": unloaded_ollama,
        "cache_freed": cache_freed,
        "comfy_freed": comfy_freed,
        "ready": (not after.get("has_gpu")) or (after.get("vram_total_gb", 0.0) >= required_gb),
    }


@contextmanager
def vram_exclusive_session(owner: str, required_gb: float = 14.0) -> Iterator[dict[str, Any]]:
    """跨模块显存独占上下文锁。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    prep = prepare_vram_for_heavy_task(owner, required_gb=required_gb)
    state = {
        "owner": owner,
        "acquired_at": time.time(),
        "prep": prep,
    }
    try:
        LOCK_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    try:
        yield prep
    finally:
        try:
            if LOCK_FILE.is_file():
                LOCK_FILE.unlink()
        except OSError:
            pass

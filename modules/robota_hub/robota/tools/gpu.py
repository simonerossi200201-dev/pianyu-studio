"""GPU status / freeing VRAM by unloading Ollama models."""
from __future__ import annotations

import shutil
import subprocess
import time

from robota import config
from robota.tools import _http

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _ollama() -> str:
    return str(config.settings()["local"].get("ollama", "http://localhost:11434")).rstrip("/")


def _num(v: str):
    v = v.strip()
    try:
        return int(float(v))
    except ValueError:
        return None  # "[N/A]" etc.


def _smi(args: list[str]) -> list[list[str]]:
    exe = shutil.which("nvidia-smi") or r"C:\Windows\System32\nvidia-smi.exe"
    r = subprocess.run([exe, *args, "--format=csv,noheader,nounits"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=15, creationflags=_NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[:300] or f"nvidia-smi exit {r.returncode}")
    return [[c.strip() for c in line.split(",")] for line in r.stdout.splitlines() if line.strip()]


def ollama_loaded() -> dict:
    """Models currently loaded in Ollama (GET /api/ps)."""
    try:
        data = _http.request("GET", _ollama() + "/api/ps", timeout=5, service="Ollama")
    except _http.ServiceDown as exc:
        return {"ok": False, "running": False, "error": str(exc), "models": []}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "running": True, "error": str(exc), "models": []}
    models = [{"name": m.get("name") or m.get("model"),
               "size_vram_mb": round((m.get("size_vram") or 0) / 2**20),
               "expires_at": m.get("expires_at")} for m in (data or {}).get("models", [])]
    return {"ok": True, "running": True, "models": models}


def gpu_status() -> dict:
    """VRAM per GPU, compute processes, and Ollama loaded models."""
    out: dict = {"ok": True, "gpus": [], "processes": [], "ollama": ollama_loaded()}
    try:
        for row in _smi(["--query-gpu=index,name,memory.total,memory.used,memory.free,utilization.gpu"]):
            idx, name, tot, used, free, util = (row + [""] * 6)[:6]
            out["gpus"].append({"index": _num(idx), "name": name, "mem_total_mb": _num(tot),
                                "mem_used_mb": _num(used), "mem_free_mb": _num(free), "util_pct": _num(util)})
        for row in _smi(["--query-compute-apps=pid,process_name,used_memory"]):
            pid, pname, mem = (row + [""] * 3)[:3]
            out["processes"].append({"pid": _num(pid), "name": pname, "used_mb": _num(mem)})
    except Exception as exc:  # noqa: BLE001
        out["ok"] = False
        out["error"] = f"nvidia-smi failed: {exc}"
    return out


def gpu_free() -> dict:
    """Unload every loaded Ollama model (keep_alive=0). Returns the list of freed models."""
    loaded = ollama_loaded()
    if not loaded["ok"]:
        return {"ok": not loaded.get("running", False), "freed": [], "note": loaded.get("error")}
    freed, errors = [], []
    for m in loaded["models"]:
        name = m["name"]
        try:
            _http.request("POST", _ollama() + "/api/generate", json={"model": name, "keep_alive": 0},
                          timeout=60, service="Ollama")
            freed.append(name)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {exc}")
    return {"ok": not errors, "freed": freed, "errors": errors}


def _free_mb() -> int | None:
    gpus = gpu_status().get("gpus") or []
    vals = [g["mem_free_mb"] for g in gpus if g.get("mem_free_mb") is not None]
    return max(vals) if vals else None


def ensure_free(min_free_mb: int = 0, wait_s: float = 20.0) -> dict:
    """Make sure at least `min_free_mb` VRAM is free (0 = always unload Ollama models).
    Used by the task queue before GPU tasks."""
    before = _free_mb()
    if min_free_mb and before is not None and before >= min_free_mb:
        return {"ok": True, "free_mb": before, "freed": [], "action": "none"}
    res = gpu_free()
    after = _free_mb()
    deadline = time.time() + wait_s
    while res["freed"] and min_free_mb and (after or 0) < min_free_mb and time.time() < deadline:
        time.sleep(1.0)
        after = _free_mb()
    ok = (after is None) or not min_free_mb or after >= min_free_mb
    return {"ok": ok, "free_mb_before": before, "free_mb": after, "freed": res["freed"],
            "errors": res.get("errors", []), "action": "unloaded_ollama"}

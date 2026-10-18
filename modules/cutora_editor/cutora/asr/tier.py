# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""T12f hardware auto-tier + three-layer word timing. One active profile.

S/A/B/C/D/E = which ASR model this machine can run.
align / word_timestamps / sentence = how words get timestamps when the
aligner is missing. Same 'active tier' state, same UI label, never a
silent fallback.
"""
from __future__ import annotations

import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from dataclasses import dataclass, field
from typing import Any, Callable, FrozenSet

HEADROOM = 0.20
WATCHDOG_MULT = 10.0
SETTINGS_TIER_KEY = "asr.tier"
SETTINGS_WORD_KEY = "asr.word_timing"

# Highest first. Minima already listed; callers apply HEADROOM on top.
# GPU auto = small. Turbo is user-optional (experimental until anti-hallucination).
# CPU = small. medium / large-v3 removed from the product.
ASR_TIERS: tuple[dict[str, Any], ...] = (
    {"id": "T", "asr_id": "asr-large-v3-turbo", "size": "turbo",
     "compute": "float16", "device": "cuda", "vram_gb": 3.0, "ram_gb": 0.0,
     "experimental": True},
    {"id": "S", "asr_id": "asr-small", "size": "small",
     "compute": "float16", "device": "cuda", "vram_gb": 2.0, "ram_gb": 0.0},
    {"id": "E", "asr_id": "asr-small", "size": "small",
     "compute": "int8", "device": "cpu", "vram_gb": 0.0, "ram_gb": 0.0},
)

WORD_TIMING_LAYERS = ("align", "word_timestamps", "sentence")
WORD_TIMING_LABEL = {
    "align": "词级对齐",
    "word_timestamps": "近似词级",
    "sentence": "句级",
}

_TIER_BY_ID = {t["id"]: t for t in ASR_TIERS}
_TIER_ORDER = [t["id"] for t in ASR_TIERS]

# Process-wide active profile. Tests inject via set_active_profile.
_ACTIVE: "ActiveProfile | None" = None
_ACTIVE_LOCK = threading.Lock()


@dataclass(frozen=True)
class HardwareProbe:
    cuda_count: int = 0
    vram_total_gb: float = 0.0
    vram_free_gb: float = 0.0
    ram_total_gb: float = 0.0
    ram_free_gb: float = 0.0
    cpu_count: int = 1
    models_on_disk: FrozenSet[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class WordTiming:
    layer: str
    notify: str | None
    auto: bool = True


@dataclass(frozen=True)
class ActiveProfile:
    asr_tier: str
    asr_id: str
    size: str
    compute: str
    device: str
    word_timing: str
    auto: bool
    label: str
    notify: str | None = None
    warning: str | None = None


class WatchdogTimeout(Exception):
    """ASR for one clip exceeded 10× duration."""


class TierOOM(Exception):
    """CUDA OOM at the current ASR tier."""


def asr_tier(tier_id: str) -> dict[str, Any]:
    row = _TIER_BY_ID.get(str(tier_id or "").strip().upper())
    if row is None:
        raise KeyError("unknown ASR tier %r" % tier_id)
    return dict(row)


def next_lower_tier(tier_id: str) -> str | None:
    tid = str(tier_id or "").strip().upper()
    try:
        i = _TIER_ORDER.index(tid)
    except ValueError:
        return None
    if i + 1 >= len(_TIER_ORDER):
        return None
    return _TIER_ORDER[i + 1]


def _model_present(asr_id: str, probe: HardwareProbe) -> bool:
    disk = probe.models_on_disk or frozenset()
    if asr_id in disk:
        return True
    aliases = {
        "asr-large-v3-turbo": ("asr_large-v3-turbo", "asr_large_v3_turbo", "turbo"),
        "asr-small": ("asr_small", "small"),
    }
    return any(a in disk for a in aliases.get(asr_id, ()))


def tier_fits(tier: dict[str, Any], probe: HardwareProbe) -> bool:
    """True when probe has 20% headroom over the listed minimum."""
    asr_id = str(tier["asr_id"])
    if asr_id != "asr-small" and not _model_present(asr_id, probe):
        return False
    if tier["device"] == "cuda":
        if int(probe.cuda_count or 0) < 1:
            return False
        need = float(tier["vram_gb"]) * (1.0 + HEADROOM)
        if float(probe.vram_free_gb or 0.0) < need:
            return False
        return True
    need_ram = float(tier["ram_gb"]) * (1.0 + HEADROOM)
    if need_ram > 0 and float(probe.ram_free_gb or 0.0) < need_ram:
        return False
    return True


def select_asr_tier(
    probe: HardwareProbe,
    preference: str = "auto",
) -> tuple[dict[str, Any], str | None]:
    """Return (tier, warning). warning is set when the user asked for too much."""
    want = (preference or "auto").strip().lower()
    if want in ("", "auto"):
        for row in ASR_TIERS:
            if row.get("experimental"):
                continue
            if tier_fits(row, probe):
                return dict(row), None
        return dict(ASR_TIERS[-1]), None
    tid = want.upper()
    if tid not in _TIER_BY_ID:
        raise KeyError("unknown ASR tier %r" % preference)
    row = dict(_TIER_BY_ID[tid])
    if tier_fits(row, probe):
        return row, None
    # Manual pick above this machine: warn, still return the asked row so the
    # caller can refuse to start. Do not silently drop.
    fitted = None
    for cand in ASR_TIERS:
        if tier_fits(cand, probe):
            fitted = cand
            break
    fitted = fitted or ASR_TIERS[-1]
    warn = "档位 %s 超出本机能力（自动最高为 %s）" % (tid, fitted["id"])
    return row, warn


def choose_word_timing(
    lang: str,
    *,
    has_align: bool,
    word_ts_available: bool = True,
) -> WordTiming:
    """align → word_timestamps → sentence. Never silent."""
    from cutora.asr.models import ALIGN_IT_SENTENCE_NOTE, ALIGN_NOT_BUNDLED_NOTE
    code = (lang or "").strip().lower()
    if has_align:
        return WordTiming("align", notify=None)
    if word_ts_available:
        return WordTiming("word_timestamps", notify="近似词级")
    note = ALIGN_IT_SENTENCE_NOTE if code == "it" else ALIGN_NOT_BUNDLED_NOTE
    return WordTiming("sentence", notify=note)


def notify_fallback(from_id: str, to_id: str, log: Callable[[str], None] | None = None) -> str:
    msg = "已从 %s 降到 %s" % (from_id, to_id)
    if callable(log):
        log(msg)
    return msg


def drop_one_tier(tier_id: str, log: Callable[[str], None] | None = None) -> str | None:
    nxt = next_lower_tier(tier_id)
    if nxt is None:
        if callable(log):
            log("已在最低档 %s，无法再降" % tier_id)
        return None
    notify_fallback(tier_id, nxt, log)
    return nxt


def format_tier_label(tier: dict[str, Any], word_timing: str, *, auto: bool) -> str:
    dev = "GPU" if tier["device"] == "cuda" else "CPU"
    prec = "fp16" if tier["compute"] == "float16" else tier["compute"]
    wt = WORD_TIMING_LABEL.get(word_timing, word_timing)
    body = "%s · %s %s · %s" % (tier["size"], dev, prec, wt)
    if auto:
        return "自动（当前：%s）" % body
    return "手动 %s（%s）" % (tier["id"], body)


def build_profile(
    probe: HardwareProbe,
    *,
    preference: str = "auto",
    lang: str = "en",
    has_align: bool | None = None,
    word_ts_available: bool = True,
) -> ActiveProfile:
    tier, warning = select_asr_tier(probe, preference)
    if has_align is None:
        has_align = _align_ready(lang, probe)
    timing = choose_word_timing(
        lang, has_align=bool(has_align), word_ts_available=word_ts_available)
    auto = (preference or "auto").strip().lower() in ("", "auto")
    label = format_tier_label(tier, timing.layer, auto=auto)
    notify = warning or timing.notify
    return ActiveProfile(
        asr_tier=str(tier["id"]),
        asr_id=str(tier["asr_id"]),
        size=str(tier["size"]),
        compute=str(tier["compute"]),
        device=str(tier["device"]),
        word_timing=timing.layer,
        auto=auto,
        label=label,
        notify=notify,
        warning=warning,
    )


def get_active_profile() -> ActiveProfile | None:
    with _ACTIVE_LOCK:
        return _ACTIVE


def set_active_profile(profile: ActiveProfile | None) -> None:
    global _ACTIVE
    with _ACTIVE_LOCK:
        _ACTIVE = profile


def apply_profile(profile: ActiveProfile, log: Callable[[str], None] | None = None) -> ActiveProfile:
    prev = get_active_profile()
    set_active_profile(profile)
    if prev is not None and prev.asr_tier != profile.asr_tier and callable(log):
        notify_fallback(prev.asr_tier, profile.asr_tier, log)
    if profile.notify and callable(log):
        log(profile.notify)
    return profile


def _align_ready(lang: str, probe: HardwareProbe) -> bool:
    code = (lang or "").strip().lower()
    disk = probe.models_on_disk or frozenset()
    if code in ("en", "it"):
        return ("align-%s" % code) in disk or "align_wav2vec2" in disk
    return ("align-%s" % code) in disk or ("align_%s" % code) in disk


def probe_hardware(impl: Callable[[], HardwareProbe] | None = None) -> HardwareProbe:
    if impl is not None:
        return impl()
    return _default_probe()


def _default_probe() -> HardwareProbe:
    cuda_count = 0
    vram_total = 0.0
    vram_free = 0.0
    try:
        import ctranslate2
        cuda_count = int(ctranslate2.get_cuda_device_count() or 0)
    except Exception:
        cuda_count = 0
    try:
        import torch
        if torch.cuda.is_available():
            cuda_count = max(cuda_count, int(torch.cuda.device_count() or 0))
            free_b, total_b = torch.cuda.mem_get_info()
            vram_free = float(free_b) / (1024 ** 3)
            vram_total = float(total_b) / (1024 ** 3)
    except Exception:
        pass
    if vram_total <= 0:
        try:
            from cutora.render.export_guard import gpu_memory_gb
            info = gpu_memory_gb()
            if info:
                vram_total, _used, vram_free = (float(info[0]), float(info[1]), float(info[2]))
                cuda_count = max(cuda_count, 1)
        except Exception:
            pass
    ram_total = 0.0
    ram_free = 0.0
    try:
        import psutil
        vm = psutil.virtual_memory()
        ram_total = float(vm.total) / (1024 ** 3)
        ram_free = float(vm.available) / (1024 ** 3)
    except Exception:
        pass
    cpu_count = int(os.cpu_count() or 1)
    return HardwareProbe(
        cuda_count=cuda_count,
        vram_total_gb=vram_total,
        vram_free_gb=vram_free,
        ram_total_gb=ram_total,
        ram_free_gb=ram_free,
        cpu_count=cpu_count,
        models_on_disk=_disk_model_ids(),
    )


def _disk_model_ids() -> frozenset[str]:
    ids: set[str] = set()
    try:
        from cutora.core.paths import models_root
        root = models_root()
        if os.path.isdir(root):
            for name in os.listdir(root):
                ids.add(name)
    except Exception:
        pass
    try:
        from cutora.asr.models import catalog, verify_model
        for row in catalog():
            if verify_model(row) == "ready":
                ids.add(str(row.get("id") or ""))
                if row.get("dirname"):
                    ids.add(str(row["dirname"]))
    except Exception:
        pass
    ids.discard("")
    return frozenset(ids)


def asr_dirname(asr_id: str) -> str:
    return {
        "asr-small": "asr_small",
        "asr-large-v3-turbo": "asr_large-v3-turbo",
    }.get(asr_id, asr_id.replace("-", "_", 1) if asr_id.startswith("asr-") else asr_id)


def resolve_asr_path(profile: ActiveProfile | None = None) -> str | None:
    """runtime/models/<dirname> for the active (or given) ASR tier."""
    from cutora.core.paths import models_root
    prof = profile or get_active_profile()
    if prof is None:
        return None
    path = os.path.join(models_root(), asr_dirname(prof.asr_id))
    if os.path.isdir(path):
        return path
    return None


def watchdog_timeout_sec(duration_sec: float) -> float:
    return max(1.0, float(duration_sec) * WATCHDOG_MULT)


def run_with_watchdog(fn: Callable[[], Any], timeout_sec: float) -> Any:
    """Run fn; if it exceeds timeout_sec raise WatchdogTimeout.

    The worker thread is not forcibly killed (CPython). Caller must treat
    the result as abandoned, drop a tier, and continue the batch.
    """
    limit = float(timeout_sec)
    pool = ThreadPoolExecutor(max_workers=1)
    fut = pool.submit(fn)
    try:
        return fut.result(timeout=limit)
    except FuturesTimeout as exc:
        raise WatchdogTimeout("ASR 超过 %.1fs 未返回" % limit) from exc
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def handle_oom(current_tier: str, log: Callable[[str], None] | None = None) -> str | None:
    """CUDA OOM → unload is the caller's job; we only pick the next tier."""
    return drop_one_tier(current_tier, log)


_SIZE_RANK = {
    "asr-large-v3-turbo": 1,
    "asr-small": 0,
}


def path_to_asr_id(asr_path: str | None) -> str:
    leaf = os.path.basename(str(asr_path or "").rstrip("\\/")).lower()
    if "turbo" in leaf:
        return "asr-large-v3-turbo"
    if "small" in leaf:
        return "asr-small"
    if "medium" in leaf:
        return "asr-medium"
    if "large-v3" in leaf or leaf in ("asr_large", "large"):
        return "asr-large-v3"
    return "asr-small"


def gpu_compute(table_compute: str) -> str:
    """Map table compute to what this GPU can actually run.

    Known platform limit: Blackwell (sm_120, RTX 50 / Luca's 5060 Ti)
    rejects CTranslate2 int8 (``efficient int8 computation``). detect_gpu_config
    already returns float16 for sm>=7 except Pascal. Always prefer that
    over the table's int8 on CUDA.
    """
    try:
        from cutora.core.config import detect_gpu_config
        dev, ct, _reason = detect_gpu_config(verbose=False)
        if dev == "cuda" and ct in ("float16", "int8"):
            return ct
    except Exception:
        pass
    return table_compute


def wanted_peak_tier_id(asr_id: str) -> str:
    """Highest table row that uses this catalog id (S for large-v3, B for medium)."""
    for row in ASR_TIERS:
        if row["asr_id"] == asr_id:
            return str(row["id"])
    return "E"


def resolve_runtime_tier(
    wanted_path: str | None,
    probe: HardwareProbe,
    log: Callable[[str], None] | None = None,
) -> tuple[dict[str, Any], str | None]:
    """Actual load = min(user ceiling from model_paths, machine capable).

    ``model_paths.json`` is the ceiling (what the user asked for).
    The hardware table is the floor we refuse to exceed. Comparison
    tables may change the default ceiling; the cap is in force today.
    """
    want_id = path_to_asr_id(wanted_path)
    if want_id not in _SIZE_RANK:
        if callable(log):
            log("模型 %s 不在产品目录，回落到 small" % want_id)
        want_id = "asr-small"
    forced = (os.environ.get("CUTORA_ASR_TIER") or "").strip().upper()
    if forced in _TIER_BY_ID:
        want_id = str(_TIER_BY_ID[forced]["asr_id"])
        want_peak_force = forced
    else:
        want_peak_force = None
    want_rank = _SIZE_RANK.get(want_id, 0)
    want_peak = want_peak_force or wanted_peak_tier_id(want_id)
    picked = None
    start = 0
    if want_peak_force:
        try:
            start = _TIER_ORDER.index(want_peak_force)
        except ValueError:
            start = 0
    for row in ASR_TIERS[start:]:
        if _SIZE_RANK.get(row["asr_id"], 0) > want_rank:
            continue
        if tier_fits(row, probe):
            picked = dict(row)
            break
    if picked is None:
        picked = dict(ASR_TIERS[-1])
    # Table E is "small CPU", but a GPU machine should still run small on CUDA.
    if int(probe.cuda_count or 0) >= 1 and picked["asr_id"] == "asr-small":
        picked = dict(picked)
        picked["device"] = "cuda"
    msg = None
    if picked["id"] != want_peak:
        wi = _TIER_ORDER.index(want_peak)
        pi = _TIER_ORDER.index(picked["id"])
        if pi > wi:
            msg = notify_fallback(want_peak, picked["id"], log)
    return picked, msg


def is_oom_error(exc: BaseException | str) -> bool:
    text = str(exc or "").lower()
    if "out of memory" in text or "cuda oom" in text:
        return True
    if "cuda" in text and "memory" in text:
        return True
    return False


def run_in_subprocess(
    argv: list[str],
    timeout_sec: float,
    *,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
) -> subprocess.CompletedProcess:
    """Run argv; on timeout kill the process tree (CUDA-safe). Threads cannot."""
    import subprocess as sp
    limit = float(timeout_sec)
    proc = sp.Popen(
        argv,
        stdout=sp.PIPE,
        stderr=sp.PIPE,
        env=env,
        cwd=cwd,
        text=True,
    )
    try:
        out, err = proc.communicate(timeout=limit)
    except sp.TimeoutExpired as exc:
        kill_process_tree(proc.pid)
        try:
            proc.kill()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
        raise WatchdogTimeout("ASR 超过 %.1fs 未返回（已杀子进程 %s）" % (
            limit, proc.pid)) from exc
    return sp.CompletedProcess(argv, proc.returncode, out, err)


def kill_process_tree(pid: int) -> None:
    """Terminate a process and its children. Windows: taskkill /F /T."""
    if not pid:
        return
    try:
        if os.name == "nt":
            import subprocess as sp
            sp.run(
                ["taskkill", "/F", "/T", "/PID", str(int(pid))],
                capture_output=True, timeout=8,
            )
        else:
            os.kill(int(pid), 9)
    except Exception:
        pass


def load_preference() -> str:
    try:
        from cutora.core.history import _load_app_settings
        raw = ((_load_app_settings() or {}).get(SETTINGS_TIER_KEY) or "auto")
        return str(raw).strip() or "auto"
    except Exception:
        return "auto"


def save_preference(value: str) -> None:
    from cutora.core.history import _load_app_settings, _save_app_settings
    settings = _load_app_settings() or {}
    settings[SETTINGS_TIER_KEY] = str(value or "auto")
    _save_app_settings(settings)


def refresh_profile(
    *,
    probe: HardwareProbe | None = None,
    preference: str | None = None,
    lang: str = "en",
    has_align: bool | None = None,
    log: Callable[[str], None] | None = None,
) -> ActiveProfile:
    hw = probe if probe is not None else probe_hardware()
    pref = preference if preference is not None else load_preference()
    profile = build_profile(hw, preference=pref, lang=lang, has_align=has_align)
    return apply_profile(profile, log=log)

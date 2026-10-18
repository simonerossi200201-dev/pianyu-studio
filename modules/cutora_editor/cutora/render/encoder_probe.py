# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Hardware encoder probe. Cache on disk. Not NVENC-only: AMF/QSV have slots.

This round the product only *selects* NVENC. AMF/QSV are probed and stored so
the table is not NVIDIA-shaped; they are not wired into encode_args yet.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timezone

from cutora.core.log import get_logger
from cutora.core.paths import settings_file
from cutora.core.proc import popen as _no_window_popen, run as _no_window_run

logger = get_logger("cutora.render.encoder_probe")

SKIP_ENV = "CUTORA_SKIP_ENCODER_PROBE"
CACHE_NAME = "encoder_probe.json"
PROBE_SECONDS = 1.0
CACHE_VERSION = 1

# Vendor slots. selected_vendor this round is only "nvenc" when it works.
SLOTS = (
    ("nvenc", "h264_nvenc"),
    ("nvenc", "hevc_nvenc"),
    ("nvenc", "av1_nvenc"),
    ("amf", "h264_amf"),
    ("amf", "hevc_amf"),
    ("qsv", "h264_qsv"),
    ("qsv", "hevc_qsv"),
    ("qsv", "av1_qsv"),
)

_cache = None


def _ffmpeg() -> str:
    from cutora.core.config import FFMPEG_PATH
    return FFMPEG_PATH


def _ffmpeg_version(ffmpeg: str) -> str:
    try:
        r = _no_window_run(
            [ffmpeg, "-hide_banner", "-version"],
            capture_output=True, timeout=15,
        )
        line = (r.stdout or b"").decode("utf-8", errors="replace").splitlines()
        return (line[0] if line else "").strip()[:200]
    except Exception as exc:
        return "unreadable: %s" % exc


def cache_path() -> str:
    return settings_file(CACHE_NAME)


def _empty_encoders() -> dict:
    return {name: {"ok": False, "error": "not probed", "seconds": 0.0} for _v, name in SLOTS}


def load_cache() -> dict | None:
    path = cache_path()
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("version") != CACHE_VERSION:
        return None
    return data


def save_cache(data: dict) -> None:
    path = cache_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def probe_argv(ffmpeg: str, encoder: str) -> list[str]:
    """-t 1, not 0.1. T08-0: 0.1 fails NVENC on this machine with EOF -22."""
    cmd = [
        ffmpeg, "-hide_banner", "-y",
        "-f", "lavfi",
        "-i", "testsrc2=size=1280x720:rate=30:duration=%.0f" % PROBE_SECONDS,
        "-t", str(int(PROBE_SECONDS)),
        "-an",
        "-c:v", encoder,
        "-pix_fmt", "yuv420p",
    ]
    if encoder.endswith("_nvenc"):
        cmd += ["-preset", "p4"]
    cmd += ["-f", "null", "-"]
    return cmd


def _run_one(ffmpeg: str, encoder: str) -> dict:
    cmd = probe_argv(ffmpeg, encoder)
    t0 = time.perf_counter()
    try:
        r = _no_window_run(cmd, capture_output=True, timeout=30)
        dt = time.perf_counter() - t0
        err = (r.stderr or b"").decode("utf-8", errors="replace")[-400:]
        ok = r.returncode == 0
        return {"ok": ok, "error": "" if ok else err.strip(), "seconds": round(dt, 3)}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "seconds": round(time.perf_counter() - t0, 3)}


def probe(force: bool = False) -> dict:
    """First-boot / cache-miss real encode. Returns the cache dict."""
    global _cache
    if os.environ.get(SKIP_ENV):
        data = {
            "version": CACHE_VERSION,
            "ffmpeg": _ffmpeg(),
            "ffmpeg_version": "skipped",
            "probed_at": None,
            "duration_sec": PROBE_SECONDS,
            "encoders": _empty_encoders(),
            "selected_vendor": None,
            "skip": True,
        }
        _cache = data
        return data
    ffmpeg = _ffmpeg()
    version = _ffmpeg_version(ffmpeg)
    cached = None if force else load_cache()
    if (
        cached
        and cached.get("ffmpeg") == ffmpeg
        and cached.get("ffmpeg_version") == version
        and isinstance(cached.get("encoders"), dict)
    ):
        _cache = cached
        return cached
    encoders = {}
    for _vendor, name in SLOTS:
        encoders[name] = _run_one(ffmpeg, name)
        logger.info(
            "encoder probe %s ok=%s %.2fs %s",
            name, encoders[name]["ok"], encoders[name]["seconds"],
            (encoders[name].get("error") or "")[:80],
        )
    nvenc_ok = bool(encoders.get("h264_nvenc", {}).get("ok"))
    data = {
        "version": CACHE_VERSION,
        "ffmpeg": ffmpeg,
        "ffmpeg_version": version,
        "probed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "duration_sec": PROBE_SECONDS,
        "encoders": encoders,
        "selected_vendor": "nvenc" if nvenc_ok else None,
        "skip": False,
    }
    save_cache(data)
    _cache = data
    return data


def ensure_probed() -> dict:
    global _cache
    if _cache is not None:
        return _cache
    return probe(force=False)


def hw_ok(encoder: str) -> bool:
    data = ensure_probed()
    row = (data.get("encoders") or {}).get(encoder) or {}
    return bool(row.get("ok"))


def pick_h264(want_gpu: bool) -> tuple[str, str]:
    """Encoder name + UI reason. Probe failure → libx264 *before* export starts."""
    if not want_gpu:
        return "libx264", "GPU 关闭，使用 libx264"
    if os.environ.get(SKIP_ENV):
        return "libx264", "探测已跳过，使用 libx264"
    data = ensure_probed()
    if hw_ok("h264_nvenc"):
        return "h264_nvenc", "h264_nvenc 探测通过"
    err = ((data.get("encoders") or {}).get("h264_nvenc") or {}).get("error") or "失败"
    return "libx264", "硬件编码不可用，已回退 libx264（%s）" % err[:80]


def pick_hevc(want_gpu: bool) -> tuple[str, str]:
    if not want_gpu:
        return "libx265", "GPU 关闭，使用 libx265"
    if os.environ.get(SKIP_ENV):
        return "libx265", "探测已跳过，使用 libx265"
    ensure_probed()
    if hw_ok("hevc_nvenc"):
        return "hevc_nvenc", "hevc_nvenc 探测通过"
    return "libx265", "hevc_nvenc 不可用，已回退 libx265"


def status_text() -> str:
    if os.environ.get(SKIP_ENV):
        return "硬件编码探测：已跳过"
    data = load_cache() or _cache
    if not data:
        return "硬件编码探测：尚未运行（首次导出或启动时探测 1 秒片源）"
    h264 = (data.get("encoders") or {}).get("h264_nvenc") or {}
    if h264.get("ok"):
        return "硬件编码：h264_nvenc 可用"
    return "硬件编码不可用，导出将使用 libx264"


def reset_for_tests() -> None:
    global _cache
    _cache = None

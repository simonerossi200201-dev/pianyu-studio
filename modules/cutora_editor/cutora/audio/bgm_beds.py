# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""短视频可循环背景配乐床（6 首）：由本地 Stable Audio Open 1.0 模型生成并完成后期无缝循环与响度对齐。

* 许可证：Stability AI Community License（年营收低于 100 万美元免费商用，详见 resources/audio/bgm_beds/LICENSE.txt）。
* 规格：48 kHz / 立体声 / 16 bit WAV，综合响度 -16.0 LUFS，峰值 ≤ -1.2 dBFS，首尾等功率无缝循环交叉淡化。
* 接口：``CATALOG``、``bgm_path``、``available_path``、``place_bgm_bed``。
"""
from __future__ import annotations

import hashlib
import json
import os
import wave

import numpy as np

from cutora.core.log import get_logger
from cutora.core.paths import RESOURCES

_log = get_logger("cutora.audio.bgm_beds")

SR = 48000
LICENSE = "Stability AI Community License (Stable Audio Open 1.0, commercial use permitted < $1M USD revenue)"
BGM_DIR = os.path.join(RESOURCES, "audio", "bgm_beds")
PROVENANCE_NAME = "PROVENANCE.json"
LICENSE_FILE_NAME = "LICENSE.txt"

_SPECS = [
    ("bgm_upbeat_vlog_120", "轻快 Vlog · 120BPM", "日常节奏", 120, 16.0),
    ("bgm_tech_pulse_128", "科技脉冲 · 128BPM", "科技数码", 128, 15.0),
    ("bgm_warm_lofi_85", "暖调 Lo-Fi · 85BPM", "氛围叙事", 85, 16.9412),
    ("bgm_cinematic_inspire_70", "电影感叙事 · 70BPM", "氛围叙事", 70, 17.1429),
    ("bgm_fashion_trap_100", "潮流卡点 · 100BPM", "时尚卡点", 100, 19.2),
    ("bgm_happy_ukulele_110", "明快尤克里里 · 110BPM", "日常节奏", 110, 17.4545),
]

CATALOG = [
    {
        "id": bid,
        "label": label,
        "group": group,
        "bpm": int(bpm),
        "duration": float(dur),
        "file": bid + ".wav",
        "license": LICENSE,
        "loopable": True,
        "model": "stabilityai/stable-audio-open-1.0",
    }
    for bid, label, group, bpm, dur in _SPECS
]

GROUPS = ["日常节奏", "科技数码", "氛围叙事", "时尚卡点"]


def catalog_by_id() -> dict[str, dict]:
    return {c["id"]: c for c in CATALOG}


def public_catalog() -> list[dict]:
    return [dict(c) for c in CATALOG]


def bgm_path(bgm_id: str, base_dir: str | None = None) -> str | None:
    c = catalog_by_id().get(str(bgm_id or ""))
    if c is None:
        return None
    return os.path.join(base_dir or BGM_DIR, c["file"])


def available_path(bgm_id: str, base_dir: str | None = None) -> str | None:
    p = bgm_path(bgm_id, base_dir=base_dir)
    return p if p and os.path.isfile(p) else None


def missing_files(base_dir: str | None = None) -> list[str]:
    root = base_dir or BGM_DIR
    return [c["id"] for c in CATALOG if not os.path.isfile(os.path.join(root, c["file"]))]


def load_provenance(base_dir: str | None = None) -> dict:
    p = os.path.join(base_dir or BGM_DIR, PROVENANCE_NAME)
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def read_wav16(path: str):
    with wave.open(path, "rb") as w:
        nch, sw, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    data = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32767.0
    return data.reshape(-1, nch), sr, nch, sw


def verify_provenance(base_dir: str | None = None) -> tuple[bool, list[str]]:
    """校验全部 6 首配乐床 WAV、LICENSE.txt 与 PROVENANCE.json 的 SHA256 一致性。"""
    root = base_dir or BGM_DIR
    errors: list[str] = []
    lic_path = os.path.join(root, LICENSE_FILE_NAME)
    if not os.path.isfile(lic_path):
        errors.append("missing LICENSE.txt")
    prov = load_provenance(root)
    items = {it.get("id"): it for it in (prov.get("items") or []) if isinstance(it, dict)}
    for c in CATALOG:
        bid = c["id"]
        wav_p = os.path.join(root, c["file"])
        if not os.path.isfile(wav_p):
            errors.append(f"missing {c['file']}")
            continue
        it = items.get(bid)
        if not it:
            errors.append(f"missing provenance entry for {bid}")
            continue
        h = hashlib.sha256()
        with open(wav_p, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest() != it.get("sha256"):
            errors.append(f"sha256 mismatch for {bid}")
    return len(errors) == 0, errors


def _resolve_timeline(project_or_timeline):
    obj = project_or_timeline
    for _ in range(3):
        if obj is None:
            return None
        if not isinstance(obj, dict) and hasattr(obj, "tracks") and hasattr(obj, "sources"):
            return obj
        nxt = obj.get("timeline") if isinstance(obj, dict) else getattr(obj, "timeline", None)
        if nxt is None or nxt is obj:
            return None
        obj = nxt
    return None


def place_bgm_bed(project_or_timeline, bgm_id: str, start_t: float = 0.0,
                  duration: float | None = None, gain_db: float | None = None):
    """将指定配乐床铺到时间线配乐轨（audio_role='bgm'），默认开启无缝循环（loop=True）。"""
    try:
        tl = _resolve_timeline(project_or_timeline)
        if tl is None:
            return None
        cat = catalog_by_id().get(str(bgm_id or ""))
        if cat is None:
            return None
        path = available_path(cat["id"])
        if path is None:
            return None
        from cutora.audio import sfx_library as _lib
        from cutora.model.timeline_model import Clip, MediaSource

        bgm_track = _lib.get_or_create_bgm_track(tl)
        if bgm_track is None or isinstance(bgm_track, dict):
            return None
        src_dur = float(cat["duration"])
        use_dur = float(duration) if (duration is not None and float(duration) > 0) else src_dur
        src = MediaSource(path, duration=src_dur, probe=False)
        src.has_video = False
        src.has_audio = True
        tl.sources[src.id] = src
        clip = Clip(src.id, 0.0, use_dur, max(0.0, float(start_t)))
        _lib.apply_new_bgm_defaults(clip)
        if gain_db is not None:
            clip.gain_db = float(gain_db)
        clip.name = cat["label"]
        clip.bgm_bed_id = cat["id"]
        bgm_track.clips.append(clip)
        return clip
    except Exception:
        _log.exception("[BgmBeds] place_bgm_bed failed")
        return None

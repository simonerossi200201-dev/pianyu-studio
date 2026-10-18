# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q207b: speech ducking. Off is the existing per-clip amix, byte for byte.

No torch. Preview scales the BGM stem in the mixer; it does not rewrite PCM.
"""
from __future__ import annotations

import numpy as np

DEPTH_DB = (-6, -12, -18)
RELEASE_MS = (80, 250, 600)
_SIDECHAIN = {
    -6: (2, "-30dB"),
    -12: (4, "-24dB"),
    -18: (8, "-18dB"),
}


def _nearest(value, choices, default):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return min(choices, key=lambda item: abs(item - value))


def normalize_duck(raw) -> dict | None:
    """None when missing or disabled. Does not invent a task key."""
    if not isinstance(raw, dict) or not raw.get("enabled"):
        return None
    return {
        "enabled": True,
        "depth_db": _nearest(raw.get("depth_db", -12), DEPTH_DB, -12),
        "release_ms": _nearest(raw.get("release_ms", 250), RELEASE_MS, 250),
    }


def sidechain_filter(depth_db=-12, release_ms=250) -> str:
    depth = _nearest(depth_db, DEPTH_DB, -12)
    release = _nearest(release_ms, RELEASE_MS, 250)
    ratio, threshold = _SIDECHAIN[depth]
    return (
        "sidechaincompress=threshold=%s:ratio=%s:attack=5:release=%d:level_sc=1"
        % (threshold, ratio, int(release))
    )


def _role_of(track) -> str:
    role = str((track or {}).get("audio_role") or "")
    if role in ("main", "bgm", "sfx"):
        return role
    return "main" if track.get("is_main") else "bgm"


def _bus(name, indices) -> str:
    if len(indices) == 1:
        return "[a%d]anull[%s]" % (indices[0], name)
    ins = "".join("[a%d]" % i for i in indices)
    return (
        "%samix=inputs=%d:duration=longest:dropout_transition=0:normalize=0[%s]"
        % (ins, len(indices), name)
    )


def ducked_filter(filter_parts, tracks, project, trim_tail) -> str | None:
    """Full filter_complex when ducking is on and both main and bgm exist."""
    duck = normalize_duck((project or {}).get("audio_duck") if isinstance(project, dict) else None)
    if duck is None or not filter_parts:
        return None
    groups = {"main": [], "bgm": [], "sfx": []}
    for i, track in enumerate(tracks or []):
        groups[_role_of(track)].append(i)
    if not groups["main"] or not groups["bgm"]:
        return None
    parts = list(filter_parts)
    parts.append(_bus("duck_main", groups["main"]))
    parts.append(_bus("duck_bgm_in", groups["bgm"]))
    parts.append(
        "[duck_bgm_in][duck_main]%s[duck_bgm]"
        % sidechain_filter(duck["depth_db"], duck["release_ms"])
    )
    outs = ["[duck_main]", "[duck_bgm]"]
    if groups["sfx"]:
        parts.append(_bus("duck_sfx", groups["sfx"]))
        outs.append("[duck_sfx]")
    mix = "".join(outs)
    parts.append(
        "%samix=inputs=%d:duration=longest:dropout_transition=0:normalize=0"
        ",alimiter=limit=0.97:level=false%s[aout]"
        % (mix, len(outs), trim_tail or "")
    )
    return ";".join(parts)


def preview_bgm_gain(main_chunk, depth_db=-12, speech_rms=0.02) -> float:
    """Linear gain for one BGM buffer. 1.0 when the voice is quiet."""
    if main_chunk is None:
        return 1.0
    arr = np.asarray(main_chunk, dtype=np.float32)
    if arr.size == 0:
        return 1.0
    rms = float(np.sqrt(np.mean(np.square(arr))))
    if rms < float(speech_rms):
        return 1.0
    depth = _nearest(depth_db, DEPTH_DB, -12)
    return float(10.0 ** (float(depth) / 20.0))


def apply_auto_fades(task, dur=0.03) -> int:
    """Write 30 ms fades on voice clips. Does not run unless asked."""
    tl = task.get("timeline") if isinstance(task, dict) else None
    if tl is None:
        return 0
    n = 0
    fade = float(dur)
    for tr in getattr(tl, "tracks", []) or []:
        if getattr(tr, "type", None) != "audio":
            continue
        if str(getattr(tr, "audio_role", "") or "") in ("bgm", "sfx"):
            continue
        for clip in getattr(tr, "clips", []) or []:
            role = str(getattr(clip, "audio_role", "") or "")
            if role in ("bgm", "sfx"):
                continue
            if float(getattr(clip, "fade_in_dur", 0.0) or 0.0) <= 0:
                clip.fade_in_dur = fade
            if float(getattr(clip, "fade_out_dur", 0.0) or 0.0) <= 0:
                clip.fade_out_dur = fade
            n += 1
    return n

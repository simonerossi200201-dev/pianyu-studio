# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q206: small ffmpeg voice presets. Off is an empty chain.

Not the T07 list verbatim: no telephone / screen-recording preset, no hall
anlmdn, no 3 kHz podcast EQ. The GUI stays free of torch.
"""
from __future__ import annotations

import copy
import os

STRENGTH_NF = {"weak": -20, "mid": -25, "strong": -30}

_CHAINS = {
    "denoise": "afftdn=nf={nf}",
    "podcast": (
        "highpass=f=80,afftdn=nf={nf},"
        "deesser=i=0.3,acompressor=threshold=-18dB:ratio=3:attack=5:release=80"
    ),
    "youtube": (
        "highpass=f=80,afftdn=nf={nf},"
        "deesser=i=0.3,acompressor=threshold=-20dB:ratio=2:attack=5:release=80"
    ),
    "hall": "highpass=f=100,afftdn=nf=-30",
}


def normalize_strength(strength) -> str:
    key = str(strength or "mid").lower()
    if key in ("弱", "weak", "light"):
        return "weak"
    if key in ("强", "strong"):
        return "strong"
    return "mid"


def normalize_preset(preset) -> str:
    key = str(preset or "off").lower()
    aliases = {
        "off": "off", "关闭": "off", "none": "off", "": "off",
        "denoise": "denoise", "仅去噪": "denoise", "denoise_afftdn": "denoise",
        "podcast": "podcast", "播客": "podcast",
        "youtube": "youtube",
        "hall": "hall", "大厅": "hall",
    }
    return aliases.get(key, "off")


def denoise_chain(proc) -> str:
    """Inline filter fragment, or '' when off / already baked to a sidecar."""
    if not isinstance(proc, dict) or not proc:
        return ""
    if proc.get("baked"):
        return ""
    preset = normalize_preset(proc.get("preset") or proc.get("op") or "off")
    if preset == "off":
        return ""
    template = _CHAINS.get(preset)
    if not template:
        return ""
    params = proc.get("params") if isinstance(proc.get("params"), dict) else {}
    try:
        nf = int(params.get("nf"))
    except (TypeError, ValueError):
        nf = STRENGTH_NF[normalize_strength(proc.get("strength"))]
    return template.format(nf=nf)


def proc_for(preset, strength="mid") -> dict:
    """Persistent clip.audio_proc. {} means off and is not written to JSON."""
    name = normalize_preset(preset)
    if name == "off":
        return {}
    level = normalize_strength(strength)
    nf = -30 if name == "hall" else STRENGTH_NF[level]
    return {
        "op": "denoise_afftdn",
        "preset": name,
        "strength": level,
        "params": {"nf": nf},
    }


def sidecar_path(proc) -> str:
    if not isinstance(proc, dict):
        return ""
    return str(proc.get("path") or "")


def take_sidecar(src_path: str, proc) -> tuple:
    """(path, chain, miss_basename). Never writes MediaSource.path.

    A sidecar file replaces the source for this mix only, and the inline
    chain stays empty so afftdn is not applied twice. A missing sidecar
    falls back to src_path and returns the basename for a proc-miss log.
    """
    side = sidecar_path(proc)
    if side and os.path.isfile(side):
        return side, "", ""
    miss = os.path.basename(side) if side else ""
    return src_path, denoise_chain(proc), miss


def _merge_proc(old, new: dict) -> dict:
    if not new:
        return {}
    out = copy.deepcopy(new)
    if not isinstance(old, dict):
        return out
    side = sidecar_path(old)
    if not side or not os.path.isfile(side):
        return out
    if old.get("preset") == out.get("preset") and old.get("strength") == out.get("strength"):
        out["path"] = side
        if old.get("src_mtime"):
            out["src_mtime"] = old.get("src_mtime")
    return out


def _voice_clip(clip) -> bool:
    role = str(getattr(clip, "audio_role", "") or "")
    return role not in ("bgm", "sfx")


def assign_denoise(task, preset, strength="mid", track_id=None, clip_index=None) -> int:
    """Write audio_proc on voice clips. Does not touch MediaSource.path."""
    new = proc_for(preset, strength)
    tl = task.get("timeline") if isinstance(task, dict) else None
    if tl is None:
        return 0
    tid = str(track_id) if track_id else ""
    want = None if clip_index is None else int(clip_index)
    n = 0
    for tr in getattr(tl, "tracks", []) or []:
        if getattr(tr, "type", None) != "audio":
            continue
        if tid and str(getattr(tr, "id", "")) != tid:
            continue
        for i, clip in enumerate(getattr(tr, "clips", []) or []):
            if want is not None and i != want:
                continue
            if want is None and not _voice_clip(clip):
                continue
            clip.audio_proc = _merge_proc(getattr(clip, "audio_proc", None), new)
            n += 1
    return n


def audition_ffmpeg_argv(ffmpeg, src, chain, dest) -> list:
    """Five-second denoise render. The caller plays dest on the process sink."""
    cmd = [str(ffmpeg), "-y", "-v", "error", "-t", "5", "-i", str(src), "-vn"]
    frag = str(chain or "").strip().strip(",")
    if frag:
        cmd += ["-af", frag]
    cmd += ["-ac", "2", "-ar", "48000", str(dest)]
    return cmd

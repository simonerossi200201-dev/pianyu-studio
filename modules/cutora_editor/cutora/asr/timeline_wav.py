# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Mix a timeline's audio clips into one wav (T20b cross-clip ASR)."""
from __future__ import annotations

import os
import tempfile

from cutora.core.config import FFMPEG_PATH


def is_multi_clip_timeline(task: dict | None) -> bool:
    """True when the task's video track has 2+ clips (stitched timeline)."""
    if not isinstance(task, dict):
        return False
    tl = task.get("timeline")
    if tl is None:
        return False
    tracks = getattr(tl, "tracks", None)
    if tracks is None and isinstance(tl, dict):
        tracks = tl.get("tracks") or []
    n = 0
    for tr in tracks or []:
        kind = getattr(tr, "type", None) or (tr.get("type") if isinstance(tr, dict) else None)
        if kind != "video":
            continue
        clips = getattr(tr, "clips", None)
        if clips is None and isinstance(tr, dict):
            clips = tr.get("clips") or []
        n += len(clips or [])
    return n >= 2


def _source_path(tl, source_id) -> str:
    sources = getattr(tl, "sources", None)
    if sources is None and isinstance(tl, dict):
        sources = tl.get("sources") or {}
    src = (sources or {}).get(source_id)
    if src is None:
        return ""
    if isinstance(src, dict):
        return str(src.get("path") or "")
    return str(getattr(src, "path", "") or "")


def _g(clip, key, default=0.0):
    if isinstance(clip, dict):
        val = clip.get(key, default)
    else:
        val = getattr(clip, key, default)
    try:
        return float(val if val is not None else default)
    except (TypeError, ValueError):
        return float(default)


def _clip_bool(clip, key, default=False):
    if isinstance(clip, dict):
        return bool(clip.get(key, default))
    return bool(getattr(clip, key, default))


def _clip_fields(clip) -> tuple[str, float, float, float]:
    if isinstance(clip, dict):
        return (
            str(clip.get("source_id") or ""),
            float(clip.get("in_t") or 0.0),
            float(clip.get("out_t") or 0.0),
            float(clip.get("start_t") or 0.0),
        )
    return (
        str(getattr(clip, "source_id", "") or ""),
        float(getattr(clip, "in_t", 0.0) or 0.0),
        float(getattr(clip, "out_t", 0.0) or 0.0),
        float(getattr(clip, "start_t", 0.0) or 0.0),
    )


def _clip_mix_dict(clip, tl) -> dict | None:
    sid, in_t, out_t, start_t = _clip_fields(clip)
    path = _source_path(tl, sid)
    if not path or not os.path.isfile(path):
        return None
    if out_t <= in_t:
        return None
    role = ""
    if isinstance(clip, dict):
        role = str(clip.get("audio_role") or "")
        is_main = bool(clip.get("is_main"))
        loop = bool(clip.get("loop"))
        proc = clip.get("audio_proc")
    else:
        role = str(getattr(clip, "audio_role", "") or "")
        is_main = bool(getattr(clip, "is_main", False))
        loop = bool(getattr(clip, "loop", False))
        proc = getattr(clip, "audio_proc", None)
    if not isinstance(proc, dict):
        proc = {}
    from cutora.audio.voice_presets import take_sidecar
    path, _chain, miss = take_sidecar(path, proc)
    if miss:
        from cutora.core.log import get_logger
        get_logger("cutora.asr.timeline_wav").info("[Audio] proc miss %s", miss)
    if not path or not os.path.isfile(path):
        return None
    row = {
        "path": path,
        "in_t": in_t,
        "out_t": out_t,
        "start_t": start_t,
        "gain_db": _g(clip, "gain_db", 0.0),
        "speed": max(0.1, _g(clip, "speed", 1.0) or 1.0),
        "fade_in_dur": _g(clip, "fade_in_dur", 0.0),
        "fade_out_dur": _g(clip, "fade_out_dur", 0.0),
        "pitch_semitones": _g(clip, "pitch_semitones", 0.0),
        "audio_role": role,
        "is_main": is_main,
        "loop": loop,
        "spatial_enabled": _clip_bool(clip, "spatial_enabled", False),
        "spatial_amount": _g(clip, "spatial_amount", 0.0),
        "fade_curve": "tri",
        "keyframes": (
            clip.get("keyframes") if isinstance(clip, dict)
            else getattr(clip, "keyframes", None)
        ),
        "_clip": clip,
    }
    if proc:
        row["audio_proc"] = {
            "op": str(proc.get("op") or ""),
            "path": str(proc.get("path") or ""),
            "preset": str(proc.get("preset") or ""),
            "params": proc.get("params") if isinstance(proc.get("params"), dict) else {},
        }
    return row


def mix_rows_for_task(task: dict, *, solo_ids=None, preview_sfx=True) -> list[dict]:
    """Clips that reach the mixer. mute/solo via compute_audio_tracks.

    Fade in/out and fade_curve come from apply_audio_transition_fades — the
    same records export muxes — not only clip.fade_in_dur/fade_out_dur.
    preview_sfx=False drops sfx rows (preview-only; export always keeps them).
    """
    from cutora.audio.sfx_library import clip_audio_role
    from cutora.model.seams import apply_audio_transition_fades
    from cutora.model.track_guard import compute_audio_tracks, compute_export_audio_tracks
    tl = (task or {}).get("timeline")
    if solo_ids:
        clips = compute_audio_tracks(task, solo_ids=solo_ids)
    else:
        clips = compute_export_audio_tracks(task) if tl is not None else []
    fade_by_id = {}
    if tl is not None:
        try:
            for rec in apply_audio_transition_fades(task):
                fade_by_id[id(rec["clip"])] = rec
        except Exception:
            from cutora.core.log import get_logger
            get_logger("cutora.asr.timeline_wav").exception(
                "apply_audio_transition_fades failed")
    rows = []
    for clip in clips or []:
        if not preview_sfx and clip_audio_role(clip) == "sfx":
            continue
        rec = _clip_mix_dict(clip, tl)
        if not rec:
            continue
        fade = fade_by_id.get(id(clip)) or {}
        if fade:
            rec["fade_in_dur"] = float(fade.get("fade_in_dur", rec["fade_in_dur"]))
            rec["fade_out_dur"] = float(fade.get("fade_out_dur", rec["fade_out_dur"]))
            rec["fade_curve"] = fade.get("fade_curve") or "tri"
        rows.append(rec)
    return rows


def track_mute_fingerprint(task: dict) -> tuple:
    from cutora.model.track_guard import iter_tracks, track_is_muted
    rows = []
    for tr in iter_tracks(task):
        if getattr(tr, "domain", "visual") != "audio":
            continue
        rows.append((
            str(getattr(tr, "id", "")),
            bool(getattr(tr, "mute", False)),
            bool(getattr(tr, "visible", True)),
            bool(track_is_muted(tr, task=task)),
        ))
    return tuple(rows)


def loudness_fingerprint(task: dict) -> tuple:
    s = task.get("loudness") if isinstance(task.get("loudness"), dict) else {}
    try:
        from cutora.audio.loudness import get_loudness_settings
        ln = get_loudness_settings()
        return (s.get("mode") or getattr(ln, "mode", ""),
                s.get("target_lufs") or getattr(ln, "target_i", None),
                bool(getattr(ln, "enabled", False)))
    except Exception:
        return (s.get("mode"), s.get("target_lufs"), None)


def _fx_for_fingerprint(task: dict) -> dict:
    fx = (task or {}).get("audio_fx") if isinstance((task or {}).get("audio_fx"), dict) else None
    if fx is None:
        try:
            from cutora.audio.sfx_library import load_fx_settings
            fx = load_fx_settings()
        except Exception:
            fx = {}
    return fx or {}


def preview_audio_fingerprint(task: dict, *, solo_ids=None, preview_sfx=True) -> str:
    import hashlib
    raw = mix_rows_for_task(task, solo_ids=solo_ids, preview_sfx=preview_sfx)
    rows = []
    need_global_wet = False
    for rec in raw:
        path = rec["path"]
        try:
            mtime = int(os.path.getmtime(path)) if os.path.isfile(path) else 0
        except OSError:
            mtime = 0
        item = (
            path, mtime,
            rec["in_t"], rec["out_t"], rec["start_t"],
            rec["gain_db"], rec["speed"],
            rec["fade_in_dur"], rec["fade_out_dur"],
            rec["pitch_semitones"], rec["audio_role"],
            rec["is_main"], rec["loop"],
            rec.get("spatial_enabled"), rec.get("spatial_amount"),
            rec.get("fade_curve") or "tri",
            rec.get("keyframes"),
        )
        proc = rec.get("audio_proc")
        if isinstance(proc, dict) and proc:
            side = str(proc.get("path") or "")
            try:
                pm = int(os.path.getmtime(side)) if side and os.path.isfile(side) else 0
            except OSError:
                pm = 0
            item = item + (str(proc.get("op") or ""), side, pm)
        rows.append(item)
        if rec.get("spatial_enabled") and float(rec.get("spatial_amount") or 0) <= 0:
            need_global_wet = True
    fx = _fx_for_fingerprint(task)
    spatial_fp = (
        str(fx.get("spatial_preset") or ""),
        fx.get("spatial_width"),
        fx.get("spatial_wet") if need_global_wet else None,
    )
    payload = (tuple(rows), track_mute_fingerprint(task), loudness_fingerprint(task),
               tuple(sorted(solo_ids or ())), round(_pad_to_sec(task), 2),
               spatial_fp, bool(preview_sfx))
    duck = task.get("audio_duck") if isinstance((task or {}).get("audio_duck"), dict) else None
    if duck and duck.get("enabled"):
        payload = payload + ((duck.get("depth_db"), duck.get("release_ms")),)
    return hashlib.sha1(repr(payload).encode("utf-8")).hexdigest()[:20]


def _pad_to_sec(task) -> float:
    """Q157：预览混音补静音到播放终点（时间线最远片段 / 渲染终点），播放器的时钟才能走完整条时间线。"""
    try:
        from cutora.preview.engine import playback_end_sec
        return float(playback_end_sec(task) or 0.0)
    except Exception:
        return 0.0


def render_preview_wav(task: dict, dest: str | None = None, *, solo_ids=None,
                       preview_sfx=True) -> str:
    """48 kHz stereo mix for the T28 preview clock. Honors mute/gain/fade/speed."""
    return render_timeline_wav(task, dest, rate=48000, channels=2, solo_ids=solo_ids,
                               pad_to=_pad_to_sec(task), preview_sfx=preview_sfx)


def render_timeline_wav(task: dict, dest: str | None = None, *, rate: int = 16000,
                        channels: int = 1, solo_ids=None, pad_to: float = 0.0,
                        preview_sfx=True) -> str:
    """Mix linked audio clips onto a wav. Times = timeline times.

    Filter graph is RenderWorker.export_audio_filter_complex — the same
    string export muxes. Output -ac/-ar may still resample for ASR.
    """
    from cutora.render.render_worker import RenderWorker
    rw = RenderWorker.__new__(RenderWorker)
    rw.export_audio_rate = 48000
    fc, tracks = rw.export_audio_filter_complex(
        task or {}, has_video=False, solo_ids=solo_ids, preview_sfx=preview_sfx)
    if not tracks:
        fallback = str((task or {}).get("video") or "")
        if not fallback or not os.path.isfile(fallback):
            raise FileNotFoundError("时间线没有可混合的音频")
        fake = {
            "audio_tracks": [{
                "path": fallback, "start": 0.0, "trim_in": 0.0,
                "gain_db": 0.0, "pitch_semitones": 0.0,
                "fade_in_dur": 0.0, "fade_out_dur": 0.0,
                "spatial_enabled": False, "spatial_amount": 0.0,
                "is_main": True, "audio_role": "main", "loop": False,
            }],
            "trim_start_sec": float((task or {}).get("trim_start_sec") or 0.0),
            "trim_end_sec": float((task or {}).get("trim_end_sec") or 0.0),
            "src_volume": float((task or {}).get("src_volume") if (task or {}).get("src_volume") is not None else 1.0),
            "audio_fx": (task or {}).get("audio_fx"),
        }
        fc, tracks = rw.export_audio_filter_complex(fake, has_video=False)
        if not tracks:
            raise FileNotFoundError("时间线没有可混合的音频")
    if pad_to and float(pad_to) > 0 and fc.endswith("[aout]"):
        fc = fc[:-len("[aout]")] + ",apad=whole_dur=%.3f[aout]" % float(pad_to)
    if dest is None:
        fd, dest = tempfile.mkstemp(prefix="cutora_tl_", suffix=".wav")
        os.close(fd)
    os.makedirs(os.path.dirname(os.path.abspath(dest)) or ".", exist_ok=True)
    ac = "2" if int(channels) >= 2 else "1"
    ar = str(int(rate or 16000))
    from cutora.core.proc import run_filter_script
    cmd_before = [FFMPEG_PATH, "-y", "-v", "error"]
    for rec in tracks:
        cmd_before += ["-i", rec["path"]]
    r = run_filter_script(
        cmd_before, fc,
        ["-map", "[aout]", "-ac", ac, "-ar", ar, dest],
        capture_output=True)
    if r.returncode != 0 or not os.path.isfile(dest) or os.path.getsize(dest) < 44:
        err = (r.stderr or b"").decode("utf-8", "replace")[-600:]
        raise RuntimeError("ffmpeg mix failed: %s" % err)
    return dest

# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q207a: pause detection and ripple-delete. No torch import at module level.

Order: word gaps, then Silero, then ffmpeg silencedetect.
Missing analysis is off: callers write task["analysis"] only when asked.
"""
from __future__ import annotations

import os
import re

from cutora.batch.title_align import SENTENCE_PAUSE_S
from cutora.ui.timeline.snap import clip_span

_SILENCE_START = re.compile(r"silence_start:\s*([0-9.]+)")
_SILENCE_END = re.compile(r"silence_end:\s*([0-9.]+)")
DEFAULT_MIN_DUR = 0.8


def collect_words(task) -> list:
    words = []
    for seg in (task or {}).get("segments") or []:
        if not isinstance(seg, dict):
            continue
        for w in seg.get("words") or []:
            if not isinstance(w, dict):
                continue
            try:
                start = float(w.get("start"))
                end = float(w.get("end"))
            except (TypeError, ValueError):
                continue
            words.append({
                "start": start,
                "end": max(end, start),
                "text": str(w.get("word") or w.get("text") or ""),
            })
    words.sort(key=lambda row: (row["start"], row["end"]))
    return words


def pauses_from_words(words, pause_s=SENTENCE_PAUSE_S, end_t=None) -> list:
    ordered = [w for w in words or [] if w["end"] >= w["start"]]
    if not ordered:
        return []
    gaps = []
    if ordered[0]["start"] >= float(pause_s):
        gaps.append({"start": 0.0, "end": float(ordered[0]["start"])})
    for left, right in zip(ordered, ordered[1:]):
        if right["start"] - left["end"] >= float(pause_s):
            gaps.append({"start": float(left["end"]), "end": float(right["start"])})
    if end_t is not None and float(end_t) - ordered[-1]["end"] >= float(pause_s):
        gaps.append({"start": float(ordered[-1]["end"]), "end": float(end_t)})
    return gaps


def parse_silence_log(text: str) -> list:
    pending = None
    rows = []
    for line in str(text or "").splitlines():
        start = _SILENCE_START.search(line)
        if start:
            pending = float(start.group(1))
            continue
        end = _SILENCE_END.search(line)
        if end and pending is not None:
            stop = float(end.group(1))
            if stop > pending:
                rows.append({"start": pending, "end": stop})
            pending = None
    return rows


def timeline_end(task) -> float:
    tl = task.get("timeline") if isinstance(task, dict) else None
    end = 0.0
    for tr in getattr(tl, "tracks", []) or []:
        if getattr(tr, "type", None) != "video":
            continue
        for clip in getattr(tr, "clips", []) or []:
            _start, stop = clip_span(clip)
            end = max(end, stop)
    if end <= 0:
        for seg in (task or {}).get("segments") or []:
            if isinstance(seg, dict):
                try:
                    end = max(end, float(seg.get("end") or 0.0))
                except (TypeError, ValueError):
                    pass
    return end


def word_time_at_char(seg, index):
    """Map a character offset to that word's start. None when the seg has no words."""
    if not isinstance(seg, dict):
        return None
    words = [w for w in (seg.get("words") or []) if isinstance(w, dict)]
    if not words:
        return None
    text = str(seg.get("text") or "")
    cursor = 0
    try:
        index = max(0, int(index))
    except (TypeError, ValueError):
        index = 0
    chosen = None
    for word in words:
        token = str(word.get("word") or word.get("text") or "")
        if not token:
            continue
        at = text.find(token, cursor) if text else -1
        if at < 0:
            at = cursor
        end = at + len(token)
        chosen = word
        if index <= end:
            break
        cursor = end
    if chosen is None:
        return None
    try:
        return float(chosen.get("start"))
    except (TypeError, ValueError):
        return None


def voice_track(task):
    tl = task.get("timeline") if isinstance(task, dict) else None
    if tl is None:
        return None
    fallback = None
    for tr in getattr(tl, "tracks", []) or []:
        if getattr(tr, "type", None) != "audio":
            continue
        role = str(getattr(tr, "audio_role", "") or "")
        if role == "main":
            return tr
        if fallback is None and role not in ("bgm", "sfx"):
            for clip in getattr(tr, "clips", []) or []:
                if getattr(clip, "is_main", False) or str(getattr(clip, "audio_role", "") or "") == "main":
                    fallback = tr
                    break
    return fallback


def _main_source_path(task) -> str:
    tr = voice_track(task)
    tl = task.get("timeline") if isinstance(task, dict) else None
    if tr is None or tl is None:
        return ""
    sources = getattr(tl, "sources", {}) or {}
    for clip in getattr(tr, "clips", []) or []:
        src = sources.get(getattr(clip, "source_id", None))
        path = str(getattr(src, "path", "") or "")
        if path and os.path.isfile(path):
            return path
    return ""


def ffmpeg_silences(path, min_dur=DEFAULT_MIN_DUR) -> list:
    if not path or not os.path.isfile(path):
        return []
    from cutora.core.config import FFMPEG_PATH
    from cutora.core.proc import run as _no_window_run
    cmd = [
        FFMPEG_PATH, "-v", "info", "-i", path,
        "-af", "silencedetect=noise=-35dB:d=%.3f" % float(min_dur),
        "-f", "null", "-",
    ]
    proc = _no_window_run(cmd, capture_output=True)
    err = (proc.stderr or b"").decode("utf-8", "replace")
    return parse_silence_log(err)


def _silero_silences(path, end_t, pause_s):
    """Best-effort. Returns None when the VAD stack is not usable."""
    try:
        import numpy as np
        from cutora.core.config import FFMPEG_PATH
        from cutora.core.proc import run as _no_window_run
        from cutora.asr.vad_silero import speech_segments_from_audio
        cmd = [
            FFMPEG_PATH, "-v", "error", "-i", path,
            "-ac", "1", "-ar", "16000", "-f", "f32le", "pipe:1",
        ]
        proc = _no_window_run(cmd, capture_output=True)
        if proc.returncode != 0 or not proc.stdout:
            return None
        wave = np.frombuffer(proc.stdout, dtype=np.float32)
        spans = speech_segments_from_audio({"waveform": wave, "sample_rate": 16000})
    except Exception:
        return None
    speech = []
    for span in spans or []:
        try:
            speech.append({"start": float(span.start), "end": float(span.end)})
        except Exception:
            continue
    if not speech:
        return []
    return pauses_from_words(
        [{"start": s["start"], "end": s["end"]} for s in speech],
        pause_s, end_t)


def detect_silences(task, pause_s=SENTENCE_PAUSE_S) -> list:
    words = collect_words(task)
    end_t = timeline_end(task)
    if words:
        return pauses_from_words(words, pause_s, end_t if end_t > 0 else None)
    path = _main_source_path(task)
    if path:
        silero = _silero_silences(path, end_t if end_t > 0 else None, pause_s)
        if silero is not None:
            return silero
        return ffmpeg_silences(path, DEFAULT_MIN_DUR)
    return []


def _extend_short_bgm(task):
    """If a bed ends before the new picture, loop it. Do not move it onto voice."""
    tl = task.get("timeline") if isinstance(task, dict) else None
    if tl is None:
        return
    end = 0.0
    for tr in getattr(tl, "tracks", []) or []:
        if getattr(tr, "type", None) != "video":
            continue
        for clip in getattr(tr, "clips", []) or []:
            _start, stop = clip_span(clip)
            end = max(end, stop)
    for tr in getattr(tl, "tracks", []) or []:
        if str(getattr(tr, "audio_role", "") or "") != "bgm":
            continue
        for clip in getattr(tr, "clips", []) or []:
            _start, stop = clip_span(clip)
            if stop + 0.05 < end:
                clip.loop = True


def remove_silences(task, min_dur=DEFAULT_MIN_DUR, start=None, end=None) -> int:
    analysis = task.get("analysis") if isinstance(task, dict) else None
    rows = list((analysis or {}).get("silences") or []) if isinstance(analysis, dict) else []
    if not rows:
        return 0
    if start is not None and end is not None:
        s0, e0 = float(start), float(end)
        rows = [
            row for row in rows
            if abs(float(row.get("start") or 0) - s0) < 0.05
            and abs(float(row.get("end") or 0) - e0) < 0.05
        ]
    kept = []
    for row in rows:
        try:
            s = float(row.get("start"))
            e = float(row.get("end"))
        except (TypeError, ValueError):
            continue
        if e - s + 1e-9 >= float(min_dur):
            kept.append({"start": s, "end": e})
    kept.sort(key=lambda row: row["start"], reverse=True)
    tr = voice_track(task)
    if tr is None or not kept:
        return 0
    from cutora.model.ripple import apply_delete_range
    tid = getattr(tr, "id", None)
    for row in kept:
        apply_delete_range(task, row["start"], row["end"], tid)
    if isinstance(task.get("analysis"), dict):
        task["analysis"].pop("silences", None)
        if not task["analysis"]:
            task.pop("analysis", None)
    _extend_short_bgm(task)
    return len(kept)

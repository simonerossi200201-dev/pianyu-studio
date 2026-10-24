# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Subtitle Track transcription & manual script alignment for any timeline track."""
from __future__ import annotations

import os
import re
from typing import Callable

from cutora.batch.title_align import (
    ensure_sentence_punctuation,
    sentences_with_times,
)
from cutora.core.log import get_logger
from cutora.subtitle.track_source import (
    get_track_manual_script,
    get_track_source_id,
    set_track_manual_script,
    set_track_segments,
    set_track_source_id,
)

logger = get_logger("cutora.subtitle.track_transcribe")

MAIN_SOURCE_ID = "__main__"
_SRT_TIME_RE = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})"
)
_BRACKET_TIME_RE = re.compile(
    r"^\s*\[(\d{1,2}):(\d{2})(?:[.:](\d{1,3}))?\]\s*(.+)$"
)


def _tl_of(task):
    if not isinstance(task, dict):
        return None
    return task.get("timeline")


def _clip_source_path(tl, clip) -> str:
    if clip is None:
        return ""
    direct = getattr(clip, "path", None) or (clip.get("path") if isinstance(clip, dict) else None)
    if direct and os.path.isfile(str(direct)):
        return str(direct)
    sid = getattr(clip, "source_id", None) or (clip.get("source_id") if isinstance(clip, dict) else None)
    sources = getattr(tl, "sources", None) if tl is not None else None
    if isinstance(sources, dict) and sid in sources:
        src = sources[sid]
        p = getattr(src, "path", None) or (src.get("path") if isinstance(src, dict) else None)
        if p:
            return str(p)
    return str(direct or "")


def list_transcribe_source_tracks(task) -> list[tuple[str, str]]:
    """Return [(source_track_id, display_label), ...] for the Subtitle Track inspector combo.

    Default first item is always ``('__main__', '🎬 默认：媒体库主视频轨道')``.
    Also lists every video and audio track on the timeline so the user can target
    any specific track.
    """
    items: list[tuple[str, str]] = [
        (MAIN_SOURCE_ID, "🎬 默认：媒体库主视频轨道"),
    ]
    tl = _tl_of(task)
    if tl is None:
        return items
    tracks = list(getattr(tl, "tracks", None) or [])
    vid_idx = 0
    aud_idx = 0
    for tr in tracks:
        ttype = str(getattr(tr, "type", "") or "")
        tid = str(getattr(tr, "id", "") or "")
        if not tid:
            continue
        custom_name = str(getattr(tr, "name", "") or "").strip()
        clips = list(getattr(tr, "clips", None) or [])
        first_file = ""
        for c in clips:
            if getattr(c, "is_thumbnail_leadin", False):
                continue
            sp = _clip_source_path(tl, c)
            if sp:
                first_file = os.path.basename(sp)
                break
        suffix = f" · {first_file}" if first_file else (f" ({len(clips)}段)" if clips else " (空)")
        if ttype == "video":
            vid_idx += 1
            base = custom_name or ("主视频轨道" if getattr(tr, "is_main_video", False) else f"视频轨道 {vid_idx}")
            items.append((tid, f"🎞️ {base}{suffix}"))
        elif ttype in ("audio", "bgm", "sfx"):
            aud_idx += 1
            role = str(getattr(tr, "audio_role", "") or "")
            role_tag = {"main": "原声", "bgm": "配乐", "sfx": "音效"}.get(role, "")
            base = custom_name or (f"音频轨道 {aud_idx}" + (f"·{role_tag}" if role_tag else ""))
            items.append((tid, f"🎵 {base}{suffix}"))
    return items


def resolve_source_track_spans(task, source_track_id: str | None = None) -> list[dict]:
    """Resolve timeline clip spans for the target track.

    Each item is ``{"path": str, "in_t": float, "out_t": float, "tl_start": float, "speed": float}``.
    """
    if not isinstance(task, dict):
        return []
    sid = str(source_track_id or MAIN_SOURCE_ID)
    tl = _tl_of(task)
    tracks = list(getattr(tl, "tracks", None) or []) if tl is not None else []
    target_tr = None
    if sid != MAIN_SOURCE_ID:
        for tr in tracks:
            if str(getattr(tr, "id", "") or "") == sid:
                target_tr = tr
                break
    else:
        for tr in tracks:
            if getattr(tr, "type", None) == "video" and getattr(tr, "is_main_video", False):
                target_tr = tr
                break
        if target_tr is None:
            for tr in tracks:
                if getattr(tr, "type", None) == "video":
                    target_tr = tr
                    break

    spans: list[dict] = []
    if target_tr is not None:
        for c in getattr(target_tr, "clips", None) or []:
            if getattr(c, "is_thumbnail_leadin", False):
                continue
            path = _clip_source_path(tl, c)
            if not path:
                continue
            in_t = max(0.0, float(getattr(c, "in_t", 0.0) or 0.0))
            out_t = float(getattr(c, "out_t", 0.0) or 0.0)
            eff = float(getattr(c, "effective_duration", 0.0) or 0.0)
            speed = max(0.05, float(getattr(c, "speed", 1.0) or 1.0))
            if out_t <= in_t:
                out_t = in_t + max(0.2, eff * speed if eff > 0 else float(task.get("duration") or 10.0))
            tl_start = max(0.0, float(getattr(c, "start_t", 0.0) or 0.0))
            spans.append({
                "path": path,
                "in_t": in_t,
                "out_t": out_t,
                "tl_start": tl_start,
                "speed": speed,
            })

    if not spans:
        main_v = str(task.get("video") or task.get("path") or "")
        dur = max(1.0, float(task.get("duration") or 10.0))
        if main_v:
            spans.append({
                "path": main_v,
                "in_t": 0.0,
                "out_t": dur,
                "tl_start": 0.0,
                "speed": 1.0,
            })
    return spans


def _default_transcribe_span(path: str, in_t: float, out_t: float, language: str) -> tuple[list[dict], list[dict]]:
    """Transcribe [in_t, out_t] of ``path`` in <=30s windows via AsrClient."""
    from cutora.asr.clip_worker import AsrClient
    from cutora.batch import text_locate_asr as LOC

    lang = LOC._hello_language(language)
    # Check if locate_asr cache already has this exact window (e.g. main video)
    words_hit, sents_hit, _ = LOC.lookup_best(path, in_t, out_t, language)
    if words_hit:
        w_filtered = [
            w for w in words_hit
            if float(w.get("end") or 0.0) >= in_t and float(w.get("start") or 0.0) <= out_t
        ]
        s_filtered = [
            s for s in (sents_hit or [])
            if float(s.get("end") or 0.0) >= in_t and float(s.get("start") or 0.0) <= out_t
        ]
        if w_filtered:
            return w_filtered, s_filtered

    if not os.path.isfile(path):
        return [], []

    client = AsrClient()
    words_all: list[dict] = []
    sents_all: list[dict] = []
    try:
        client.start(lang)
        chunk_len = float(LOC.MAX_LOCATE_WINDOW_SEC)
        overlap = 1.0
        cur = float(in_t)
        while cur < float(out_t) - 0.05:
            wend = min(float(out_t), cur + chunk_len)
            w_chunk, s_chunk = LOC._transcribe_window(client, path, lang, cur, wend)
            cut = words_all[-1]["end"] if words_all else -1.0
            words_all.extend(x for x in w_chunk if float(x.get("start") or 0.0) >= cut - 0.02)
            scut = sents_all[-1]["end"] if sents_all else -1.0
            sents_all.extend(x for x in s_chunk if float(x.get("end") or 0.0) > scut + 0.3)
            if wend >= float(out_t):
                break
            cur = wend - overlap
    finally:
        try:
            client.close()
        except Exception:
            pass
    return words_all, sents_all


def transcribe_track_to_segments(
    task: dict,
    sub_track,
    *,
    source_track_id: str | None = None,
    language: str = "auto",
    transcribe_fn: Callable[[str, float, float, str], tuple[list[dict], list[dict]]] | None = None,
) -> list[dict]:
    """Transcribe the selected source track and write punctuated subtitle segments to ``sub_track``."""
    sid = str(source_track_id or get_track_source_id(sub_track) or MAIN_SOURCE_ID)
    set_track_source_id(sub_track, sid)
    spans = resolve_source_track_spans(task, sid)
    runner = transcribe_fn or _default_transcribe_span

    tl_words: list[dict] = []
    tl_sents: list[dict] = []

    for sp in spans:
        path = sp["path"]
        in_t = float(sp["in_t"])
        out_t = float(sp["out_t"])
        tl_start = float(sp["tl_start"])
        speed = max(0.05, float(sp["speed"]))
        raw_words, raw_sents = runner(path, in_t, out_t, language)
        for w in raw_words or []:
            ws = float(w.get("start") or 0.0)
            we = float(w.get("end") or ws)
            # Map from source clip time [in_t, out_t] to timeline time
            rel_s = ws - in_t if ws >= in_t - 0.25 else ws
            rel_e = we - in_t if we >= in_t - 0.25 else we
            t_s = max(tl_start, tl_start + max(0.0, rel_s) / speed)
            t_e = max(t_s + 0.05, tl_start + max(0.05, rel_e) / speed)
            tl_words.append({
                "start": round(t_s, 3),
                "end": round(t_e, 3),
                "text": str(w.get("text") or w.get("word") or "").strip(),
            })
        for s in raw_sents or []:
            ss = float(s.get("start") or 0.0)
            se = float(s.get("end") or ss)
            rel_s = ss - in_t if ss >= in_t - 0.25 else ss
            rel_e = se - in_t if se >= in_t - 0.25 else se
            t_s = max(tl_start, tl_start + max(0.0, rel_s) / speed)
            t_e = max(t_s + 0.1, tl_start + max(0.1, rel_e) / speed)
            tl_sents.append({
                "start": round(t_s, 3),
                "end": round(t_e, 3),
                "text": ensure_sentence_punctuation(str(s.get("text") or "").strip()),
            })

    # If no words came back from runner, check task['_title_locate'] when targeting main track
    if not tl_words and sid == MAIN_SOURCE_ID and isinstance(task.get("_title_locate"), dict):
        loc = task["_title_locate"]
        for w in loc.get("segments") or []:
            if isinstance(w, dict) and str(w.get("text") or "").strip():
                tl_words.append({
                    "start": float(w.get("start") or 0.0),
                    "end": float(w.get("end") or 0.0),
                    "text": str(w.get("text") or "").strip(),
                })
        for s in loc.get("sentences") or []:
            if isinstance(s, dict) and str(s.get("text") or "").strip():
                tl_sents.append({
                    "start": float(s.get("start") or 0.0),
                    "end": float(s.get("end") or 0.0),
                    "text": ensure_sentence_punctuation(str(s.get("text") or "").strip()),
                })

    # Store transcribed words on sub_track._extra for subsequent manual script alignment
    if sub_track is not None and not isinstance(sub_track, dict):
        extra = getattr(sub_track, "_extra", None)
        if not isinstance(extra, dict):
            extra = {}
            sub_track._extra = extra
        extra["asr_words"] = tl_words

    timed_sents = sentences_with_times(tl_words, tl_sents)
    sub_tid = str(getattr(sub_track, "id", "") or "") if sub_track is not None else ""
    segs: list[dict] = []
    for idx, sent in enumerate(timed_sents):
        s_t = round(float(sent.get("start") or 0.0), 3)
        e_t = round(max(s_t + 0.2, float(sent.get("end") or (s_t + 1.5))), 3)
        txt = ensure_sentence_punctuation(str(sent.get("text") or "").strip())
        if not txt:
            continue
        seg_words = [
            {
                "word": w["text"],
                "start": round(float(w["start"]), 3),
                "end": round(float(w["end"]), 3),
            }
            for w in tl_words
            if float(w["end"]) > s_t + 0.01 and float(w["start"]) < e_t - 0.01
        ]
        seg = {
            "id": f"sub_{sub_tid[:6]}_{idx + 1}" if sub_tid else f"sub_{idx + 1}",
            "start": s_t,
            "end": e_t,
            "text": txt,
            "words": seg_words,
        }
        if sub_tid:
            seg["_track_id"] = sub_tid
        segs.append(seg)

    set_track_segments(task, sub_track, segs)
    if segs and not get_track_manual_script(sub_track).strip():
        set_track_manual_script(sub_track, "\n".join(s["text"] for s in segs))
    return segs


def _hms_to_sec(h: str, m: str, s: str, ms: str) -> float:
    ms_str = (ms or "0").ljust(3, "0")[:3]
    return int(h) * 3600.0 + int(m) * 60.0 + int(s) + int(ms_str) / 1000.0


def _parse_timed_lines(raw_text: str) -> list[dict]:
    """Parse SRT/VTT blocks or [mm:ss.xx] lines if present."""
    lines = [ln.rstrip() for ln in (raw_text or "").splitlines()]
    out: list[dict] = []
    i = 0
    while i < len(lines):
        m_srt = _SRT_TIME_RE.search(lines[i])
        if m_srt:
            s_t = _hms_to_sec(m_srt.group(1), m_srt.group(2), m_srt.group(3), m_srt.group(4))
            e_t = _hms_to_sec(m_srt.group(5), m_srt.group(6), m_srt.group(7), m_srt.group(8))
            i += 1
            text_lines = []
            while i < len(lines) and lines[i].strip():
                if _SRT_TIME_RE.search(lines[i]):
                    break
                text_lines.append(lines[i].strip())
                i += 1
            body = " ".join(text_lines).strip()
            if body:
                out.append({
                    "start": round(s_t, 3),
                    "end": round(max(s_t + 0.2, e_t), 3),
                    "text": ensure_sentence_punctuation(body),
                })
            continue
        m_br = _BRACKET_TIME_RE.match(lines[i])
        if m_br:
            mm = int(m_br.group(1))
            ss = int(m_br.group(2))
            ms_raw = m_br.group(3) or "0"
            ms = int(ms_raw.ljust(3, "0")[:3])
            s_t = mm * 60.0 + ss + ms / 1000.0
            body = m_br.group(4).strip()
            if body:
                out.append({
                    "start": round(s_t, 3),
                    "end": round(s_t + 2.5, 3),
                    "text": ensure_sentence_punctuation(body),
                })
        i += 1
    # Fix open-ended bracket times so each ends where the next starts
    for j in range(len(out) - 1):
        if out[j]["end"] > out[j + 1]["start"]:
            out[j]["end"] = round(max(out[j]["start"] + 0.2, out[j + 1]["start"] - 0.04), 3)
    return out


def _split_plain_script(raw_text: str) -> list[str]:
    """Split user-pasted script into clean sentence lines."""
    raw = (raw_text or "").strip()
    if not raw:
        return []
    raw_lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    # Skip SRT index-only lines or WEBVTT header
    cleaned_lines = [
        ln for ln in raw_lines
        if not ln.isdigit() and ln.upper() != "WEBVTT"
    ]
    if len(cleaned_lines) >= 2:
        return [ensure_sentence_punctuation(ln) for ln in cleaned_lines if ln]
    # Single block/paragraph: split by sentence-ending punctuation
    single = cleaned_lines[0] if cleaned_lines else raw
    parts = re.split(r"(?<=[.!?。！？；;])\s+", single)
    out = [ensure_sentence_punctuation(p.strip()) for p in parts if p.strip()]
    return out


def build_segments_from_manual_text(
    text: str,
    *,
    task: dict,
    sub_track=None,
    source_track_id: str | None = None,
) -> list[dict]:
    """Convert user-pasted subtitle text (SRT, [mm:ss], or plain sentences) into track segments.

    Aligns against ASR words if available on ``sub_track`` or ``task``, otherwise distributes
    proportionally across the target source track's timeline span.
    """
    from cutora.batch import text_batch as TB

    set_track_manual_script(sub_track, text)
    sid = str(source_track_id or get_track_source_id(sub_track) or MAIN_SOURCE_ID)
    set_track_source_id(sub_track, sid)
    sub_tid = str(getattr(sub_track, "id", "") or "") if sub_track is not None else ""

    # 1. Check if text already has explicit timestamps (SRT / VTT / [mm:ss])
    timed = _parse_timed_lines(text)
    if timed:
        segs = []
        for idx, item in enumerate(timed):
            seg = {
                "id": f"sub_{sub_tid[:6]}_{idx + 1}" if sub_tid else f"sub_{idx + 1}",
                "start": item["start"],
                "end": item["end"],
                "text": item["text"],
                "words": [],
            }
            if sub_tid:
                seg["_track_id"] = sub_tid
            segs.append(seg)
        set_track_segments(task, sub_track, segs)
        return segs

    # 2. Plain sentence script
    sentences = _split_plain_script(text)
    if not sentences:
        set_track_segments(task, sub_track, [])
        return []

    # Check if we have ASR words for this track
    asr_words: list[dict] = []
    if sub_track is not None and not isinstance(sub_track, dict):
        extra = getattr(sub_track, "_extra", None) or {}
        if isinstance(extra.get("asr_words"), list):
            asr_words = list(extra["asr_words"])
    if not asr_words and sid == MAIN_SOURCE_ID and isinstance(task, dict):
        loc = task.get("_title_locate")
        if isinstance(loc, dict) and isinstance(loc.get("segments"), list):
            asr_words = list(loc["segments"])

    spans = resolve_source_track_spans(task, sid)
    if spans:
        span_start = min(float(sp["tl_start"]) for sp in spans)
        span_end = max(
            float(sp["tl_start"]) + (float(sp["out_t"]) - float(sp["in_t"])) / max(0.05, float(sp["speed"]))
            for sp in spans
        )
    else:
        span_start = 0.0
        span_end = max(2.0, float((task or {}).get("duration") or 10.0))

    if asr_words:
        span_start = min(span_start, float(asr_words[0].get("start") or 0.0))
        span_end = max(span_end, float(asr_words[-1].get("end") or span_end))

    total_dur = max(1.0, span_end - span_start)
    weights = [max(1, len(re.sub(r"\s+", "", s))) for s in sentences]
    total_w = float(sum(weights))

    segs: list[dict] = []
    cursor_t = span_start
    remaining_words = list(asr_words)

    for idx, (sent, w) in enumerate(zip(sentences, weights)):
        prop_dur = total_dur * (float(w) / total_w)
        s_t = cursor_t
        e_t = min(span_end, s_t + max(0.4, prop_dur))
        matched_words = []

        if remaining_words:
            loc_res = TB.locate_text(remaining_words, sent)
            if loc_res.get("ok") and loc_res.get("end") is not None:
                cand_s = float(loc_res.get("start") or s_t)
                cand_e = float(loc_res.get("end") or e_t)
                if cand_e > cand_s and cand_s >= cursor_t - 0.5:
                    s_t = max(cursor_t, cand_s)
                    e_t = max(s_t + 0.25, cand_e)
                    matched_words = [
                        {
                            "word": rw.get("text") or rw.get("word") or "",
                            "start": round(float(rw.get("start") or 0.0), 3),
                            "end": round(float(rw.get("end") or 0.0), 3),
                        }
                        for rw in remaining_words
                        if float(rw.get("end") or 0.0) > s_t and float(rw.get("start") or 0.0) < e_t
                    ]
                    remaining_words = [
                        rw for rw in remaining_words
                        if float(rw.get("start") or 0.0) >= e_t - 0.05
                    ]

        if idx == len(sentences) - 1 and not matched_words:
            e_t = max(s_t + 0.3, span_end)

        cursor_t = e_t
        seg = {
            "id": f"sub_{sub_tid[:6]}_{idx + 1}" if sub_tid else f"sub_{idx + 1}",
            "start": round(s_t, 3),
            "end": round(e_t, 3),
            "text": sent,
            "words": matched_words,
        }
        if sub_tid:
            seg["_track_id"] = sub_tid
        segs.append(seg)

    set_track_segments(task, sub_track, segs)
    return segs

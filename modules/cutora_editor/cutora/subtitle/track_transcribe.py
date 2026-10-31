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


class TranscribeCancelled(RuntimeError):
    """The user pressed 取消 while a subtitle-track transcription was running."""


def _requested_language(language: str | None) -> str | None:
    """'auto' / '' → None (= detect from audio); otherwise a whisper language code.

    The project-wide default (config.LANGUAGE) wins over detection when the user set one.
    Before this fix '' / 'auto' silently became 'en', so Chinese / Arabic / Malagasy /
    Swahili audio was decoded as English.
    """
    lang = str(language or "").strip().lower()
    if lang and lang != "auto":
        return lang
    try:
        from cutora.core import config as cfg
        lang = str(getattr(cfg, "LANGUAGE", "") or "").strip().lower()
    except Exception:
        lang = ""
    if lang and lang != "auto":
        return lang
    return None


class TrackAsrSession:
    """One ASR worker process for a whole subtitle-track run.

    Reuses the shared ASR pipeline (``cutora.asr.clip_worker.AsrClient`` → worker process →
    ``engine.get_whisperx_model`` with hardware-tier model / compute-type selection, Silero
    VAD, ``transcribe_clip`` punctuation-preserving raw sentences). The model is loaded once
    per run — previously every clip of the source track spawned its own worker and loaded the
    model again. Callable with the ``transcribe_fn`` signature
    ``(path, in_t, out_t, language) -> (words, sentences)``.

    Thread contract: ``__call__`` runs on a worker thread; ``cancel()`` may be called from the
    GUI thread and never blocks (it kills the worker process tree in a daemon thread so a
    blocking roundtrip returns at once).
    """

    def __init__(self, language: str = "auto", *, should_stop=None, on_progress=None,
                 total_sec: float = 0.0, log=None):
        self.language = language
        self.should_stop = should_stop or (lambda: False)
        self.on_progress = on_progress
        self.total_sec = max(0.0, float(total_sec or 0.0))
        self.done_sec = 0.0
        self.log = log
        self.client = None
        self.lang: str | None = None
        self.detected: str | None = None
        self._cancelled = False

    # -- helpers ---------------------------------------------------------
    def _check(self) -> None:
        if self._cancelled or self.should_stop():
            raise TranscribeCancelled("cancelled")

    def _report(self, msg: str) -> None:
        if callable(self.on_progress):
            pct = int(round(100.0 * self.done_sec / self.total_sec)) if self.total_sec > 0 else 0
            try:
                self.on_progress(max(0, min(99, pct)), msg)
            except Exception:
                pass

    def _ensure_client(self, path: str, in_t: float, out_t: float) -> None:
        if self.client is not None:
            return
        from cutora.asr.clip_worker import AsrClient
        from cutora.batch import text_locate_asr as LOC
        self._check()
        self._report("正在加载语音识别模型…")
        client = AsrClient(log=self.log)
        self.client = client
        want = _requested_language(self.language)
        if want:
            client.start(want)
            self.lang = want
            return
        # Auto-detect: load the model without an aligner, detect on the first ≤30 s of the
        # source span, then load the aligner for the detected language (if one exists).
        client.start("en", defer_align=True)
        self._check()
        self._report("正在识别语种…")
        lang = None
        try:
            w_end = min(float(out_t), float(in_t) + float(LOC.MAX_LOCATE_WINDOW_SEC))
            det = client.detect(path, timeout_sec=180, win_start=in_t, win_end=w_end)
            lang = str(det.get("language") or "").strip().lower() or None
        except TranscribeCancelled:
            raise
        except Exception as exc:
            self._check()
            logger.warning("track transcribe: language detect failed (%s), falling back", exc)
        self._check()
        if not lang:
            lang = LOC._hello_language("auto")
        self.detected = lang
        self.lang = lang
        try:
            client.load_align(lang)
        except Exception as exc:
            # No wav2vec2 aligner for this language (e.g. mg / sw): transcribe_clip falls
            # back to faster-whisper word timestamps, punctuation is unaffected.
            self._check()
            logger.info("track transcribe: no aligner for %s (%s)", lang, exc)

    # -- transcribe_fn ---------------------------------------------------
    def __call__(self, path: str, in_t: float, out_t: float, language: str):
        from cutora.batch import text_locate_asr as LOC
        self._check()
        span_len = max(0.0, float(out_t) - float(in_t))
        words_hit, sents_hit, win_label = LOC.lookup_best(path, in_t, out_t, language)
        if words_hit:
            win_covers = (win_label == "full") or (not os.path.isfile(path))
            if not win_covers and win_label and "-" in str(win_label):
                try:
                    wa, wb = (float(x) for x in str(win_label).split("-", 1))
                    win_covers = (wa <= float(in_t) + 0.25 and wb >= float(out_t) - 0.5)
                except (TypeError, ValueError):
                    win_covers = True
            if win_covers:
                w_filtered = [
                    w for w in words_hit
                    if float(w.get("end") or 0.0) >= in_t and float(w.get("start") or 0.0) <= out_t
                ]
                s_filtered = [
                    s for s in (sents_hit or [])
                    if float(s.get("end") or 0.0) >= in_t and float(s.get("start") or 0.0) <= out_t
                ]
                if w_filtered:
                    self.done_sec += span_len
                    self._report("已复用缓存的识别结果")
                    return w_filtered, s_filtered
        if not os.path.isfile(path):
            self.done_sec += span_len
            return [], []
        self._ensure_client(path, in_t, out_t)
        words_all: list[dict] = []
        sents_all: list[dict] = []
        chunk_len = float(LOC.MAX_LOCATE_WINDOW_SEC)
        overlap = 1.0
        cur = float(in_t)
        base_done = self.done_sec
        while cur < float(out_t) - 0.05:
            self._check()
            wend = min(float(out_t), cur + chunk_len)
            try:
                w_chunk, s_chunk = LOC._transcribe_window(self.client, path, self.lang, cur, wend)
            except Exception:
                self._check()          # killed by cancel() → report as cancel, not error
                raise
            cut = words_all[-1]["end"] if words_all else -1.0
            words_all.extend(x for x in w_chunk if float(x.get("start") or 0.0) >= cut - 0.02)
            scut = sents_all[-1]["end"] if sents_all else -1.0
            sents_all.extend(x for x in s_chunk if float(x.get("end") or 0.0) > scut + 0.3)
            self.done_sec = base_done + (wend - float(in_t))
            self._report("正在转录 %s  %.0f / %.0f 秒" % (
                os.path.basename(path), self.done_sec, self.total_sec or span_len))
            if wend >= float(out_t):
                break
            cur = wend - overlap
        self.done_sec = base_done + span_len
        return words_all, sents_all

    def cancel(self) -> None:
        self._cancelled = True
        client = self.client
        if client is None:
            return
        import threading
        threading.Thread(
            target=lambda: client.close(force=True), name="track-asr-cancel", daemon=True,
        ).start()

    def close(self) -> None:
        client, self.client = self.client, None
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def make_default_runner(language: str = "auto", *, should_stop=None, on_progress=None,
                        total_sec: float = 0.0, log=None) -> TrackAsrSession:
    """Factory used by the subtitle-track inspector worker (tests monkeypatch this)."""
    return TrackAsrSession(language, should_stop=should_stop, on_progress=on_progress,
                           total_sec=total_sec, log=log)


def _default_transcribe_span(path: str, in_t: float, out_t: float, language: str) -> tuple[list[dict], list[dict]]:
    """Transcribe [in_t, out_t] of ``path`` in <=30s windows (one-shot session)."""
    session = TrackAsrSession(language)
    try:
        return session(path, in_t, out_t, language)
    finally:
        session.close()


def spans_total_sec(spans: list[dict]) -> float:
    return float(sum(max(0.0, float(sp.get("out_t") or 0.0) - float(sp.get("in_t") or 0.0))
                     for sp in spans or []))


def compute_track_transcript(
    spans: list[dict],
    *,
    language: str = "auto",
    transcribe_fn: Callable[[str, float, float, str], tuple[list[dict], list[dict]]] | None = None,
    sub_track_id: str = "",
    title_locate: dict | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Pure part of the transcription (safe on a worker thread: never touches the timeline).

    ``spans`` comes from :func:`resolve_source_track_spans` (resolved on the GUI thread).
    Returns ``(segments, timeline_words)``; apply them with :func:`apply_track_transcript`.
    """
    runner = transcribe_fn or _default_transcribe_span
    stop = should_stop or (lambda: False)
    tl_words: list[dict] = []
    tl_sents: list[dict] = []

    for sp in spans or []:
        if stop():
            raise TranscribeCancelled("cancelled")
        path = sp["path"]
        in_t = float(sp["in_t"])
        out_t = float(sp["out_t"])
        tl_start = float(sp["tl_start"])
        speed = max(0.05, float(sp["speed"]))
        raw_words, raw_sents = runner(path, in_t, out_t, language)
        clip_src_span = max(0.05, out_t - in_t)
        tl_end_max = tl_start + clip_src_span / speed
        for w in raw_words or []:
            ws = float(w.get("start") or 0.0)
            we = float(w.get("end") or ws)
            # Map from source clip time [in_t, out_t] to timeline time [tl_start, tl_end_max]
            rel_s = ws - in_t if ws >= in_t - 0.25 else ws
            rel_e = we - in_t if we >= in_t - 0.25 else we
            if rel_e <= 0.0 or rel_s >= clip_src_span + 0.05:
                continue
            rel_s = max(0.0, min(clip_src_span, rel_s))
            rel_e = max(rel_s + 0.04, min(clip_src_span, rel_e))
            t_s = max(tl_start, min(tl_end_max, tl_start + rel_s / speed))
            t_e = max(t_s + 0.04, min(tl_end_max, tl_start + rel_e / speed))
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
            if rel_e <= 0.0 or rel_s >= clip_src_span + 0.05:
                continue
            rel_s = max(0.0, min(clip_src_span, rel_s))
            rel_e = max(rel_s + 0.08, min(clip_src_span, rel_e))
            t_s = max(tl_start, min(tl_end_max, tl_start + rel_s / speed))
            t_e = max(t_s + 0.08, min(tl_end_max, tl_start + rel_e / speed))
            tl_sents.append({
                "start": round(t_s, 3),
                "end": round(t_e, 3),
                "text": ensure_sentence_punctuation(str(s.get("text") or "").strip()),
            })

    # If no words came back from runner, use the cached title-locate transcript (main track)
    if not tl_words and isinstance(title_locate, dict):
        loc = title_locate
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

    timed_sents = sentences_with_times(tl_words, tl_sents)
    sub_tid = str(sub_track_id or "")
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
    return segs, tl_words


def apply_track_transcript(task: dict, sub_track, segs: list[dict], tl_words: list[dict]) -> list[dict]:
    """GUI-thread part: write segments onto the subtitle track (+ task['segments'])."""
    # Store transcribed words on sub_track._extra for subsequent manual script alignment
    if sub_track is not None and not isinstance(sub_track, dict):
        extra = getattr(sub_track, "_extra", None)
        if not isinstance(extra, dict):
            extra = {}
            sub_track._extra = extra
        extra["asr_words"] = list(tl_words or [])
    segs = set_track_segments(task, sub_track, segs)
    if segs and not get_track_manual_script(sub_track).strip():
        set_track_manual_script(sub_track, "\n".join(s["text"] for s in segs))
    return segs


def title_locate_for(task, source_track_id: str) -> dict | None:
    if str(source_track_id or MAIN_SOURCE_ID) != MAIN_SOURCE_ID or not isinstance(task, dict):
        return None
    loc = task.get("_title_locate")
    if not isinstance(loc, dict):
        return None
    import copy
    return copy.deepcopy({"segments": loc.get("segments") or [],
                          "sentences": loc.get("sentences") or []})


def transcribe_track_to_segments(
    task: dict,
    sub_track,
    *,
    source_track_id: str | None = None,
    language: str = "auto",
    transcribe_fn: Callable[[str, float, float, str], tuple[list[dict], list[dict]]] | None = None,
) -> list[dict]:
    """Synchronous convenience wrapper (blocks!). The UI uses ``SubTrackTranscribeWorker``."""
    sid = str(source_track_id or get_track_source_id(sub_track) or MAIN_SOURCE_ID)
    set_track_source_id(sub_track, sid)
    spans = resolve_source_track_spans(task, sid)
    sub_tid = str(getattr(sub_track, "id", "") or "") if sub_track is not None else ""
    segs, tl_words = compute_track_transcript(
        spans,
        language=language,
        transcribe_fn=transcribe_fn,
        sub_track_id=sub_tid,
        title_locate=title_locate_for(task, sid),
    )
    return apply_track_transcript(task, sub_track, segs, tl_words)


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

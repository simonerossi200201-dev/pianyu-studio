# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed view over existing task['segments'] dicts.

Field names on disk stay as they are (word / role / wid, …). Extra keys are
kept in ``extra`` so deserialize_render_task and old projects stay compatible.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


_WORD_KNOWN = frozenset({"text", "word", "start", "end", "score"})
_SEG_KNOWN = frozenset({
    "id", "start", "end", "text", "words",
    "speaker", "lang", "style_ref", "locked", "lines",
})


@dataclass
class Word:
    text: str
    start: float
    end: float
    score: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Segment:
    id: str
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)
    speaker: str | None = None
    lang: str | None = None
    style_ref: str | None = None
    locked: bool = False
    lines: list[str] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_opt_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_opt_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def word_from_dict(data: dict | None) -> Word:
    """Read a word dict. Accepts ``word`` (existing) or ``text`` (native JSON)."""
    raw = data if isinstance(data, dict) else {}
    text = raw.get("text")
    if text is None or text == "":
        text = raw.get("word") or ""
    extra = {k: v for k, v in raw.items() if k not in _WORD_KNOWN}
    return Word(
        text=str(text),
        start=_as_float(raw.get("start")),
        end=_as_float(raw.get("end")),
        score=_as_opt_float(raw.get("score")),
        extra=extra,
    )


def word_to_project_dict(word: Word) -> dict:
    """Write a word using existing project field names (``word``, not ``text``)."""
    out: dict[str, Any] = {
        "word": word.text,
        "start": word.start,
        "end": word.end,
    }
    if word.score is not None:
        out["score"] = word.score
    for key, value in word.extra.items():
        if key not in out:
            out[key] = value
    return out


def word_to_native_dict(word: Word) -> dict:
    out: dict[str, Any] = {
        "text": word.text,
        "start": word.start,
        "end": word.end,
    }
    if word.score is not None:
        out["score"] = word.score
    return out


def _as_lines(value: Any) -> list[str] | None:
    if not isinstance(value, list) or not value:
        return None
    out = [str(item) for item in value]
    return out or None


def segment_from_dict(data: dict | None) -> Segment:
    """Read a segment dict. Missing new fields default; unknown keys kept."""
    raw = data if isinstance(data, dict) else {}
    words_raw = raw.get("words") or []
    words = [
        word_from_dict(item)
        for item in words_raw
        if isinstance(item, dict)
    ]
    extra = {k: v for k, v in raw.items() if k not in _SEG_KNOWN}
    return Segment(
        id=str(raw["id"]) if raw.get("id") not in (None, "") else "",
        start=_as_float(raw.get("start")),
        end=_as_float(raw.get("end")),
        text=str(raw.get("text") or ""),
        words=words,
        speaker=_as_opt_str(raw.get("speaker")),
        lang=_as_opt_str(raw.get("lang")),
        style_ref=_as_opt_str(raw.get("style_ref")),
        locked=bool(raw.get("locked", False)),
        lines=_as_lines(raw.get("lines")),
        extra=extra,
    )


def segment_to_project_dict(seg: Segment) -> dict:
    """Write a segment compatible with existing task JSON / renderer."""
    out: dict[str, Any] = {
        "start": seg.start,
        "end": seg.end,
        "text": seg.text,
        "words": [word_to_project_dict(w) for w in seg.words],
    }
    if seg.id:
        out["id"] = seg.id
    if seg.speaker is not None:
        out["speaker"] = seg.speaker
    if seg.lang is not None:
        out["lang"] = seg.lang
    if seg.style_ref is not None:
        out["style_ref"] = seg.style_ref
    if seg.locked:
        out["locked"] = True
    if seg.lines:
        out["lines"] = list(seg.lines)
    if "role" not in seg.extra:
        out["role"] = "default"
    for key, value in seg.extra.items():
        if key not in out:
            out[key] = value
    return out


def segment_to_native_dict(seg: Segment) -> dict:
    return {
        "id": seg.id,
        "start": seg.start,
        "end": seg.end,
        "text": seg.text,
        "words": [word_to_native_dict(w) for w in seg.words],
        "speaker": seg.speaker,
        "lang": seg.lang,
        "style_ref": seg.style_ref,
        "locked": bool(seg.locked),
        "lines": list(seg.lines) if seg.lines else None,
    }


def segments_from_dicts(items: list | None) -> list[Segment]:
    out: list[Segment] = []
    for item in items or []:
        if isinstance(item, dict):
            out.append(segment_from_dict(item))
        elif isinstance(item, Segment):
            out.append(item)
    return out


def segments_to_project_dicts(segments: list[Segment] | None) -> list[dict]:
    return [segment_to_project_dict(s) for s in (segments or [])]

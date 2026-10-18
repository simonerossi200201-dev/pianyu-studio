# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Decide language before ASR: detect first, confirm only on low confidence."""
from __future__ import annotations

from typing import Any, Callable

from cutora.asr.languages import whisper_language_arg

BATCH_RESULT_COLUMNS = ("language", "confidence")

StartFn = Callable[[str], Any]
DetectFn = Callable[[str], dict]
ConfirmFn = Callable[[dict], str | None]


def needs_detect(requested: str | None) -> bool:
    return whisper_language_arg(requested) is None


def normalize_subtitle_languages(language, current: str | None = None) -> list[str]:
    """Languages to run for subtitle.auto.

    Omitted / empty → [] (keep the current setting). ``auto`` or a code →
    one pass. A list → one pass per code, same media.
    """
    _ = current
    if language is None:
        return []
    if isinstance(language, (list, tuple)):
        out = []
        for item in language:
            code = str(item or "").strip().lower()
            if code:
                out.append(code)
        return out
    code = str(language).strip().lower()
    if not code:
        return []
    return [code]


def run_single_auto(
    *,
    media: str,
    requested: str | None,
    interactive: bool,
    detect_fn: DetectFn,
    start_fn: StartFn,
    ask_confirm_fn: ConfirmFn,
) -> dict:
    """Resolve language, optionally confirm, then start recognition.

    ``ask_confirm_fn`` returns the language to use, or None to cancel.
    Recognition (``start_fn``) is called only after a decision.
    """
    explicit = whisper_language_arg(requested)
    if explicit:
        start_fn(explicit)
        return {"started": True, "confirmed": False, "language": explicit, "warn": False}
    det = detect_fn(media)
    lang = str(det.get("language") or "")
    low = bool(det.get("low_confidence"))
    if low and interactive:
        chosen = ask_confirm_fn(det)
        if not chosen:
            return {
                "started": False, "confirmed": True,
                "language": lang, "warn": True, "detection": det,
            }
        start_fn(str(chosen))
        return {
            "started": True, "confirmed": True,
            "language": str(chosen), "warn": True, "detection": det,
        }
    start_fn(lang)
    return {
        "started": True, "confirmed": False,
        "language": lang, "warn": low, "detection": det,
    }


def plan_batch(
    tasks: list,
    *,
    detect_fn: DetectFn,
    requested: str | None = "auto",
    ask_confirm_fn: ConfirmFn | None = None,
) -> list[dict]:
    """Per-file detect. Never calls ``ask_confirm_fn`` (batch is non-modal)."""
    _ = ask_confirm_fn
    explicit = whisper_language_arg(requested)
    rows = []
    for i, task in enumerate(tasks or []):
        path = str((task or {}).get("video") or "")
        if explicit:
            rows.append({
                "media_id": i,
                "path": path,
                "language": explicit,
                "confidence": None,
                "warn": False,
            })
            continue
        det = detect_fn(path) if path else {
            "language": "en", "confidence": 0.0, "low_confidence": True,
        }
        rows.append({
            "media_id": i,
            "path": path,
            "language": str(det.get("language") or ""),
            "confidence": det.get("confidence"),
            "warn": bool(det.get("low_confidence")),
            "detection": det,
        })
    return rows


def yellow_media_ids(rows: list[dict]) -> list[int]:
    return [int(r["media_id"]) for r in rows if r.get("warn")]

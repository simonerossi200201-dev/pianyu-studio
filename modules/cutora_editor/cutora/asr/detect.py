# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Language detection from the first 30 s of audio, with confidence."""
from __future__ import annotations

import os

from cutora.asr.languages import get_language
from cutora.core.commands import CommandError
from cutora.core.log import get_logger

logger = get_logger("cutora.asr.detect")

DETECT_SECONDS = 30
SAMPLE_RATE = 16000
LOW_CONFIDENCE_THRESHOLD = 0.6
THRESHOLD_SETTING_KEY = "asr.detect_confidence_threshold"


def get_detect_threshold() -> float:
    """Read ``asr.detect_confidence_threshold`` from app settings (default 0.6)."""
    try:
        from cutora.core.history import _load_app_settings
        raw = (_load_app_settings() or {}).get(
            THRESHOLD_SETTING_KEY, LOW_CONFIDENCE_THRESHOLD)
        val = float(raw)
    except (TypeError, ValueError, Exception):
        val = LOW_CONFIDENCE_THRESHOLD
    if val <= 0.0 or val > 1.0:
        return LOW_CONFIDENCE_THRESHOLD
    return val


def build_detection_result(language: str, confidence: float) -> dict:
    lang = str(language or "").strip().lower()
    conf = float(confidence)
    entry = get_language(lang) or {
        "code": lang,
        "name_en": lang,
        "name_native": lang,
        "name_zh": lang,
        "script": "other",
        "line_break": "fallback",
        "experimental": True,
    }
    low = conf < get_detect_threshold()
    name = entry.get("name_zh") or lang
    prompt = ""
    if low:
        from cutora.ui.i18n import tr
        prompt = tr("检测为 {name}（低置信），请确认", name=name)
    return {
        "language": lang,
        "confidence": conf,
        "low_confidence": low,
        "experimental": bool(entry.get("experimental")),
        "script": entry.get("script") or "other",
        "line_break": entry.get("line_break") or "fallback",
        "name_zh": entry.get("name_zh") or lang,
        "name_en": entry.get("name_en") or lang,
        "name_native": entry.get("name_native") or lang,
        "prompt_zh": prompt,
    }


def detect_from_array(audio, model=None) -> dict:
    pipeline = model
    if pipeline is None:
        from cutora.asr.engine import get_whisperx_model
        pipeline = get_whisperx_model()
    fw = getattr(pipeline, "model", None)
    if fw is None or not hasattr(fw, "detect_language"):
        raise CommandError("识别模型不支持 detect_language")
    clip = audio[: DETECT_SECONDS * SAMPLE_RATE]
    language, confidence, _rest = fw.detect_language(clip)
    result = build_detection_result(str(language), float(confidence))
    logger.info(
        "detect_language %s confidence=%.3f low=%s",
        result["language"], result["confidence"], result["low_confidence"],
    )
    return result


def detect_for_media(path: str, model=None) -> dict:
    media = str(path or "").strip()
    if not media or not os.path.isfile(media):
        raise CommandError("媒体文件不存在: %s" % (path,))
    from cutora.asr.engine import get_whisperx_model, whisperx
    pipeline = model or get_whisperx_model()
    from cutora.asr.audio_io import load_audio as _load_audio_nowin
    audio = _load_audio_nowin(media)   # Q133：不开黑框
    return detect_from_array(audio, model=pipeline)

# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Lock one language for a stitch batch, then refuse a tier we cannot align."""
from __future__ import annotations

from cutora.asr.models import align_model_label, align_tier, language_capability
from cutora.asr.languages import align_phrase


class StitchLanguageError(RuntimeError):
    """Raised before any cut when the batch language cannot be aligned."""


def read_whisper_info(info) -> tuple[str, float]:
    """``info.language`` and ``info.language_probability`` from faster-whisper."""
    lang = getattr(info, "language", None)
    prob = getattr(info, "language_probability", None)
    if lang is None and isinstance(info, dict):
        lang = info.get("language")
        prob = info.get("language_probability")
    if prob is None:
        prob = 0.0
    try:
        conf = float(prob)
    except (TypeError, ValueError):
        conf = 0.0
    return str(lang or "").strip().lower(), conf


def read_whisper_detection(model, audio_or_path):
    """Call faster-whisper ``transcribe(language=None)`` and return its info.

    A whisperx pipeline is unwrapped via ``.model``. A test double may
    implement ``transcribe`` and return ``(segments, info)``. Segments are
    not iterated: ``info`` is filled before the generator runs.
    """
    fw = model
    inner = getattr(model, "model", None)
    if inner is not None and hasattr(inner, "transcribe"):
        fw = inner
    result = fw.transcribe(audio_or_path, language=None)
    if isinstance(result, tuple) and len(result) >= 2:
        return result[1]
    raise StitchLanguageError("识别模型没有返回语言检测结果，拼接没有开始。")


def detection_log(code: str, prob: float) -> str:
    cap = language_capability(code) if code else {"name_zh": code or "未知", "code": code}
    name = cap.get("name_zh") or code or "未知"
    shown = code or "未知"
    return "检测到%s（%s），置信度 %.0f%%。" % (name, shown, float(prob) * 100.0)


def align_choice_log(code: str) -> str:
    cap = language_capability(code)
    tier = str(cap.get("align_tier") or "none")
    return "对齐档位：%s（%s），模型 %s。" % (
        align_phrase(tier), tier, align_model_label(code))


def unstitchable_message(code: str) -> str:
    cap = language_capability(code)
    name = cap.get("name_zh") or code or "这种语言"
    shown = code or "?"
    return (
        "检测到%s（%s）。这种语言能把声音转成文字，但没有逐词时间对齐，"
        "拼接不知道每个词从哪一秒开始，所以还不能拼。"
        "请换一种标着「逐词精确」或「可用（精度略低）」的语言。"
        % (name, shown)
    )


def resolve_detected_language(info, confirm_fn) -> str:
    """Lock one code for the whole batch.

    ``confirm_fn`` is called only when the probability is below the detect
    threshold. It returns the code to use, or None if the user cancelled.
    A ``none`` align tier raises before any cut.
    """
    from cutora.asr.detect import build_detection_result, get_detect_threshold
    lang, prob = read_whisper_info(info)
    if prob < get_detect_threshold():
        det = build_detection_result(lang, prob)
        chosen = confirm_fn(det)
        if not chosen:
            raise StitchLanguageError("已取消：没有确认检测到的语言，拼接没有开始。")
        lang = str(chosen).strip().lower()
    if not lang:
        raise StitchLanguageError(
            "没有从这段声音里认出语言，拼接没有开始。"
            "请在语言里改选一种标着「逐词精确」或「可用（精度略低）」的。")
    if align_tier(lang) == "none":
        raise StitchLanguageError(unstitchable_message(lang))
    return lang

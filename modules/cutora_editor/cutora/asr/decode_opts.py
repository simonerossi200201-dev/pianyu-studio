# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Whisper decode options shared by the turbo (and all CT2) load path.

``condition_on_previous_text=True`` is the main turbo hallucination source:
the model feeds its own output into the next window and loops on silence
or music. Product code must pass these into whisperx.load_model and into
faster-whisper.transcribe; relying on a vendor default is not a guarantee.
"""
from __future__ import annotations

from typing import Any

# compression_ratio_threshold / no_speech_threshold start at whisper defaults.
# Q38 measures each change; do not retune here without a new row in
# docs/plan/reports/Q38_turbo_hallucination.md.
ASR_DECODE_OPTIONS: dict[str, Any] = {
    "condition_on_previous_text": False,
    "compression_ratio_threshold": 2.4,
    "no_speech_threshold": 0.6,
}


def asr_decode_options() -> dict[str, Any]:
    return dict(ASR_DECODE_OPTIONS)

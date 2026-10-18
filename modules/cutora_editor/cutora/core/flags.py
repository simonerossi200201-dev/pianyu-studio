# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Process-wide environment flags.

``CUTORA_RENDER_CHILD`` is the current name. The previous name is still
read so older parent processes and leftover shells keep working.
"""
from __future__ import annotations

import os

RENDER_CHILD_ENV = "CUTORA_RENDER_CHILD"
# Compatibility: T01 and earlier parents set this. Do not write it from new code.
RENDER_CHILD_ENV_LEGACY = "AUTOSUB_RENDER_CHILD"
RECHECK_ASR_ENV = "CUTORA_RECHECK_ASR"
RECHECK_ASR_ENV_LEGACY = "AUTOSUB_RECHECK_ASR"


def is_render_child() -> bool:
    """True when this process is a pure render worker (no torch / CUDA)."""
    return os.environ.get(RENDER_CHILD_ENV) == "1" or os.environ.get(
        RENDER_CHILD_ENV_LEGACY) == "1"


def mark_render_child() -> None:
    """Mark this process as a render child. Sets the current name; keeps the old one readable."""
    apply_render_child(os.environ)


def apply_render_child(env: dict) -> dict:
    """Set both current and legacy render-child flags on ``env``."""
    env[RENDER_CHILD_ENV] = "1"
    env[RENDER_CHILD_ENV_LEGACY] = "1"
    return env


def clear_render_child(env: dict | None = None) -> dict:
    """Drop both render-child flags (e.g. before launching the guard window)."""
    target = os.environ if env is None else env
    target.pop(RENDER_CHILD_ENV, None)
    target.pop(RENDER_CHILD_ENV_LEGACY, None)
    return target


def recheck_asr_dir() -> str:
    return (os.environ.get(RECHECK_ASR_ENV) or os.environ.get(
        RECHECK_ASR_ENV_LEGACY) or "").strip()

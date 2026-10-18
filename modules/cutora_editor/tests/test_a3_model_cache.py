# -*- coding: utf-8 -*-
"""A3: cached whisperx model returns before hardware probe."""
from __future__ import annotations

import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_get_whisperx_model_cache_before_probe():
    src = open(
        os.path.join(REPO, "cutora", "asr", "engine.py"), encoding="utf-8"
    ).read()
    fn = src.split("def get_whisperx_model", 1)[1]
    fn = fn.split("\ndef ", 1)[0]
    cache_at = fn.find("return _WHISPERX_MODEL")
    probe_at = fn.find("probe = probe_hardware()")
    assert cache_at != -1
    assert probe_at != -1
    assert cache_at < probe_at

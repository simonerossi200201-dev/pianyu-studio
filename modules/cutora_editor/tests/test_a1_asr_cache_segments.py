# -*- coding: utf-8 -*-
"""A1: stitch project_from_placements copies _asr_cache words with start_t−in_t."""
from __future__ import annotations

import json
import os
import time

from cutora.batch.timeline_stitch import (
    ASR_CACHE_NAME,
    ClipPlacement,
    project_from_placements,
    segments_from_asr_cache,
)


def _write_cache(folder, path, words, language="en"):
    from cutora.batch.timeline_stitch import _clip_fingerprint
    os.makedirs(folder, exist_ok=True)
    key = _clip_fingerprint(path, language)
    payload = {
        key: {
            "words": words,
            "dur": 10.0,
            "timing_tier": 1,
            "incomplete": 0,
            "role": "default",
            "env": "QQ==",
        }
    }
    dest = os.path.join(folder, ASR_CACHE_NAME)
    with open(dest, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    return dest


def test_offset_start_t_minus_in_t(tmp_path):
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"fake")
    words = [["hello", 2.0, 2.4], ["world", 2.5, 3.0], ["skip", 8.0, 8.5]]
    cache_dir = str(tmp_path / "_stitched")
    _write_cache(cache_dir, str(src), words)
    place = ClipPlacement(str(src), in_t=2.0, out_t=4.0, start_t=10.0)
    segs = segments_from_asr_cache([place], cache_dir)
    flat = []
    for s in segs:
        for w in s.get("words") or []:
            flat.append((w["word"], w["start"], w["end"]))
    assert [("hello", 10.0, 10.4), ("world", 10.5, 11.0)] == [
        (a, round(b, 3), round(c, 3)) for a, b, c in flat]
    assert "skip" not in [x[0] for x in flat]
    assert segs[0].get("timing_tier") == 1
    assert segs[0].get("role") == "default"
    assert segs[0].get("env") == "QQ=="


def test_words_match_cache_after_roundtrip(tmp_path):
    src = tmp_path / "a.mp4"
    src.write_bytes(b"x")
    words = [["one", 0.0, 0.3], ["two", 0.4, 0.8], ["three", 1.0, 1.4]]
    dest = tmp_path / "g" / "_stitched" / "stitched.asproj.json"
    dest.parent.mkdir(parents=True)
    _write_cache(str(dest.parent), str(src), words)
    t0 = time.perf_counter()
    project_from_placements(
        [ClipPlacement(str(src), 0.0, 2.0, 0.0)], str(dest), text="one two three")
    elapsed = time.perf_counter() - t0
    data = json.loads(dest.read_text(encoding="utf-8"))
    segs = data["tasks"][0]["segments"]
    got = [(w["word"], round(float(w["start"]), 3), round(float(w["end"]), 3))
           for s in segs for w in (s.get("words") or [])]
    assert got == [("one", 0.0, 0.3), ("two", 0.4, 0.8), ("three", 1.0, 1.4)]
    assert elapsed < 5.0


def test_luca_t023_cache_words_if_present():
    cache = os.path.join(
        r"D:\1_PROJECT\2026-09-15\A\T0023-认真-0915-v1\clips\_stitched",
        ASR_CACHE_NAME)
    if not os.path.isfile(cache):
        return
    raw = json.loads(open(cache, encoding="utf-8").read())
    n_words = sum(len((v or {}).get("words") or []) for v in raw.values()
                  if isinstance(v, dict))
    assert n_words > 300

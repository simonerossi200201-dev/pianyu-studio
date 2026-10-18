# -*- coding: utf-8 -*-
"""ASR coverage floor — locks 'quietly dropped the second half of the clip'.

Golden does not run ASR. These 5 clips (copied from T0035/T0040, including
T0040-s002-p09) each have a hand-checked reference. Word coverage ≥ floor
and last-word end ≥ duration − 1.0 s. Floor is per ASR size.
"""
from __future__ import annotations

import json
import os

from cutora.batch import script_stitcher as ss

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(REPO, "tests", "fixtures", "asr_coverage")
REFS = os.path.join(FIX, "refs.json")


def _load():
    return json.loads(open(REFS, encoding="utf-8").read())


def coverage_ratio(words, ref_text):
    script = ss.tokenize(ref_text)
    m = ss.match_clip_global(list(words), script)
    return float(m.get("hit") or 0) / max(1, len(script))


def test_bug06_p09_fixture_present():
    data = _load()
    names = [c["file"] for c in data["clips"]]
    assert "T0040-s002-p09.mp4" in names
    for row in data["clips"]:
        path = os.path.join(FIX, row["file"])
        assert os.path.isfile(path), path
        assert 6.0 <= float(row["dur"]) <= 8.01


def test_coverage_metric_on_synthetic_p09():
    data = _load()
    p09 = next(c for c in data["clips"] if "s002-p09" in c["file"])
    truncated = [("many", 0.0, 0.74), ("people", 0.8, 1.2),
                 ("use", 1.3, 1.6), ("scripture", 1.7, 2.2),
                 ("to", 2.2, 2.3), ("condemn", 2.3, 2.7),
                 ("others", 2.42, 2.90)]
    assert coverage_ratio(truncated, p09["ref"]) < 0.7
    assert ss.last_word_end(truncated) < float(p09["dur"]) - 1.0
    full = truncated + [
        ("but", 3.0, 3.2), ("forget", 3.3, 3.7), ("that", 3.7, 3.9),
        ("jesus", 4.0, 4.4), ("also", 4.5, 4.8), ("brought", 4.9, 5.3),
        ("grace", 5.4, 5.8), ("mercy", 6.0, 6.5), ("and", 6.6, 6.7),
        ("freedom", 7.0, 7.8),
    ]
    assert coverage_ratio(full, p09["ref"]) >= 0.9
    assert ss.last_word_end(full) >= float(p09["dur"]) - 1.0


def test_asr_coverage_floor_live():
    from cutora.asr.engine import get_align_model, get_whisperx_model
    from cutora.asr.models import get_model
    from cutora.asr.tier import path_to_asr_id, resolve_runtime_tier, probe_hardware
    from cutora.core.config import DEVICE
    from cutora.core.paths import get_model_paths

    data = _load()
    floors = data["floor"]
    slack = float(data["tail_slack_sec"])
    probe = probe_hardware()
    wanted = (get_model_paths() or {}).get("asr_path")
    picked, _ = resolve_runtime_tier(wanted, probe)
    size_key = picked["asr_id"]
    floor = float(floors.get(size_key, floors.get("default", 0.9)))
    model = get_whisperx_model()
    try:
        align_model, metadata = get_align_model("en")
    except TypeError:
        align_model, metadata = get_align_model()
    failures = []
    for row in data["clips"]:
        path = os.path.join(FIX, row["file"])
        dur = float(row["dur"])
        audio = ss.load_audio_16k(path)
        status = {}
        words = ss.transcribe_clip(
            model, align_model, metadata, audio, dur, "en", DEVICE,
            status=status)
        cov = coverage_ratio(words, row["ref"])
        last = ss.last_word_end(words)
        if cov < floor:
            failures.append("%s coverage %.2f < %.2f text=%s" % (
                row["file"], cov, floor, " ".join(w[0] for w in words)))
        if last < dur - slack:
            failures.append("%s last_end %.2f < %.2f" % (
                row["file"], last, dur - slack))
        if "s002-p09" in row["file"]:
            names = {w[0].lower() for w in words}
            # 8.0s fixture ends on mercy (envelope dies after 7.5s).
            for need in ("grace", "mercy"):
                if need not in names:
                    failures.append("p09 missing %s" % need)
    assert not failures, "\n".join(failures)

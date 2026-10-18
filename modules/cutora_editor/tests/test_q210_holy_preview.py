# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q210: match-duration uses the timeline, stem builds do not overlap,
preview edge fade matches apply_edge_fade."""
from __future__ import annotations

import os
import sys
import threading
import time

import numpy as np

if "PySide6" not in sys.modules:
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")

from cutora.audio.stem_cache import StemCache, StemRebuildGate
from cutora.model.match_span import match_target_sec, refresh_match_targets
from cutora.model.timeline import Timeline
from cutora.model.timeline_model import Clip, Track


def _video(source_id, inn, out, start):
    tr = Track("video")
    tr.clips.append(Clip(source_id, inn, out, start))
    return tr


def test_match_target_uses_timeline_not_a_short_clock():
    tl = Timeline(1080, 1920, 30)
    tl.add_track(_video("main", 0.0, 193.856, 0.0))
    btr = _video("b", 0.0, 15.042, 0.0)
    btr.clips[0].loop = True
    btr.clips[0].match_duration = True
    btr.clips[0]._match_target_dur = 58.708
    tl.add_track(btr)
    holy_tr = _video("holy", 0.0, 10.005, 2.77)
    holy = holy_tr.clips[0]
    holy.loop = True
    holy.match_duration = True
    holy._match_target_dur = 55.938
    tl.add_track(holy_tr)
    bgm = Track("audio")
    bgm.clips.append(Clip("bgm", 0.0, 195.856, 0.0))
    tl.add_track(bgm)
    task = {"timeline": tl}
    changed = refresh_match_targets(task)
    assert abs(holy._match_target_dur - (195.856 - 2.77)) < 1e-6
    assert abs(btr.clips[0]._match_target_dur - 195.856) < 1e-6
    assert len(changed) == 2
    assert refresh_match_targets(task) == []
    assert abs(match_target_sec(task, holy) - holy._match_target_dur) < 1e-9


def test_match_target_numeric():
    tl = Timeline(100, 100, 30)
    tl.add_track(_video("main", 0.0, 193.856, 0.0))
    holy_tr = _video("holy", 0.0, 10.0, 2.77)
    holy = holy_tr.clips[0]
    holy.match_duration = True
    holy._match_target_dur = 55.938
    tl.add_track(holy_tr)
    task = {"timeline": tl}
    refresh_match_targets(task)
    assert abs(holy._match_target_dur - (193.856 - 2.77)) < 1e-6


def test_stem_gate_runs_the_dirty_request_once():
    gate = StemRebuildGate()
    assert gate.begin() is True
    assert gate.begin() is False
    assert gate.begin() is False
    assert gate.finish() is True
    assert gate.running is True
    assert gate.finish() is False
    assert gate.running is False
    assert gate.begin() is True


def test_stem_builds_do_not_overlap(monkeypatch):
    state = {"inside": 0, "max": 0}
    release = threading.Event()
    entered = threading.Event()

    def slow(_task, _rows, *, role="main", pad_to=0.0):
        state["inside"] += 1
        state["max"] = max(state["max"], state["inside"])
        if role == "main":
            entered.set()
            assert release.wait(2.0)
        state["inside"] -= 1
        return np.zeros((32, 2), dtype=np.float32)

    monkeypatch.setattr("cutora.audio.stem_cache.render_role_pcm", slow)
    cache = StemCache()
    task = {"timeline": None}
    done = []

    def run():
        cache.build(task, task_key="t")
        done.append(1)

    first = threading.Thread(target=run)
    second = threading.Thread(target=run)
    first.start()
    assert entered.wait(2.0)
    second.start()
    time.sleep(0.08)
    assert state["max"] == 1
    assert len(done) == 0
    release.set()
    first.join(3)
    second.join(3)
    assert len(done) == 2
    assert state["max"] == 1
    assert state["inside"] == 0


def test_preview_fade_matches_full_fade():
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from cutora.render.fade_strips import fade_qimage_fast
    from cutora.render.textfx.fade import apply_edge_fade
    from cutora.render.textfx.raster import as_bgra

    class Obj:
        fade_top = 0.1
        fade_bottom = 0.1
        fade_left = 0.1
        fade_right = 0.1

    rng = np.random.default_rng(7)
    for w, h in ((64, 40), (721, 333), (720, 1280)):
        img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(QColor(0, 0, 0, 255))
        _owned, bgra = as_bgra(img)
        bgra[:] = rng.integers(0, 256, bgra.shape, dtype=np.uint8)
        fast = fade_qimage_fast(img, Obj())
        full = apply_edge_fade(img, 0.1, 0.1, 0.1, 0.1)
        assert np.array_equal(as_bgra(fast)[1], as_bgra(full)[1])

    class Wide:
        fade_top = 0.7
        fade_bottom = 0.7
        fade_left = 0.6
        fade_right = 0.6

    img = QImage(80, 50, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(8, 16, 32, 200))
    fast = fade_qimage_fast(img, Wide())
    full = apply_edge_fade(img, 0.7, 0.7, 0.6, 0.6)
    assert np.array_equal(as_bgra(fast)[1], as_bgra(full)[1])
    assert fade_qimage_fast(img, type("Z", (), {})()) is img

    bgr = QImage(720, 1280, QImage.Format.Format_BGR888)
    bgr.fill(QColor(40, 80, 120))
    fast = fade_qimage_fast(bgr, Obj())
    full = apply_edge_fade(bgr, 0.1, 0.1, 0.1, 0.1)
    assert np.array_equal(as_bgra(fast)[1], as_bgra(full)[1])

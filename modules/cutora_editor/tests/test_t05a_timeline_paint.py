# -*- coding: utf-8 -*-
"""T05a: 1000-clip timeline paint stays under 16 ms with viewport culling."""
from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(["t05a-paint"])
    return app


def test_paint_1000_clips_under_16ms(qapp):
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track
    from cutora.ui.timeline.widget import TimelineWidget

    tl = Timeline(1080, 1920, 30)
    track = Track("video")
    for i in range(1000):
        track.clips.append(Clip("src", 0.0, 1.0, float(i)))
    tl.tracks.append(track)
    w = TimelineWidget()
    w.resize(800, 360)
    w.pixels_per_second = 40.0
    w.scroll_x = 200.0
    w.set_data(1_000_000, {"timeline": tl, "segments": []})
    w.show()
    qapp.processEvents()
    # Warm-up so font/theme init is not in the timed sample.
    w.repaint()
    qapp.processEvents()
    samples = []
    for _ in range(5):
        t0 = time.perf_counter()
        w.repaint()
        samples.append((time.perf_counter() - t0) * 1000.0)
    w.close()
    best = min(samples)
    print("T05A_PAINT_MS min=%.2f all=%s" % (best, ["%.2f" % x for x in samples]),
          flush=True)
    assert best < 16.0, samples

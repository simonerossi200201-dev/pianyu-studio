# -*- coding: utf-8 -*-
"""BUG-01: waveform get_lod must not bool() a numpy array during timeline paint."""
from __future__ import annotations

import os
import subprocess

import numpy as np
import pytest

from cutora.audio.waveform import get_waveform_cache

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(REPO, "runtime", "venv", "Scripts", "python.exe")


def test_get_lod_does_not_bool_ndarray():
    cache = get_waveform_cache()
    peaks = np.column_stack((
        np.full(400, -0.4, dtype=np.float32),
        np.full(400, 0.4, dtype=np.float32),
    ))
    cache.put("bug01-src", peaks)
    for pps in (2.0, 20.0, 200.0):
        got = cache.get_lod("bug01-src", pps)
        assert got is not None, pps
        assert got.ndim == 2 and got.shape[1] == 2, (pps, got.shape)


def test_timeline_paint_with_waveform_zero_painterror():
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONUNBUFFERED"] = "1"
    env["CUTORA_SKIP_ENCODER_PROBE"] = "1"
    r = subprocess.run(
        [PY, "-u", os.path.abspath(__file__), "--paint"],
        cwd=REPO, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=90,
    )
    out = (r.stdout or "") + "\n" + (r.stderr or "")
    assert r.returncode == 0, out[-4000:]
    assert "BUG01_PAINT_OK" in (r.stdout or ""), out[-4000:]
    assert "PaintError" not in out
    assert "ambiguous" not in out.lower()
    assert "BUG03_PLAYHEAD_OK" in (r.stdout or ""), out[-4000:]


def _run_paint() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    from PySide6.QtWidgets import QApplication
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip
    from cutora.ui.timeline.widget import TimelineWidget

    app = QApplication.instance() or QApplication(["bug01-paint"])
    peaks = np.column_stack((
        np.full(400, -0.4, dtype=np.float32),
        np.full(400, 0.4, dtype=np.float32),
    ))
    cache = get_waveform_cache()
    sid = "bug01-audio"
    cache.put(sid, peaks)
    tl = Timeline(1080, 1920, 30)
    audio = None
    for t in tl.tracks:
        if getattr(t, "type", None) == "audio":
            audio = t
            break
    if audio is None:
        from cutora.model.timeline_model import Track
        audio = Track("audio")
        tl.add_track(audio)
    audio.clips.append(Clip(sid, 0.0, 2.0, 0.0))
    tl.sources[sid] = {"id": sid, "path": "dummy.wav", "duration": 2.0}
    task = {"duration": 2.0, "segments": [], "style": {}, "timeline": tl}
    w = TimelineWidget()
    w.resize(800, 400)
    w.set_data(2000, task)
    w.position = 1000
    w.pixels_per_second = 40
    w.show()
    w.repaint()
    img = w.grab().toImage()
    px = int(round((w.position / 1000.0) * w.pixels_per_second))
    found = False
    for y in range(0, img.height(), 3):
        c = img.pixelColor(px, y)
        if c.red() > 160 and c.green() < 120 and c.blue() < 120:
            found = True
            break
    if not found:
        raise SystemExit("playhead column %d has no red pixel" % px)
    print("BUG01_PAINT_OK", flush=True)
    print("BUG03_PLAYHEAD_OK x=%d" % px, flush=True)
    return 0


if __name__ == "__main__":
    import sys
    if "--paint" in sys.argv:
        raise SystemExit(_run_paint())
    raise SystemExit("usage: --paint")

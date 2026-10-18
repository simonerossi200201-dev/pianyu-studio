# -*- coding: utf-8 -*-
"""Time the first VideoCanvas paints while opening the Q71 32-clip project."""
from __future__ import annotations

import os
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")
os.environ.setdefault("PYTHONUTF8", "1")

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "tests"))

from cutora.core.runtime import init_torch_runtime  # noqa: E402
init_torch_runtime(render_child=False)

from test_q71_open import (  # noqa: E402
    _StallWatch,
    _make_win,
    _n_clip_project,
    _qapp,
    _videos,
)


def _wrap(obj, name, bag, key=None):
    orig = getattr(obj, name)
    key = key or name

    def wrapped(*a, **k):
        t0 = time.perf_counter()
        try:
            return orig(*a, **k)
        finally:
            bag.append((key, (time.perf_counter() - t0) * 1000.0))

    setattr(obj, name, wrapped)
    return orig


def main():
    files = _videos()
    dest = os.path.join(tempfile.mkdtemp(prefix="q71p_"), "q71_32.asproj.json")
    _n_clip_project(dest, n=32, files=files)
    app = _qapp()
    win = _make_win()
    canvas = win.video_canvas
    paints = []
    flags = {"n": 0, "worker": None}

    orig_paint = canvas.paintEvent

    def paintEvent(event, _orig=orig_paint):
        flags["n"] += 1
        n = flags["n"]
        worker = getattr(win, "_preview_worker", None)
        running = bool(worker is not None and worker.isRunning())
        owns = canvas._preview_worker_owns_decode()
        prog = getattr(canvas, "_program_frame", None)
        bag = []
        wraps = [
            _wrap(canvas, "_paint_scene", bag),
            _wrap(canvas, "_paint_stacked_scene", bag),
            _wrap(canvas, "_draw_preview_local", bag),
            _wrap(canvas, "_get_cv_frame_for_preview", bag),
            _wrap(canvas, "_preview_worker_owns_decode", bag),
            _wrap(canvas, "_alpha_checker_wanted", bag),
        ]
        t0 = time.perf_counter()
        try:
            return _orig(event)
        finally:
            dt = (time.perf_counter() - t0) * 1000.0
            paints.append({
                "n": n,
                "ms": round(dt, 1),
                "worker_running": running,
                "owns_decode": owns,
                "has_prog": prog is not None and not getattr(prog, "isNull", lambda: True)(),
                "parts": [(k, round(v, 1)) for k, v in bag],
            })
            for name, orig in (
                ("_paint_scene", wraps[0]),
                ("_paint_stacked_scene", wraps[1]),
                ("_draw_preview_local", wraps[2]),
                ("_get_cv_frame_for_preview", wraps[3]),
                ("_preview_worker_owns_decode", wraps[4]),
                ("_alpha_checker_wanted", wraps[5]),
            ):
                setattr(canvas, name, orig)

    canvas.paintEvent = paintEvent
    win.show()
    app.processEvents()
    watch = _StallWatch()
    watch.start(app)
    win.command_project_open(path=dest)
    app.processEvents()
    watch.stop()
    print("max_gap_ms", round(watch.max_gap_ms, 1))
    print("slow", watch.slow)
    print("paints", len(paints))
    for row in paints[:8]:
        print(row)
    win.close()


if __name__ == "__main__":
    main()

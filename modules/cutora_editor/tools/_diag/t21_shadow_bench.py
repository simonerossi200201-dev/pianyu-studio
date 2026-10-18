# -*- coding: utf-8 -*-
"""Q21: ms/frame with projection on vs off (skip_blur False = export path)."""
from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from PySide6.QtWidgets import QApplication  # noqa: E402
QApplication.instance() or QApplication(["t21-bench"])

from cutora.ui.panels.fonts import load_embedded_fonts  # noqa: E402
from cutora.render.textfx.layout import layout_event  # noqa: E402
from cutora.render.textfx.raster import raster_roi  # noqa: E402
from cutora.render.textfx.spec import from_style  # noqa: E402

load_embedded_fonts()
CANVAS_W, CANVAS_H = 720, 1280


def _style(shadow):
    return {
        "font_family": "Arimo",
        "fontsize": 72,
        "bold": True,
        "pos_x": 360,
        "pos_y": 900,
        "fx": {
            "fill": {"type": "solid", "colors": ["#F4EBD8"]},
            "stroke": {"width_em": 0, "color": "#E11D48"},
            "glow": {"sigma_em": 0, "strength": 0},
            "shadow": shadow,
            "bg": {"type": "none"},
            "anim": {"in": "none", "loop": "none", "out": "none"},
            "emphasis": {"words": []},
        },
    }


def _event():
    text = "Find its place"
    words = text.split()
    step = 0.4
    return {
        "text": text, "start": 0.0, "end": 2.0, "id": "s0",
        "words": [
            {"word": w, "start": i * step, "end": i * step + step}
            for i, w in enumerate(words)
        ],
    }


def _bench(style, n=40, warmup=8, skip_blur=False):
    event = _event()
    spec = from_style(style)
    lay = layout_event(event, style, spec, CANVAS_W, CANVAS_H)
    for _ in range(warmup):
        raster_roi(lay, spec, style, None, CANVAS_W, CANVAS_H,
                   skip_blur=skip_blur, t=0.55, event=event)
    t0 = time.perf_counter()
    for _ in range(n):
        raster_roi(lay, spec, style, None, CANVAS_W, CANVAS_H,
                   skip_blur=skip_blur, t=0.55, event=event)
    dt = (time.perf_counter() - t0) * 1000.0 / n
    return dt, lay.roi_w, lay.roi_h


off = _style({"sigma_em": 0, "alpha": 0})
legacy = _style({
    "sigma_em": 0.12, "dx_em": 0.08, "dy_em": 0.10,
    "color": "#141A26", "alpha": 0.55,
})
para = _style({
    "light": "parallel", "angle_deg": 45, "distance_em": 0.45,
    "size_em": 0.12, "falloff": 0.3, "color": "#141A26FF", "strength": 0.7,
})
point = _style({
    "light": "point", "angle_deg": 45, "distance_em": 0.8,
    "size_em": 0.12, "falloff": 0.3, "color": "#141A26FF", "strength": 0.7,
})
preview = _style({
    "light": "parallel", "angle_deg": 45, "distance_em": 0.45,
    "size_em": 0.12, "falloff": 0.3, "color": "#141A26FF", "strength": 0.7,
})

rows = []
for name, st, skip in (
    ("off export", off, False),
    ("legacy export", legacy, False),
    ("parallel export", para, False),
    ("point export", point, False),
    ("off preview skip_blur", off, True),
    ("parallel preview skip_blur", preview, True),
):
    ms, rw, rh = _bench(st, skip_blur=skip)
    rows.append((name, ms, rw, rh))
    print("%-28s  %7.3f ms/frame  roi=%dx%d" % (name, ms, rw, rh))

off_ms = rows[0][1]
para_ms = rows[2][1]
delta = (para_ms - off_ms) / max(off_ms, 1e-6) * 100.0
print("parallel vs off: %+.1f%%" % delta)

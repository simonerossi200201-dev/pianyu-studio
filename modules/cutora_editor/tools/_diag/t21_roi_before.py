# -*- coding: utf-8 -*-
"""Q21: snapshot ROI clipping + legacy shadow hash BEFORE product changes."""
from __future__ import annotations

import hashlib
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402

QApplication.instance() or QApplication(["t21-roi-before"])

from cutora.ui.panels.fonts import load_embedded_fonts  # noqa: E402
from cutora.render.textfx.layout import layout_event  # noqa: E402
from cutora.render.textfx.raster import as_bgra, raster_roi  # noqa: E402
from cutora.render.textfx.spec import from_style  # noqa: E402

load_embedded_fonts()

CANVAS_W, CANVAS_H = 720, 1280
OUT_DIR = os.path.join(REPO, "docs", "plan", "reports")
os.makedirs(OUT_DIR, exist_ok=True)


def _style(shadow):
    return {
        "font_family": "Arimo",
        "fontsize": 96,
        "bold": True,
        "pos_x": 360,
        "pos_y": 640,
        "fx": {
            "fill": {"type": "solid", "colors": ["#F4EBD8"], "angle": 0.0},
            "stroke": {"width_em": 0, "color": "#E11D48", "join": "round"},
            "glow": {"sigma_em": 0, "strength": 0},
            "shadow": shadow,
            "bg": {"type": "none"},
            "anim": {"in": "none", "loop": "none", "out": "none"},
            "emphasis": {"words": [], "color": "#22D3EE", "scale": 1.0},
        },
    }


def _event(text="HELLO"):
    words = text.split()
    step = 0.4
    return {
        "text": text,
        "start": 0.0,
        "end": 2.0,
        "id": "s0",
        "words": [
            {"word": w, "start": i * step, "end": i * step + step}
            for i, w in enumerate(words)
        ],
    }


def _paint(style, skip_blur=False):
    event = _event()
    spec = from_style(style)
    lay = layout_event(event, style, spec, CANVAS_W, CANVAS_H)
    img = raster_roi(
        lay, spec, style, None, CANVAS_W, CANVAS_H,
        skip_blur=skip_blur, t=0.55, event=event,
    )
    owned, view = as_bgra(img)
    return owned, view, lay


def _md5(view):
    return hashlib.md5(view.tobytes()).hexdigest()


def _edge_alpha(view):
    a = view[..., 3]
    top = int(a[0, :].max())
    bot = int(a[-1, :].max())
    left = int(a[:, 0].max())
    right = int(a[:, -1].max())
    return top, bot, left, right, int(a.max())


lines = []

# 1) Large offset + small sigma: does the shadow hit the ROI edge?
big = _style({
    "sigma_em": 0.08,
    "dx_em": 0.30,
    "dy_em": 0.30,
    "color": "#141A26",
    "alpha": 0.9,
})
img, view, lay = _paint(big, skip_blur=False)
edge = _edge_alpha(view)
path = os.path.join(OUT_DIR, "T21_roi_before.png")
img.save(path)
lines.append("ROI before (sigma=0.08 dx=0.30 dy=0.30)")
lines.append("  origin=%s roi=%sx%s text=(%.1f,%.1f,%.1f,%.1f) em=%.1f" % (
    lay.origin, lay.roi_w, lay.roi_h,
    lay.text_x, lay.text_y, lay.text_w, lay.text_h, lay.em,
))
lines.append("  edge max alpha top/bot/left/right=%s  amax=%s" % (edge[:4], edge[4]))
lines.append("  clipped=%s" % (max(edge[:4]) > 0))
lines.append("  png=%s md5=%s" % (path, _md5(view)))

# 2) skip_blur currently drops the whole shadow
img_s, view_s, _ = _paint(big, skip_blur=True)
off = _style({"sigma_em": 0, "dx_em": 0.30, "dy_em": 0.30, "alpha": 0.9})
img_off, view_off, _ = _paint(off, skip_blur=False)
lines.append("skip_blur=True md5=%s  no-shadow md5=%s  same=%s" % (
    _md5(view_s), _md5(view_off), view_s.tobytes() == view_off.tobytes(),
))

# 3) Freeze a moderate legacy hash (the compat lock).
legacy = _style({
    "sigma_em": 0.12,
    "dx_em": 0.08,
    "dy_em": 0.10,
    "color": "#141A26",
    "alpha": 0.55,
})
img_l, view_l, lay_l = _paint(legacy, skip_blur=False)
legacy_path = os.path.join(OUT_DIR, "T21_legacy_shadow.png")
img_l.save(legacy_path)
lines.append("legacy sigma=0.12 dx=0.08 dy=0.10")
lines.append("  roi=%sx%s md5=%s png=%s" % (
    lay_l.roi_w, lay_l.roi_h, _md5(view_l), legacy_path,
))

report = os.path.join(OUT_DIR, "T21_roi_before.txt")
text = "\n".join(lines) + "\n"
open(report, "w", encoding="utf-8").write(text)
print(text, end="")
print("wrote", report)

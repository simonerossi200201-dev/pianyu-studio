# -*- coding: utf-8 -*-
"""Capture draw_line_bar md5 before Q22 product changes."""
from __future__ import annotations

import hashlib
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from PySide6.QtWidgets import QApplication
from cutora.ui.panels.fonts import load_embedded_fonts
from cutora.render.textfx.layout import layout_event
from cutora.render.textfx.raster import as_bgra
from cutora.render.textfx.spec import from_style
from cutora.render.textfx.word_fx import draw_line_bar

CANVAS_W, CANVAS_H = 1080, 1920


def main():
    QApplication.instance() or QApplication(["t22-legacy"])
    load_embedded_fonts()
    text = "ONE TWO THREE FOUR"
    words = text.split()
    step = 0.4
    event = {
        "text": text,
        "start": 0.0,
        "end": 1.6,
        "lines": [text],
        "id": "s0",
        "words": [
            {"word": w, "start": i * step, "end": i * step + step}
            for i, w in enumerate(words)
        ],
    }
    style = {
        "font_family": "Arimo",
        "fontsize": 64,
        "bold": True,
        "pos_x": 540,
        "pos_y": 960,
        "wrap_lines": 1,
        "fx": {
            "follow_sentence": True,
            "fill": {"type": "solid", "colors": ["#F4EBD8"]},
            "stroke": {"width_em": 0},
            "glow": {"sigma_em": 0, "strength": 0},
            "bg": {"type": "none"},
            "anim": {"in": "none", "loop": "none", "out": "none"},
            "line_bar": {
                "color": "#F2C14E",
                "alpha": 0.7,
                "height_em": 0.22,
                "pad_em": 0.18,
                "offset_y_em": 0.06,
            },
        },
    }
    spec = from_style(style)
    lay = layout_event(event, style, spec, CANVAS_W, CANVAS_H)
    img = draw_line_bar(None, lay, spec, 0.8, event, style=style)
    owned, view = as_bgra(img)
    digest = hashlib.md5(view.tobytes()).hexdigest()
    out = os.path.join(REPO, "docs", "plan", "reports", "T22_legacy_bar.png")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    img.save(out)
    print("md5", digest)
    print("roi", lay.roi_w, lay.roi_h, "origin", lay.origin)
    print("line", [(ln.x, ln.y, ln.w, ln.h) for ln in lay.lines])
    print("png", out)


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""Measure draw_line_bar ms/frame (small-ROI paint vs full ROI)."""
from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from PySide6.QtWidgets import QApplication
from cutora.ui.panels.fonts import load_embedded_fonts
from cutora.render.textfx.layout import layout_event
from cutora.render.textfx.spec import from_style
from cutora.render.textfx.word_fx import draw_line_bar


def _style(anim, glow=0.0):
    return {
        "font_family": "Arimo",
        "fontsize": 64,
        "bold": True,
        "pos_x": 540,
        "pos_y": 960,
        "wrap_lines": 1,
        "fx": {
            "follow_sentence": True,
            "fill": {"type": "solid", "colors": ["#F4EBD8"]},
            "bg": {"type": "none"},
            "anim": {"in": "none", "loop": "none", "out": "none"},
            "line_bar": {
                "color": "#F2C14E",
                "alpha": 0.7,
                "height_em": 0.22,
                "anim": anim,
                "glow_em": glow,
                "style": "solid",
            },
        },
    }


def bench(label, style, n=80):
    event = {
        "text": "ONE TWO THREE FOUR",
        "start": 0.0,
        "end": 1.6,
        "id": "s0",
        "lines": ["ONE TWO THREE FOUR"],
        "words": [
            {"word": w, "start": i * 0.4, "end": i * 0.4 + 0.4}
            for i, w in enumerate("ONE TWO THREE FOUR".split())
        ],
    }
    spec = from_style(style)
    lay = layout_event(event, style, spec, 1080, 1920)
    ts = [0.1 + i * (1.4 / n) for i in range(n)]
    for t in ts[:5]:
        draw_line_bar(None, lay, spec, t, event, style=style)
    t0 = time.perf_counter()
    for t in ts:
        draw_line_bar(None, lay, spec, t, event, style=style)
    dt = (time.perf_counter() - t0) * 1000.0 / n
    print("%s: %.3f ms/frame  roi=%dx%d" % (label, dt, lay.roi_w, lay.roi_h))
    return dt


def main():
    QApplication.instance() or QApplication(["t22-perf"])
    load_embedded_fonts()
    a = bench("anim=none solid glow=0", _style("none", 0.0))
    b = bench("anim=draw solid glow=0", _style("draw", 0.0))
    c = bench("anim=draw solid glow=0.2", _style("draw", 0.2))
    out = os.path.join(REPO, "docs", "plan", "reports", "T22_line_bar_perf.txt")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("draw_line_bar ms/frame (n=80, 1080x1920 layout)\n")
        fh.write("anim=none solid glow=0: %.3f\n" % a)
        fh.write("anim=draw solid glow=0: %.3f\n" % b)
        fh.write("anim=draw solid glow=0.2: %.3f\n" % c)
    print("wrote", out)


if __name__ == "__main__":
    main()

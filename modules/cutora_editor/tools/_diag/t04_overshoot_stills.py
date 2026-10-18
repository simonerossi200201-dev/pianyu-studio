# -*- coding: utf-8 -*-
"""Overshoot clamp stills: long-line before/after + short-line identity."""
from __future__ import annotations

import hashlib
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
os.chdir(REPO)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

IMG = os.path.join(REPO, "docs", "plan", "reports", "img")
IN_S = 0.45


def _md5(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        h.update(fh.read())
    return h.hexdigest()


def _bbox(img, layout):
    from cutora.render.textfx.raster import as_bgra
    import numpy as np
    _o, bgra = as_bgra(img)
    a = bgra[..., 3]
    ys, xs = np.nonzero(a > 8)
    if xs.size == 0:
        return None
    ox, oy = layout.origin
    return (
        int(xs.min()) + ox, int(ys.min()) + oy,
        int(xs.max()) + ox, int(ys.max()) + oy,
    )


def _style(anim, fontsize, cw, ch):
    return {
        "fontsize": fontsize,
        "fontsize_pct": fontsize / float(min(cw, ch)),
        "wrap_lines": 1,
        "font_family": "Arimo",
        "bold": True,
        "pos_x": cw / 2.0,
        "pos_y": ch * 0.5,
        "fx": {"anim": {"in": anim, "loop": "none", "out": "none",
                        "in_s": IN_S, "unit": "segment", "easing": "linear"},
               "bg": {"type": "none"}, "glow": {"sigma_em": 0},
               "shadow": {"sigma_em": 0}},
    }


def _event(text):
    words = text.split()
    step = 0.4 / max(1, len(words))
    return {
        "text": text, "start": 0.0, "end": 2.0, "id": "ov1",
        "words": [{"word": w, "start": i * step, "end": (i + 1) * step}
                  for i, w in enumerate(words)],
    }


def _peak_t(anim, fontsize, cw, ch, text):
    """t at max |scale| during in-phase (stomp peaks at u≈0, impact_pop later)."""
    from cutora.render.textfx.layout import layout_event
    from cutora.render.textfx.spec import from_style
    from cutora.render.textfx.anim import transform_at
    st = _style(anim, fontsize, cw, ch)
    ev = _event(text)
    spec = from_style(st)
    lay = layout_event(ev, st, spec, cw, ch)
    best_t, best_s = 0.0, -1.0
    for i in range(41):
        t = (i / 40.0) * IN_S
        xf = transform_at(t, ev, spec, lay)
        if xf.alpha < 0.5:
            continue
        s = max(abs(xf.scale_x), abs(xf.scale_y))
        if s > best_s:
            best_s, best_t = s, t
    return best_t, best_s, spec, lay, ev, st


def _to_canvas(img, layout, cw, ch, mx, my):
    from PySide6.QtGui import QImage, QPainter, QColor, QPen
    from PySide6.QtCore import QRectF, Qt
    canvas = QImage(cw, ch, QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(QColor(0, 0, 0))
    p = QPainter(canvas)
    try:
        if img is not None and not img.isNull():
            ox, oy = layout.origin
            p.drawImage(int(ox), int(oy), img)
        p.setPen(QPen(QColor(220, 60, 60, 180), 1, Qt.PenStyle.DashLine))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(QRectF(mx, my, cw - 2 * mx, ch - 2 * my))
    finally:
        p.end()
    return canvas


def _render(spec, lay, ev, st, cw, ch, t, clamp):
    from cutora.render.textfx.raster import raster_roi
    from cutora.render.textfx import raster as raster_mod
    orig = raster_mod._clamp_xf_to_safe
    if not clamp:
        raster_mod._clamp_xf_to_safe = lambda xf, *a, **k: xf
    try:
        img = raster_roi(lay, spec, st, None, cw, ch, skip_blur=True, t=t, event=ev)
    finally:
        raster_mod._clamp_xf_to_safe = orig
    return img, lay, _bbox(img, lay)


def _report(name, bb, ba, cw, mx, my):
    print("%s before_bbox=%s after_bbox=%s" % (name, bb, ba), flush=True)
    for tag, box in (("before", bb), ("after", ba)):
        if not box:
            print("  %s empty" % tag, flush=True)
            continue
        print(
            "  %s vs SAFE L=%.1f R=%.1f T=%.1f B=%.1f overflow_L=%s overflow_R=%s"
            % (tag, box[0] - mx, (cw - mx) - box[2], box[1] - my,
               (1920 - my) - box[3], box[0] < mx, box[2] > cw - mx),
            flush=True)


def main() -> int:
    from PySide6.QtWidgets import QApplication
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.subtitle.stylepack import SAFE_MARGIN
    app = QApplication.instance() or QApplication([])
    _ = app
    load_embedded_fonts()
    os.makedirs(IMG, exist_ok=True)
    cw, ch = 1080, 1920
    mx, my = cw * SAFE_MARGIN, ch * SAFE_MARGIN
    print("SAFE_MARGIN=%.2f canvas=%dx%d inset=(%.1f,%.1f,%.1f,%.1f)" % (
        SAFE_MARGIN, cw, ch, mx, my, cw - mx, ch - my), flush=True)
    long_line = (
        "THIS WIDE TITLE LINE FILLS THE SAFE BOX SO PEAK SCALE MUST OVERSHOOT"
    )
    jobs = [
        ("stomp", long_line, 72),
        ("impact_pop", long_line, 72),
        ("stomp_short", "SDK READY.", 56),
    ]
    for name, text, fs in jobs:
        anim = "stomp" if "stomp" in name else "impact_pop"
        t, peak_s, spec, lay, ev, st = _peak_t(anim, fs, cw, ch, text)
        print("%s peak_t=%.4f peak_scale=%.4f" % (name, t, peak_s), flush=True)
        before, lay_b, bb = _render(spec, lay, ev, st, cw, ch, t, clamp=False)
        after, lay_a, ba = _render(spec, lay, ev, st, cw, ch, t, clamp=True)
        print("%s roi_origin=%s" % (name, lay.origin), flush=True)
        pb = os.path.join(IMG, "T04_overshoot_%s_before.png" % name)
        pa = os.path.join(IMG, "T04_overshoot_%s_after.png" % name)
        _to_canvas(before, lay_b, cw, ch, mx, my).save(pb)
        _to_canvas(after, lay_a, cw, ch, mx, my).save(pa)
        same = _md5(pb) == _md5(pa)
        print("%s md5_same=%s" % (name, same), flush=True)
        _report(name, bb, ba, cw, mx, my)
        if name == "stomp_short" and not same:
            print("FAIL short-line identity", flush=True)
            return 1
        if name != "stomp_short" and ba:
            if ba[0] < mx or ba[2] > cw - mx:
                print("FAIL %s after still overflows SAFE" % name, flush=True)
                return 1
            if (ba[0] - mx) < 1.0 - 1e-9 or ((cw - mx) - ba[2]) < 1.0 - 1e-9:
                print("FAIL %s after inner room < 1px" % name, flush=True)
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

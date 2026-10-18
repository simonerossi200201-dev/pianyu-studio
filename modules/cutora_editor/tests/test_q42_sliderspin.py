# -*- coding: utf-8 -*-
"""Q42: SliderSpin slider/spin must be wired; valueChanged must fire."""
from __future__ import annotations

import ast
import copy
import hashlib
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANVAS_W, CANVAS_H = 720, 1280
T_S = 0.55
# Large enough that a 0.08→0.24 em stroke is not a 1-pixel fluke.
CHANGED_PX_MIN = 400
STROKE_AFTER_EM = 0.24


def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["q42-sliderspin"])


def _panel():
    import cutora.ui.panels.property  # noqa: F401
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.panels.subtitle_style import SubtitleStylePanel
    load_embedded_fonts()
    return SubtitleStylePanel()


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


def _base_fx(width_em=0.0):
    return {
        "font_family": "Arimo",
        "fontsize": 96,
        "bold": True,
        "pos_x": 360,
        "pos_y": 640,
        "fx": {
            "fill": {"type": "solid", "colors": ["#F4EBD8"], "angle": 0.0},
            "stroke": {"width_em": width_em, "color": "#E11D48", "join": "round"},
            "glow": {"sigma_em": 0, "strength": 0},
            "shadow": {"sigma_em": 0, "alpha": 0},
            "bg": {"type": "none"},
            "anim": {"in": "none", "loop": "none", "out": "none"},
            "emphasis": {"words": [], "color": "#22D3EE", "scale": 1.0},
        },
    }


def _task(style):
    from cutora.model.timeline import Timeline
    ev = _event()
    return {
        "style": copy.deepcopy(style),
        "segments": [ev],
        "timeline": Timeline(CANVAS_W, CANVAS_H, 30),
        "text_overlays": [],
        "image_overlays": [],
    }


def _argb32(img):
    from PySide6.QtGui import QImage
    import numpy as np
    conv = img.convertToFormat(QImage.Format.Format_ARGB32)
    w, h = conv.width(), conv.height()
    bpl = conv.bytesPerLine()
    raw = bytes(conv.constBits())
    arr = np.frombuffer(raw, dtype=np.uint8).reshape((h, bpl))[:, : w * 4].copy()
    return arr.reshape((h, w, 4))


def _md5_arr(arr):
    return hashlib.md5(arr.tobytes()).hexdigest()


def _changed_px(a, b, thresh=8):
    import numpy as np
    d = np.abs(a.astype(np.int16) - b.astype(np.int16)).max(axis=2)
    return int((d > thresh).sum())


def _export_frame(style):
    from PySide6.QtGui import QColor
    from cutora.render.export_renderer import ExportRenderer

    def _bg(painter):
        painter.fillRect(0, 0, CANVAS_W, CANVAS_H, QColor("#1A1A2A"))

    img = ExportRenderer.render_frame(
        _task(style), CANVAS_W, CANVAS_H, T_S, draw_background=_bg,
    )
    return _argb32(img)


def _preview_frame(style, text="HELLO"):
    import numpy as np
    from cutora.render.textfx.layout import layout_event
    from cutora.render.textfx.raster import as_bgra, raster_roi
    from cutora.render.textfx.spec import from_style
    from cutora.ui.panels.fonts import load_embedded_fonts

    load_embedded_fonts()
    event = _event(text)
    spec = from_style(style)
    lay = layout_event(event, style, spec, CANVAS_W, CANVAS_H)
    img = raster_roi(
        lay, spec, style, None, CANVAS_W, CANVAS_H,
        skip_blur=True, t=T_S, event=event,
    )
    _owned, view = as_bgra(img)
    canvas = np.zeros((CANVAS_H, CANVAS_W, 4), dtype=np.uint8)
    ox, oy = int(lay.origin[0]), int(lay.origin[1])
    h, w = view.shape[:2]
    y1 = min(CANVAS_H, oy + h)
    x1 = min(CANVAS_W, ox + w)
    if y1 > oy and x1 > ox:
        canvas[oy:y1, ox:x1] = view[: y1 - oy, : x1 - ox]
    return canvas


def _iter_py(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in filenames:
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


def _unreachable_after_return(tree):
    """Same-suite statements that follow a `return` (not later branches)."""
    hits = []

    def scan_suite(stmts, qual):
        for i, stmt in enumerate(stmts):
            walk_node(stmt, qual)
            if isinstance(stmt, ast.Return) and i + 1 < len(stmts):
                nxt = stmts[i + 1]
                hits.append((qual, nxt.lineno, type(nxt).__name__))

    def walk_node(node, qual):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            q = "%s.%s" % (qual, node.name) if qual else node.name
            scan_suite(node.body, q)
            return
        if isinstance(node, ast.ClassDef):
            q = "%s.%s" % (qual, node.name) if qual else node.name
            scan_suite(node.body, q)
            return
        for attr in ("body", "orelse", "finalbody"):
            child = getattr(node, attr, None)
            if isinstance(child, list):
                scan_suite(child, qual)
        for handler in getattr(node, "handlers", None) or []:
            scan_suite(handler.body, qual)
        for case in getattr(node, "cases", None) or []:
            scan_suite(case.body, qual)

    scan_suite(tree.body, "")
    return hits


def test_sliderspin_two_way():
    from cutora.ui.widgets.inspector import SliderSpin

    _qapp()
    sp = SliderSpin(0, 10, 0.1, 2)
    hits = []
    sp.valueChanged.connect(lambda v: hits.append(float(v)))

    raw = int(round(3.5 * sp._scale))
    sp.slider.setValue(raw)
    assert abs(float(sp.spin.value()) - 3.5) < 1e-9, sp.spin.value()
    assert abs(float(sp.value()) - 3.5) < 1e-9, sp.value()
    assert hits, "slider setValue did not emit SliderSpin.valueChanged"
    assert abs(hits[-1] - 3.5) < 1e-9, hits

    hits.clear()
    sp.spin.setValue(7.2)
    assert sp.slider.value() == int(round(7.2 * sp._scale)), sp.slider.value()
    assert abs(float(sp.value()) - 7.2) < 1e-9, sp.value()
    assert hits, "spin setValue did not emit SliderSpin.valueChanged"
    assert abs(hits[-1] - 7.2) < 1e-9, hits

    hits.clear()
    sp.setValue(4.0)
    assert abs(float(sp.spin.value()) - 4.0) < 1e-9, sp.spin.value()
    assert sp.slider.value() == int(round(4.0 * sp._scale)), sp.slider.value()
    assert hits, "SliderSpin.setValue did not emit valueChanged"


def test_sliderspin_no_feedback_loop():
    from cutora.ui.widgets.inspector import SliderSpin

    _qapp()
    sp = SliderSpin(0, 100, 1, 0)
    hits = []
    sp.valueChanged.connect(lambda v: hits.append(float(v)))
    for i in range(20):
        sp.slider.setValue(i + 1)
    assert len(hits) == 20, (
        "expected 20 valueChanged fires, got %d (0=unwired, 40=feedback loop)"
        % len(hits)
    )
    assert [int(v) for v in hits] == list(range(1, 21)), hits

    hits.clear()
    for i in range(20):
        sp.spin.setValue(30 + i)
    assert len(hits) == 20, (
        "spin→slider expected 20 fires, got %d" % len(hits)
    )


def test_sliderspin_drives_fx():
    from cutora.ui.widgets.inspector import SliderSpin

    _qapp()
    panel = _panel()
    style = _base_fx(width_em=0.0)
    panel.load_data(copy.deepcopy(style))
    panel.block_signals = False
    panel.current_data = copy.deepcopy(style)
    panel.sub_strk.setChecked(True)

    sp = panel.fx_stroke_width_em
    assert isinstance(sp, SliderSpin), type(sp).__name__

    before = panel.collect()
    w0 = float((before.get("fx") or {}).get("stroke", {}).get("width_em") or 0)
    # Section toggle-on writes a default (~0.08). Drive the slider off that.
    assert w0 < 0.15, w0
    target_raw = int(round(STROKE_AFTER_EM * sp._scale))
    assert target_raw != int(round(float(sp.spin.value()) * sp._scale)), (
        sp.spin.value(), target_raw
    )
    sp.slider.setValue(target_raw)
    assert abs(float(sp.spin.value()) - STROKE_AFTER_EM) < 0.02, sp.spin.value()

    after = panel.collect()
    w1 = float((after.get("fx") or {}).get("stroke", {}).get("width_em") or 0)
    assert abs(w1 - STROKE_AFTER_EM) < 0.02, after.get("fx", {}).get("stroke")
    assert abs(w1 - w0) > 0.08, (w0, w1)

    export0 = _export_frame(before)
    export1 = _export_frame(after)
    n_export = _changed_px(export0, export1)
    assert _md5_arr(export0) != _md5_arr(export1), (
        "ExportRenderer.render_frame pixels identical after slider stroke change"
    )
    assert n_export > CHANGED_PX_MIN, n_export

    preview0 = _preview_frame(before)
    preview1 = _preview_frame(after)
    n_preview = _changed_px(preview0, preview1)
    assert _md5_arr(preview0) != _md5_arr(preview1), (
        "preview raster_roi pixels identical after slider stroke change"
    )
    assert n_preview > CHANGED_PX_MIN, n_preview
    panel.close()


def test_no_unreachable_code():
    root = os.path.join(REPO, "cutora")
    files = list(_iter_py(root))
    assert files, root
    hits = []
    for path in files:
        rel = os.path.relpath(path, REPO).replace("\\", "/")
        src = open(path, encoding="utf-8").read()
        tree = ast.parse(src, filename=path)
        for qual, lineno, kind in _unreachable_after_return(tree):
            hits.append("%s:%d %s after return (%s)" % (rel, lineno, qual, kind))
    assert hits == [], "unreachable code after return:\n" + "\n".join(hits)

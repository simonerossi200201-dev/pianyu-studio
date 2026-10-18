# -*- coding: utf-8 -*-
"""Q36: vertical writing_mode in layout.py — one path for overlay and subtitle."""
from __future__ import annotations

import hashlib
import math
import os
import pathlib

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CUTORA = os.path.join(REPO, "cutora")
CANVAS_W, CANVAS_H = 720, 1280
CJK = "Noto Sans SC"

# Frozen 2026-09-21 from HEAD before Q36 layout changes (build/_q36_metrics_probe.py).
# Overlay HELLO, no writing_mode, FrameRenderer.draw_layers / ExportRenderer.
LEGACY_OVERLAY_MD5 = "6dc61ce7e8f69c325581efaea17014a0"
# Subtitle HELLO, no writing_mode, FrameRenderer.draw_subtitle.
LEGACY_SUBTITLE_MD5 = "0a432ad84b285d89aa24ddf7189c914f"


def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["q36-vertical"])


def _fonts():
    from cutora.ui.panels.fonts import load_embedded_fonts
    load_embedded_fonts()


def _event(text):
    return {
        "text": text,
        "start": 0.0,
        "end": 2.0,
        "id": "q36s0",
        "words": [{"word": text, "start": 0.0, "end": 2.0, "index": 0}],
    }


def _style(writing="horizontal", orientation="mixed", **extra):
    s = {
        "font_family": CJK,
        "fontsize": 64,
        "bold": True,
        "pos_x": 360,
        "pos_y": 640,
        "wrap_width_percent": 86,
        "wrap_lines": 12,
        "writing_mode": writing,
        "text_orientation": orientation,
        "fx": {
            "fill": {"type": "solid", "colors": ["#F4EBD8"]},
            "stroke": {"width_em": 0, "color": "#E11D48", "join": "round"},
            "bg": {"type": "none"},
            "shadow": {"distance_em": 0, "enabled": False},
        },
    }
    s.update(extra)
    return s


def _layout(style, text="你好世界"):
    from cutora.render.textfx.layout import layout_event
    from cutora.render.textfx.spec import from_style
    spec = from_style(style)
    lay = layout_event(_event(text), style, spec, CANVAS_W, CANVAS_H)
    return lay, spec


def _path_bbox(lay, style, spec):
    from cutora.render.textfx.raster import collect_run_paths
    x0 = y0 = 1e9
    x1 = y1 = -1e9
    n = 0
    for path, _run in collect_run_paths(lay, style, spec):
        br = path.boundingRect()
        if br.width() <= 0 and br.height() <= 0:
            continue
        n += 1
        x0 = min(x0, br.x())
        y0 = min(y0, br.y())
        x1 = max(x1, br.x() + br.width())
        y1 = max(y1, br.y() + br.height())
    assert n > 0
    return float(x1 - x0), float(y1 - y0)


def _md5(img):
    from PySide6.QtGui import QImage
    conv = img.convertToFormat(QImage.Format.Format_ARGB32)
    return hashlib.md5(
        bytes(conv.constBits())[: conv.height() * conv.bytesPerLine()]
    ).hexdigest()


def _bgra(img):
    from PySide6.QtGui import QImage
    conv = img.convertToFormat(QImage.Format.Format_ARGB32)
    hh, ww = conv.height(), conv.width()
    bpl = conv.bytesPerLine()
    raw = np.frombuffer(bytes(conv.constBits()), dtype=np.uint8)
    return raw.reshape(hh, bpl)[:, : ww * 4].reshape(hh, ww, 4).copy()


def _legacy_overlay(**extra):
    ov = {
        "type": "text",
        "content": "HELLO",
        "start": 0.0,
        "end": 4.0,
        "x": 80, "y": 200, "w": 560, "h": 160,
        "font_family": "Arimo",
        "fontsize": 72,
        "bold": True,
        "italic": False,
        "color": "#F5B700",
        "fx": {
            "fill": {"type": "solid", "colors": ["#F5B700"]},
            "stroke": {"width_em": 0.08, "color": "#141A26", "join": "round"},
            "bg": {"type": "none"},
            "shadow": {"distance_em": 0, "enabled": False},
        },
    }
    ov.update(extra)
    return ov


def _legacy_sub_style():
    return {
        "font_family": "Arimo",
        "fontsize": 72,
        "bold": True,
        "pos_x": 360,
        "pos_y": 640,
        "fx": {
            "fill": {"type": "solid", "colors": ["#F5B700"]},
            "stroke": {"width_em": 0.08, "color": "#141A26", "join": "round"},
            "bg": {"type": "none"},
            "shadow": {"distance_em": 0, "enabled": False},
        },
    }


def _paint_overlay(task, t=1.0, fill="#142028", backdrop=None):
    from PySide6.QtGui import QColor, QImage, QPainter
    from cutora.render.frame_renderer import FrameRenderer
    img = QImage(CANVAS_W, CANVAS_H, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(fill))
    p = QPainter(img)
    try:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if backdrop is not None:
            p.drawImage(0, 0, backdrop)
        FrameRenderer.draw_layers(
            p, task, CANVAS_W, CANVAS_H, t, render_order=["text"],
            backdrop=backdrop)
    finally:
        p.end()
    return img


def _export_frame(task, t=1.0, fill="#142028", backdrop=None, render_order=None):
    from PySide6.QtGui import QColor
    from cutora.render.export_renderer import ExportRenderer

    def _bg(painter):
        painter.fillRect(0, 0, CANVAS_W, CANVAS_H, QColor(fill))
        if backdrop is not None:
            painter.drawImage(0, 0, backdrop)

    return ExportRenderer.render_frame(
        task, CANVAS_W, CANVAS_H, t, draw_background=_bg, backdrop=backdrop,
        render_order=render_order)


def _paint_subtitle(style, text="HELLO", t=0.5, fill="#142028"):
    from PySide6.QtGui import QColor, QImage, QPainter
    from cutora.render.frame_renderer import FrameRenderer
    img = QImage(CANVAS_W, CANVAS_H, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(fill))
    p = QPainter(img)
    try:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        FrameRenderer.draw_subtitle(
            p, _event(text), style, t, CANVAS_W, CANVAS_H)
    finally:
        p.end()
    return img


def _col_struct(lay):
    cols = []
    for ln in lay.lines:
        runs = ln.runs or []
        xs = [r.x for r in runs] or [ln.x]
        ys = [r.y for r in runs] or [ln.y]
        cols.append({
            "text": "".join(r.text for r in runs),
            "n": len(runs),
            "x": min(xs),
            "y": min(ys),
            "rots": tuple(round(float(r.orient_deg), 1) for r in runs),
        })
    if not cols:
        return cols
    minx = min(c["x"] for c in cols)
    miny = min(c["y"] for c in cols)
    out = []
    for c in cols:
        out.append({
            "text": c["text"],
            "n": c["n"],
            "x": round(c["x"] - minx, 1),
            "y": round(c["y"] - miny, 1),
            "rots": c["rots"],
        })
    return out


def test_q36_vertical_layout():
    _qapp()
    _fonts()
    text = "你好世界"
    h_lay, h_spec = _layout(_style("horizontal"), text)
    v_lay, v_spec = _layout(_style("vertical_rl"), text)
    hw, hh = _path_bbox(h_lay, _style("horizontal"), h_spec)
    vw, vh = _path_bbox(v_lay, _style("vertical_rl"), v_spec)
    assert vh > vw, (vw, vh)
    assert hw > hh, (hw, hh)
    assert abs(hw - vh) / max(hw, 1.0) <= 0.05, (hw, hh, vw, vh)
    assert abs(hh - vw) / max(hh, 1.0) <= 0.05, (hw, hh, vw, vh)


def test_q36_vertical_columns():
    _qapp()
    _fonts()
    text = "一二三四五六七八九十甲乙丙丁"
    style_rl = _style("vertical_rl", wrap_width_percent=18, fontsize=64)
    style_lr = _style("vertical_lr", wrap_width_percent=18, fontsize=64)
    rl, _ = _layout(style_rl, text)
    lr, _ = _layout(style_lr, text)
    n = len([c for c in text])
    assert len(rl.lines) > 1, [ln.text for ln in rl.lines]
    k = len(rl.lines[0].text)
    assert k < n and k >= 1
    assert len(rl.lines) == math.ceil(n / k), (
        [ln.text for ln in rl.lines], k, n)
    assert rl.lines[1].x < rl.lines[0].x, (
        rl.lines[0].x, rl.lines[1].x)
    assert len(lr.lines) == len(rl.lines)
    assert lr.lines[1].x > lr.lines[0].x, (
        lr.lines[0].x, lr.lines[1].x)


def test_q36_vertical_punctuation():
    _qapp()
    _fonts()
    text = "你好。"
    style = _style("vertical_rl")
    lay, spec = _layout(style, text)
    from cutora.render.textfx.raster import collect_run_paths
    period = None
    cell = None
    for path, run in collect_run_paths(lay, style, spec):
        if run.text == "。":
            period = path.boundingRect()
            cell = (run.x, run.y, run.w, run.h)
            break
    assert period is not None and cell is not None
    cx = period.x() + period.width() * 0.5
    cy = period.y() + period.height() * 0.5
    x, y, w, h = cell
    assert cx > x + w * 0.5, (cx, x, w, cy, y, h)
    assert cy < y + h * 0.5, (cx, x, w, cy, y, h)
    dist = math.hypot(cx - (x + w * 0.5), cy - (y + h * 0.5))
    assert dist > 0.15 * min(w, h), dist


def test_q36_vertical_latin_rotated():
    _qapp()
    _fonts()
    text = "A"
    mixed, mspec = _layout(_style("vertical_rl", "mixed", font_family="Arimo"), text)
    up, uspec = _layout(_style("vertical_rl", "upright", font_family="Arimo"), text)
    from cutora.render.textfx.raster import collect_run_paths
    mb = collect_run_paths(mixed, _style("vertical_rl", "mixed", font_family="Arimo"), mspec)[0][0].boundingRect()
    ub = collect_run_paths(up, _style("vertical_rl", "upright", font_family="Arimo"), uspec)[0][0].boundingRect()
    assert mb.width() > mb.height(), (mb.width(), mb.height())
    assert ub.height() > ub.width(), (ub.width(), ub.height())
    assert mixed.lines[0].runs[0].orient_deg == 90.0
    assert up.lines[0].runs[0].orient_deg == 0.0


def test_q36_vertical_fx_still_works():
    _qapp()
    _fonts()
    from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter
    text = "竖排效果"
    base = _style("vertical_rl", fontsize=72, wrap_width_percent=50)

    def _raster(style, backdrop=None):
        from cutora.render.textfx.layout import layout_event
        from cutora.render.textfx.raster import as_bgra, raster_roi
        from cutora.render.textfx.spec import from_style
        ev = _event(text)
        spec = from_style(style)
        lay = layout_event(ev, style, spec, CANVAS_W, CANVAS_H)
        img = raster_roi(
            lay, spec, style, backdrop, CANVAS_W, CANVAS_H,
            skip_blur=False, t=0.5, event=ev)
        owned, view = as_bgra(img)
        canvas = np.zeros((CANVAS_H, CANVAS_W, 4), dtype=np.uint8)
        ox, oy = int(lay.origin[0]), int(lay.origin[1])
        h, w = view.shape[:2]
        y1 = min(CANVAS_H, oy + h)
        x1 = min(CANVAS_W, ox + w)
        canvas[oy:y1, ox:x1] = view[: y1 - oy, : x1 - ox]
        return canvas, lay

    plain, lay = _raster(base)
    stroked = dict(base)
    stroked["fx"] = dict(base["fx"])
    stroked["fx"]["stroke"] = {
        "width_em": 0.16, "color": "#E11D48", "join": "round", "enabled": True,
    }
    s_arr, _ = _raster(stroked)
    delta = np.abs(s_arr.astype(np.int16) - plain.astype(np.int16)).sum(axis=2)
    assert int((delta > 20).sum()) > 80, int((delta > 20).sum())
    # Stroke is red-ish on the rim: more BGRA red (ch 2) than the fill-only image.
    red_gain = s_arr[..., 2].astype(np.int16) - plain[..., 2].astype(np.int16)
    assert int((red_gain > 30).sum()) > 40, int((red_gain > 30).sum())

    shadowed = dict(base)
    shadowed["fx"] = dict(base["fx"])
    shadowed["fx"]["shadow"] = {
        "enabled": True, "light": "parallel", "angle_deg": 135.0,
        "distance_em": 0.40, "size_em": 0.18, "strength": 0.8,
        "color": "#141A26",
    }
    sh_arr, sh_lay = _raster(shadowed)
    a_on = sh_arr[..., 3].astype(np.int16)
    a_off = plain[..., 3].astype(np.int16)
    extra = np.clip(a_on - a_off, 0, 255)
    assert int((extra > 20).sum()) > 60, int((extra > 20).sum())
    ys, xs = np.where(extra > 20)
    cy_text = sh_lay.text_y + sh_lay.text_h * 0.5
    cx_text = sh_lay.text_x + sh_lay.text_w * 0.5
    assert float(ys.mean()) > cy_text - 8, (float(ys.mean()), cy_text)
    assert float(xs.mean()) < cx_text + 8, (float(xs.mean()), cx_text)

    grad = QImage(CANVAS_W, CANVAS_H, QImage.Format.Format_ARGB32_Premultiplied)
    g = QLinearGradient(0, 0, 0, CANVAS_H)
    g.setColorAt(0.0, QColor("#E02020"))
    g.setColorAt(1.0, QColor("#2020E0"))
    gp = QPainter(grad)
    gp.fillRect(0, 0, CANVAS_W, CANVAS_H, g)
    gp.end()
    glass = dict(base)
    glass["fx"] = dict(base["fx"])
    glass["fx"]["bg"] = {
        "type": "glass", "alpha": 0.55, "radius_em": 0.4,
        "pad_em": [0.8, 0.7], "glass_thickness_em": 0.14,
        "glass_refraction": 0.55,
    }
    gl_arr, gl_lay = _raster(glass, backdrop=grad)
    gx = int(gl_lay.hit_x + gl_lay.hit_w * 0.5)
    y_top = max(0, int(gl_lay.hit_y) + 4)
    y_bot = min(CANVAS_H - 1, int(gl_lay.hit_y + gl_lay.hit_h) - 4)
    top = gl_arr[y_top, gx]
    bot = gl_arr[y_bot, gx]
    assert int(top[2]) > int(top[0]) + 20, (top.tolist(), bot.tolist())
    assert int(bot[0]) > int(bot[2]) + 20, (top.tolist(), bot.tolist())
    assert int(top[2]) > int(bot[2]) + 20, (top.tolist(), bot.tolist())

    # Preview vs export: overlay with the same vertical + stroke + shadow + glass.
    ov = {
        "type": "text",
        "content": text,
        "start": 0.0, "end": 4.0,
        "x": 200, "y": 80, "w": 320, "h": 1100,
        "font_family": CJK, "fontsize": 72, "bold": True,
        "writing_mode": "vertical_rl",
        "fx": glass["fx"],
    }
    task = {"text_overlays": [ov], "segments": [], "style": {}}
    prev = _paint_overlay(task, backdrop=grad, fill="#000000")
    exp = _export_frame(task, backdrop=grad, fill="#000000", render_order=["text"])
    pa, ea = _bgra(prev), _bgra(exp)
    mad = float(np.mean(np.abs(pa.astype(np.int16) - ea.astype(np.int16)))) / 255.0
    assert mad <= 0.012, mad


def test_q36_overlay_and_subtitle_agree(monkeypatch):
    _qapp()
    _fonts()
    from PySide6.QtGui import QColor, QImage, QPainter
    from cutora.render.frame_renderer import FrameRenderer
    from cutora.render.textfx.layout import layout_event as real_layout
    from cutora.subtitle.text_layer import overlay_event, overlay_to_style

    captured = []

    def wrap(*a, **k):
        r = real_layout(*a, **k)
        captured.append(r)
        return r

    monkeypatch.setattr("cutora.render.text_layer_paint.layout_event", wrap)
    monkeypatch.setattr("cutora.render.textfx.pipeline.layout_event", wrap)

    text = "甲乙丙丁戊己庚辛壬癸"
    ov = {
        "type": "text",
        "content": text,
        "start": 0.0, "end": 4.0,
        "x": 180, "y": 60, "w": 360, "h": 220,
        "font_family": CJK, "fontsize": 64, "bold": True,
        "writing_mode": "vertical_rl",
        "fx": {
            "fill": {"type": "solid", "colors": ["#F4EBD8"]},
            "stroke": {"width_em": 0},
            "bg": {"type": "none"},
            "shadow": {"distance_em": 0, "enabled": False},
        },
    }
    img = QImage(CANVAS_W, CANVAS_H, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor("#142028"))
    p = QPainter(img)
    try:
        from cutora.render.text_layer_paint import paint_text_overlay
        paint_text_overlay(p, ov, 1.0, video_w=CANVAS_W, video_h=CANVAS_H)
    finally:
        p.end()
    assert captured, "overlay path did not call layout_event"
    ov_lay = captured[0]
    captured.clear()

    style = overlay_to_style(ov, CANVAS_W, CANVAS_H)
    event = overlay_event(ov)
    img2 = QImage(CANVAS_W, CANVAS_H, QImage.Format.Format_ARGB32_Premultiplied)
    img2.fill(QColor("#142028"))
    p = QPainter(img2)
    try:
        FrameRenderer.draw_subtitle(
            p, event, style, 1.0, CANVAS_W, CANVAS_H)
    finally:
        p.end()
    assert captured, "subtitle path did not call layout_event"
    sub_lay = captured[-1]

    assert _col_struct(ov_lay) == _col_struct(sub_lay)
    assert len(ov_lay.lines) > 1


def test_q36_no_post_hoc_vertical():
    import cutora.render.text_layer_paint as paint
    assert not hasattr(paint, "_stack_vertical")
    src = pathlib.Path(paint.__file__).read_text(encoding="utf-8")
    assert "writing_mode" not in src
    render_root = pathlib.Path(CUTORA) / "render"
    hits = []
    for path in render_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "_stack_vertical" in text:
            hits.append(str(path.relative_to(REPO)))
    assert hits == [], hits


def test_q36_migrate_legacy_vertical():
    _qapp()
    _fonts()
    from cutora.subtitle.style_schema import migrate_subtitle_style
    from cutora.subtitle.text_layer import migrate_text_overlay, overlay_to_style

    ov = {"type": "text", "content": "你好", "writing_mode": "vertical"}
    migrate_text_overlay(ov)
    assert ov["writing_mode"] == "vertical_rl"

    style = overlay_to_style(
        {"content": "你好", "writing_mode": "vertical",
         "x": 0, "y": 0, "w": 200, "h": 400, "fontsize": 40},
        CANVAS_W, CANVAS_H)
    assert style["writing_mode"] == "vertical_rl"

    sub = {"writing_mode": "vertical", "fontsize": 40, "font_family": CJK}
    migrate_subtitle_style(sub)
    assert sub["writing_mode"] == "vertical_rl"

    lay, _ = _layout({
        "font_family": CJK, "fontsize": 64, "bold": True,
        "pos_x": 360, "pos_y": 640, "wrap_width_percent": 18,
        "wrap_lines": 12, "writing_mode": "vertical",
        "fx": {"fill": {"type": "solid", "colors": ["#F4EBD8"]},
               "stroke": {"width_em": 0}, "bg": {"type": "none"},
               "shadow": {"distance_em": 0}},
    }, "一二三四五六七八九十甲乙")
    assert len(lay.lines) > 1
    assert lay.lines[1].x < lay.lines[0].x


def test_q36_horizontal_is_byte_identical():
    _qapp()
    _fonts()
    ov = _legacy_overlay()
    assert "writing_mode" not in ov
    task = {"text_overlays": [ov], "segments": [], "style": {}}
    prev = _paint_overlay(task)
    exp = _export_frame(task, render_order=["text"])
    assert _md5(prev) == LEGACY_OVERLAY_MD5
    assert _md5(exp) == LEGACY_OVERLAY_MD5

    st = _legacy_sub_style()
    assert "writing_mode" not in st
    sub_prev = _paint_subtitle(st, "HELLO")
    assert _md5(sub_prev) == LEGACY_SUBTITLE_MD5
    sub_task = {
        "segments": [_event("HELLO")],
        "style": st,
        "text_overlays": [],
    }
    sub_exp = _export_frame(sub_task, t=0.5, render_order=["subs"])
    from PySide6.QtGui import QColor, QImage, QPainter
    from cutora.render.frame_renderer import FrameRenderer
    img = QImage(CANVAS_W, CANVAS_H, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor("#142028"))
    p = QPainter(img)
    try:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        FrameRenderer.draw_layers(
            p, sub_task, CANVAS_W, CANVAS_H, 0.5, render_order=["subs"])
    finally:
        p.end()
    mad = float(np.mean(np.abs(
        _bgra(img).astype(np.int16) - _bgra(sub_exp).astype(np.int16)
    ))) / 255.0
    assert mad <= 0.012, mad
    assert _md5(img) == LEGACY_SUBTITLE_MD5

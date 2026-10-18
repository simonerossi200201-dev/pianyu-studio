# -*- coding: utf-8 -*-
"""Q86: split feather must not go dark, and the outer half of the band stays opaque."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

A = (200, 60, 60)
B = (60, 60, 200)
# 2x2 pairs sampled away from the cross: left column A/C, top row A/B.
C = (60, 60, 200)
D = (200, 60, 60)


def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["q86-feather"])


def _luma(rgb):
    r, g, b = rgb
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _convex(rgb, c0, c1, tol=3.0):
    """True when rgb is within tol of some t*c0+(1-t)*c1, t in [0,1]."""
    d = [c0[i] - c1[i] for i in range(3)]
    span = max(range(3), key=lambda i: abs(d[i]))
    if abs(d[span]) < 1e-6:
        return all(abs(rgb[i] - c0[i]) <= tol for i in range(3))
    t = (rgb[span] - c1[span]) / d[span]
    if t < -0.05 or t > 1.05:
        return False
    t = min(1.0, max(0.0, t))
    expect = [t * c0[i] + (1.0 - t) * c1[i] for i in range(3)]
    return all(abs(rgb[i] - expect[i]) <= tol for i in range(3))


def _split(layout, feather, curve="straight"):
    from cutora.render.split_layout import apply_layout
    sp = apply_layout({"enabled": True, "layout": layout})
    sp["border"] = {
        "style": "none", "width_px": 6, "color": "#F2C14E",
        "color2": "#FFFFFF", "curve": curve, "shadow_px": 12, "glass_sigma": 8,
    }
    for d in sp.get("dividers") or []:
        d["feather_px"] = float(feather)
    return sp


def _paint_on(target_fill, split, w, h, colors):
    """Product path. target_fill is (r,g,b,a) so tests can use black or transparent."""
    from PySide6.QtGui import QColor, QImage, QPainter
    from cutora.render.split_screen import _LayerTrack, paint_split_video_tracks

    tracks = []
    for cid, rgb in colors:
        img = QImage(8, 8, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(QColor(rgb[0], rgb[1], rgb[2], 255))
        tracks.append(_LayerTrack(cid, img))

    def _paint_one(dest, tr, dw, dh):
        src = tr._image
        dest.fillRect(0, 0, int(dw), int(dh), src.pixelColor(0, 0))

    out = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(QColor(*target_fill))
    p = QPainter(out)
    try:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        paint_split_video_tracks(p, {"split": split}, tracks, w, h, _paint_one)
    finally:
        p.end()
    return out


def _rgb(img, x, y):
    c = img.pixelColor(int(x), int(y))
    return (c.red(), c.green(), c.blue()), c.alpha()


def _assert_band(img, samples, c0, c1):
    """samples: iterable of (x, y). Opaque canvas, so RGB is the on-black color."""
    floor = min(_luma(c0), _luma(c1)) - 2.0
    for x, y in samples:
        rgb, _a = _rgb(img, x, y)
        assert _luma(rgb) >= floor, (x, y, rgb, _luma(rgb), floor)
        assert _convex(rgb, c0, c1, tol=3.0), (x, y, rgb, c0, c1)


def _normal_samples(w, h, axis, pos, feather):
    """Pixels along the divider normal, across ±feather, several stations."""
    f = int(feather)
    out = []
    if axis == "h":
        y0 = int(round(pos * h))
        xs = (w // 2, w // 5, (4 * w) // 5)
        for x in xs:
            for dy in range(-f, f + 1):
                y = y0 + dy
                if 0 <= y < h:
                    out.append((x, y))
    else:
        x0 = int(round(pos * w))
        ys = (h // 2, h // 5, (4 * h) // 5)
        for y in ys:
            for dx in range(-f, f + 1):
                x = x0 + dx
                if 0 <= x < w:
                    out.append((x, y))
    return out


def test_q86_no_dark_seam():
    """Feather 48 must stay a convex mix, never darker than the darker cell.

    Top/bottom, 2x2 (each divider, away from the cross), and a wave.
    """
    _qapp()
    from cutora.render.split_screen import reset_cache

    w, h = 180, 320
    feather = 48
    reset_cache()
    tb = _paint_on((0, 0, 0, 255), _split("top_bottom", feather), w, h,
                   (("A", A), ("B", B)))
    _assert_band(tb, _normal_samples(w, h, "h", 0.5, feather), A, B)

    reset_cache()
    wave = _paint_on((0, 0, 0, 255), _split("top_bottom", feather, "wave"), w, h,
                     (("A", A), ("B", B)))
    # Wave crosses the straight divider at the centre; peaks sit off to the side.
    # Walk the whole width so a peak that still composites over black fails.
    y_mid = h // 2
    wave_pts = []
    for x in range(0, w, 3):
        for dy in range(-feather, feather + 1, 2):
            y = y_mid + dy
            if 0 <= y < h:
                wave_pts.append((x, y))
    _assert_band(wave, wave_pts, A, B)

    reset_cache()
    grid = _paint_on(
        (0, 0, 0, 255), _split("grid_2x2", feather), w, h,
        (("A", A), ("B", (60, 160, 80)), ("C", B), ("D", (220, 180, 60))))
    # Left of the vertical divider: only A (top) and C (bottom) meet.
    left = [(x, y) for x, y in _normal_samples(w, h, "h", 0.5, feather) if x < w // 2 - feather]
    _assert_band(grid, left, A, B)
    # Above the horizontal divider: only A (left) and B (right) meet.
    # B here is (60, 160, 80), not the top_bottom blue.
    top = [(x, y) for x, y in _normal_samples(w, h, "v", 0.5, feather) if y < h // 2 - feather]
    _assert_band(grid, top, A, (60, 160, 80))


# feather=0, RGBA8888 constBits, captured before the overscan change.
_FEATHER0_SHA = {
    "tb": "250d11a29c3810f63e6b96e56827c434a9d7e9b952f42914528511180507f3a9",
    "grid": "4a94079f2e5355be411bb9b6bf31db8257ec0f0bdbae10762523ddc4a21d2090",
    "odd": "643ac04364fd918fc4fcb78d095da91ab8e335e977f297f609fa6326f98d5074",
}


def _rgba_sha(img):
    import hashlib
    from PySide6.QtGui import QImage
    c = img.convertToFormat(QImage.Format.Format_RGBA8888)
    return hashlib.sha256(bytes(c.constBits())).hexdigest()


def test_q86_feather0_matches_hard_edge():
    """feather 0 is the pre-fix hard partition, including the shared odd-height seam."""
    _qapp()
    from cutora.render.split_screen import reset_cache

    reset_cache()
    tb = _paint_on((0, 0, 0, 255), _split("top_bottom", 0), 120, 240,
                   (("A", A), ("B", B)))
    assert _rgba_sha(tb) == _FEATHER0_SHA["tb"]
    reset_cache()
    grid = _paint_on(
        (0, 0, 0, 255), _split("grid_2x2", 0), 120, 240,
        (("A", A), ("B", (60, 160, 80)), ("C", B), ("D", (220, 180, 60))))
    assert _rgba_sha(grid) == _FEATHER0_SHA["grid"]
    reset_cache()
    odd = _paint_on((0, 0, 0, 255), _split("top_bottom", 0), 80, 121,
                    (("A", A), ("B", B)))
    assert _rgba_sha(odd) == _FEATHER0_SHA["odd"]


def test_q86_feather_outside_rect_has_pixels():
    """The half of the feather that leaves the cell rect is still opaque."""
    _qapp()
    from cutora.render.split_screen import reset_cache

    reset_cache()
    w, h = 120, 240
    feather = 48
    # Transparent target: alpha is the split's own coverage, not a black canvas.
    img = _paint_on((0, 0, 0, 0), _split("top_bottom", feather), w, h,
                    (("A", A), ("B", B)))
    x = w // 2
    y_div = h // 2
    # Outer half for cell A is below the rect (A ends at the divider).
    for dy in (feather // 4, feather // 2, (3 * feather) // 4):
        _rgb_lo, a_lo = _rgb(img, x, y_div + dy)
        _rgb_hi, a_hi = _rgb(img, x, y_div - dy)
        assert a_lo == 255, (dy, a_lo, _rgb_lo)
        assert a_hi == 255, (dy, a_hi, _rgb_hi)


def _channel_delta(a, b):
    """Per-pixel max channel delta. RGBA, tightly read so padding is ignored."""
    import numpy as np
    from PySide6.QtGui import QImage

    def _arr(img):
        c = img.convertToFormat(QImage.Format.Format_RGBA8888)
        w, h = c.width(), c.height()
        bpl = c.bytesPerLine()
        raw = np.frombuffer(bytes(c.constBits()), dtype=np.uint8).reshape(h, bpl)
        return raw[:, : w * 4].reshape(h, w, 4).astype(np.int16)

    return np.max(np.abs(_arr(a) - _arr(b)), axis=2)


def _near_guide(x, y, handles, radius):
    for h in handles:
        dx = float(x) - float(h["x"])
        dy = float(y) - float(h["y"])
        if (dx * dx + dy * dy) ** 0.5 <= radius:
            return True
        pts = h.get("pts") or []
        for i in range(len(pts) - 1):
            x0, y0 = pts[i]
            x1, y1 = pts[i + 1]
            vx, vy = x1 - x0, y1 - y0
            seglen2 = vx * vx + vy * vy + 1e-6
            t = ((x - x0) * vx + (y - y0) * vy) / seglen2
            t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
            qx, qy = x0 + t * vx, y0 + t * vy
            if ((x - qx) ** 2 + (y - qy) ** 2) ** 0.5 <= radius:
                return True
    return False


def test_q86_guides_toggle(monkeypatch):
    """Guides off: preview matches the export frame. On: only the guide pixels differ.

    Dragging the divider, or selecting a split track, shows the guide even when
    the switch is off. Releasing the drag follows the switch again.
    """
    _qapp()
    import cutora.render.safe_zones as safe_zones
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtWidgets import QWidget
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track
    from cutora.render.split_layout import apply_split_to_task, set_track_cell
    from cutora.render.split_screen import handle_centers
    from cutora.ui.canvas import VideoCanvas

    monkeypatch.setattr(safe_zones, "active_bands", lambda *_a, **_k: [])

    w, h = 120, 200
    tl = Timeline(w, h, 15)
    ta, tb = Track("video"), Track("video")
    ta.clips.append(Clip("none-a", 0.0, 1.0, 0.0))
    tb.clips.append(Clip("none-b", 0.0, 1.0, 0.0))
    tl.add_track(ta)
    tl.add_track(tb)
    task = {
        "timeline": tl, "video": "", "segments": [],
        "text_overlays": [], "image_overlays": [],
        "duration": 1.0, "trim_end_sec": 1.0,
    }
    apply_split_to_task(task, layout="top_bottom", enabled=True, border={
        "style": "none", "width_px": 6, "color": "#F2C14E",
        "curve": "straight", "shadow_px": 0, "glass_sigma": 4,
    })
    set_track_cell(task, ta.id, "A")
    set_track_cell(task, tb.id, "B")

    host = QWidget()
    host.timeline = type("TL", (), {})()
    host.timeline.selected_track = None
    host.timeline._solo_ids = set()
    canvas = VideoCanvas(host)
    canvas._task_data = task
    canvas._video_dims = (w, h)
    canvas._current_time = 0.2
    canvas._show_split_guides = False
    canvas._split_drag = None

    def _grab(export_mode):
        img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(QColor(0, 0, 0, 255))
        p = QPainter(img)
        try:
            canvas._draw_preview_local(p, export_mode=export_mode)
        finally:
            p.end()
        return img

    export = _grab(True)
    hidden = _grab(False)
    delta = _channel_delta(hidden, export)
    assert int(delta.max()) <= 2, int(delta.max())

    canvas._show_split_guides = True
    shown = _grab(False)
    delta_on = _channel_delta(shown, export)
    ys, xs = (delta_on > 2).nonzero()
    assert len(xs) > 10, len(xs)
    handles = handle_centers(task.get("split"), w, h)
    far = [(int(x), int(y)) for x, y in zip(xs, ys) if not _near_guide(int(x), int(y), handles, 16.0)]
    assert not far, far[:8]

    canvas._show_split_guides = False
    canvas._split_drag = {"id": "d1", "mode": "pos"}
    dragged = _grab(False)
    assert int(_channel_delta(dragged, export).max()) > 2
    canvas._split_drag = None
    released = _grab(False)
    assert int(_channel_delta(released, export).max()) <= 2

    host.timeline.selected_track = ta
    selected = _grab(False)
    assert int(_channel_delta(selected, export).max()) > 2
    host.timeline.selected_track = None
    cleared = _grab(False)
    assert int(_channel_delta(cleared, export).max()) <= 2
    canvas.close()
    host.close()


def test_q86_guides_button_persists(monkeypatch):
    """The preview toolbar switch writes app settings and the canvas flag, not the project."""
    _qapp()
    import cutora.core.history as hist
    import cutora.ui.main_window as mw

    store = {}

    def _load():
        return dict(store)

    def _save(settings):
        store.clear()
        store.update(settings if isinstance(settings, dict) else {})

    monkeypatch.setattr(hist, "_load_app_settings", _load)
    monkeypatch.setattr(hist, "_save_app_settings", _save)
    monkeypatch.setattr(mw, "_load_app_settings", _load)
    monkeypatch.setattr(mw, "_save_app_settings", _save)

    win = mw.MainWindow()
    try:
        btn = win.btn_split_guides
        assert btn.objectName() == "btn_split_guides"
        assert "分屏参考线" in btn.toolTip()
        assert btn.isCheckable()
        assert btn.isChecked() is True
        assert win.video_canvas._show_split_guides is True
        btn.setChecked(False)
        assert win.video_canvas._show_split_guides is False
        assert store.get("preview.split_guides") is False
        btn.setChecked(True)
        assert win.video_canvas._show_split_guides is True
        assert store.get("preview.split_guides") is True
    finally:
        win.close()


def test_q86_split_frame_cost():
    """720x1280 2x2 glass preview composite: <= 24 ms and <= 1/3 of the 440.8 ms baseline."""
    _qapp()
    import platform
    import time
    from cutora.render.split_layout import apply_layout
    from cutora.render.split_screen import reset_cache

    before_ms = 440.837937499964
    w, h = 720, 1280
    sp = apply_layout({"enabled": True, "layout": "grid_2x2"})
    sp["border"] = {
        "style": "glass", "width_px": 14, "color": "#F2C14E",
        "color2": "#FFFFFF", "curve": "straight", "shadow_px": 10, "glass_sigma": 8,
    }
    colors = (
        ("A", (200, 60, 60)), ("B", (60, 160, 80)),
        ("C", (60, 60, 200)), ("D", (220, 180, 60)),
    )
    reset_cache()
    _paint_on((0, 0, 0, 255), sp, w, h, colors)
    _paint_on((0, 0, 0, 255), sp, w, h, colors)
    samples = []
    for _ in range(8):
        t0 = time.perf_counter()
        _paint_on((0, 0, 0, 255), sp, w, h, colors)
        samples.append((time.perf_counter() - t0) * 1000.0)
    samples.sort()
    # Drop the slowest sample so one scheduler hitch does not fail the gate.
    per_ms = sum(samples[:-1]) / (len(samples) - 1)
    where = "%s | %s | py %s | samples %s" % (
        platform.platform(), platform.processor(), platform.python_version(),
        [round(s, 2) for s in samples])
    assert per_ms <= before_ms / 3.0, (round(per_ms, 2), where)
    # This CPU: cached glass 2x2 is ~21.5 ms (Q86 wrote 12 ms after a 440 ms baseline).
    assert per_ms <= 24.0, (round(per_ms, 2), where)


def test_q86_wave_sign_follows_curve():
    """Wave divider: the ramp's zero crossing must sit ON the wave. The old SDF
    took its sign from the straight line, so between crest and centre line the
    feather pointed the wrong way (a hard notch next to every crest)."""
    _qapp()
    import numpy as np
    from cutora.render.split_screen import _divider_sd, divider_polyline

    w, h = 180, 320
    d = {"id": "d0", "axis": "h", "pos": 0.5, "rotation_deg": 0.0}
    pts = divider_polyline(d, w, h, curve="wave", n=721)
    for x in range(4, w - 4, 9):
        ys = np.arange(h, dtype=np.float32) + 0.5
        xs = np.full_like(ys, x + 0.5)
        sd = _divider_sd(xs[:, None], ys[:, None], d, w, h, "wave")[:, 0]
        idx = np.where(np.diff(np.sign(sd)) != 0)[0]
        assert len(idx) == 1, (x, idx)
        y_cross = float(ys[idx[0]])
        y_curve = min(pts, key=lambda p: abs(p[0] - (x + 0.5)))[1]
        assert abs(y_cross - y_curve) <= 1.5, (x, y_cross, y_curve)

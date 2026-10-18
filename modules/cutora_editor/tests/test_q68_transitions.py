# -*- coding: utf-8 -*-
"""Q68: transition continuity, uniqueness, intensity knobs, canvas==export."""
from __future__ import annotations

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Near-endpoint MAD vs A/B. Chromatic Plus-blend / pixelate-from-2 / scanline
# 0.12 overlay currently sit well above this on a textured field.
CONTINUITY_MAD = 0.055
# Pairwise uniqueness: flash_white vs fade_white currently match at all three.
DUP_MAD = 0.025
CANVAS_EXPORT_MAD = 0.04


def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["q68-transitions"])


def _qimage_rgba(img):
    from PySide6.QtGui import QImage
    conv = img.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = conv.width(), conv.height()
    bpl = conv.bytesPerLine()
    raw = bytes(conv.constBits())
    arr = np.frombuffer(raw, dtype=np.uint8)
    if arr.size < h * bpl:
        arr = np.frombuffer(conv.bits(), dtype=np.uint8)
    arr = arr.reshape((h, bpl))[:, : w * 4].reshape((h, w, 4)).copy()
    return arr


def _mad(a, b):
    if a.shape != b.shape:
        h = min(a.shape[0], b.shape[0])
        w = min(a.shape[1], b.shape[1])
        a = a[:h, :w]
        b = b[:h, :w]
    return float(np.mean(np.abs(a.astype(np.int16) - b.astype(np.int16)))) / 255.0


def _textured_pair(w=64, h=48):
    """High-frequency field — solids hide blur/pixelate/bokeh as a dissolve."""
    from PySide6.QtGui import QColor, QImage, QPainter, QPen
    from PySide6.QtCore import Qt

    def _one(seed):
        img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        p = QPainter(img)
        try:
            p.fillRect(0, 0, w, h, QColor(
                40 + (seed * 3) % 80,
                30 + (seed * 5) % 90,
                50 + (seed * 7) % 100,
            ))
            for y in range(int(h)):
                for x in range(int(w)):
                    if (x + y + seed) % 4 == 0:
                        p.setPen(QColor(
                            (x * 17 + seed * 9) & 255,
                            (y * 11 + seed * 5) & 255,
                            (x * 3 + y * 13 + seed) & 255,
                        ))
                        p.drawPoint(x, y)
            p.setPen(QPen(QColor(255, 240, 200), 2))
            p.drawLine(0, seed % h, w - 1, (seed * 3) % h)
            p.setBrush(QColor(220, 80, 60, 220))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(4 + seed % 8, 4, 14, 14)
            p.setBrush(QColor(60, 160, 230, 220))
            p.drawRect(w - 22, h - 18, 16, 12)
        finally:
            p.end()
        return img

    a = _one(11)
    b = _one(200)
    # Must not be near-solid or the continuity test is a free pass.
    aa = _qimage_rgba(a)[..., :3].astype(np.float32)
    ba = _qimage_rgba(b)[..., :3].astype(np.float32)
    assert float(aa.std()) > 18.0, aa.std()
    assert float(ba.std()) > 18.0, ba.std()
    assert _mad(aa, ba) > 0.12
    return a, b


def test_q68_continuity_all():
    """t=0.02 ≈ A and t=0.98 ≈ B for every library preset. Textured sources."""
    _qapp()
    from cutora.render import anim_engine

    names = [p for _lab, p in anim_engine.STANDALONE_TRANSITION_LIBRARY]
    assert names, "empty transition library"
    a, b = _textured_pair(64, 48)
    a_rgb = _qimage_rgba(a)[..., :3]
    b_rgb = _qimage_rgba(b)[..., :3]
    failed = []
    for preset in names:
        c02 = anim_engine.compose_standalone_transition(0.02, preset, a, b, 64, 48)
        c98 = anim_engine.compose_standalone_transition(0.98, preset, a, b, 64, 48)
        if c02 is None or c02.isNull() or c98 is None or c98.isNull():
            failed.append((preset, "null"))
            continue
        d02 = _mad(_qimage_rgba(c02)[..., :3], a_rgb)
        d98 = _mad(_qimage_rgba(c98)[..., :3], b_rgb)
        if d02 > CONTINUITY_MAD:
            failed.append((preset, "t02-vs-A", round(d02, 4)))
        if d98 > CONTINUITY_MAD:
            failed.append((preset, "t98-vs-B", round(d98, 4)))
    # Scanline overlay is only ~0.016 MAD on a busy field; pin it with A→A
    # so the 0.12 row veil is the only difference.
    sl02 = anim_engine.compose_standalone_transition(0.02, "scanlines", a, a, 64, 48)
    sl98 = anim_engine.compose_standalone_transition(0.98, "scanlines", a, a, 64, 48)
    if sl02 is None or sl02.isNull() or sl98 is None or sl98.isNull():
        failed.append(("scanlines", "null-aa"))
    else:
        d_sl02 = _mad(_qimage_rgba(sl02)[..., :3], a_rgb)
        d_sl98 = _mad(_qimage_rgba(sl98)[..., :3], a_rgb)
        if d_sl02 > 0.01:
            failed.append(("scanlines", "t02-overlay", round(d_sl02, 4)))
        if d_sl98 > 0.01:
            failed.append(("scanlines", "t98-overlay", round(d_sl98, 4)))
    assert failed == [], failed


def test_q68_no_duplicates():
    """Any two library presets differ at t=0.3 or 0.5 or 0.7 on a textured field."""
    _qapp()
    from cutora.render import anim_engine

    names = [p for _lab, p in anim_engine.STANDALONE_TRANSITION_LIBRARY]
    a, b = _textured_pair(48, 32)
    frames = {}
    for preset in names:
        bag = []
        for t in (0.3, 0.5, 0.7):
            img = anim_engine.compose_standalone_transition(t, preset, a, b, 48, 32)
            assert img is not None and not img.isNull(), preset
            bag.append(_qimage_rgba(img)[..., :3])
        frames[preset] = bag
    twins = []
    for i, pa in enumerate(names):
        for pb in names[i + 1:]:
            mads = [_mad(frames[pa][k], frames[pb][k]) for k in range(3)]
            if max(mads) <= DUP_MAD:
                twins.append((pa, pb, [round(x, 4) for x in mads]))
    assert twins == [], twins
    # wipe_right/up/down used to paint as wipe_left.
    wr = anim_engine.compose_standalone_transition(0.5, "wipe_right", a, b, 48, 32)
    wu = anim_engine.compose_standalone_transition(0.5, "wipe_up", a, b, 48, 32)
    wd = anim_engine.compose_standalone_transition(0.5, "wipe_down", a, b, 48, 32)
    wl = anim_engine.compose_standalone_transition(0.5, "wipe_left", a, b, 48, 32)
    left = _qimage_rgba(wl)[..., :3]
    for name, img in (("wipe_right", wr), ("wipe_up", wu), ("wipe_down", wd)):
        assert img is not None and not img.isNull(), name
        assert _mad(_qimage_rgba(img)[..., :3], left) > DUP_MAD, name


def _paint_trans(track, trans, t, w, h, img_a, img_b):
    from PySide6.QtGui import QImage, QPainter
    from cutora.render.transition_runtime import paint_standalone_transition
    clips = list(getattr(track, "clips", []) or [])
    out = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(0)
    p = QPainter(out)
    try:
        def render_clip(clip):
            if clip is clips[0]:
                return img_a
            return img_b
        paint_standalone_transition(p, track, trans, t, w, h, render_clip)
    finally:
        p.end()
    return out


def test_q68_intensity_reaches_render():
    """Property panel intensity spin writes trans.intensity; export paint reads it."""
    _qapp()
    from cutora.model.timeline_model import Clip, Track, TransitionItem
    from cutora.ui.panels.property import PropertyPanel

    a, b = _textured_pair(64, 48)
    tr = Track("video")
    tr.clips.extend([Clip("a", 0.0, 2.0, 0.0), Clip("b", 0.0, 2.0, 1.0)])
    trans = TransitionItem(start_t=0.65, duration=0.7, preset="pixelate")
    tr.transitions.append(trans)
    panel = PropertyPanel()
    panel.load_data("transition", (tr, trans))
    assert hasattr(panel, "trans_intensity_sp")
    assert hasattr(panel, "trans_dir_cb")
    assert hasattr(panel, "trans_easing_cb")
    t_mid = trans.start_t + trans.duration * 0.5
    panel.trans_intensity_sp.setValue(0.25)
    assert abs(float(trans.intensity) - 0.25) < 1e-6
    lo = _paint_trans(tr, trans, t_mid, 64, 48, a, b)
    panel.trans_intensity_sp.setValue(2.2)
    assert abs(float(trans.intensity) - 2.2) < 1e-6
    hi = _paint_trans(tr, trans, t_mid, 64, 48, a, b)
    d = _mad(_qimage_rgba(lo)[..., :3], _qimage_rgba(hi)[..., :3])
    assert d > 0.04, d
    panel.trans_easing_cb.setCurrentIndex(0)  # linear
    assert trans.easing == "linear"
    panel.trans_dir_cb.setCurrentIndex(1)  # right
    assert trans.direction == "right"


def _ffmpeg():
    from cutora.core.paths import FFMPEG
    return FFMPEG


def _gradient_mp4(path, kind="xy", frames=24, fps=24, size="64x64"):
    """Ramp, not a solid — canvas vs export must disagree if they sample differently."""
    import subprocess
    dur = max(frames, 1) / float(fps)
    src = "testsrc" if kind == "xy" else "testsrc2"
    subprocess.check_call(
        [
            _ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi",
            "-i", "%s=s=%s:r=%d:d=%.4f" % (src, size, fps, dur),
            "-frames:v", str(int(frames)),
            "-pix_fmt", "yuv420p",
            str(path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def test_q68_canvas_matches_export(tmp_path):
    """VideoCanvas transition path vs RenderWorker._paint_export_transition.

    Gradient clips at 24 fps (not the old canvas fps=30 default). The test
    calls two product entry points; it does not invoke one helper twice.
    """
    _qapp()
    from PySide6.QtGui import QImage, QPainter
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, MediaSource, Track, TransitionItem
    from cutora.render.readers import _open_video_capture
    from cutora.render.render_worker import RenderWorker
    from cutora.ui.canvas import VideoCanvas

    fps = 24.0
    pa = str(tmp_path / "a.mp4")
    pb = str(tmp_path / "b.mp4")
    _gradient_mp4(pa, "xy", frames=24, fps=24)
    _gradient_mp4(pb, "yx", frames=24, fps=24)
    w, h = 64, 64
    tl = Timeline(w, h, fps)
    sa, sb = MediaSource(pa), MediaSource(pb)
    tl.sources[sa.id] = sa
    tl.sources[sb.id] = sb
    tr = Track("video")
    tr.id = "tv"
    tr.clips.extend([Clip(sa.id, 0.0, 1.0, 0.0), Clip(sb.id, 0.0, 1.0, 1.0)])
    trans = TransitionItem(start_t=0.65, duration=0.7, preset="cross_dissolve")
    tr.transitions.append(trans)
    tl.tracks.append(tr)
    task = {"timeline": tl, "dims": (w, h), "video": pa, "segments": []}
    t = trans.start_t + trans.duration * 0.5

    canvas = VideoCanvas()
    canvas.set_data(task, t)
    canvas_img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    canvas_img.fill(0)
    cp = QPainter(canvas_img)
    try:
        canvas._draw_standalone_transition_composite(cp, tr, trans, tl, w, h)
    finally:
        cp.end()

    rw = RenderWorker.__new__(RenderWorker)
    rw.project = task
    rw._geom_last_log_t = 0.0
    caps = {}
    states = {}
    try:
        for sid, src in (getattr(tl, "sources", {}) or {}).items():
            cap = _open_video_capture(getattr(src, "path", None))
            if cap is not None and cap.isOpened():
                caps[sid] = cap
        export = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        export.fill(0)
        ep = QPainter(export)
        try:
            rw._paint_export_transition(
                ep, tr, trans, float(t), int(w), int(h),
                lambda c: caps.get(getattr(c, "source_id", None)),
                lambda c: states.setdefault(
                    str(getattr(c, "source_id", "") or ""),
                    {"last_time": -1.0, "last_frame": None, "src_fps": fps}),
            )
        finally:
            ep.end()
        mad = _mad(_qimage_rgba(canvas_img)[..., :3], _qimage_rgba(export)[..., :3])
        assert mad < CANVAS_EXPORT_MAD, mad
        assert int(_qimage_rgba(canvas_img)[..., 3].min()) == 255
        assert int(_qimage_rgba(export)[..., 3].min()) == 255
        # Mix is not a copy of either endpoint.
        assert float(_qimage_rgba(canvas_img)[..., :3].std()) > 8.0
    finally:
        for cap in caps.values():
            cap.release()
        for cap in list(canvas._cv_caps.values()):
            try:
                cap.release()
            except Exception:
                pass


def test_q68_thumbs_textured_and_idle():
    """Thumbs are not solids; timers run only on hover/selected."""
    _qapp()
    from PySide6.QtGui import QPixmap
    from cutora.ui.panels.transition_panel import TransitionPanel, THUMB_W, THUMB_H
    p = TransitionPanel()
    p.show()
    assert p.video_count() > 0
    live = []
    for card in p._video_cards:
        thumb = card.thumb
        assert not thumb._timer.isActive(), card.preset
        arr = _qimage_rgba(thumb._out)
        assert float(arr[..., :3].std()) > 12.0, card.preset
        pix = QPixmap(THUMB_W, THUMB_H)
        thumb._prog = 0.45
        thumb.render(pix)
        painted = _qimage_rgba(pix.toImage())[..., :3]
        fallback = float(np.mean(np.abs(painted.astype(np.int16) - np.array([0x2f, 0x3b, 0x4a]))))
        if fallback < 2:
            live.append(card.preset)
    assert live == [], live
    first = p._video_cards[0]
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QEnterEvent
    first.enterEvent(QEnterEvent(QPointF(1, 1), QPointF(1, 1), QPointF(1, 1)))
    assert first.thumb._timer.isActive()
    first.leaveEvent(None)
    assert not first.thumb._timer.isActive()
    p.set_active_preset(first.preset)
    assert first.thumb._timer.isActive()
    p.set_active_preset("")
    assert not first.thumb._timer.isActive()


def _mouse(w, kind, x, y):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    pos = QPointF(float(x), float(y))
    typ = {
        "press": QEvent.Type.MouseButtonPress,
        "move": QEvent.Type.MouseMove,
        "release": QEvent.Type.MouseButtonRelease,
    }[kind]
    btn = Qt.MouseButton.LeftButton
    pressed = Qt.MouseButton.NoButton if kind == "release" else btn
    caused = Qt.MouseButton.NoButton if kind == "move" else btn
    ev = QMouseEvent(typ, pos, pos, caused, pressed, Qt.KeyboardModifier.NoModifier)
    if kind == "press":
        w.mousePressEvent(ev)
    elif kind == "move":
        w.mouseMoveEvent(ev)
    else:
        w.mouseReleaseEvent(ev)


def test_q68_handle_stays_on_seam():
    """Drag cannot leave the clip join; canvas.update runs while dragging."""
    _qapp()
    from PySide6.QtWidgets import QWidget
    from cutora.model.seams import transition_at_seam
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track, TransitionItem
    from cutora.ui.timeline.widget import TimelineWidget

    tl = Timeline(64, 64, 30)
    tr = Track("video")
    tr.id = "tv"
    tr.clips.extend([
        Clip("a", 0.0, 2.0, 0.0),
        Clip("b", 0.0, 2.0, 2.0),
        Clip("c", 0.0, 2.0, 4.0),
    ])
    trans = TransitionItem(start_t=1.825, duration=0.35, preset="cross_dissolve")
    tr.transitions.append(trans)
    tl.tracks.append(tr)
    task = {"timeline": tl, "segments": []}

    class _Canvas(QWidget):
        def __init__(self):
            super().__init__()
            self.n = 0

        def update(self, *a, **k):
            self.n += 1
            return super().update(*a, **k)

    host = QWidget()
    host.video_canvas = _Canvas()
    w = TimelineWidget()
    w.setParent(host)
    w.pixels_per_second = 100.0
    w.scroll_x = 0
    w.resize(800, 200)
    w.set_data(4000, task)
    w.show()
    from test_q55_transitions import _FakeWin, _bind_bus
    win = _FakeWin(task, w)
    win.video_canvas = host.video_canvas
    _bind_bus(win)
    # Reparent so window() sees video_canvas: TimelineWidget's window is host.
    host.resize(800, 240)
    host.show()
    row_y = w.RULER_H + w.TOP_PAD + 4
    left_x = trans.start_t * w.pixels_per_second
    n0 = host.video_canvas.n
    _mouse(w, "press", left_x, row_y)
    _mouse(w, "move", 2.6 * w.pixels_per_second, row_y)  # past seam 2.0
    assert trans.start_t <= 2.0 + 1e-6, trans.start_t
    assert trans.start_t + trans.duration >= 2.0 - 1e-6
    assert transition_at_seam(tr, 2.0, fps=30.0) is trans
    assert host.video_canvas.n > n0
    _mouse(w, "release", 2.6 * w.pixels_per_second, row_y)
    assert transition_at_seam(tr, 2.0, fps=30.0) is trans
    # Right handle cannot cross the next seam at 4.0.
    right_x = (trans.start_t + trans.duration) * w.pixels_per_second
    _mouse(w, "press", right_x, row_y)
    _mouse(w, "move", 5.5 * w.pixels_per_second, row_y)
    assert trans.start_t + trans.duration < 4.0 - 1.0 / 30.0, trans.duration
    _mouse(w, "release", 5.5 * w.pixels_per_second, row_y)
    assert transition_at_seam(tr, 2.0, fps=30.0) is trans
    assert transition_at_seam(tr, 4.0, fps=30.0) is not trans

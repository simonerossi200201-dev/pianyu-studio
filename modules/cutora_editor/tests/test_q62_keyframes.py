# -*- coding: utf-8 -*-
"""Q62: keyframe periphery — canvas drag, playhead watches, one-param edit,
subtitle/sticker bake, split, diamond drag.

Defect-exposing on purpose:
- canvas drag of a keyed clip that only wrote clip.x would bounce (value_at
  unchanged, export pixels unchanged)
- watches that only refresh on select stay solid/hollow after the playhead moves
- on_change_video keying all five paths would stamp scale with the spin's
  stale value when X is edited
- subtitle opacity keyframes that bake into style["opacity"] but never get
  read by textfx.draw look identical at t=0 and t=1
- image overlays with elements skip bake_host in draw_stickers
- split_at clone() that does not shift t makes the right half replay from 0
"""
from __future__ import annotations

import copy
import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from test_q31_keyframes import (  # noqa: E402
    _color_mp4,
    _export_frame,
    _mad,
    _qapp,
    _qimage_rgb,
    _task_with_clip,
)


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _mean_lum(arr, y0=None, y1=None, x0=None, x1=None):
    h, w = arr.shape[0], arr.shape[1]
    y0 = 0 if y0 is None else y0
    y1 = h if y1 is None else y1
    x0 = 0 if x0 is None else x0
    x1 = w if x1 is None else x1
    band = arr[y0:y1, x0:x1]
    return float(band.astype(np.float64).mean()) / 255.0


def _mouse(typ, x, y, button=None):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    if button is None:
        button = Qt.MouseButton.LeftButton
    return QMouseEvent(
        typ, QPointF(float(x), float(y)),
        button, button, Qt.KeyboardModifier.NoModifier,
    )


def _preview_composited(task, t):
    """PreviewEngine.frame_at — video + overlays/subs. Not video_frame_at."""
    from cutora.preview.engine import PreviewEngine
    eng = PreviewEngine()
    try:
        return eng.frame_at(task, float(t))
    finally:
        eng.close()


def _export_composited(task, t):
    """ExportRenderer.render_frame — independent of PreviewEngine."""
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtCore import Qt
    from cutora.model.canvas import canvas_wh
    from cutora.render.export_renderer import ExportRenderer

    w, h = canvas_wh(task)
    img = ExportRenderer.render_frame(task, int(w), int(h), float(t))
    assert isinstance(img, QImage)
    return img


def _white_png(path, w=80, h=50):
    from PySide6.QtGui import QColor, QImage
    img = QImage(int(w), int(h), QImage.Format.Format_ARGB32)
    img.fill(QColor(240, 240, 240, 255))
    assert img.save(str(path), "PNG")


def _win_for_task(task):
    from PySide6.QtWidgets import QWidget
    from cutora.core.history import HistoryManager

    class _Win(QWidget):
        def __init__(self, task):
            super().__init__()
            self.tasks = [task]
            self.curr_idx = 0
            self.project_path = "mem"
            self.history_mgr = HistoryManager()
            self.timeline = None

        def push_undo(self, action_name="操作", command=None, params=None, source=None):
            self.history_mgr.push(action_name, self._snap())

        def capture_undo_after(self):
            self.history_mgr.set_after(self._snap())

        def _snap(self):
            return {"tasks": copy.deepcopy(self.tasks), "curr_idx": self.curr_idx}

        def undo(self):
            state = self.history_mgr.undo()
            if state:
                self.tasks[:] = state["tasks"]
                self.curr_idx = state["curr_idx"]

        def _refresh_task_view(self, idx):
            return None

    return _Win(task)


# ---------------------------------------------------------------------------
# 1. canvas drag of a keyed clip writes a keyframe, export pixels move
# ---------------------------------------------------------------------------

def test_q62_canvas_drag_updates_keyframe(tmp_path):
    """Drag a clip that already has x keyframes: playhead value_at must
    change, and the export frame at that t must move. Writing only clip.x
    is the bounce-back bug — resolve_clip_visual ignores the static field.
    """
    _qapp()
    from PySide6.QtCore import QEvent, QPointF, QRectF
    from PySide6.QtWidgets import QApplication
    from cutora.render.keyframe import value_at
    from cutora.ui.canvas import VideoCanvas

    media = str(tmp_path / "red.mp4")
    _color_mp4(media, "0xE01010", frames=60, size="80x80")
    kfs = {"x": [
        {"t": 0.0, "v": -40.0, "ease": "linear"},
        {"t": 1.0, "v": 40.0, "ease": "linear"},
    ]}
    task, _tr, clip = _task_with_clip(
        media, start_t=0.0, duration=2.0, kfs=kfs, canvas=(160, 90))
    t = 0.5
    before_v = float(value_at(clip, "x", t, 99.0))
    assert abs(before_v - 0.0) < 1e-6, before_v
    before_img = _qimage_rgb(_export_frame(task, t))

    canvas = VideoCanvas()
    canvas.resize(160, 90)
    canvas.set_data(task, t)
    canvas.show()
    QApplication.processEvents()
    canvas._dragging_item = {"type": "video_clip", "clip": clip}
    canvas._drag_mode = "move"
    canvas._resize_handle = None
    canvas._selected_rect = None
    canvas._drag_origin_rect = None
    canvas._drag_accum = None
    canvas._last_mouse_pos = QPointF(40.0, 45.0)
    canvas.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, 80.0, 45.0))
    QApplication.processEvents()

    after_v = float(value_at(clip, "x", t, 99.0))
    assert abs(after_v - before_v) > 10.0, (
        "canvas drag of a keyed clip did not change value_at at the playhead "
        "(still %s; static clip.x=%s) — bounce-back bug" % (after_v, clip.x)
    )
    after_img = _qimage_rgb(_export_frame(task, t))
    mad = _mad(before_img, after_img)
    assert mad > 0.02, (
        "export frame at playhead did not move after canvas drag (mad=%.5f) "
        "value_at %s → %s" % (mad, before_v, after_v)
    )
    canvas.close()


# ---------------------------------------------------------------------------
# 2. stopwatch + spin follow the playhead
# ---------------------------------------------------------------------------

def test_q62_watch_follows_playhead(tmp_path):
    """Two x keys at 0 and 1. Moving the playhead to 0.5 must hollow the
    stopwatch and put the interpolated value into the X spin (not clip.x).
    """
    _qapp()
    from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget
    from cutora.render.keyframe import set_point
    from cutora.ui.panels.property import PropertyPanel
    from cutora.ui.timeline.widget import TimelineWidget

    media = str(tmp_path / "red.mp4")
    _color_mp4(media, "0xE01010", frames=60, size="80x80")
    task, tr, clip = _task_with_clip(
        media, start_t=0.0, duration=2.0, x=0.0, canvas=(160, 90))
    set_point(clip, "x", 0.0, 0.0)
    set_point(clip, "x", 1.0, 80.0)
    # Static field stays at 0 so a refresh that reads clip.x would not move.
    clip.x = 0.0

    win = QWidget()
    tlw = TimelineWidget()
    panel = PropertyPanel()
    lay = QVBoxLayout(win)
    lay.addWidget(panel)
    lay.addWidget(tlw)
    win.timeline = tlw
    win.tasks = [task]
    win.curr_idx = 0
    tlw.set_data(2000, task)
    panel.load_data("video_clip", (tr, clip))
    win.show()
    QApplication.processEvents()

    tlw.set_position(0)
    QApplication.processEvents()
    assert panel.vid_x_kf._enabled is True
    assert panel.vid_x_kf._on_key is True, "playhead on first key should be solid"
    assert abs(float(panel.vid_x.value()) - 0.0) < 0.51, panel.vid_x.value()

    tlw.set_position(500)
    QApplication.processEvents()
    assert panel.vid_x_kf._on_key is False, (
        "playhead between keys still painted solid — watches not hooked to playhead"
    )
    x_mid = float(panel.vid_x.value())
    assert abs(x_mid - 40.0) < 0.51, (
        "X spin did not follow value_at(0.5)=40 (got %s, static clip.x=%s)"
        % (x_mid, clip.x)
    )

    tlw.set_position(1000)
    QApplication.processEvents()
    assert panel.vid_x_kf._on_key is True, "playhead on second key should be solid"
    assert abs(float(panel.vid_x.value()) - 80.0) < 0.51, panel.vid_x.value()
    win.close()


# ---------------------------------------------------------------------------
# 3. editing X must not stamp a key on scale
# ---------------------------------------------------------------------------

def test_q62_edit_one_param_only(tmp_path):
    """Change X while scale is also keyed. Scale's point list must be
    byte-for-byte the same. The old on_change_video called _auto_key on
    every path, writing the spin's stale scale into a new point.
    """
    _qapp()
    from PySide6.QtWidgets import QApplication, QVBoxLayout
    from cutora.core.commands import get_bus, reset_bus_for_tests
    from cutora.render.keyframe import keyframes_of, set_point
    from cutora.ui.panels.property import PropertyPanel
    from cutora.ui.timeline.widget import TimelineWidget

    media = str(tmp_path / "red.mp4")
    _color_mp4(media, "0xE01010", frames=60, size="80x80")
    task, tr, clip = _task_with_clip(
        media, start_t=0.0, duration=2.0, scale=1.0, canvas=(160, 90))
    set_point(clip, "x", 0.0, 0.0)
    set_point(clip, "x", 1.0, 80.0)
    set_point(clip, "scale", 0.0, 1.0)
    set_point(clip, "scale", 1.0, 2.0)
    scale_before = copy.deepcopy(keyframes_of(clip).get("scale"))

    reset_bus_for_tests()
    win = _win_for_task(task)
    tlw = TimelineWidget()
    panel = PropertyPanel()
    lay = QVBoxLayout(win)
    lay.addWidget(panel)
    lay.addWidget(tlw)
    win.timeline = tlw
    tlw.set_data(2000, task)
    tlw.set_position(500)
    bus = get_bus()
    bus.bind_window(win)
    panel.load_data("video_clip", (tr, clip))
    win.show()
    QApplication.processEvents()

    panel.block_signals = False
    # 25, not the interpolated 40: after watches follow the playhead the
    # spin already shows 40 and setValue(40) would not emit.
    panel.vid_x.setValue(25.0)
    QApplication.processEvents()

    scale_after = keyframes_of(clip).get("scale")
    assert scale_after == scale_before, (
        "editing X mutated scale keyframes\nbefore=%s\nafter=%s"
        % (scale_before, scale_after)
    )
    x_pts = keyframes_of(clip).get("x") or []
    assert any(abs(float(p.get("t") or 0) - 0.5) < 0.02 for p in x_pts), (
        "editing X at playhead 0.5 did not write an x key: %s" % x_pts
    )
    win.close()


# ---------------------------------------------------------------------------
# 4. subtitle opacity keyframes actually paint
# ---------------------------------------------------------------------------

def test_q62_subtitle_opacity_keyframe_renders(tmp_path):
    """Opacity 0 → 255 on a white subtitle. t=0 of the subtitle region must
    be darker than t=1. bake_host writing style['opacity'] is not enough —
    textfx.draw has to apply it. Preview and export both asserted.
    """
    _qapp()
    from cutora.model.timeline import Timeline
    from cutora.ui.panels.fonts import load_embedded_fonts

    load_embedded_fonts()
    media = str(tmp_path / "black.mp4")
    _color_mp4(media, "0x101010", frames=60, size="160x90")
    tl = Timeline(160, 90, 30.0)
    task = {
        "video": media,
        "dims": (160, 90),
        "canvas_width": 160,
        "canvas_height": 90,
        "timeline": tl,
        "style": {
            "font_family": "Arimo",
            "fontsize": 64,
            "bold": True,
            "pos_x": 80,
            "pos_y": 45,
            "opacity": 255,
            "fx": {
                "fill": {"type": "solid", "colors": ["#FFFFFF"], "angle": 0.0},
                "stroke": {"width_em": 0.0, "color": "#000000", "join": "round"},
                "glow": {"sigma_em": 0, "strength": 0},
                "shadow": {"sigma_em": 0, "alpha": 0},
                "bg": {"type": "none"},
                "anim": {"in": "none", "loop": "none", "out": "none"},
            },
            "keyframes": {
                "opacity": [
                    {"t": 0.0, "v": 0.0, "ease": "linear"},
                    {"t": 1.0, "v": 255.0, "ease": "linear"},
                ],
            },
        },
        "segments": [{
            "text": "HELLO",
            "start": 0.0,
            "end": 2.0,
            "id": "s0",
            "words": [
                {"word": "HELLO", "start": 0.0, "end": 2.0},
            ],
        }],
        "text_overlays": [],
        "image_overlays": [],
    }

    prev0 = _qimage_rgb(_preview_composited(task, 0.0))
    prev1 = _qimage_rgb(_preview_composited(task, 1.0))
    exp0 = _qimage_rgb(_export_composited(task, 0.0))
    exp1 = _qimage_rgb(_export_composited(task, 1.0))

    # Center band where the word sits.
    lum_p0 = _mean_lum(prev0, 20, 70, 20, 140)
    lum_p1 = _mean_lum(prev1, 20, 70, 20, 140)
    lum_e0 = _mean_lum(exp0, 20, 70, 20, 140)
    lum_e1 = _mean_lum(exp1, 20, 70, 20, 140)
    assert lum_p1 - lum_p0 > 0.08, (
        "preview subtitle opacity 0→255 did not change brightness "
        "(t0=%.4f t1=%.4f) — style opacity ignored by textfx.draw"
        % (lum_p0, lum_p1)
    )
    assert lum_e1 - lum_e0 > 0.08, (
        "export subtitle opacity 0→255 did not change brightness "
        "(t0=%.4f t1=%.4f)" % (lum_e0, lum_e1)
    )
    mad_pe0 = _mad(prev0, exp0)
    mad_pe1 = _mad(prev1, exp1)
    assert mad_pe0 < 0.05, "preview/export t=0 diverged mad=%.5f" % mad_pe0
    assert mad_pe1 < 0.05, "preview/export t=1 diverged mad=%.5f" % mad_pe1


# ---------------------------------------------------------------------------
# 5. new-style image overlay keyframes paint
# ---------------------------------------------------------------------------

def test_q62_sticker_keyframe_renders(tmp_path):
    """Image overlay with elements (draw_stickers path). x -40→40: t=0 and
    t=1 export frames must differ. draw_single_overlay returns early for
    these, so bake_host has to run inside draw_stickers.
    """
    _qapp()
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Track

    png = str(tmp_path / "card.png")
    _white_png(png, 80, 50)
    media = str(tmp_path / "bg.mp4")
    _color_mp4(media, "0x101010", frames=60, size="160x90")
    tl = Timeline(160, 90, 30.0)
    img_tr = Track("image")
    img_tr.id = "img0"
    tl.add_track(img_tr)
    ov = {
        "type": "image",
        "start": 0.0,
        "end": 2.0,
        "x": 0.0,
        "y": 20.0,
        "w": 80.0,
        "h": 50.0,
        "opacity": 255,
        "scale": 1.0,
        "rotation": 0,
        "_track_id": "img0",
        "elements": [{
            "kind": "image",
            "path": png,
            "x": 0.0,
            "y": 0.0,
            "w": 80.0,
            "h": 50.0,
            "opacity": 255,
            "rotation": 0,
        }],
        "keyframes": {
            "x": [
                {"t": 0.0, "v": -40.0, "ease": "linear"},
                {"t": 1.0, "v": 40.0, "ease": "linear"},
            ],
        },
    }
    task = {
        "video": media,
        "dims": (160, 90),
        "canvas_width": 160,
        "canvas_height": 90,
        "timeline": tl,
        "style": {},
        "segments": [],
        "text_overlays": [],
        "image_overlays": [ov],
    }

    prev0 = _qimage_rgb(_preview_composited(task, 0.0))
    prev1 = _qimage_rgb(_preview_composited(task, 1.0))
    exp0 = _qimage_rgb(_export_composited(task, 0.0))
    exp1 = _qimage_rgb(_export_composited(task, 1.0))
    mad_p = _mad(prev0, prev1)
    mad_e = _mad(exp0, exp1)
    assert mad_p > 0.02, (
        "preview sticker x=-40→40 produced identical frames (mad=%.5f) "
        "— draw_stickers skipped bake_host" % mad_p
    )
    assert mad_e > 0.02, (
        "export sticker x=-40→40 produced identical frames (mad=%.5f)"
        % mad_e
    )
    assert _mad(prev0, exp0) < 0.05
    assert _mad(prev1, exp1) < 0.05


# ---------------------------------------------------------------------------
# 6. split keeps the animation continuous
# ---------------------------------------------------------------------------

def test_q62_split_preserves_animation(tmp_path):
    """Split a keyed clip at 1.0s. Frames 1/30s either side of the cut must
    match the pre-split frames (MAD small). An unshifted clone replays x
    from the first key on the right half.
    """
    _qapp()
    media = str(tmp_path / "red.mp4")
    _color_mp4(media, "0xE01010", frames=90, size="80x80")
    kfs = {"x": [
        {"t": 0.0, "v": -40.0, "ease": "linear"},
        {"t": 2.0, "v": 40.0, "ease": "linear"},
    ]}
    task, tr, clip = _task_with_clip(
        media, start_t=0.0, duration=2.0, kfs=kfs, canvas=(160, 90))
    t_lo, t_hi = 1.0 - 1.0 / 30.0, 1.0 + 1.0 / 30.0
    before_lo = _qimage_rgb(_export_frame(task, t_lo))
    before_hi = _qimage_rgb(_export_frame(task, t_hi))
    # The animation is moving; the two frames are not identical, but close.
    assert _mad(before_lo, before_hi) < 0.08

    right = clip.split_at(1.0)
    assert right is not None
    tr.clips.append(right)

    after_lo = _qimage_rgb(_export_frame(task, t_lo))
    after_hi = _qimage_rgb(_export_frame(task, t_hi))
    mad_lo = _mad(before_lo, after_lo)
    mad_hi = _mad(before_hi, after_hi)
    assert mad_lo < 0.02, (
        "left of cut changed after split (mad=%.5f) — left keyframes broken"
        % mad_lo
    )
    assert mad_hi < 0.02, (
        "right of cut changed after split (mad=%.5f) — right half replayed "
        "from t=0 instead of continuing" % mad_hi
    )
    # Continuity across the cut after the split.
    assert _mad(after_lo, after_hi) < 0.08


# ---------------------------------------------------------------------------
# 7. diamonds drag; paste lands on the playhead
# ---------------------------------------------------------------------------

def test_q62_diamond_drag_and_paste_at_playhead():
    """Left-drag a diamond must call through to move_point (t changes).
    Paste must write at the playhead, not at the diamond that was clicked.
    """
    _qapp()
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication
    from cutora.core.commands import get_bus, reset_bus_for_tests
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track
    from cutora.render.keyframe import keyframes_of, set_point
    from cutora.ui.timeline.widget import TimelineWidget

    tl = Timeline(160, 90, 30.0)
    tr = Track("video")
    tr.id = "v0"
    clip = Clip("s", 0.0, 2.0, 0.0)
    set_point(clip, "x", 0.0, 0.0)
    set_point(clip, "x", 1.0, 80.0)
    tr.clips.append(clip)
    tl.add_track(tr)
    task = {"timeline": tl, "duration": 2.0}

    reset_bus_for_tests()
    win = _win_for_task(task)
    w = TimelineWidget()
    w.setParent(win)
    win.timeline = w
    bus = get_bus()
    bus.bind_window(win)
    w.resize(640, 200)
    w.pixels_per_second = 100.0
    w.set_data(2000, task)
    w.show()
    QApplication.processEvents()

    rows = w._track_positions().get("rows") or []
    assert rows, "no timeline rows"
    _t_obj, row_y, row_h = rows[0]
    cy = row_y + row_h - float(w.KF_BAND_H) / 2.0
    hit = w._hit_keyframe(100.0, cy)
    assert hit is not None, "diamond at t=1.0 was not hittable"
    assert abs(float(hit["t"]) - 1.0) < 0.05, hit

    w.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, 100.0, cy))
    # Drag 0.4s to the left: 40 px at 100 px/s.
    w.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, 60.0, cy))
    w.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, 60.0, cy))
    QApplication.processEvents()

    xs = [float(p["t"]) for p in (keyframes_of(clip).get("x") or [])]
    assert any(abs(t - 0.6) < 0.08 for t in xs), (
        "dragging the t=1 diamond did not move it (times=%s)" % xs
    )
    assert not any(abs(t - 1.0) < 0.02 for t in xs), (
        "t=1 diamond still there after drag: %s" % xs
    )

    # Paste at playhead 1.5s, clicking the remaining t=0 diamond.
    w.set_position(1500)
    from cutora.render.keyframe import copy_point
    copy_point(clip, "x", 0.0)
    hit0 = w._hit_keyframe(0.0, cy)
    assert hit0 is not None
    w._keyframe_paste_at_playhead(hit0)
    xs2 = [float(p["t"]) for p in (keyframes_of(clip).get("x") or [])]
    assert any(abs(t - 1.5) < 0.05 for t in xs2), (
        "paste wrote at the clicked diamond instead of the playhead: %s" % xs2
    )
    w.close()
    win.close()

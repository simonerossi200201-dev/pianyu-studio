# -*- coding: utf-8 -*-
"""Q69: timeline colour wrap-up, 9-slice glow cache, drag feel."""
from __future__ import annotations

import copy
import os

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PPS = 40.0

# Hardcoded chrome Q53/Q69 found on the timeline. Must not dominate a grab.
_COLD = (
    (90, 140, 255),
    (150, 180, 255),
    (59, 157, 255),
    (255, 140, 0),
    (255, 120, 30),
    (100, 180, 255),
)


def _qapp():
    from PySide6.QtWidgets import QApplication, QStyleFactory
    app = QApplication.instance() or QApplication(["q69-timeline"])
    app.setStyle(QStyleFactory.create("Fusion"))
    return app


def _apply_theme(theme_id):
    from cutora.ui.theme.apply import apply_theme
    apply_theme(theme_id)


def _bgra(img):
    from PySide6.QtGui import QImage
    conv = img.convertToFormat(QImage.Format.Format_ARGB32)
    w, h = conv.width(), conv.height()
    bpl = conv.bytesPerLine()
    raw = bytes(conv.constBits())
    arr = np.frombuffer(raw, dtype=np.uint8).reshape((h, bpl))[:, : w * 4].copy()
    return arr.reshape((h, w, 4))


def _count_rgb(view, rgb, slop=6):
    tr, tg, tb = rgb
    r = view[..., 2].astype(np.int16)
    g = view[..., 1].astype(np.int16)
    b = view[..., 0].astype(np.int16)
    return int(
        (
            (np.abs(r - tr) <= slop)
            & (np.abs(g - tg) <= slop)
            & (np.abs(b - tb) <= slop)
        ).sum()
    )


def _make_coloured_timeline():
    """Two video clips with a gap, a selected transition tape, a text clip."""
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track, TransitionItem
    from cutora.ui.timeline.widget import TimelineWidget

    model = Timeline(1080, 1920, 30)
    video = Track("video")
    video.clips.append(Clip("s1", 0.0, 1.2, 0.2))
    video.clips.append(Clip("s1", 0.0, 1.2, 3.0))
    trans = TransitionItem(1.2, 0.6, "cross_dissolve")
    video.transitions.append(trans)
    text = Track("text")
    audio = Track("audio")
    audio.clips.append(Clip("s1", 0.0, 2.0, 0.5))
    model.tracks.extend([video, text, audio])

    w = TimelineWidget()
    w.resize(720, 280)
    w.pixels_per_second = PPS
    w.scroll_x = 0
    w.scroll_y = 0
    w.duration = 12000
    w.position = 0
    w.trim_start_sec = 0.4
    w.trim_end_sec = 5.0
    w.task_data = {
        "timeline": model,
        "segments": [],
        "text_overlays": [
            {"start": 0.5, "end": 3.5, "content": "Hi", "_track_id": text.id},
        ],
        "trim_start_sec": 0.4,
        "trim_end_sec": 5.0,
    }
    w.selected_transition = (video, trans)
    return w


def test_q69_no_cold_colors():
    """Six palettes: timeline grab has no dominant off-palette chrome."""
    from PySide6.QtWidgets import QApplication
    from cutora.ui.theme.tokens import BUILTIN_THEME_IDS, current, hex_rgb, mix_hex

    app = _qapp()
    w = _make_coloured_timeline()
    w.show()
    QApplication.processEvents()

    keys = (
        "bg0", "bg1", "bg2", "bg3", "line", "border",
        "fg0", "fg1", "fg2", "fg_muted",
        "accent", "accent_hi", "accent_dim",
        "info", "ok", "warn", "err", "err_text", "glow",
        "track_video", "track_audio", "track_subtitle", "track_text", "track_image",
    )

    for tid in BUILTIN_THEME_IDS:
        _apply_theme(tid)
        QApplication.processEvents()
        w.update()
        QApplication.processEvents()
        w.repaint()
        QApplication.processEvents()
        view = _bgra(w.grab().toImage())
        t = current()
        allowed = [hex_rgb(getattr(t, k)) for k in keys]
        allowed.append(hex_rgb(mix_hex(t.bg1, t.bg2, 0.55)))

        for rgb in _COLD:
            close = False
            for pr, pg, pb in allowed:
                if abs(rgb[0] - pr) <= 14 and abs(rgb[1] - pg) <= 14 and abs(rgb[2] - pb) <= 14:
                    close = True
                    break
            if close:
                continue
            n = _count_rgb(view, rgb, slop=8)
            assert n < 60, (
                "%s still paints cold chrome %s (%d px)"
                % (tid, rgb, n)
            )

        # Gap between the two video clips must not be a track_video wash.
        rows = w._track_positions().get("rows", [])
        video_row = None
        for t_obj, row_y, row_h in rows:
            if getattr(t_obj, "type", "") == "video":
                video_row = (row_y, row_h)
                break
        assert video_row is not None, "%s: no video row" % tid
        gy = int(video_row[0] + video_row[1] / 2)
        gx = int(2.0 * PPS)
        patch = view[max(0, gy - 4): gy + 4, max(0, gx - 8): gx + 8]
        assert patch.size > 0, "%s: empty gap patch" % tid
        mean = patch.reshape(-1, 4).astype(np.float32).mean(axis=0)
        mb, mg, mr = float(mean[0]), float(mean[1]), float(mean[2])
        bg = hex_rgb(t.bg0)
        tv = hex_rgb(t.track_video)
        dist_bg = ((mr - bg[0]) ** 2 + (mg - bg[1]) ** 2 + (mb - bg[2]) ** 2) ** 0.5
        dist_tv = ((mr - tv[0]) ** 2 + (mg - tv[1]) ** 2 + (mb - tv[2]) ** 2) ** 0.5
        assert dist_bg < 28 or dist_bg < dist_tv - 8, (
            "%s video-lane gap (%.0f,%.0f,%.0f) still near track_video %s "
            "(dist_bg=%.1f dist_tv=%.1f)"
            % (tid, mr, mg, mb, t.track_video, dist_bg, dist_tv)
        )

    w.close()


def test_q69_glow_cache_width_independent():
    """200 different widths share one inner-glow tile (height is the key)."""
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtWidgets import QApplication
    from cutora.ui.timeline.widget import (
        TimelineWidget, clip_glow_build_count, reset_clip_glow_cache,
    )

    app = _qapp()
    _apply_theme("roma-gold")
    w = TimelineWidget()
    reset_clip_glow_cache()
    img = QImage(400, 80, QImage.Format.Format_ARGB32)
    img.fill(0)
    painter = QPainter(img)
    try:
        for i in range(200):
            rect = QRectF(4.0, 8.0, 12.0 + i, 40.0)
            w._paint_clip_chrome(painter, rect, "video", False)
    finally:
        painter.end()
    n = clip_glow_build_count()
    w.close()
    assert n == 1, "200 different-width clips built %d glow tiles, want 1" % n


def test_q69_glow_hidpi():
    """DPR=2 tile is 2× the logical pixel size."""
    from PySide6.QtGui import QColor
    from cutora.ui.theme.tokens import RADIUS_SM
    from cutora.ui.timeline.widget import (
        CLIP_GLOW_PX, _clip_glow_tile, reset_clip_glow_cache,
    )

    _qapp()
    reset_clip_glow_cache()
    pix = _clip_glow_tile(40, QColor("#4A5F7A"), 110, RADIUS_SM, dpr=2.0)
    log_w = 2 * CLIP_GLOW_PX + 1
    assert abs(float(pix.devicePixelRatio()) - 2.0) < 1e-6, pix.devicePixelRatio()
    assert pix.width() == log_w * 2, "DPR=2 width %d, want %d" % (pix.width(), log_w * 2)
    assert pix.height() == 80, "DPR=2 height %d, want 80" % pix.height()


def _mouse(w, kind, x, y, alt=False):
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
    mods = (Qt.KeyboardModifier.AltModifier if alt
            else Qt.KeyboardModifier.NoModifier)
    ev = QMouseEvent(typ, pos, pos, caused, pressed, mods)
    if kind == "press":
        w.mousePressEvent(ev)
    elif kind == "move":
        w.mouseMoveEvent(ev)
    else:
        w.mouseReleaseEvent(ev)


def test_q69_alt_click_no_duplicate():
    """Alt click without a drag must not clone the clip."""
    from PySide6.QtWidgets import QApplication
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track
    from cutora.ui.timeline.widget import TimelineWidget

    app = _qapp()
    _apply_theme("roma-gold")
    model = Timeline(1080, 1920, 30)
    track = Track("video")
    track.clips.append(Clip("s1", 0.0, 3.0, 0.5))
    model.tracks.append(track)
    w = TimelineWidget()
    w.resize(640, 200)
    w.pixels_per_second = PPS
    w.scroll_x = 0
    w.duration = 8000
    w.task_data = {"timeline": model, "segments": []}
    w.show()
    QApplication.processEvents()

    rows = w._track_positions().get("rows", [])
    video_row = None
    for t_obj, row_y, row_h in rows:
        if getattr(t_obj, "type", "") == "video":
            video_row = (t_obj, row_y, row_h)
            break
    assert video_row is not None
    _tr, row_y, row_h = video_row
    cx = 0.5 * PPS + 1.5 * PPS  # middle of the 3s clip
    cy = row_y + row_h / 2.0
    before = len(track.clips)
    _mouse(w, "press", cx, cy, alt=True)
    _mouse(w, "release", cx, cy, alt=True)
    QApplication.processEvents()
    after = len(track.clips)
    w.close()
    assert after == before == 1, (
        "Alt click without drag duplicated clip (%d → %d)" % (before, after)
    )


class _Win:
    def __init__(self, task):
        from cutora.core.history import HistoryManager
        self.tasks = [task]
        self.curr_idx = 0
        self.project_path = "mem"
        self.history_mgr = HistoryManager()
        self.timeline = type("TLW", (), {"update": lambda self: None})()

    def push_undo(self, action_name="操作", command=None, params=None, source=None):
        self.history_mgr.push(action_name, self._snap())

    def capture_undo_after(self):
        self.history_mgr.set_after(self._snap())

    def _snap(self):
        return {"tasks": copy.deepcopy(self.tasks), "curr_idx": self.curr_idx}

    def _refresh_task_view(self, idx):
        return None


def test_q69_word_clamped_to_segment():
    """First-word left and last-word right cannot leave the subtitle span."""
    from cutora.core.commands import CommandBus, get_registry
    from cutora.subtitle.track_source import set_segments

    words = [
        {"word": "aa", "start": 2.00, "end": 2.30},
        {"word": "bb", "start": 2.30, "end": 2.60},
        {"word": "cc", "start": 2.60, "end": 3.00},
    ]
    task = {}
    set_segments(task, [{
        "start": 2.0,
        "end": 3.0,
        "text": "aa bb cc",
        "words": copy.deepcopy(words),
    }])
    win = _Win(task)
    bus = CommandBus(get_registry())
    bus.bind_window(win)

    bus.execute("subtitle.trim_word", {
        "seg_id": 0, "word_index": 0, "edge": "left", "t": 0.0,
    }, source="ui")
    got = win.tasks[0]["segments"][0]["words"]
    assert float(got[0]["start"]) >= 2.0 - 1e-9, (
        "first word left escaped the segment: %s" % got[0]
    )

    bus.execute("subtitle.trim_word", {
        "seg_id": 0, "word_index": 2, "edge": "right", "t": 9.0,
    }, source="ui")
    got = win.tasks[0]["segments"][0]["words"]
    assert float(got[2]["end"]) <= 3.0 + 1e-9, (
        "last word right escaped the segment: %s" % got[2]
    )


def test_q69_zoom_roundtrip():
    """Zoom in 5× then out 5× returns the original pixels/second."""
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QApplication
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track
    from cutora.ui.timeline.container import TimelineContainer
    from cutora.ui.timeline.widget import TimelineWidget

    app = _qapp()
    tl = Timeline(1080, 1920, 30)
    track = Track("video")
    track.clips.append(Clip("src", 0.0, 10.0, 0.0))
    tl.tracks.append(track)
    w = TimelineWidget()
    box = TimelineContainer(w)
    box.resize(800, 280)
    w.pixels_per_second = 40.0
    w.scroll_x = 80.0
    w.set_data(10_000, {"timeline": tl, "segments": []})
    w.position = 3500
    box.show()
    app.processEvents()
    origin = float(w.pixels_per_second)

    def _wheel(delta):
        event = QWheelEvent(
            QPoint(400, 40),
            box.mapToGlobal(QPoint(400, 40)),
            QPoint(0, 0),
            QPoint(0, delta),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.ControlModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )
        box.wheelEvent(event)
        app.processEvents()

    for _ in range(5):
        _wheel(120)
    for _ in range(5):
        _wheel(-120)
    got = float(w.pixels_per_second)
    box.close()
    w.close()
    assert abs(got - origin) < 1e-6, (
        "zoom roundtrip drifted: origin=%.6f got=%.6f" % (origin, got)
    )

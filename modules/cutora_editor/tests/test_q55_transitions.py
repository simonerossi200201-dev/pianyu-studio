# -*- coding: utf-8 -*-
"""Q55: freeze-frame handles, timeline duration drag, expanded transition library."""
from __future__ import annotations

import copy
import json
import os
import subprocess

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["q55-transitions"])


def _ffmpeg():
    from cutora.core.paths import FFMPEG
    return FFMPEG


def _color_mp4(path, color, frames=10, fps=30, size="64x64"):
    dur = max(frames, 1) / float(fps)
    subprocess.check_call(
        [
            _ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi",
            "-i", "color=c=%s:s=%s:r=%d:d=%.4f" % (color, size, fps, dur),
            "-frames:v", str(int(frames)),
            "-pix_fmt", "yuv420p",
            str(path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _fade_red_mp4(path, frames=10, fps=30, size="64x64"):
    """Dark red at frame 0, bright red at the last frame (fade-in)."""
    dur = max(frames, 1) / float(fps)
    subprocess.check_call(
        [
            _ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi",
            "-i", "color=c=0xE01010:s=%s:r=%d:d=%.4f" % (size, fps, dur),
            "-vf", "fade=t=in:st=0:d=%.4f:c=0x201010" % (dur * 0.9),
            "-frames:v", str(int(frames)),
            "-pix_fmt", "yuv420p",
            str(path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


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


def _luma(rgb):
    r = rgb[..., 0].astype(np.float32)
    g = rgb[..., 1].astype(np.float32)
    b = rgb[..., 2].astype(np.float32)
    return 0.299 * r + 0.587 * g + 0.114 * b


def _mad(a, b):
    if a.shape != b.shape:
        h = min(a.shape[0], b.shape[0])
        w = min(a.shape[1], b.shape[1])
        a = a[:h, :w]
        b = b[:h, :w]
    return float(np.mean(np.abs(a.astype(np.int16) - b.astype(np.int16)))) / 255.0


def _task_two_clips(path_a, path_b, fps=30.0, frames_a=10, frames_b=10,
                    trans_frames=20, overlap=False, preset="cross_dissolve",
                    canvas=(64, 64)):
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, MediaSource, Track, TransitionItem

    w, h = canvas
    tl = Timeline(w, h, fps)
    sa = MediaSource(path_a)
    sb = MediaSource(path_b)
    tl.sources[sa.id] = sa
    tl.sources[sb.id] = sb
    tr = Track("video")
    tr.id = "track-video-q55"
    dur_a = frames_a / fps
    dur_b = frames_b / fps
    trans_dur = trans_frames / fps
    if overlap:
        # B starts halfway through A so the transition sits fully inside both.
        clip_a = Clip(sa.id, 0.0, dur_a, 0.0)
        clip_b = Clip(sb.id, 0.0, dur_b, dur_a * 0.5)
        seam = dur_a * 0.5 + (dur_a * 0.5) / 2.0
        t0 = seam - trans_dur / 2.0
    else:
        clip_a = Clip(sa.id, 0.0, dur_a, 0.0)
        clip_b = Clip(sb.id, 0.0, dur_b, dur_a)
        t0 = dur_a - trans_dur / 2.0
    tr.clips.extend([clip_a, clip_b])
    trans = TransitionItem(start_t=t0, duration=trans_dur, preset=preset)
    tr.transitions.append(trans)
    tl.tracks.append(tr)
    task = {
        "timeline": tl,
        "dims": (w, h),
        "video": path_a,
        "segments": [],
        "text_overlays": [],
        "image_overlays": [],
    }
    return task, tr, trans, clip_a, clip_b


def _preview_frame(task, t):
    from cutora.preview.engine import PreviewEngine
    from cutora.render.export_renderer import ExportRenderer
    from cutora.model.canvas import canvas_wh
    eng = PreviewEngine()
    try:
        video = eng.video_frame_at(task, t)
        w, h = canvas_wh(task)

        def _bg(p, _img=video):
            p.drawImage(0, 0, _img)

        return ExportRenderer.render_frame(task, int(w), int(h), float(t), draw_background=_bg)
    finally:
        eng.close()


def _export_video_frame(task, t):
    """Export video layer via the same helper export uses (freeze on)."""
    from PySide6.QtGui import QImage, QPainter, QColor
    from PySide6.QtCore import Qt
    from cutora.model.canvas import canvas_wh
    from cutora.preview.engine import PreviewEngine
    from cutora.render.export_renderer import ExportRenderer

    eng = PreviewEngine()
    try:
        w, h = canvas_wh(task)
        video = eng.video_frame_at(task, t)

        def _bg(p, _img=video):
            p.drawImage(0, 0, _img)

        return ExportRenderer.render_frame(
            task, int(w), int(h), float(t), draw_background=_bg)
    finally:
        eng.close()


# ── freeze math ───────────────────────────────────────

def test_q55_source_time_in_range_unchanged():
    from cutora.model.timeline_model import Clip
    from cutora.render.transition_runtime import source_time_for_clip

    clip = Clip("s", 1.0, 3.0, 2.0)
    clip.speed = 1.0
    t = 2.5  # elapsed 0.5 → src 1.5
    a, fa = source_time_for_clip(clip, t, source_duration=10.0, fps=30.0, extend=False)
    b, fb = source_time_for_clip(clip, t, source_duration=10.0, fps=30.0, extend=True)
    assert fa is False and fb is False
    assert a == pytest.approx(1.5, abs=1e-9)
    assert b == pytest.approx(a, abs=1e-12)


def test_q55_source_time_freeze_out_and_in():
    from cutora.model.timeline_model import Clip
    from cutora.render.transition_runtime import source_time_for_clip

    # A uses the whole 10-frame source.
    a = Clip("s", 0.0, 10 / 30.0, 0.0)
    src, frozen = source_time_for_clip(
        a, 15 / 30.0, source_duration=10 / 30.0, fps=30.0, extend=True)
    assert frozen is True
    assert src == pytest.approx((10 / 30.0) - 1 / 30.0, abs=1e-9)

    b = Clip("s", 0.0, 10 / 30.0, 10 / 30.0)
    src, frozen = source_time_for_clip(
        b, 5 / 30.0, source_duration=10 / 30.0, fps=30.0, extend=True)
    assert frozen is True
    assert src == pytest.approx(0.0, abs=1e-9)


def test_q55_partial_handles_use_real_then_freeze():
    from cutora.model.timeline_model import Clip
    from cutora.render.transition_runtime import source_time_for_clip

    # out_t=0.2, source=1.0 → 0.8s extra after the trim.
    a = Clip("s", 0.0, 0.2, 0.0)
    src, frozen = source_time_for_clip(
        a, 0.5, source_duration=1.0, fps=30.0, extend=True)
    assert frozen is False
    assert src == pytest.approx(0.5, abs=1e-9)
    src, frozen = source_time_for_clip(
        a, 1.5, source_duration=1.0, fps=30.0, extend=True)
    assert frozen is True
    assert src == pytest.approx(1.0 - 1 / 30.0, abs=1e-9)


def test_q55_no_black_when_handles_short(tmp_path):
    _qapp()
    pa = str(tmp_path / "a.mp4")
    pb = str(tmp_path / "b.mp4")
    _color_mp4(pa, "0xC85028", frames=10)
    _color_mp4(pb, "0x2878C8", frames=10)
    task, tr, trans, *_ = _task_two_clips(pa, pb, frames_a=10, frames_b=10, trans_frames=20)
    fps = 30.0
    n = 20  # two 10-frame clips; t in [0, 20/30)
    from cutora.preview.engine import PreviewEngine
    eng = PreviewEngine()
    try:
        for i in range(n):
            t = i / fps
            # Video layer (fill 0) — export canvas is already black, so holes
            # only show up here as alpha < 255 / luma 0.
            video = eng.video_frame_at(task, t)
            arr = _qimage_rgba(video)
            assert arr.shape[2] == 4
            assert int(arr[..., 3].min()) == 255, (
                "frame %d alpha min=%d — freeze did not fill opaque"
                % (i, int(arr[..., 3].min()))
            )
            y = _luma(arr[..., :3])
            assert float(y.min()) > 0.0, (
                "frame %d min luma=%s — black hole in the transition"
                % (i, float(y.min()))
            )
    finally:
        eng.close()


def test_q55_enough_handles_byte_identical(tmp_path):
    """In-range overlapping clips: extend=True must not change source frames.

    Uses a fade (not a solid color) so seeking the wrong source time
    would actually change bytes. This is the 'enough handles, real frames'
    lock — not a full-export hash (old dual-alpha compose also leaked).
    """
    _qapp()
    pa = str(tmp_path / "a.mp4")
    pb = str(tmp_path / "b.mp4")
    _fade_red_mp4(pa, frames=60)
    _fade_red_mp4(pb, frames=60)
    task, tr, trans, ca, cb = _task_two_clips(
        pa, pb, frames_a=60, frames_b=60, trans_frames=15, overlap=True)
    from cutora.render.readers import _open_video_capture
    from cutora.render.render_worker import RenderWorker
    rw = RenderWorker.__new__(RenderWorker)
    caps = []
    try:
        mid = trans.start_t + trans.duration * 0.5
        def _grab(path, clip, extend):
            cap = _open_video_capture(path)
            assert cap is not None and cap.isOpened()
            caps.append(cap)
            st = {"last_time": -1.0, "last_frame": None}
            return rw._get_video_frame_at_time(cap, clip, mid, st, extend_handles=extend)
        fa0 = _grab(pa, ca, False)
        fa1 = _grab(pa, ca, True)
        fb0 = _grab(pb, cb, False)
        fb1 = _grab(pb, cb, True)
        assert fa0 is not None and fa1 is not None
        assert fb0 is not None and fb1 is not None
        import cv2
        probe = cv2.VideoCapture(pa)
        ok0, first = probe.read()
        probe.set(cv2.CAP_PROP_POS_FRAMES, 45)
        ok1, later = probe.read()
        probe.release()
        assert ok0 and ok1
        assert first.tobytes() != later.tobytes(), "fixture is solid — would hide extend bugs"
        assert fa0.tobytes() == fa1.tobytes()
        assert fb0.tobytes() == fb1.tobytes()
    finally:
        for cap in caps:
            cap.release()


def test_q55_freeze_uses_correct_frame(tmp_path):
    _qapp()
    pa = str(tmp_path / "a.mp4")
    pb = str(tmp_path / "b.mp4")
    _fade_red_mp4(pa, frames=10)
    _color_mp4(pb, "0x10A0A0", frames=10)
    task, tr, trans, ca, cb = _task_two_clips(
        pa, pb, frames_a=10, frames_b=10, trans_frames=20, preset="wipe_left")
    import cv2
    cap = cv2.VideoCapture(pa)
    assert cap.isOpened()
    frames = []
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        frames.append(fr.copy())
    cap.release()
    assert len(frames) >= 10
    a_first = frames[0]
    a_last = frames[-1]
    # After A ends, wipe_left right edge is A's freeze = last frame, not first.
    t = 15 / 30.0
    img = _preview_frame(task, t)
    arr = _qimage_rgba(img)
    col = arr[:, -2, :3]
    last_bgr = a_last[a_last.shape[0] // 2, a_last.shape[1] // 2]
    first_bgr = a_first[a_first.shape[0] // 2, a_first.shape[1] // 2]
    last_rgb = np.array([last_bgr[2], last_bgr[1], last_bgr[0]], dtype=np.int16)
    first_rgb = np.array([first_bgr[2], first_bgr[1], first_bgr[0]], dtype=np.int16)
    sample = col[col.shape[0] // 2].astype(np.int16)
    d_last = float(np.mean(np.abs(sample - last_rgb)))
    d_first = float(np.mean(np.abs(sample - first_rgb)))
    assert last_rgb[0] > first_rgb[0] + 20, (first_rgb.tolist(), last_rgb.tolist())
    assert d_last + 8 < d_first, (
        "A freeze matched first frame (d_last=%s d_first=%s sample=%s last=%s first=%s)"
        % (d_last, d_first, sample.tolist(), last_rgb.tolist(), first_rgb.tolist())
    )

    capb = cv2.VideoCapture(pb)
    ok, b0 = capb.read()
    capb.release()
    assert ok
    t = 5 / 30.0
    img = _preview_frame(task, t)
    arr = _qimage_rgba(img)
    left = arr[:, 1, :3]
    b0_rgb = np.array([b0[b0.shape[0] // 2, b0.shape[1] // 2][2],
                       b0[b0.shape[0] // 2, b0.shape[1] // 2][1],
                       b0[b0.shape[0] // 2, b0.shape[1] // 2][0]], dtype=np.int16)
    sample_l = left[left.shape[0] // 2].astype(np.int16)
    d_b0 = float(np.mean(np.abs(sample_l - b0_rgb)))
    assert d_b0 < 40, ("B freeze left edge not first frame d=%s sample=%s b0=%s"
                       % (d_b0, sample_l.tolist(), b0_rgb.tolist()))


# test_q55_preview_matches_export removed in Q68: PreviewEngine.video_frame_at
# already calls RenderWorker._paint_export_transition, so it compared a function
# to itself. Canvas vs export is test_q68_canvas_matches_export.


def test_q55_no_collateral_on_non_transition_exports(tmp_path):
    """No TransitionItem: in-range frames identical with/without extend,
    every frame opaque, seam is a cut not a blend.
    """
    _qapp()
    pa = str(tmp_path / "a.mp4")
    pb = str(tmp_path / "b.mp4")
    _fade_red_mp4(pa, frames=30)
    _color_mp4(pb, "0x2878C8", frames=30)
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, MediaSource, Track
    from cutora.render.readers import _open_video_capture
    from cutora.render.render_worker import RenderWorker
    from cutora.preview.engine import PreviewEngine

    fps = 30.0
    tl = Timeline(64, 64, fps)
    sa, sb = MediaSource(pa), MediaSource(pb)
    tl.sources[sa.id] = sa
    tl.sources[sb.id] = sb
    tr = Track("video")
    tr.id = "tv"
    ca = Clip(sa.id, 0.0, 30 / fps, 0.0)
    cb = Clip(sb.id, 0.0, 30 / fps, 30 / fps)
    tr.clips.extend([ca, cb])
    tl.tracks.append(tr)
    task = {
        "timeline": tl, "dims": (64, 64), "video": pa,
        "segments": [], "text_overlays": [], "image_overlays": [],
    }
    assert not getattr(tr, "transitions", [])

    rw = RenderWorker.__new__(RenderWorker)
    caps = []
    try:
        for clip, path, t in ((ca, pa, 10 / fps), (cb, pb, 40 / fps)):
            cap = _open_video_capture(path)
            assert cap is not None and cap.isOpened()
            caps.append(cap)
            st0 = {"last_time": -1.0, "last_frame": None}
            st1 = {"last_time": -1.0, "last_frame": None}
            f0 = rw._get_video_frame_at_time(cap, clip, t, st0, extend_handles=False)
            f1 = rw._get_video_frame_at_time(cap, clip, t, st1, extend_handles=True)
            assert f0 is not None and f1 is not None
            assert f0.tobytes() == f1.tobytes()
    finally:
        for cap in caps:
            cap.release()

    eng = PreviewEngine()
    try:
        for i in range(60):
            t = i / fps
            video = eng.video_frame_at(task, t)
            arr = _qimage_rgba(video)
            assert int(arr[..., 3].min()) == 255, i
            assert float(_luma(arr[..., :3]).min()) > 0.0, i
        # Seam is a hard cut: last A frame is red-ish, first B frame is blue-ish.
        a_arr = _qimage_rgba(eng.video_frame_at(task, 29 / fps))
        b_arr = _qimage_rgba(eng.video_frame_at(task, 30 / fps))
        a_r = float(a_arr[..., 0].mean())
        b_b = float(b_arr[..., 2].mean())
        assert a_r > 80, a_r
        assert b_b > 80, b_b
        assert _mad(a_arr[..., :3], b_arr[..., :3]) > 0.15
    finally:
        eng.close()


# ── library ───────────────────────────────────────────

def _solid(w, h, rgb):
    from PySide6.QtGui import QColor, QImage
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(*rgb))
    return img


def _halves(w, h, left_rgb, right_rgb):
    from PySide6.QtGui import QColor, QImage, QPainter
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    p = QPainter(img)
    try:
        p.fillRect(0, 0, w // 2, h, QColor(*left_rgb))
        p.fillRect(w // 2, 0, w - w // 2, h, QColor(*right_rgb))
    finally:
        p.end()
    return img


def _coord_pattern(w, h, seed):
    """Non-uniform field so scale/blur/wipe differ from a plain dissolve."""
    from PySide6.QtGui import QColor, QImage, QPainter
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    p = QPainter(img)
    try:
        for y in range(int(h)):
            for x in range(int(w)):
                p.setPen(QColor(
                    (x * 13 + seed) & 255,
                    (y * 7 + seed * 3) & 255,
                    (x + y * 2 + seed) & 255,
                ))
                p.drawPoint(x, y)
    finally:
        p.end()
    return img


def test_q55_each_transition_t0_t1_t05():
    _qapp()
    from cutora.render import anim_engine
    names = [p for _lab, p in anim_engine.STANDALONE_TRANSITION_LIBRARY]
    a = _coord_pattern(48, 32, 20)
    b = _coord_pattern(48, 32, 180)
    failed = []
    for preset in names:
        c0 = anim_engine.compose_standalone_transition(0.0, preset, a, b, 48, 32)
        c1 = anim_engine.compose_standalone_transition(1.0, preset, a, b, 48, 32)
        c5 = anim_engine.compose_standalone_transition(0.5, preset, a, b, 48, 32)
        if c0 is None or c0.isNull() or c1 is None or c1.isNull() or c5 is None or c5.isNull():
            failed.append((preset, "null"))
            continue
        a0 = _qimage_rgba(c0)
        a1 = _qimage_rgba(c1)
        a5 = _qimage_rgba(c5)
        d0 = _mad(a0[..., :3], _qimage_rgba(a)[..., :3])
        d1 = _mad(a1[..., :3], _qimage_rgba(b)[..., :3])
        d5a = _mad(a5[..., :3], _qimage_rgba(a)[..., :3])
        d5b = _mad(a5[..., :3], _qimage_rgba(b)[..., :3])
        if d0 > 0.04:
            failed.append((preset, "t0", d0))
        if d1 > 0.04:
            failed.append((preset, "t1", d1))
        if d5a < 0.01 and d5b < 0.01:
            failed.append((preset, "t05-still-a-or-b", d5a, d5b))
        if d5a < 0.008:
            failed.append((preset, "t05==A", d5a))
        if d5b < 0.008:
            failed.append((preset, "t05==B", d5b))
        if int(a5[..., 3].min()) != 255:
            failed.append((preset, "t05-alpha", int(a5[..., 3].min())))
    cross = anim_engine.compose_standalone_transition(0.5, "cross_dissolve", a, b, 48, 32)
    cross_arr = _qimage_rgba(cross)[..., :3]
    dissolve_ok = {
        "cross_dissolve", "cross_dissolve_out", "cross_dissolve_both",
        "cross_dissolve_in", "zoom_dissolve", "blur_dissolve",
    }
    for preset in names:
        if preset in dissolve_ok:
            continue
        c5 = anim_engine.compose_standalone_transition(0.5, preset, a, b, 48, 32)
        d = _mad(_qimage_rgba(c5)[..., :3], cross_arr)
        if d < 0.02:
            failed.append((preset, "t05==cross_dissolve_fallback", d))
    assert failed == [], failed


def test_q55_all_transitions_have_thumb():
    _qapp()
    from cutora.render.anim_engine import STANDALONE_TRANSITION_LIBRARY
    from cutora.ui.panels.transition_panel import TransitionPanel, VIDEO_ITEMS
    p = TransitionPanel()
    p.show()
    assert VIDEO_ITEMS == list(STANDALONE_TRANSITION_LIBRARY)
    assert p.video_count() == len(STANDALONE_TRANSITION_LIBRARY)
    from PySide6.QtGui import QPixmap
    blank = []
    for card in p._video_cards:
        thumb = card.thumb
        assert thumb is not None, card.preset
        pix = QPixmap(thumb.size())
        pix.fill("#2f3b4a")
        thumb._prog = 0.45
        thumb.render(pix)
        img = pix.toImage()
        arr = _qimage_rgba(img)
        # fallback empty fill is #2f3b4a; a live thumb must differ
        if float(np.mean(np.abs(arr[..., :3].astype(np.int16) - np.array([0x2f, 0x3b, 0x4a])))) < 2:
            blank.append(card.preset)
    assert blank == [], "blank thumbs: %s" % blank


# ── drag handles ──────────────────────────────────────

def _dump_task(task):
    from cutora.model.project_io import serialize_render_task
    return json.dumps(serialize_render_task(task), ensure_ascii=False,
                      sort_keys=True, separators=(",", ":"))


class _FakeWin:
    def __init__(self, task, timeline_widget):
        self.tasks = [task]
        self.curr_idx = 0
        self.project_path = "mem"
        from cutora.core.history import HistoryManager
        self.history_mgr = HistoryManager()
        self.timeline = timeline_widget

    def _require_unlocked(self, tr, track_id=None):
        return None

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

    def command_set_transition(self, **kwargs):
        from cutora.ui.command_bodies import CommandBodies
        return CommandBodies.command_set_transition(self, **kwargs)


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


def _bind_bus(win):
    from cutora.core.commands import get_bus, reset_bus_for_tests
    reset_bus_for_tests()
    bus = get_bus()
    bus.bind_window(win)
    return bus


def test_q55_drag_handle_changes_duration():
    _qapp()
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track, TransitionItem
    from cutora.ui.timeline.widget import TimelineWidget
    from cutora.model.project_io import serialize_render_task

    tl = Timeline(64, 64, 30)
    tr = Track("video")
    tr.id = "tv"
    tr.clips.extend([Clip("a", 0.0, 2.0, 0.0), Clip("b", 0.0, 2.0, 2.0)])
    trans = TransitionItem(start_t=1.825, duration=0.35, preset="cross_dissolve")
    tr.transitions.append(trans)
    tl.tracks.append(tr)
    task = {"timeline": tl, "segments": []}
    w = TimelineWidget()
    w.pixels_per_second = 100.0
    w.scroll_x = 0
    w.resize(800, 200)
    w.set_data(4000, task)
    w.show()
    win = _FakeWin(task, w)
    _bind_bus(win)
    orig_dur = float(trans.duration)
    orig_start = float(trans.start_t)
    row_y = w.RULER_H + w.TOP_PAD + 4
    right_x = (orig_start + orig_dur) * w.pixels_per_second
    _mouse(w, "press", right_x, row_y)
    _mouse(w, "move", right_x + 40, row_y)  # +0.4s
    _mouse(w, "release", right_x + 40, row_y)
    assert trans.duration > orig_dur + 0.2, (orig_dur, trans.duration)
    dumped = serialize_render_task(task)
    got = dumped["timeline"]["tracks"][0]["transitions"][0]["duration"]
    assert abs(float(got) - float(trans.duration)) < 1e-6


def test_q55_drag_one_undo():
    _qapp()
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track, TransitionItem
    from cutora.ui.timeline.widget import TimelineWidget

    tl = Timeline(64, 64, 30)
    tr = Track("video")
    tr.id = "tv"
    tr.clips.extend([Clip("a", 0.0, 2.0, 0.0), Clip("b", 0.0, 2.0, 2.0)])
    trans = TransitionItem(start_t=1.825, duration=0.35, preset="cross_dissolve")
    tr.transitions.append(trans)
    tl.tracks.append(tr)
    task = {"timeline": tl, "segments": []}
    w = TimelineWidget()
    w.pixels_per_second = 100.0
    w.scroll_x = 0
    w.resize(800, 200)
    w.set_data(4000, task)
    win = _FakeWin(task, w)
    _bind_bus(win)
    before = _dump_task(task)
    row_y = w.RULER_H + w.TOP_PAD + 4
    right_x = (trans.start_t + trans.duration) * w.pixels_per_second
    _mouse(w, "press", right_x, row_y)
    _mouse(w, "move", right_x + 50, row_y)
    _mouse(w, "release", right_x + 50, row_y)
    after = _dump_task(task)
    assert after != before
    win.undo()
    # undo restores tasks list; widget still holds the live trans object.
    restored = _dump_task(win.tasks[0])
    assert restored == before


def test_q55_drag_beyond_handles_marks_freeze(tmp_path):
    _qapp()
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, MediaSource, Track, TransitionItem
    from cutora.render.transition_runtime import transition_needs_freeze
    from cutora.ui.timeline.widget import TimelineWidget

    pa = str(tmp_path / "a.mp4")
    pb = str(tmp_path / "b.mp4")
    _color_mp4(pa, "0xC85028", frames=60)
    _color_mp4(pb, "0x2878C8", frames=60)
    sa, sb = MediaSource(pa), MediaSource(pb)
    tl = Timeline(64, 64, 30)
    tl.sources[sa.id] = sa
    tl.sources[sb.id] = sb
    tr = Track("video")
    tr.id = "tv"
    # A has 1s extra after out_t; B has 0.5s extra before in_t.
    tr.clips.extend([
        Clip(sa.id, 0.0, 1.0, 0.0),
        Clip(sb.id, 0.5, 1.5, 1.0),
    ])
    trans = TransitionItem(start_t=0.9, duration=0.2, preset="cross_dissolve")
    tr.transitions.append(trans)
    tl.tracks.append(tr)
    task = {"timeline": tl, "segments": []}
    assert not transition_needs_freeze(tr, trans, sources=tl.sources, fps=30.0)
    w = TimelineWidget()
    w.pixels_per_second = 80.0
    w.scroll_x = 0
    w.resize(800, 200)
    w.set_data(4000, task)
    win = _FakeWin(task, w)
    _bind_bus(win)
    row_y = w.RULER_H + w.TOP_PAD + 4
    right_x = (trans.start_t + trans.duration) * w.pixels_per_second
    _mouse(w, "press", right_x, row_y)
    _mouse(w, "move", right_x + 200, row_y)  # +2.5s, way past 1s clips
    _mouse(w, "release", right_x + 200, row_y)
    assert trans.duration > 1.0
    assert transition_needs_freeze(tr, trans, sources=tl.sources, fps=30.0)


def test_q55_write_perf_report(tmp_path):
    """Time each library entry at 1280×720; write the Q55 perf report."""
    _qapp()
    import time
    from cutora.render import anim_engine
    a = _solid(1280, 720, (200, 40, 40))
    b = _solid(1280, 720, (40, 160, 200))
    budget_ms = 1000.0 / 30.0
    rows = []
    for label, preset in anim_engine.STANDALONE_TRANSITION_LIBRARY:
        # warmup
        anim_engine.compose_standalone_transition(0.5, preset, a, b, 1280, 720)
        t0 = time.perf_counter()
        n = 8
        for i in range(n):
            anim_engine.compose_standalone_transition(
                (i + 1) / (n + 1), preset, a, b, 1280, 720)
        ms = (time.perf_counter() - t0) * 1000.0 / n
        note = "仅导出" if ms > budget_ms else "预览+导出"
        rows.append((label, preset, ms, note))
    from cutora.render.transitions import EXPORT_ONLY_PRESETS
    EXPORT_ONLY_PRESETS.clear()
    for _lab, preset, ms, note in rows:
        if note == "仅导出":
            EXPORT_ONLY_PRESETS.add(preset)
    dest = os.path.join(REPO, "docs", "plan", "reports", "Q55_transition_perf.md")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    lines = [
        "# Q55 转场单帧耗时",
        "",
        "画布 1280×720，每项 1 次预热 + 8 次计时。单帧预算 33.3 ms（30 fps）。",
        "",
        "| 名称 | preset | ms/帧 | 路径 |",
        "|---|---|---:|---|",
    ]
    for label, preset, ms, note in rows:
        lines.append("| %s | `%s` | %.2f | %s |" % (label, preset, ms, note))
    lines.append("")
    open(dest, "w", encoding="utf-8").write("\n".join(lines))
    assert os.path.isfile(dest)
    assert rows, "no transitions timed"

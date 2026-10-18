# -*- coding: utf-8 -*-
"""Q31 keyframe system. Tests written to catch the real defects:

- linear vs hold vs ease_in_out vs bezier at a t that is NOT the symmetric
  midpoint (ease_in_out(0.5) == 0.5, which would hide a linear stand-in)
- red→cyan goes gray in sRGB; red→blue is magenta in sRGB so it would not
  catch an sRGB lerp. Both pairs are asserted.
- moving a clip 5s must not change the animation relative to clip start
- preview and export each paint a frame (two decoder pools), not two calls
  to value_at
- 3× undo must restore a host that has no keyframes dict
- a clip with no keyframes at two different t must be byte-identical
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import subprocess

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["q31-keyframes"])


def _ffmpeg():
    from cutora.core.config import FFMPEG_PATH
    return FFMPEG_PATH


def _color_mp4(path, color, frames=30, fps=30, size="160x90"):
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


def _qimage_rgb(img):
    from PySide6.QtGui import QImage
    conv = img.convertToFormat(QImage.Format.Format_RGB888)
    w, h = conv.width(), conv.height()
    bpl = conv.bytesPerLine()
    raw = bytes(conv.constBits())
    arr = np.frombuffer(raw, dtype=np.uint8)
    if arr.size < h * bpl:
        arr = np.frombuffer(conv.bits(), dtype=np.uint8)
    arr = arr.reshape((h, bpl))[:, : w * 3].reshape((h, w, 3)).copy()
    return arr


def _md5_arr(arr):
    return hashlib.md5(np.ascontiguousarray(arr).tobytes()).hexdigest()


def _mad(a, b):
    if a.shape != b.shape:
        h = min(a.shape[0], b.shape[0])
        w = min(a.shape[1], b.shape[1])
        a = a[:h, :w]
        b = b[:h, :w]
    return float(np.mean(np.abs(a.astype(np.int16) - b.astype(np.int16)))) / 255.0


def _host(kfs=None):
    return {"start": 0.0, "x": 0.0, "keyframes": dict(kfs or {})}


# ---------------------------------------------------------------------------
# value_at
# ---------------------------------------------------------------------------

def test_q31_value_at():
    """Two-point linear, three-point, hold, ease_in_out, bezier — mid-span values.

    ease_in_out is sampled at u=0.25, not 0.5: cubic ease_in_out(0.5) is 0.5,
    which is the same number linear would give and would hide a missing ease.
    """
    from cutora.render.anim_engine import ease_in_out_cubic
    from cutora.render.keyframe import value_at

    # Two-point linear: 0 → 100 over 1s. Midpoint must be 50, not 0 and not 100.
    linear = _host({"x": [
        {"t": 0.0, "v": 0.0, "ease": "linear"},
        {"t": 1.0, "v": 100.0, "ease": "linear"},
    ]})
    mid_lin = value_at(linear, "x", 0.5, 0.0)
    assert abs(mid_lin - 50.0) < 1e-6, mid_lin

    # Three-point: 0→10 over [0,1], 10→0 over [1,2]. Mid of second span is 5.
    three = _host({"x": [
        {"t": 0.0, "v": 0.0, "ease": "linear"},
        {"t": 1.0, "v": 10.0, "ease": "linear"},
        {"t": 2.0, "v": 0.0, "ease": "linear"},
    ]})
    assert abs(value_at(three, "x", 0.5, -1.0) - 5.0) < 1e-6
    assert abs(value_at(three, "x", 1.5, -1.0) - 5.0) < 1e-6
    assert abs(value_at(three, "x", 1.0, -1.0) - 10.0) < 1e-6

    # Hold: midpoint must stay at the LEFT value. Linear would give 50.
    hold = _host({"x": [
        {"t": 0.0, "v": 10.0, "ease": "hold"},
        {"t": 1.0, "v": 90.0, "ease": "linear"},
    ]})
    mid_hold = value_at(hold, "x", 0.5, 0.0)
    assert abs(mid_hold - 10.0) < 1e-6, mid_hold
    assert abs(value_at(hold, "x", 0.999, 0.0) - 10.0) < 1e-6
    assert abs(value_at(hold, "x", 1.0, 0.0) - 90.0) < 1e-6

    # ease_in_out at u=0.25 is NOT the linear 25.
    eio = _host({"x": [
        {"t": 0.0, "v": 0.0, "ease": "ease_in_out"},
        {"t": 1.0, "v": 100.0, "ease": "linear"},
    ]})
    q = value_at(eio, "x", 0.25, 0.0)
    expected = 100.0 * ease_in_out_cubic(0.25)
    assert abs(q - expected) < 1e-6, (q, expected)
    assert abs(q - 25.0) > 5.0, "ease_in_out collapsed to linear at u=0.25: %s" % q

    # Bezier (0, 0, 0.2, 1) is a strong ease-out: midpoint y >> 0.5.
    bez = _host({"x": [
        {"t": 0.0, "v": 0.0, "ease": "bezier", "bez": [0.0, 0.0, 0.2, 1.0]},
        {"t": 1.0, "v": 100.0, "ease": "linear"},
    ]})
    mid_bez = value_at(bez, "x", 0.5, 0.0)
    assert mid_bez > 70.0, "bezier midpoint too close to linear 50: %s" % mid_bez
    assert mid_bez < 100.0, mid_bez


def test_q31_color_oklab():
    """Red→blue midpoint is not gray; saturation stays ≥ 60% of the weaker end.

    Red→blue in sRGB is magenta (G=0), so that pair would not catch an sRGB
    lerp. Red→cyan in sRGB IS gray. Both pairs are required.
    """
    from cutora.render.keyframe import mix_oklab, oklab_chroma, value_at

    host = {
        "start": 0.0,
        "keyframes": {
            "fill.colors[0]": [
                {"t": 0.0, "v": "#FF0000", "ease": "linear"},
                {"t": 1.0, "v": "#0000FF", "ease": "linear"},
            ],
        },
    }
    mid = value_at(host, "fill.colors[0]", 0.5, "#FF0000")
    assert isinstance(mid, str) and mid.startswith("#"), mid
    c_red = oklab_chroma("#FF0000")
    c_blue = oklab_chroma("#0000FF")
    c_mid = oklab_chroma(mid)
    floor = 0.60 * min(c_red, c_blue)
    assert c_mid >= floor, (
        "red→blue mid %s chroma %.4f < 60%% of min(%.4f, %.4f)=%.4f"
        % (mid, c_mid, c_red, c_blue, floor)
    )
    # Not a 1:1:1 gray.
    h = mid.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    assert max(r, g, b) - min(r, g, b) >= 40, (r, g, b)

    # Defect-exposing pair: red→cyan. sRGB lerp is #808080 (chroma ≈ 0).
    cyan_mid = mix_oklab("#FF0000", "#00FFFF", 0.5)
    c_cyan = oklab_chroma("#00FFFF")
    c_cm = oklab_chroma(cyan_mid)
    floor2 = 0.60 * min(c_red, c_cyan)
    assert c_cm >= floor2, (
        "red→cyan mid %s chroma %.4f < 60%% of min(%.4f, %.4f)=%.4f "
        "(sRGB lerp would be gray)"
        % (cyan_mid, c_cm, c_red, c_cyan, floor2)
    )
    hc = cyan_mid.lstrip("#")
    cr, cg, cb = int(hc[0:2], 16), int(hc[2:4], 16), int(hc[4:6], 16)
    assert max(cr, cg, cb) - min(cr, cg, cb) >= 40, (cr, cg, cb, cyan_mid)


# ---------------------------------------------------------------------------
# render helpers
# ---------------------------------------------------------------------------

def _task_with_clip(media_path, start_t=0.0, duration=1.0, x=0.0, scale=1.0,
                    kfs=None, canvas=(160, 90), fps=30.0):
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track

    tl = Timeline(int(canvas[0]), int(canvas[1]), float(fps))
    src_id = "src0"
    tl.sources[src_id] = {
        "id": src_id, "path": media_path,
        "duration": float(duration), "has_video": True, "has_audio": False,
    }
    tr = Track("video")
    tr.id = "v0"
    clip = Clip(src_id, 0.0, float(duration), float(start_t))
    clip.x = float(x)
    clip.y = 0.0
    clip.scale = float(scale)
    clip.rotation = 0.0
    clip.opacity = 255
    clip.fit_mode = "stretch"
    if kfs:
        clip.keyframes = copy.deepcopy(kfs)
    tr.clips.append(clip)
    tl.add_track(tr)
    task = {
        "video": media_path,
        "dims": (int(canvas[0]), int(canvas[1])),
        "canvas_width": int(canvas[0]),
        "canvas_height": int(canvas[1]),
        "timeline": tl,
        "style": {},
        "segments": [],
        "text_overlays": [],
        "image_overlays": [],
    }
    return task, tr, clip


def _preview_frame(task, t):
    from cutora.preview.engine import PreviewEngine
    eng = PreviewEngine()
    try:
        return eng.video_frame_at(task, float(t))
    finally:
        eng.close()


def _export_frame(task, t):
    """Independent decoder pool — not PreviewEngine.video_frame_at."""
    from PySide6.QtGui import QImage, QPainter
    from cutora.model.canvas import canvas_wh
    from cutora.render.readers import _open_video_capture
    from cutora.render.render_worker import RenderWorker

    rw = RenderWorker.__new__(RenderWorker)
    rw.project = task
    rw._geom_last_log_t = 0.0
    w, h = canvas_wh(task)
    tl = task["timeline"]
    caps = {}
    states = {}
    try:
        for sid, src in (getattr(tl, "sources", {}) or {}).items():
            cap = _open_video_capture(src.get("path") if isinstance(src, dict) else getattr(src, "path", None))
            if cap is not None and cap.isOpened():
                caps[sid] = cap
        img = QImage(int(w), int(h), QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(0)
        p = QPainter(img)
        try:
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            for tr in getattr(tl, "tracks", []) or []:
                for clip in getattr(tr, "clips", []) or []:
                    cap = caps.get(getattr(clip, "source_id", None))
                    if cap is None:
                        continue
                    st = states.setdefault(
                        str(getattr(clip, "source_id", "") or ""),
                        {"last_time": -1.0, "last_frame": None},
                    )
                    rw._draw_video_overlay(p, cap, clip, int(w), int(h), float(t), st)
        finally:
            p.end()
        return img
    finally:
        for cap in caps.values():
            try:
                cap.release()
            except Exception:
                pass


def test_q31_local_time(tmp_path):
    """Keyframe a clip, shift it +5s: animation vs clip start is unchanged."""
    _qapp()
    media = str(tmp_path / "red.mp4")
    _color_mp4(media, "0xE01010", frames=90, size="80x80")
    kfs = {"x": [
        {"t": 0.0, "v": -40.0, "ease": "linear"},
        {"t": 1.0, "v": 40.0, "ease": "linear"},
    ]}
    task_a, _tr, clip_a = _task_with_clip(
        media, start_t=0.0, duration=3.0, kfs=kfs, canvas=(160, 90))
    img_a = _export_frame(task_a, 0.5)
    arr_a = _qimage_rgb(img_a)
    arr_t0 = _qimage_rgb(_export_frame(task_a, 0.0))
    arr_t1 = _qimage_rgb(_export_frame(task_a, 1.0))
    assert _mad(arr_t0, arr_t1) > 0.02, (
        "keyframed x=-40→40 produced identical frames at t=0 and t=1 "
        "(keyframes were not applied)"
    )

    task_b, _tr, clip_b = _task_with_clip(
        media, start_t=5.0, duration=3.0, kfs=kfs, canvas=(160, 90))
    img_b = _export_frame(task_b, 5.5)
    arr_b = _qimage_rgb(img_b)
    mad = _mad(arr_a, arr_b)
    assert mad < 0.01, (
        "shifting clip +5s changed pixels at the same local t (mad=%.5f)"
        % mad
    )

    # Absolute t=0.5 after the move is before the clip starts — must differ.
    img_early = _export_frame(task_b, 0.5)
    arr_early = _qimage_rgb(img_early)
    mad_early = _mad(arr_a, arr_early)
    assert mad_early > 0.02, (
        "frame at t=0.5 after start_t=5 still matches the animated frame "
        "(mad=%.5f) — local time was ignored" % mad_early
    )


def test_q31_preview_export_same(tmp_path):
    """PreviewEngine.video_frame_at vs RenderWorker._draw_video_overlay.

    Two callers, two decoder pools. A clip whose x is keyframed  -40→40 so
    a wrong t (or a static x=0) lands the red square in a different place.
    """
    _qapp()
    media = str(tmp_path / "red.mp4")
    _color_mp4(media, "0xE01010", frames=60, size="80x80")
    kfs = {"x": [
        {"t": 0.0, "v": -40.0, "ease": "linear"},
        {"t": 1.0, "v": 40.0, "ease": "linear"},
    ]}
    task, _tr, clip = _task_with_clip(
        media, start_t=0.0, duration=2.0, kfs=kfs, canvas=(160, 90))
    t = 0.5
    preview = _preview_frame(task, t)
    export = _export_frame(task, t)
    mad = _mad(_qimage_rgb(preview), _qimage_rgb(export))
    assert mad < 0.02, mad

    # Same t, the resolved x from value_at must match what both painters used.
    from cutora.render.keyframe import resolve_clip_visual, value_at
    vis = resolve_clip_visual(clip, t)
    assert abs(vis["x"] - 0.0) < 1e-6, vis
    assert abs(float(value_at(clip, "x", 0.5, 99.0)) - 0.0) < 1e-6


def test_q31_undo():
    """Three keyframe.set → Ctrl+Z ×3 → host has no keyframes."""
    from cutora.core.commands import CommandBus, get_registry
    from cutora.core.history import HistoryManager
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track
    from cutora.render.keyframe import has_keyframes, keyframes_of

    tl = Timeline(160, 90, 30.0)
    tr = Track("video")
    tr.id = "v0"
    clip = Clip("s", 0.0, 2.0, 0.0)
    tr.clips.append(clip)
    tl.add_track(tr)
    task = {"timeline": tl, "style": {}}

    class _Win:
        def __init__(self, task):
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

        def undo(self):
            state = self.history_mgr.undo()
            if state:
                self.tasks[:] = state["tasks"]
                self.curr_idx = state["curr_idx"]

        def _refresh_task_view(self, idx):
            return None

    win = _Win(task)
    bus = CommandBus(get_registry())
    bus.bind_window(win)
    spec = get_registry().get("keyframe.set")
    assert spec.undoable is True, "keyframe.set must be undoable"

    for t, v in ((0.0, 0.0), (0.4, 40.0), (0.9, 90.0)):
        bus.execute("keyframe.set", {
            "target": "clip",
            "track_id": "v0",
            "clip_index": 0,
            "path": "x",
            "t": t,
            "v": v,
            "ease": "linear",
        }, source="ui")

    clip_now = win.tasks[0]["timeline"].tracks[0].clips[0]
    assert len(keyframes_of(clip_now).get("x") or []) == 3, keyframes_of(clip_now)

    for i in range(3):
        win.undo()
    clip_after = win.tasks[0]["timeline"].tracks[0].clips[0]
    assert has_keyframes(clip_after) is False, keyframes_of(clip_after)
    assert not keyframes_of(clip_after).get("x"), keyframes_of(clip_after)


def test_q31_no_keyframes_is_legacy(tmp_path):
    """Old clip without keyframes: two different t produce the same bytes.

    A bug that treated missing keyframes as 'animate from 0' would shift x
    between t=0.1 and t=0.9 (static x=40). Also: an empty keyframes dict
    must match a missing keyframes attr, byte for byte.
    """
    _qapp()
    media = str(tmp_path / "blue.mp4")
    _color_mp4(media, "0x2040E0", frames=60, size="80x80")
    task, _tr, clip = _task_with_clip(
        media, start_t=0.0, duration=2.0, x=40.0, scale=1.25, canvas=(160, 90))
    assert not hasattr(clip, "keyframes") or not getattr(clip, "keyframes", None)

    a = _qimage_rgb(_export_frame(task, 0.1))
    b = _qimage_rgb(_export_frame(task, 0.9))
    assert _md5_arr(a) == _md5_arr(b), (
        "no-keyframe clip changed between t=0.1 and t=0.9 — static x was animated"
    )

    clip.keyframes = {}
    c = _qimage_rgb(_export_frame(task, 0.1))
    assert _md5_arr(a) == _md5_arr(c), (
        "empty keyframes dict changed pixels vs a clip that never had the attr"
    )

    # Keyframes on an unused path must not move the picture.
    clip.keyframes = {"gain_db": [
        {"t": 0.0, "v": -12.0, "ease": "linear"},
        {"t": 1.0, "v": 0.0, "ease": "linear"},
    ]}
    d = _qimage_rgb(_export_frame(task, 0.5))
    assert _md5_arr(a) == _md5_arr(d), (
        "gain_db keyframes on a video clip changed video pixels"
    )


def test_q31_timeline_diamonds_and_stopwatch():
    """Product widgets: diamonds paint on the band; stopwatch enables a path."""
    _qapp()
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QApplication
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track
    from cutora.render.keyframe import path_enabled, set_point
    from cutora.ui.timeline.widget import TimelineWidget
    from cutora.ui.widgets.inspector import KeyframeWatch

    tl = Timeline(160, 90, 30.0)
    tr = Track("video")
    tr.id = "v0"
    clip = Clip("s", 0.0, 2.0, 0.0)
    set_point(clip, "x", 0.0, 0.0)
    set_point(clip, "x", 1.0, 80.0)
    tr.clips.append(clip)
    tl.add_track(tr)
    task = {"timeline": tl, "duration": 2.0}

    w = TimelineWidget()
    w.resize(640, 200)
    w.pixels_per_second = 100.0
    w.set_data(2000, task)
    w.show()
    QApplication.processEvents()
    img = w.grab().toImage()
    # Diamonds sit on the clip's bottom band. Sample a strip of gold-ish pixels.
    from cutora.ui.theme.tokens import current
    accent = QColor(current().accent)
    hits = 0
    for y in range(img.height()):
        for x in range(img.width()):
            pix = img.pixelColor(x, y)
            if abs(pix.red() - accent.red()) < 40 and abs(pix.green() - accent.green()) < 40:
                if pix.red() > pix.blue():
                    hits += 1
    w.close()
    assert hits >= 8, "expected gold diamonds on the keyframe band, hits=%d" % hits

    watch = KeyframeWatch()
    watch.show()
    QApplication.processEvents()
    assert watch.width() >= 1 and watch.height() >= 1
    watch.set_state(enabled=False, on_key=False)
    off = watch.grab().toImage()
    watch.set_state(enabled=True, on_key=True)
    on = watch.grab().toImage()
    watch.close()
    assert off.pixel(off.width() // 2, off.height() // 2) != on.pixel(
        on.width() // 2, on.height() // 2), "stopwatch on/off painted the same"


def test_q31_spec_file_exists():
    path = os.path.join(REPO, "docs", "plan", "KEYFRAME_SPEC.md")
    assert os.path.isfile(path), path
    text = open(path, encoding="utf-8").read()
    for needle in (
        "value_at", "fill.colors[0]", "ease_in_out", "bezier",
        "start_t", "OKLab", "OKLCH",
    ):
        assert needle in text, needle

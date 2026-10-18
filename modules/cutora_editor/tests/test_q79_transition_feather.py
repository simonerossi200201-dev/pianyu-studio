# -*- coding: utf-8 -*-
"""Q79: wipe angle actually tilts 「擦」; edged transitions take a feather."""
from __future__ import annotations

import hashlib
import os
import subprocess

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# t=0.5, 96×64, pattern() below, captured before the Q79 feather/angle change.
# feather=0 (and the omitted default) must stay byte-identical.
_BASELINE_MD5 = {
    "cross_dissolve": "25c52eadb0aee279eb23d2dda1553fd7",
    "fade_dark": "9c3a421f17f82ed4bd46271e4d026a85",
    "fade_white": "5c65ae66b72ed07bba39174eb31762ed",
    "push_left": "3749f7c04b0a68ac00b96c35ff9fc167",
    "push_right": "c9320f7fd78faf0c03bdb7a4b6ce69c5",
    "push_up": "533e4b8fe5b93b35c07fb46b8d800ef9",
    "push_down": "769f5cde35f16f086bb30ef8ee9723a9",
    "wipe_left": "25ef60836e965cb4d81bec467b6b3ad9",
    "zoom_dissolve": "5cdea77e68a19c1d1f4ff5ca5eaf6d02",
    "blur_dissolve": "163c321e1b1b8f2489eee6865715c249",
    "flash_white": "5c65ae66b72ed07bba39174eb31762ed",
    "push_out_left": "7eb409372f06261c3b93bbcf546f8036",
    "push_out_right": "5a9d0aed2b5b4b1a116105c2f5abca0a",
    "push_out_up": "16b381ae4acd6d5a78305a56a5448a91",
    "push_out_down": "5f58634c059fe69fa43be023de8bac2f",
    "push_split": "5786ed229e56c9a81fcb3f17c4ce7867",
    "smash_zoom": "eadc6af988db7ad49eea575b92958da1",
    "zoom_out_trans": "2e8be719d6676e0cbe5a2d25292d1f78",
    "wipe_angle": "d8e2e407d3fff61474efac55a8404f44",
    "wipe_circle": "fa8af850dc277d7937a0db58990a70e5",
    "wipe_clock": "5ca6b7a4eb720bc6736555ea52a134bb",
    "wipe_blinds": "154507e95a9b5c33a02773095b263a61",
    "rotate_dissolve": "f301ed408bfbe4c0767d826d8a2d2667",
    "page_turn": "9761a67f4837f025e2ebf8f1e1f65284",
    "cube_spin": "95d89ce48c65de38943dc1632326c433",
    "flash_black": "9c3a421f17f82ed4bd46271e4d026a85",
    "exposure_dissolve": "efeb40ab945a8c38b7ffb2fb4f671e18",
    "bloom_dissolve": "3891ebf1e023f525dfbbf01dc66a319a",
    "rgb_split": "1f906bfff5629f4255497b76b329d97a",
    "blur_directional": "295e8a0b221a80d662c6a95c27f81221",
    "blur_radial": "e2d863692a4abf6f94117dcfdc6d0c13",
    "bokeh_dissolve": "9f806d9df921412af5b2f515506c1cb9",
    "glitch": "adb1543241c6695797eb2046bb34f618",
    "pixelate": "9f434faf8b38ca58e61848756f658367",
    "scanlines": "fb328c202de14018b3ad4c6a47ed8c1c",
}

# The task names these families. A registry that drops one of them is a miss.
_REQUIRED_EDGED = (
    "wipe_left", "wipe_angle", "wipe_circle", "wipe_clock", "wipe_blinds",
    "push_left", "push_right", "push_up", "push_down",
    "push_out_left", "push_out_right", "push_out_up", "push_out_down",
    "push_split", "page_turn", "cube_spin",
)


def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["q79-feather"])


def _qimage_rgba(img):
    from PySide6.QtGui import QImage
    conv = img.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = conv.width(), conv.height()
    bpl = conv.bytesPerLine()
    raw = bytes(conv.constBits())
    arr = np.frombuffer(raw, dtype=np.uint8)
    if arr.size < h * bpl:
        arr = np.frombuffer(conv.bits(), dtype=np.uint8)
    return arr.reshape((h, bpl))[:, : w * 4].reshape((h, w, 4)).copy()


def _tight_md5(img) -> str:
    arr = _qimage_rgba(img)
    return hashlib.md5(arr.tobytes()).hexdigest()


def _mad(a, b):
    if a.shape != b.shape:
        h = min(a.shape[0], b.shape[0])
        w = min(a.shape[1], b.shape[1])
        a = a[:h, :w]
        b = b[:h, :w]
    return float(np.mean(np.abs(a.astype(np.int16) - b.astype(np.int16)))) / 255.0


def _pattern(w, h, seed):
    from PySide6.QtGui import QColor, QImage, QPainter
    img = QImage(int(w), int(h), QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(12 + int(seed), 18, 28))
    p = QPainter(img)
    p.fillRect(0, 0, int(w) // 2, int(h), QColor(
        220 if seed == 0 else 20,
        30 if seed == 0 else 180,
        40 if seed == 0 else 90))
    p.fillRect(int(w) // 3, int(h) // 5, int(w) // 4, int(h) // 3, QColor(250, 220, 40))
    p.fillRect(int(w) // 2, int(h) // 2, int(w) // 3, int(h) // 4, QColor(20, 200, 220))
    p.end()
    return img


def _solid(w, h, rgb):
    from PySide6.QtGui import QColor, QImage
    img = QImage(int(w), int(h), QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(*rgb))
    return img


def _paint(track, trans, t, w, h, img_a, img_b):
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


def _boundary_x(rgb, y):
    """First column that is closer to red (A) than blue (B). B wipes in from the left."""
    row = rgb[int(y)]
    red = row[:, 0].astype(np.int16)
    blue = row[:, 2].astype(np.int16)
    hit = np.flatnonzero(red > blue)
    if hit.size == 0:
        return int(row.shape[0])
    return int(hit[0])


def _max_hdiff(rgb, y):
    row = rgb[int(y), :, :3].astype(np.int16)
    d = np.abs(np.diff(row, axis=0))
    return int(d.max()) if d.size else 0


def _ffmpeg():
    from cutora.core.paths import FFMPEG
    return FFMPEG


def _color_mp4(path, color, frames=12, fps=24, size="80x48"):
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


def _two_clip_task(path_a, path_b, preset, fps=24.0, canvas=(80, 48)):
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, MediaSource, Track, TransitionItem
    w, h = canvas
    tl = Timeline(w, h, fps)
    sa, sb = MediaSource(path_a), MediaSource(path_b)
    tl.sources[sa.id] = sa
    tl.sources[sb.id] = sb
    tr = Track("video")
    tr.id = "q79"
    tr.clips.extend([
        Clip(sa.id, 0.0, 0.5, 0.0),
        Clip(sb.id, 0.0, 0.5, 0.5),
    ])
    trans = TransitionItem(start_t=0.25, duration=0.5, preset=preset)
    tr.transitions.append(trans)
    tl.tracks.append(tr)
    task = {"timeline": tl, "dims": (w, h), "video": path_a, "segments": []}
    return task, tl, tr, trans


def _canvas_and_export(task, tl, tr, trans, t, w, h):
    """Preview entry (VideoCanvas) and export entry (RenderWorker). Not one helper twice."""
    from PySide6.QtGui import QImage, QPainter
    from cutora.render.readers import _open_video_capture
    from cutora.render.render_worker import RenderWorker
    from cutora.ui.canvas import VideoCanvas

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
    fps = float(getattr(tl, "fps", 24.0) or 24.0)
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
        return canvas_img, export
    finally:
        for cap in caps.values():
            cap.release()
        for cap in list(getattr(canvas, "_cv_caps", {}).values()):
            try:
                cap.release()
            except Exception:
                pass


def test_q79_wipe_angle_changes_pixels(tmp_path):
    """「擦」/wipe_left: angle 0° is a vertical edge; 45° tilts by tan(45°).

    The angle spin is what the property panel writes. Preview (canvas) and
    export (RenderWorker) must both show the tilt.
    """
    _qapp()
    from cutora.model.timeline_model import Clip, Track, TransitionItem
    from cutora.ui.panels.property import PropertyPanel

    w, h = 160, 80
    a = _solid(w, h, (255, 0, 0))
    b = _solid(w, h, (0, 0, 255))
    tr = Track("video")
    tr.clips.extend([Clip("a", 0.0, 2.0, 0.0), Clip("b", 0.0, 2.0, 1.0)])
    trans = TransitionItem(start_t=0.65, duration=0.7, preset="wipe_left")
    tr.transitions.append(trans)
    panel = PropertyPanel()
    panel.show()
    panel.load_data("transition", (tr, trans))
    from cutora.ui.widgets.inspector import inspector_row_of
    row = inspector_row_of(panel._trans_angle_host)
    assert row is not None and row.isVisible(), "擦 must show the angle row"

    panel.trans_angle_sp.setValue(0.0)
    assert float(getattr(trans, "angle", -1)) == pytest.approx(0.0, abs=1e-6)
    t_mid = trans.start_t + trans.duration * 0.5
    img0 = _paint(tr, trans, t_mid, w, h, a, b)
    panel.trans_angle_sp.setValue(45.0)
    assert float(trans.angle) == pytest.approx(45.0, abs=1e-6)
    img45 = _paint(tr, trans, t_mid, w, h, a, b)
    rgb0 = _qimage_rgba(img0)[..., :3]
    rgb45 = _qimage_rgba(img45)[..., :3]
    assert _mad(rgb0, rgb45) > 0.04, _mad(rgb0, rgb45)

    y0, y1 = 8, 56
    x0_v, x1_v = _boundary_x(rgb0, y0), _boundary_x(rgb0, y1)
    assert abs(x0_v - x1_v) <= 1, (x0_v, x1_v)
    x0, x1 = _boundary_x(rgb45, y0), _boundary_x(rgb45, y1)
    expect = abs(y1 - y0) * np.tan(np.deg2rad(45.0))
    assert abs(abs(x1 - x0) - expect) <= 3.0, (x0, x1, expect)

    # Preview canvas vs export worker, same TransitionItem the panel just wrote.
    pa = str(tmp_path / "a.mp4")
    pb = str(tmp_path / "b.mp4")
    _color_mp4(pa, "red")
    _color_mp4(pb, "blue")
    task, tl, trv, tv = _two_clip_task(pa, pb, "wipe_left")
    tv.angle = 0.0
    t = tv.start_t + tv.duration * 0.5
    c0, e0 = _canvas_and_export(task, tl, trv, tv, t, 80, 48)
    tv.angle = 45.0
    c45, e45 = _canvas_and_export(task, tl, trv, tv, t, 80, 48)
    assert _mad(_qimage_rgba(c45)[..., :3], _qimage_rgba(e45)[..., :3]) < 0.04
    assert _mad(_qimage_rgba(c0)[..., :3], _qimage_rgba(c45)[..., :3]) > 0.04
    assert _mad(_qimage_rgba(e0)[..., :3], _qimage_rgba(e45)[..., :3]) > 0.04


def test_q79_feather_softens_edge(tmp_path):
    """Feather 0% is a hard step; 30% of the short side spreads that step."""
    _qapp()
    from cutora.model.timeline_model import Clip, Track, TransitionItem
    from cutora.ui.panels.property import PropertyPanel

    w, h = 160, 80
    a = _solid(w, h, (255, 0, 0))
    b = _solid(w, h, (0, 0, 255))
    tr = Track("video")
    tr.clips.extend([Clip("a", 0.0, 2.0, 0.0), Clip("b", 0.0, 2.0, 1.0)])
    trans = TransitionItem(start_t=0.65, duration=0.7, preset="wipe_left")
    tr.transitions.append(trans)
    panel = PropertyPanel()
    panel.load_data("transition", (tr, trans))
    t_mid = trans.start_t + trans.duration * 0.5
    panel.trans_feather.setValue(0)
    assert float(trans.feather) == pytest.approx(0.0, abs=1e-6)
    hard = _qimage_rgba(_paint(tr, trans, t_mid, w, h, a, b))
    panel.trans_feather.setValue(30)
    assert float(trans.feather) == pytest.approx(30.0, abs=1e-6)
    soft = _qimage_rgba(_paint(tr, trans, t_mid, w, h, a, b))
    y = h // 2
    hard_step = _max_hdiff(hard, y)
    soft_step = _max_hdiff(soft, y)
    assert hard_step >= 200, hard_step
    assert soft_step < hard_step * 0.35, (soft_step, hard_step)
    # Still a wipe, not a flat frame: left side is B, right side is A.
    assert int(soft[y, 4, 2]) > int(soft[y, 4, 0]) + 40
    assert int(soft[y, -5, 0]) > int(soft[y, -5, 2]) + 40

    pa = str(tmp_path / "a.mp4")
    pb = str(tmp_path / "b.mp4")
    _color_mp4(pa, "red")
    _color_mp4(pb, "blue")
    task, tl, trv, tv = _two_clip_task(pa, pb, "wipe_left")
    t = tv.start_t + tv.duration * 0.5
    tv.feather = 0.0
    c0, e0 = _canvas_and_export(task, tl, trv, tv, t, 80, 48)
    tv.feather = 30.0
    c30, e30 = _canvas_and_export(task, tl, trv, tv, t, 80, 48)
    assert _mad(_qimage_rgba(c30)[..., :3], _qimage_rgba(e30)[..., :3]) < 0.04
    assert _mad(_qimage_rgba(c0)[..., :3], _qimage_rgba(c30)[..., :3]) > 0.02
    assert _mad(_qimage_rgba(e0)[..., :3], _qimage_rgba(e30)[..., :3]) > 0.02


def test_q79_feather_zero_is_unchanged():
    """feather omitted and feather=0 match the pre-change frame, byte for byte."""
    _qapp()
    from cutora.render import anim_engine
    a = _pattern(96, 64, 0)
    b = _pattern(96, 64, 1)
    lib = [v for _, v in anim_engine.STANDALONE_TRANSITION_LIBRARY]
    assert set(lib) == set(_BASELINE_MD5)
    bad = []
    for preset in lib:
        bare = anim_engine.compose_standalone_transition(0.5, preset, a, b, 96, 64)
        zero = anim_engine.compose_standalone_transition(
            0.5, preset, a, b, 96, 64, feather=0)
        hb, hz = _tight_md5(bare), _tight_md5(zero)
        if hb != _BASELINE_MD5[preset] or hz != _BASELINE_MD5[preset] or hb != hz:
            bad.append((preset, hb, hz, _BASELINE_MD5[preset]))
    assert bad == [], bad


def test_q79_every_edged_transition_has_feather():
    """Every library preset marked edged accepts feather and feather>0 changes pixels."""
    _qapp()
    from cutora.render import anim_engine
    from cutora.render.transitions import EDGED_TRANSITION_PRESETS
    from cutora.ui.panels.property import transition_param_info
    missing = [p for p in _REQUIRED_EDGED if p not in EDGED_TRANSITION_PRESETS]
    assert missing == [], missing
    lib = [v for _, v in anim_engine.STANDALONE_TRANSITION_LIBRARY]
    # Not the baseline pattern: those two frames share the same yellow/cyan
    # blocks, so a soft edge through the middle barely moves any pixel.
    from PySide6.QtGui import QColor, QPainter
    a = _solid(96, 64, (220, 40, 30))
    b = _solid(96, 64, (20, 50, 210))
    pa = QPainter(a)
    pa.fillRect(8, 6, 30, 40, QColor(250, 220, 40))
    pa.end()
    pb = QPainter(b)
    pb.fillRect(50, 8, 36, 44, QColor(20, 220, 160))
    pb.end()
    quiet = []
    for preset in lib:
        if preset not in EDGED_TRANSITION_PRESETS:
            info = transition_param_info(preset)
            assert not info.get("feather"), preset
            continue
        info = transition_param_info(preset)
        assert info.get("feather"), preset
        hard = anim_engine.compose_standalone_transition(
            0.5, preset, a, b, 96, 64, feather=0)
        soft = anim_engine.compose_standalone_transition(
            0.5, preset, a, b, 96, 64, feather=40)
        d = _mad(_qimage_rgba(hard)[..., :3], _qimage_rgba(soft)[..., :3])
        if d <= 0.02:
            quiet.append((preset, round(d, 4)))
    assert quiet == [], quiet

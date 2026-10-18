# -*- coding: utf-8 -*-
"""Q32 colour grade. Tests written to catch the real defects:

- exposure +1 EV must double linear-light luma, not sRGB codes
  (a mid-grey patch: doubling sRGB 0.40 → 0.80 is a different number
  than doubling linear then encoding)
- LUT vs direct on a ramp, not a lattice-aligned solid (solids hide
  interpolation error; lattice points of a 33³ cube are exact)
- three +0.3 saturation layers: sequential 1.3³, not summed 0.9
  (and a non-commutative exposure→sat→contrast order lock)
- identity .cube on a ramp, not a solid (wrong axis order still
  preserves greys / primaries at the corners)
- bypass must be the original QImage bytes, not an sRGB round-trip
- scopes must actually downscale; skip frames must not run the
  expensive path
"""
from __future__ import annotations

import hashlib
import os
import threading
import time

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["q32-grade"])


def _lin(c):
    """Independent sRGB EOTF. Must not import the product conversion."""
    x = np.asarray(c, dtype=np.float64)
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)


def _srgb(c):
    x = np.clip(np.asarray(c, dtype=np.float64), 0.0, None)
    return np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(x, 1.0 / 2.4) - 0.055)


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


def _md5_qimage(img):
    arr = _qimage_rgba(img)
    return hashlib.md5(np.ascontiguousarray(arr).tobytes()).hexdigest()


def _ramp_rgb(h=48, w=96):
    """Horizontal + vertical ramp, not a solid and not 33-lattice aligned."""
    yy = np.linspace(0.07, 0.93, h, dtype=np.float32)[:, None]
    xx = np.linspace(0.11, 0.89, w, dtype=np.float32)[None, :]
    r = np.broadcast_to(xx, (h, w)).copy()
    g = np.broadcast_to(yy, (h, w)).copy()
    b = np.broadcast_to(0.5 * xx + 0.35 * (1.0 - yy), (h, w)).copy()
    rgb = np.stack([r, g, b], axis=-1)
    return np.clip(rgb, 0.0, 1.0)


def _write_identity_cube(path, size=33):
    lines = ['TITLE "identity"', "LUT_3D_SIZE %d" % int(size), ""]
    n = int(size)
    den = float(n - 1)
    # IRIDAS: R fastest, then G, then B
    for bi in range(n):
        b = bi / den
        for gi in range(n):
            g = gi / den
            for ri in range(n):
                r = ri / den
                lines.append("%.6f %.6f %.6f" % (r, g, b))
    with open(path, "w", encoding="ascii") as fh:
        fh.write("\n".join(lines) + "\n")


def _solid_srgb(value, h=32, w=32):
    rgb = np.full((h, w, 3), float(value), dtype=np.float32)
    return rgb


def _ffmpeg():
    from cutora.core.config import FFMPEG_PATH
    return FFMPEG_PATH


def _color_mp4(path, color, frames=16, fps=30, size="80x80"):
    import subprocess
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


def _task_with_clip(media_path, duration=1.0, canvas=(80, 80), fps=30.0):
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
    clip = Clip(src_id, 0.0, float(duration), 0.0)
    clip.fit_mode = "stretch"
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


def _export_frame(task, t):
    from PySide6.QtGui import QImage, QPainter
    from cutora.model.canvas import canvas_wh
    from cutora.render.export_renderer import ExportRenderer
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
            path = src.get("path") if isinstance(src, dict) else getattr(src, "path", None)
            # 产品导出是软解。默认 hw=True 会走硬解，和预览的软解差 1 个色阶。
            cap = _open_video_capture(path, hw=False)
            if cap is not None and cap.isOpened():
                caps[sid] = cap
        img = QImage(int(w), int(h), QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(0)

        def _draw_bg(p):
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

        return ExportRenderer.render_frame(
            task, int(w), int(h), float(t), draw_background=_draw_bg)
    finally:
        for cap in caps.values():
            try:
                cap.release()
            except Exception:
                pass


def _preview_frame(task, t):
    from cutora.preview.engine import PreviewEngine
    eng = PreviewEngine()
    try:
        return eng.frame_at(task, float(t))
    finally:
        eng.close()


# ---------------------------------------------------------------------------
# linear light
# ---------------------------------------------------------------------------

def test_q32_linear_light():
    """Exposure +1 EV doubles linear luma (±1%), not sRGB codes.

    Patch is sRGB 0.40 — doubling the code would give 0.80, while
    doubling linear then encoding is a different number (~0.61).
    Black (0) and white (1) would hide either bug.
    """
    from cutora.render.grade import apply_grade_rgb, identity_grade

    rgb = _solid_srgb(0.40, 24, 24)
    g = identity_grade()
    g["exposure"] = 1.0
    out = apply_grade_rgb(rgb, g)
    assert out.shape == rgb.shape, out.shape

    lin_in = _lin(rgb)
    lin_out = _lin(out)
    ratio = float(lin_out.mean() / max(lin_in.mean(), 1e-12))
    assert abs(ratio - 2.0) <= 0.01, (
        "linear luma ratio=%s (want 2.0 ±1%%) — multiplied in sRGB?" % ratio
    )
    srgb_ratio = float(out.mean() / max(rgb.mean(), 1e-12))
    assert abs(srgb_ratio - 2.0) > 0.05, (
        "sRGB codes also doubled (ratio=%s) — not a linear-light exposure" % srgb_ratio
    )
    # Doubling the sRGB code 0.40 → 0.80 must not be what we got.
    assert abs(float(out.mean()) - 0.80) > 0.05, float(out.mean())


def test_q32_lut_equals_direct():
    """33³ LUT vs direct per-pixel on a ramp. Max |Δ| ≤ 1/255.

    Params are non-trivial (exposure, contrast, sat, temperature) so an
    identity LUT or a skipped op would miss. A solid on a lattice node
    would make nearest-neighbour look exact.
    """
    from cutora.render.grade import apply_3d_lut, apply_grade_rgb, build_3d_lut, identity_grade

    rgb = _ramp_rgb(40, 80)
    g = identity_grade()
    g["exposure"] = 0.35
    g["contrast"] = 0.22
    g["saturation"] = 0.40
    g["temperature"] = -650.0
    g["tint"] = 0.18
    direct = apply_grade_rgb(rgb, g)
    lut = build_3d_lut([g])
    mapped = apply_3d_lut(rgb, lut)
    delta = np.abs(direct.astype(np.float64) - mapped.astype(np.float64)).max()
    assert delta <= 1.0 / 255.0 + 1e-9, "max |LUT-direct|=%s (> 1/255)" % delta
    # The grade must have moved pixels; an identity pair would also be ≤ 1/255.
    moved = np.abs(direct.astype(np.float64) - rgb.astype(np.float64)).max()
    assert moved > 4.0 / 255.0, "grade was a no-op (max Δ=%s)" % moved


def test_q32_three_layers():
    """clip / track / project each +0.3 sat == three sequential applies.

    Adding the three offsets (0.9) is not 1.3³. A grey patch would hide
    saturation entirely — use a red-orange ramp.
    """
    from cutora.render.grade import (
        apply_3d_lut,
        apply_grade_rgb,
        build_3d_lut,
        identity_grade,
    )

    rgb = _ramp_rgb(32, 64)
    # Push chroma so sat is visible (ramp already is, but lock a red corner).
    rgb = rgb.copy()
    rgb[:8, :8] = (0.82, 0.18, 0.08)

    def sat(amount):
        g = identity_grade()
        g["saturation"] = float(amount)
        return g

    clip_g, track_g, proj_g = sat(0.3), sat(0.3), sat(0.3)
    sequential = apply_grade_rgb(rgb, clip_g)
    sequential = apply_grade_rgb(sequential, track_g)
    sequential = apply_grade_rgb(sequential, proj_g)
    lut = build_3d_lut([clip_g, track_g, proj_g])
    stacked = apply_3d_lut(rgb, lut)
    delta = np.abs(sequential.astype(np.float64) - stacked.astype(np.float64)).max()
    assert delta <= 1.0 / 255.0 + 1e-9, "stacked LUT vs sequential max Δ=%s" % delta

    once = apply_grade_rgb(rgb, sat(0.3))
    summed = apply_grade_rgb(rgb, sat(0.9))
    # 1.3³ must not collapse to a single +0.3, nor to a summed +0.9.
    d_once = np.abs(sequential.astype(np.float64) - once.astype(np.float64)).max()
    d_sum = np.abs(sequential.astype(np.float64) - summed.astype(np.float64)).max()
    assert d_once > 4.0 / 255.0, "three layers collapsed to one (+0.3)"
    assert d_sum > 2.0 / 255.0, "three layers summed offsets (0.9) instead of composing"

    # Order lock: exposure then contrast is not contrast then exposure.
    a = identity_grade(); a["exposure"] = 0.6
    b = identity_grade(); b["contrast"] = 0.5
    fwd = apply_grade_rgb(apply_grade_rgb(rgb, a), b)
    rev = apply_grade_rgb(apply_grade_rgb(rgb, b), a)
    assert np.abs(fwd.astype(np.float64) - rev.astype(np.float64)).max() > 2.0 / 255.0, (
        "exposure→contrast equalled contrast→exposure — order was lost"
    )
    lut_fwd = apply_3d_lut(rgb, build_3d_lut([a, b]))
    assert np.abs(fwd.astype(np.float64) - lut_fwd.astype(np.float64)).max() <= 1.0 / 255.0 + 1e-9


def test_q32_cube_import(tmp_path):
    """Identity .cube on a ramp is byte-identical to the input.

    A 2³ identity with swapped R/B still preserves greys; a ramp does not.
    """
    from cutora.render.grade import apply_cube_rgb, parse_cube

    path = str(tmp_path / "identity_33.cube")
    _write_identity_cube(path, size=33)
    cube = parse_cube(path)
    rgb = _ramp_rgb(36, 72)
    u8 = np.clip(np.round(rgb * 255.0), 0, 255).astype(np.uint8)
    out = apply_cube_rgb(u8, cube, mix=1.0)
    assert out.dtype == np.uint8
    assert out.shape == u8.shape
    assert np.array_equal(out, u8), (
        "identity cube changed %d bytes (max Δ=%s)"
        % (int(np.count_nonzero(out != u8)), int(np.abs(out.astype(np.int16) - u8.astype(np.int16)).max()))
    )


def test_q32_bypass_is_bitexact(tmp_path):
    """Identity / missing grade: export bytes match a render that never grades.

    An sRGB round-trip of an 8-bit frame is not bitexact on a decoded
    yuv420 colour bar — that is the defect this catches.
    """
    _qapp()
    media = str(tmp_path / "orange.mp4")
    _color_mp4(media, "0xC06020", frames=12, size="80x80")
    task, tr, clip = _task_with_clip(media, duration=0.4, canvas=(80, 80))

    from cutora.render import grade as grade_mod
    from cutora.render.export_renderer import ExportRenderer

    calls = {"n": 0}
    orig = grade_mod.apply_grade_to_frame

    def _count(img, task_data, t, **kw):
        calls["n"] += 1
        return orig(img, task_data, t, **kw)

    grade_mod.apply_grade_to_frame = _count
    ExportRenderer.render_frame.__globals__["apply_grade_to_frame"] = _count
    try:
        img_id = _export_frame(task, 0.1)
        digest_id = _md5_qimage(img_id)
        assert calls["n"] >= 1, "export did not go through apply_grade_to_frame"

        clip.grade = grade_mod.identity_grade()
        tr.grade = grade_mod.identity_grade()
        task["grade"] = grade_mod.identity_grade()
        img_explicit = _export_frame(task, 0.1)
        assert _md5_qimage(img_explicit) == digest_id

        preview = _preview_frame(task, 0.1)
        export = _export_frame(task, 0.1)
        assert _md5_qimage(preview) == _md5_qimage(export), (
            "preview and export diverged on identity grade"
        )
    finally:
        grade_mod.apply_grade_to_frame = orig
        ExportRenderer.render_frame.__globals__["apply_grade_to_frame"] = orig

    # Force the grade function to actually touch pixels: if bypass is a
    # round-trip, a patched identity that still converts will fail this
    # comparison against a never-graded compose.
    from PySide6.QtGui import QImage, QPainter
    from cutora.model.canvas import canvas_wh
    from cutora.render.readers import _open_video_capture
    from cutora.render.render_worker import RenderWorker

    rw = RenderWorker.__new__(RenderWorker)
    rw.project = task
    rw._geom_last_log_t = 0.0
    w, h = canvas_wh(task)
    tl = task["timeline"]
    path = media
    cap = _open_video_capture(path)
    try:
        raw = QImage(int(w), int(h), QImage.Format.Format_ARGB32_Premultiplied)
        raw.fill(0)
        p = QPainter(raw)
        try:
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            st = {"last_time": -1.0, "last_frame": None}
            rw._draw_video_overlay(p, cap, clip, int(w), int(h), 0.1, st)
        finally:
            p.end()
    finally:
        cap.release()

    # Identity grade after composite must match the ungraded composite
    # byte-for-byte (overlays are empty so this is the video layer).
    from cutora.render.grade import apply_grade_to_frame
    graded = apply_grade_to_frame(raw.copy(), task, 0.1)
    assert _md5_qimage(graded) == _md5_qimage(raw), (
        "identity apply_grade_to_frame changed pixels (sRGB round-trip?)"
    )


def test_q32_scope_downscale():
    """Scopes use a 1/4-res copy and skip 2 of every 3 frames.

    A 720×1280 frame downscaled is 180×320. Skip frames must return in
    well under a millisecond and must not bump compute_count.
    """
    _qapp()
    from PySide6.QtGui import QImage, QColor
    from cutora.render.scopes import SCOPE_DOWNSCALE, SCOPE_EVERY, ScopeSampler

    w, h = 720, 1280
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(40, 180, 90, 255))
    sampler = ScopeSampler()
    assert SCOPE_DOWNSCALE == 4, SCOPE_DOWNSCALE
    assert SCOPE_EVERY == 3, SCOPE_EVERY

    ran = sampler.feed(img)
    assert ran is True, "first frame must compute"
    sw, sh = sampler.last_size
    assert (sw, sh) == (w // 4, h // 4), "scope size=%s want %s" % ((sw, sh), (w // 4, h // 4))
    assert sampler.compute_count == 1
    assert sampler.last_source_size == (w, h)

    t0 = time.perf_counter()
    skip_a = sampler.feed(img)
    skip_b = sampler.feed(img)
    dt = time.perf_counter() - t0
    assert skip_a is False and skip_b is False, (skip_a, skip_b)
    assert sampler.compute_count == 1, "skip frames ran the expensive path"
    assert dt < 0.002, "skip path took %.4fs — blocked the preview thread" % dt

    assert sampler.feed(img) is True
    assert sampler.compute_count == 2

    # feed() from another thread must return (no deadlock on the skip path).
    holder = {"ok": False, "dt": 99.0}

    def _other():
        t1 = time.perf_counter()
        sampler.feed(img)
        holder["dt"] = time.perf_counter() - t1
        holder["ok"] = True

    th = threading.Thread(target=_other, name="q32-scope-skip")
    th.start()
    th.join(0.05)
    assert not th.is_alive(), "scope feed blocked a non-preview thread"
    assert holder["ok"] is True
    assert holder["dt"] < 0.01, holder["dt"]


def test_q32_preview_export_same(tmp_path):
    """PreviewEngine.frame_at vs ExportRenderer with a live grade.

    Two callers, two decoder pools. Exposure +0.7 on a coloured clip so
    a missing preview hook stays at the ungraded orange.
    """
    _qapp()
    media = str(tmp_path / "teal.mp4")
    _color_mp4(media, "0x2080A0", frames=20, size="80x80")
    task, tr, clip = _task_with_clip(media, duration=0.5, canvas=(80, 80))
    from cutora.render.grade import identity_grade
    g = identity_grade()
    g["exposure"] = 0.7
    g["saturation"] = 0.35
    clip.grade = g
    t = 0.1
    preview = _preview_frame(task, t)
    export = _export_frame(task, t)
    a = _qimage_rgba(preview)[..., :3].astype(np.int16)
    b = _qimage_rgba(export)[..., :3].astype(np.int16)
    mad = float(np.mean(np.abs(a - b))) / 255.0
    assert mad < 0.02, mad
    # Ungraded must differ from graded (the grade actually ran).
    clip.grade = identity_grade()
    plain = _export_frame(task, t)
    clip.grade = g
    graded = _export_frame(task, t)
    pa = _qimage_rgba(plain)[..., :3].astype(np.int16)
    ga = _qimage_rgba(graded)[..., :3].astype(np.int16)
    assert float(np.mean(np.abs(pa - ga))) / 255.0 > 0.01, (
        "graded export matched identity — preview/export both skipped the grade"
    )


def test_q32_scope_on_preview():
    """VideoCanvas scopes: 1/4-res, skip 2/3 frames, compute off this thread.

    A 720×1280 program frame must sample 180×320. Two skip presents must
    return in well under a millisecond and must not bump compute_count.
    Overlay QImage is produced from that sample (not a full-res copy).
    """
    _qapp()
    from PySide6.QtGui import QImage, QColor
    from PySide6.QtWidgets import QApplication
    from cutora.ui.canvas import VideoCanvas
    from cutora.render.scopes import SCOPE_DOWNSCALE, SCOPE_EVERY

    canvas = VideoCanvas()
    canvas._video_dims = (720, 1280)
    canvas.set_scope_mode("luma")
    img = QImage(720, 1280, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(40, 180, 90, 255))

    canvas.set_program_frame(img)
    deadline = time.time() + 2.0
    while time.time() < deadline:
        QApplication.processEvents()
        sam = getattr(canvas, "_scope_sampler", None)
        if sam is not None and sam.compute_count >= 1:
            break
        time.sleep(0.01)
    sam = getattr(canvas, "_scope_sampler", None)
    assert sam is not None and sam.compute_count >= 1, "scope worker never computed"
    assert sam.last_source_size == (720, 1280), sam.last_source_size
    assert sam.last_size == (720 // SCOPE_DOWNSCALE, 1280 // SCOPE_DOWNSCALE), sam.last_size

    t0 = time.perf_counter()
    canvas.set_program_frame(img)
    canvas.set_program_frame(img)
    dt = time.perf_counter() - t0
    assert sam.compute_count == 1, "skip presents ran the expensive path"
    assert dt < 0.02, "skip set_program_frame took %.4fs — blocked the preview thread" % dt
    assert SCOPE_EVERY == 3

    deadline = time.time() + 1.0
    while time.time() < deadline and getattr(canvas, "_scope_overlay", None) is None:
        QApplication.processEvents()
        time.sleep(0.01)
    ov = getattr(canvas, "_scope_overlay", None)
    assert ov is not None and not ov.isNull(), "scope overlay was not applied"
    assert ov.width() == 720, ov.width()
    assert ov.height() < 1280, "overlay used the full frame instead of a strip"


def test_q32_color_wheel_not_sliders():
    """Lift/gamma/gain wheels are circular; RGB is not three sliders."""
    _qapp()
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QMouseEvent, QWheelEvent
    from PySide6.QtCore import QPointF, QEvent
    from PySide6.QtWidgets import QSlider
    from cutora.ui.widgets.inspector import ColorWheel

    wheel = ColorWheel()
    wheel.show()
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()
    sliders = wheel.findChildren(QSlider)
    assert sliders == [], "ColorWheel contained QSlider children: %s" % sliders
    r0, g0, y0 = wheel.value()
    assert abs(r0) < 1e-6 and abs(g0) < 1e-6 and abs(y0) < 1e-6, wheel.value()

    cx = wheel.width() // 2
    cy = wheel.height() // 2
    # Drag toward the right of the disc — must move chroma, not stay at 0.
    press = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(cx, cy),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    wheel.mousePressEvent(press)
    move = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(cx + wheel.width() // 3, cy),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    wheel.mouseMoveEvent(move)
    r1, g1, y1 = wheel.value()
    chroma = (r1 * r1 + g1 * g1) ** 0.5
    assert chroma > 0.05, "drag on the disc did not change colour bias: %s" % (wheel.value(),)

    before_y = y1
    we = QWheelEvent(
        QPointF(2, 2),
        QPointF(2, 2),
        QPoint(0, 0),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    wheel.wheelEvent(we)
    r2, g2, y2 = wheel.value()
    assert abs(y2 - before_y) > 1e-4, "wheel on the ring did not change luminance: %s" % (wheel.value(),)


def _subtitle_style():
    """Mid-luma blue fill. Default pack #F4EBD8 is near-white and clips
    under +1 EV, which would hide a whole-frame LUT. Shadow/glow off so
    the assertion can use solid glyph interiors, not AA fringe."""
    from cutora.subtitle.stylepack import default_fx
    fx = default_fx()
    fx["fill"] = {"type": "solid", "colors": ["#5080C8"]}
    fx["stroke"] = {"width_em": 0, "color": "#5080C8", "join": "round"}
    fx["glow"]["sigma_em"] = 0
    fx["glow"]["strength"] = 0
    fx["shadow"]["sigma_em"] = 0
    fx["shadow"]["alpha"] = 0
    fx["shadow"]["dx_em"] = 0
    fx["shadow"]["dy_em"] = 0
    fx["bg"]["type"] = "none"
    return {
        "color_text": "#5080C8",
        "color_high": "#5080C8",
        "font_size": 28,
        "fontsize": 28,
        "font_family": "Arimo",
        "position": "center",
        "align": "center",
        "bold": True,
        "fx": fx,
    }


def test_q32_bypass_zero_cost(tmp_path):
    """Identity plan must not enter apply_grade_to_qimage.

    prepare_grade_plan scans once; per-frame work is a flag check.
    A live +1 EV must still go through apply_grade_to_qimage (so a
    skipped-always stub cannot pass).
    """
    _qapp()
    media = str(tmp_path / "grey.mp4")
    _color_mp4(media, "0x606060", frames=8, size="64x64")
    task, tr, clip = _task_with_clip(media, duration=0.3, canvas=(64, 64))
    from cutora.render import grade as grade_mod
    from cutora.render.grade import identity_grade, prepare_grade_plan

    assert prepare_grade_plan(task) is False
    assert task["_grade_on"] is False

    calls = {"n": 0}
    orig = grade_mod.apply_grade_to_qimage

    def _count(img, grades, cache=None):
        calls["n"] += 1
        return orig(img, grades, cache=cache)

    grade_mod.apply_grade_to_qimage = _count
    try:
        _export_frame(task, 0.05)
        assert calls["n"] == 0, (
            "identity export still built a LUT (%d apply_grade_to_qimage calls)"
            % calls["n"]
        )
        g = identity_grade()
        g["exposure"] = 1.0
        clip.grade = g
        assert prepare_grade_plan(task) is True
        _export_frame(task, 0.05)
        assert calls["n"] >= 1, "live clip grade never reached apply_grade_to_qimage"
    finally:
        grade_mod.apply_grade_to_qimage = orig


def test_q32_clip_grade_spares_subtitles(tmp_path):
    """Clip +1 EV must brighten the video and leave subtitle pixels untouched.

    Mid-blue `#5080C8` (not white): +1 EV in linear light moves it, so a
    post-composite whole-frame LUT would change those pixels. White would
    clip to 255 and hide the bug.
    """
    _qapp()
    media = str(tmp_path / "orange.mp4")
    _color_mp4(media, "0xC06020", frames=16, size="160x160")
    task, tr, clip = _task_with_clip(media, duration=0.5, canvas=(160, 160))
    task["style"] = _subtitle_style()
    task["segments"] = [{
        "start": 0.0,
        "end": 2.0,
        "text": "GRADE",
        "words": [{"word": "GRADE", "start": 0.0, "end": 2.0, "wid": 0}],
        "lines": ["GRADE"],
    }]
    from cutora.render.grade import identity_grade

    t = 0.1
    plain_video_task = dict(task)
    plain_video_task["segments"] = []
    v0 = _qimage_rgba(_export_frame(plain_video_task, t))[..., :3]
    s0 = _qimage_rgba(_export_frame(task, t))[..., :3]
    # Solid fill only. AA fringe is a blend with the video and MUST move
    # when the clip under it gets brighter — that is not the bug.
    fill = np.array([0x50, 0x80, 0xC8], dtype=np.int16)
    near = np.all(np.abs(s0.astype(np.int16) - fill) <= 2, axis=-1)
    sub_mask = np.zeros(near.shape, dtype=bool)
    sub_mask[1:-1, 1:-1] = (
        near[1:-1, 1:-1] & near[:-2, 1:-1] & near[2:, 1:-1]
        & near[1:-1, :-2] & near[1:-1, 2:]
    )
    assert int(sub_mask.sum()) > 80, (
        "subtitle fill #5080C8 did not paint (%d px, painted mean=%s)"
        % (int(sub_mask.sum()), s0.mean(axis=(0, 1)))
    )

    g = identity_grade()
    g["exposure"] = 1.0
    clip.grade = g
    v1 = _qimage_rgba(_export_frame(plain_video_task, t))[..., :3]
    s1 = _qimage_rgba(_export_frame(task, t))[..., :3]

    video_delta = float(np.mean(np.abs(v1.astype(np.int16) - v0.astype(np.int16)))) / 255.0
    assert video_delta > 0.02, (
        "clip +1 EV did not brighten the video (Δ=%s) — grade never ran" % video_delta
    )

    sub_delta = np.abs(
        s1[sub_mask].astype(np.int16) - s0[sub_mask].astype(np.int16)
    )
    assert int(sub_delta.max()) == 0, (
        "clip grade changed subtitle fill (max Δ=%s, n=%s) — LUT was applied after composite"
        % (int(sub_delta.max()), int(np.count_nonzero(sub_delta)))
    )

    preview = _qimage_rgba(_preview_frame(task, t))[..., :3]
    export = s1
    mad = float(np.mean(np.abs(preview.astype(np.int16) - export.astype(np.int16)))) / 255.0
    assert mad < 0.02, "preview/export diverged with clip grade + subs mad=%s" % mad


def test_q32_pip_grade_isolated(tmp_path):
    """Lower-track clip +1 EV must not touch the upper PiP clip.

    Upper clip is a smaller teal square (scale 0.4) on top of a full-frame
    orange. Whole-frame grade would brighten the teal centre; clip-isolated
    grade must leave it byte-identical to the ungraded PiP.
    """
    _qapp()
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track
    from cutora.render.grade import identity_grade

    orange = str(tmp_path / "orange.mp4")
    teal = str(tmp_path / "teal.mp4")
    _color_mp4(orange, "0xC06020", frames=16, size="160x160")
    _color_mp4(teal, "0x2080A0", frames=16, size="160x160")

    tl = Timeline(160, 160, 30.0)
    tl.sources["src0"] = {
        "id": "src0", "path": orange, "duration": 0.5,
        "has_video": True, "has_audio": False,
    }
    tl.sources["src1"] = {
        "id": "src1", "path": teal, "duration": 0.5,
        "has_video": True, "has_audio": False,
    }
    lower = Track("video")
    lower.id = "v_low"
    lower.order = 0
    c_low = Clip("src0", 0.0, 0.5, 0.0)
    c_low.fit_mode = "stretch"
    lower.clips.append(c_low)
    upper = Track("video")
    upper.id = "v_up"
    upper.order = 1
    c_up = Clip("src1", 0.0, 0.5, 0.0)
    c_up.fit_mode = "stretch"
    c_up.scale = 0.4
    upper.clips.append(c_up)
    tl.add_track(lower)
    tl.add_track(upper)
    task = {
        "video": orange,
        "dims": (160, 160),
        "canvas_width": 160,
        "canvas_height": 160,
        "timeline": tl,
        "style": {},
        "segments": [],
        "text_overlays": [],
        "image_overlays": [],
    }

    t = 0.1
    both_plain = _qimage_rgba(_export_frame(task, t))[..., :3]
    h, w = both_plain.shape[:2]
    cy, cx = h // 2, w // 2
    # Interior of the small upper clip; corners are the lower clip.
    centre = (slice(cy - 12, cy + 12), slice(cx - 12, cx + 12))
    corner = (slice(0, 16), slice(0, 16))

    g = identity_grade()
    g["exposure"] = 1.0
    c_low.grade = g
    graded = _qimage_rgba(_export_frame(task, t))[..., :3]

    centre_delta = int(np.abs(
        graded[centre].astype(np.int16) - both_plain[centre].astype(np.int16)
    ).max())
    assert centre_delta == 0, (
        "lower-clip grade changed upper PiP pixels (max Δ=%s) — whole-frame LUT?"
        % centre_delta
    )

    corner_delta = float(np.mean(np.abs(
        graded[corner].astype(np.int16) - both_plain[corner].astype(np.int16)
    ))) / 255.0
    assert corner_delta > 0.02, (
        "lower clip +1 EV did not change the uncovered region (Δ=%s)" % corner_delta
    )

    preview = _qimage_rgba(_preview_frame(task, t))[..., :3]
    mad = float(np.mean(np.abs(preview.astype(np.int16) - graded.astype(np.int16)))) / 255.0
    assert mad < 0.02, "preview/export PiP mad=%s" % mad

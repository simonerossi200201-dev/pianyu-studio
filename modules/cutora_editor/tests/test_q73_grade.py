# -*- coding: utf-8 -*-
"""Q73: the grade frame path has to stay the same picture and get fast.

Pillow's trilinear must match the numpy trilinear it replaces (MAD < 1
on 8-bit codes). A lattice-aligned solid would hide an axis swap, so
the picture is a ramp that misses the 33³ nodes.

Per-channel grades (exposure, contrast, lift/gamma/gain, curves) must
not touch the 3D lookup. A grey field would hide a per-code error in
the 1D map, so that check uses the same ramp.
"""
from __future__ import annotations

import os
import time

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["q73-grade"])


def _ramp_u8(h=72, w=96):
    """Not a solid, and not sitting on 33³ lattice points."""
    yy = np.linspace(0.07, 0.93, h, dtype=np.float32)[:, None]
    xx = np.linspace(0.11, 0.89, w, dtype=np.float32)[None, :]
    r = np.broadcast_to(xx, (h, w)).copy()
    g = np.broadcast_to(yy, (h, w)).copy()
    b = np.broadcast_to(0.5 * xx + 0.35 * (1.0 - yy), (h, w)).copy()
    rgb = np.clip(np.stack([r, g, b], axis=-1), 0.0, 1.0)
    return np.clip(np.rint(rgb * 255.0), 0, 255).astype(np.uint8)


def test_q73_pillow_lut_matches_reference():
    """Same ramp, same 33³ table: Pillow vs numpy trilinear, MAD < 1.

    The table is a real grade (exposure + saturation + temperature), not
    identity — an identity pair is MAD 0 for a swapped-axis bug that
    happens to preserve greys. The ramp misses the lattice, so nearest
    vs linear would show up too.
    """
    from cutora.render.grade import (
        apply_3d_lut,
        apply_pillow_3d,
        build_3d_lut,
        identity_grade,
    )

    g = identity_grade()
    g["exposure"] = 0.35
    g["saturation"] = 0.45
    g["temperature"] = -650.0
    lut = build_3d_lut([g])
    table = lut.table
    u8 = _ramp_u8()
    ref_f = apply_3d_lut(u8.astype(np.float32) / 255.0, table)
    ref = np.clip(np.rint(ref_f * 255.0), 0, 255).astype(np.uint8)
    got = apply_pillow_3d(u8, table)
    assert got.shape == ref.shape, got.shape
    assert got.dtype == np.uint8, got.dtype
    delta = np.abs(got.astype(np.int16) - ref.astype(np.int16))
    mad = float(delta.mean())
    assert mad < 1.0, "Pillow vs numpy trilinear MAD=%s (max %s)" % (mad, int(delta.max()))
    moved = np.abs(got.astype(np.int16) - u8.astype(np.int16))
    assert int(moved.max()) > 8, "grade table was a no-op (max Δ=%s)" % int(moved.max())


def _qimage_rgb(u8):
    from PySide6.QtGui import QImage

    src = np.ascontiguousarray(u8)
    h, w = src.shape[:2]
    img = QImage(src.data, w, h, int(src.strides[0]), QImage.Format.Format_RGB888)
    return img.copy()


def _rgba_u8(img):
    from PySide6.QtGui import QImage

    conv = img.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = conv.width(), conv.height()
    bpl = conv.bytesPerLine()
    raw = bytes(conv.constBits())
    arr = np.frombuffer(raw, dtype=np.uint8)
    return arr.reshape((h, bpl))[:, : w * 4].reshape((h, w, 4)).copy()


def test_q73_per_channel_skips_3d():
    """Exposure-only must not call the 3D lookup, and must match the formula.

    A patched counter that wraps a dead name would also read 0. Saturation
    on the same ramp must increment it, and the exposure result has to
    match apply_grade_rgb within MAD < 1 — a skipped grade fails that.
    """
    _qapp()
    from cutora.render import grade as grade_mod
    from cutora.render.grade import apply_grade_rgb, apply_grade_to_qimage, identity_grade

    u8 = _ramp_u8()
    qimg = _qimage_rgb(u8)
    g = identity_grade()
    g["exposure"] = 0.55
    # Lift is per-channel and not equal on R/G/B. A bake that only
    # multiplies a shared exposure would miss it and still skip 3D.
    g["lift"] = [0.04, -0.02, 0.01, 0.0]
    g["contrast"] = 0.18

    calls = {"n": 0}
    orig = grade_mod.apply_pillow_3d

    def _count(rgb, lut):
        calls["n"] += 1
        return orig(rgb, lut)

    grade_mod.apply_pillow_3d = _count
    try:
        out = apply_grade_to_qimage(qimg, [g])
        assert calls["n"] == 0, "exposure/contrast/lift called 3D lookup %d times" % calls["n"]
        got = _rgba_u8(out)[..., :3]
        direct = apply_grade_rgb(u8.astype(np.float32) / 255.0, g)
        ref = np.clip(np.rint(np.clip(direct, 0.0, 1.0) * 255.0), 0, 255).astype(np.uint8)
        delta = np.abs(got.astype(np.int16) - ref.astype(np.int16))
        mad = float(delta.mean())
        assert mad < 1.0, "1D map vs formula MAD=%s (max %s)" % (mad, int(delta.max()))
        assert int(delta.max()) <= 1, "1D map vs formula max Δ=%s" % int(delta.max())
        moved = np.abs(got.astype(np.int16) - u8.astype(np.int16))
        assert float(moved.mean()) > 4.0, "exposure did not move the ramp (mean Δ=%s)" % float(moved.mean())

        # Export frames are BGR888. A channel swap still changes a ramp, so
        # the RGB check above would stay green.
        from PySide6.QtGui import QImage

        bgr = np.ascontiguousarray(u8[..., ::-1])
        bq = QImage(bgr.data, bgr.shape[1], bgr.shape[0], int(bgr.strides[0]),
                    QImage.Format.Format_BGR888).copy()
        bout = apply_grade_to_qimage(bq, [g])
        assert calls["n"] == 0, "BGR exposure path called 3D lookup"
        bgot = _rgba_u8(bout)[..., :3]
        bdelta = np.abs(bgot.astype(np.int16) - ref.astype(np.int16))
        assert float(bdelta.mean()) < 1.0, "BGR 1D vs formula MAD=%s" % float(bdelta.mean())

        # Q180：饱和度（线性空间里是仿射）走直接计算，不再查 33³ 表；自然饱和度（逐像素系数）仍走 3D 表
        sat = identity_grade()
        sat["saturation"] = 0.4
        apply_grade_to_qimage(bq, [sat])
        assert calls["n"] == 0, "saturation should take the affine fast path, not apply_pillow_3d"
        vib = identity_grade()
        vib["vibrance"] = 0.4
        apply_grade_to_qimage(bq, [vib])
        assert calls["n"] >= 1, "vibrance never reached apply_pillow_3d — counter is on a dead name"
    finally:
        grade_mod.apply_pillow_3d = orig


def test_q73_project_identity_skips_layer():
    """An identity project grade returns the same QImage and does not look up.

    Clip/track grades are a different call. Removing the early return inside
    apply_grade_to_frame would round-trip the canvas even when the grade is
    a no-op — that is the bytes this asserts against. A live contrast must
    still enter apply_grade_to_qimage, or a stub that never grades passes.
    """
    _qapp()
    from cutora.render import grade as grade_mod
    from cutora.render.grade import apply_grade_to_frame, identity_grade

    u8 = _ramp_u8(48, 64)
    img = _qimage_rgb(u8)
    before = _rgba_u8(img)
    task = {"grade": identity_grade()}
    calls = {"n": 0}
    orig = grade_mod.apply_grade_to_qimage

    def _count(image, grades, cache=None):
        calls["n"] += 1
        return orig(image, grades, cache=cache)

    grade_mod.apply_grade_to_qimage = _count
    try:
        out = apply_grade_to_frame(img, task, 0.1)
        assert out is img, "identity project grade returned a new image"
        assert calls["n"] == 0, "identity project grade entered apply_grade_to_qimage"
        assert np.array_equal(_rgba_u8(out), before)
        task["grade"]["contrast"] = 0.35
        graded = apply_grade_to_frame(img, task, 0.1)
        assert calls["n"] >= 1, "live project contrast never graded"
        moved = np.abs(_rgba_u8(graded)[..., :3].astype(np.int16) - before[..., :3].astype(np.int16))
        assert float(moved.mean()) > 2.0, "project contrast did not move pixels"
    finally:
        grade_mod.apply_grade_to_qimage = orig


def _write_warm_cube(path):
    n = 17
    den = float(n - 1)
    lines = ['TITLE "q73-warm"', "LUT_3D_SIZE %d" % n, ""]
    for bi in range(n):
        b = bi / den
        for gi in range(n):
            gch = gi / den
            for ri in range(n):
                r = min(1.0, (ri / den) * 1.12 + 0.02)
                lines.append("%.6f %.6f %.6f" % (r, gch, b))
    with open(path, "w", encoding="ascii") as fh:
        fh.write("\n".join(lines) + "\n")


def test_q73_scopes_off_by_default():
    """A new project's preview grab must not contain the scope strip.

    The old scope was painted over the bottom quarter of the picture.
    This frame is magenta (240, 40, 160). The luma waveform is green
    and the strip behind it is a dark bar, so the lower-middle band
    would no longer match the centre. Safe-zone chrome, if the user
    has it on, sits in the outer 8% and is outside this band.
    Three grade editors are wired the same way the inspector does,
    because that is what a new project actually constructs.
    """
    _qapp()
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtWidgets import QApplication

    from cutora.model.timeline import Timeline
    from cutora.ui.canvas import VideoCanvas
    from cutora.ui.panels.grade import GradeEditor

    canvas = VideoCanvas()
    editors = []
    try:
        for title in ("调色", "轨道调色", "工程调色"):
            ed = GradeEditor(title)
            editors.append(ed)
            assert ed.scope_mode.currentData() == "off", ed.scope_mode.currentData()
            ed.scope_mode_changed.connect(canvas.set_scope_mode)
        tl = Timeline(360, 640, 30.0)
        task = {
            "timeline": tl,
            "segments": [],
            "style": {},
            "text_overlays": [],
            "image_overlays": [],
            "dims": (360, 640),
        }
        canvas.resize(400, 700)
        canvas.set_data(task, 0.0)
        frame = QImage(360, 640, QImage.Format.Format_RGB32)
        frame.fill(QColor(240, 40, 160))
        canvas.set_program_frame(frame)
        canvas.show()
        app = QApplication.instance()
        app.processEvents()
        canvas.repaint()
        app.processEvents()

        sam = getattr(canvas, "_scope_sampler", None)
        assert sam is None or sam.mode == "off", getattr(sam, "mode", None)
        flo = getattr(canvas, "_scope_float_win", None)
        assert flo is None or not flo.isVisible(), "scope float is up on a new project"

        grab = canvas.grab().toImage()
        rect = canvas._preview_target_rect()
        assert rect.width() > 40 and rect.height() > 40, rect
        arr = _rgba_u8(grab)
        x0 = int(rect.x()) + 6
        x1 = int(rect.x() + rect.width()) - 6
        y_lo0 = int(rect.y() + rect.height() * 0.72)
        y_lo1 = int(rect.y() + rect.height() * 0.88)
        y_mid0 = int(rect.y() + rect.height() * 0.40)
        y_mid1 = int(rect.y() + rect.height() * 0.55)
        low = arr[y_lo0:y_lo1, x0:x1, :3].astype(np.int16)
        mid = arr[y_mid0:y_mid1, x0:x1, :3].astype(np.int16)
        assert low.size > 0 and mid.size > 0, (low.shape, mid.shape)
        # Centre of a magenta frame. If the preview never painted, both
        # bands are the canvas background and this mean is not ~240.
        assert abs(float(mid[..., 0].mean()) - 240.0) < 20.0, float(mid[..., 0].mean())
        gap = abs(float(low[..., 0].mean()) - float(mid[..., 0].mean()))
        assert gap < 12.0, "lower band R drifted from the centre by %s — scope bar?" % gap
        green = (low[..., 1] > 160) & (low[..., 1] > low[..., 0] + 30) & (low[..., 2] < 140)
        assert int(green.sum()) == 0, "scope waveform pixels in the preview: %d" % int(green.sum())
    finally:
        canvas.close()
        for ed in editors:
            ed.close()


def test_q73_realtime(tmp_path):
    """Full primary + cube + track sat + project contrast at 720×1280.

    Median of 20 frames, not the mean: the first frame bakes the 33³
    table and must not set the budget. A 64×64 stand-in would pass a
    lookup that is still ~15 ms per megapixel.
    """
    _qapp()
    from PySide6.QtGui import QImage

    from cutora.render.grade import (
        apply_clip_track_grade,
        apply_grade_to_frame,
        identity_grade,
    )

    cube = str(tmp_path / "warm.cube")
    _write_warm_cube(cube)
    h, w = 1280, 720
    yy = np.linspace(20, 230, h, dtype=np.float32)[:, None]
    xx = np.linspace(15, 240, w, dtype=np.float32)[None, :]
    r = np.broadcast_to(xx, (h, w))
    gch = np.broadcast_to(yy, (h, w))
    b = np.clip(0.45 * xx + 0.25 * yy, 0, 255)
    rgb = np.stack([r, gch, b], axis=-1)
    u8 = np.ascontiguousarray(np.clip(np.rint(rgb), 0, 255).astype(np.uint8))
    src = QImage(u8.data, w, h, int(u8.strides[0]), QImage.Format.Format_RGB888).copy()
    assert src.width() == 720 and src.height() == 1280, (src.width(), src.height())

    def _full():
        g = identity_grade()
        g["exposure"] = 0.35
        g["contrast"] = 0.22
        g["pivot"] = 0.18
        g["temperature"] = -400.0
        g["tint"] = 0.12
        g["saturation"] = 0.35
        g["vibrance"] = 0.20
        g["lift"] = [0.02, 0.0, -0.01, 0.01]
        g["gamma"] = [0.0, 0.03, 0.0, 0.0]
        g["gain"] = [0.0, 0.0, 0.04, 0.02]
        g["hsl"]["red"] = {"h": 0.08, "s": 0.10, "l": 0.0}
        g["cube"] = cube
        g["cube_mix"] = 0.75
        return g

    class _Host:
        pass

    clip = _Host()
    clip.grade = _full()
    track = _Host()
    track.grade = identity_grade()
    track.grade["saturation"] = 0.15
    task = {"grade": identity_grade()}
    task["grade"]["contrast"] = 0.10

    samples = []
    last = None
    for _ in range(20):
        t0 = time.perf_counter()
        graded = apply_clip_track_grade(src, clip, track)
        last = apply_grade_to_frame(graded, task, 0.0)
        samples.append((time.perf_counter() - t0) * 1000.0)
    samples.sort()
    median = 0.5 * (samples[9] + samples[10])
    assert median <= 33.0, "median %.2f ms over 20 frames (min %.2f max %.2f)" % (
        median, samples[0], samples[-1])
    got = _rgba_u8(last)[..., :3]
    moved = np.abs(got.astype(np.int16) - u8.astype(np.int16))
    assert float(moved.mean()) > 4.0, "full grade was a no-op (mean Δ=%s)" % float(moved.mean())

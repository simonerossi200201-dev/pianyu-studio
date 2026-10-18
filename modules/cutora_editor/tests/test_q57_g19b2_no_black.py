# -*- coding: utf-8 -*-
"""Q57: g19b-2 must not re-record the crossfade-into-black hole.

The golden hash only says 'same as that day'. This file says 'no hole':
video-layer alpha is 255 on every transition frame, and the encoded
window's mean luma does not collapse the way the 2026-09-19 baseline did
(~60 vs ~120). Per-pixel min luma is 0 on this footage (auditorium
shadows) even outside transitions — that is not the defect.
"""
from __future__ import annotations

import json
import math
import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CASE = os.path.join(REPO, "tests", "golden", "g19b-2_stitch_crossfade")
PROJECT = os.path.join(CASE, "project.asproj.json")
EXPECTED = os.path.join(CASE, "expected.mp4")
FPS = 30.0
# Bug baseline window-min mean luma was 59–70. Freeze export is 119–121.
MEAN_LUMA_FLOOR = 100.0


def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["q57-g19b2"])


def _luma_rgb(rgb):
    r = rgb[..., 0].astype(np.float32)
    g = rgb[..., 1].astype(np.float32)
    b = rgb[..., 2].astype(np.float32)
    return 0.299 * r + 0.587 * g + 0.114 * b


def _luma_bgr(fr):
    b = fr[..., 0].astype(np.float32)
    g = fr[..., 1].astype(np.float32)
    r = fr[..., 2].astype(np.float32)
    return 0.299 * r + 0.587 * g + 0.114 * b


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


def _frame_index(t, fps=FPS):
    return int(math.floor(float(t) * float(fps) + 1e-6))


def _seams(task):
    tl = task.get("timeline")
    track = None
    for tr in getattr(tl, "tracks", []) or []:
        if getattr(tr, "type", None) == "video":
            track = tr
            break
    assert track is not None
    out = []
    for trans in getattr(track, "transitions", []) or []:
        t0 = float(getattr(trans, "start_t", 0.0) or 0.0)
        dur = float(getattr(trans, "duration", 0.0) or 0.0)
        t1 = t0 + dur
        n0 = _frame_index(t0)
        n1 = _frame_index(t1)
        while n0 / FPS < t0 - 1e-9:
            n0 += 1
        last = n1
        if last / FPS > t1 + 1e-9:
            last -= 1
        if last < n0:
            last = n0
        out.append((t0, t1, list(range(n0, last + 1))))
    assert len(out) >= 4, "g19b-2 must keep its four cross_dissolve seams"
    return out


@pytest.mark.skipif(not os.path.isfile(PROJECT), reason="g19b-2 project missing")
def test_q57_g19b2_has_no_black_frames():
    _qapp()
    from cutora.model.project_io import deserialize_render_task
    from cutora.preview.engine import PreviewEngine

    data = json.loads(open(PROJECT, encoding="utf-8").read())
    task = deserialize_render_task(data["tasks"][0])
    seams = _seams(task)

    eng = PreviewEngine()
    try:
        for i, (t0, t1, ns) in enumerate(seams):
            for n in ns:
                img = eng.video_frame_at(task, n / FPS)
                arr = _qimage_rgba(img)
                alpha = arr[..., 3]
                y = _luma_rgb(arr[..., :3])
                a_min = int(alpha.min())
                y_mean = float(y.mean())
                assert a_min == 255, (
                    "seam %d frame %d t=%.3f alpha min=%d — freeze hole is back"
                    % (i, n, n / FPS, a_min)
                )
                assert y_mean > 10.0, (
                    "seam %d frame %d t=%.3f mean luma=%.2f — collapsed black frame"
                    % (i, n, n / FPS, y_mean)
                )
    finally:
        eng.close()

    if not os.path.isfile(EXPECTED):
        pytest.skip("expected.mp4 missing")
    import cv2
    cap = cv2.VideoCapture(EXPECTED)
    assert cap.isOpened(), EXPECTED
    means = []
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        means.append(float(_luma_bgr(fr).mean()))
    cap.release()
    means = np.asarray(means, dtype=np.float32)
    assert len(means) > 100, len(means)
    for i, (t0, t1, ns) in enumerate(seams):
        idx = [n for n in ns if n < len(means)]
        assert idx, (i, t0, t1)
        window_min = float(means[idx].min())
        assert window_min >= MEAN_LUMA_FLOOR, (
            "seam %d encoded mean-luma min=%.2f < %.1f — looks like the "
            "old black-hole baseline (59-70), not the freeze export (~120)"
            % (i, window_min, MEAN_LUMA_FLOOR)
        )

# -*- coding: utf-8 -*-
"""g19 clip-start frames vs source n=0 after POS_FRAMES seek."""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
G19 = os.path.join(REPO, "tests", "golden", "g19_stitch_timeline")
G19B = os.path.join(REPO, "tests", "golden", "g19b_stitch_crossfade")
G19_2 = os.path.join(REPO, "tests", "golden", "g19-2_stitch_timeline")
G19B_2 = os.path.join(REPO, "tests", "golden", "g19b-2_stitch_crossfade")
MEDIA = os.path.join(REPO, "tests", "golden", "_media", "g19_stitch_timeline")
OLD_MP4 = os.path.join(G19, "expected.mp4")
NEW_MP4 = os.path.join(G19_2, "expected.mp4")

CLIPS = (
    ("seg2", 8.0, "T0035-s004-p02.mp4"),
    ("seg3", 12.71, "T0035-s014-p04.mp4"),
)


def _ffmpeg():
    from cutora.core.config import FFMPEG_PATH
    return FFMPEG_PATH


def _extract_n(src, n, dest, scale=None):
    vf = "select=eq(n\\,%d)" % int(n)
    if scale:
        vf = vf + ",scale=%d:%d" % scale
    cmd = [
        _ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
        "-i", src, "-vf", vf, "-vframes", "1", dest,
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=30)
    return r.returncode == 0 and os.path.isfile(dest) and os.path.getsize(dest) > 0


def _rgb_png(path):
    from PySide6.QtGui import QImage
    import numpy as np
    img = QImage(path).convertToFormat(QImage.Format.Format_RGB888)
    w, h, bpl = img.width(), img.height(), img.bytesPerLine()
    arr = np.frombuffer(bytes(img.constBits()), dtype=np.uint8)
    arr = arr.reshape((h, bpl))[:, :w * 3].reshape((h, w, 3))
    return arr.copy()


def _mad(a, b):
    import numpy as np
    h = min(a.shape[0], b.shape[0])
    w = min(a.shape[1], b.shape[1])
    return float(np.mean(np.abs(
        a[:h, :w].astype(np.int16) - b[:h, :w].astype(np.int16)))) / 255.0


@pytest.mark.skipif(not os.path.isfile(OLD_MP4), reason="g19 expected.mp4 missing")
def test_g19_clip_start_matches_source_frame0(tmp_path):
    from PySide6.QtGui import QImage
    from cutora.preview.engine import PreviewEngine, timeline_frame_index
    from cutora.model.project_io import deserialize_render_task
    import json
    new_mp4 = str(tmp_path / "g19_new.mp4")
    data = json.loads(open(os.path.join(G19, "project.asproj.json"), encoding="utf-8").read())
    task = deserialize_render_task(data["tasks"][0])
    import golden_check as gc
    host, _app = gc.boot_host()
    gc.reset_case_caches()
    outs = gc.render_serial(host, [task], str(tmp_path / "out"), "quality", False)
    assert outs and os.path.isfile(outs[0])
    os.replace(outs[0], new_mp4)

    eng = PreviewEngine()
    try:
        for tag, start_t, src_name in CLIPS:
            fps = 30.0
            frame_n = timeline_frame_index(start_t, fps)
            if frame_n / fps < start_t:
                frame_n += 1
            t_paint = frame_n / fps
            src = os.path.join(MEDIA, src_name)
            assert os.path.isfile(src), src
            painted = eng.video_frame_at(task, float(t_paint))
            src0 = str(tmp_path / ("%s_src0.png" % tag))
            newf = str(tmp_path / ("%s_new.png" % tag))
            oldf = str(tmp_path / ("%s_old.png" % tag))
            assert _extract_n(src, 0, src0, scale=(1080, 1920)), (tag, "src0")
            assert _extract_n(new_mp4, frame_n, newf), (tag, "new")
            assert _extract_n(OLD_MP4, frame_n, oldf), (tag, "old")
            src_a = _rgb_png(src0)
            new_a = _rgb_png(newf)
            old_a = _rgb_png(oldf)
            paint_a = _qimg_rgb(painted)
            mad_src = _mad(paint_a, src_a)
            mad_new = _mad(new_a, paint_a)
            mad_old = _mad(old_a, paint_a)
            print("G19_FRAME", tag, "n", frame_n, "t", t_paint,
                  "src", mad_src, "new", mad_new, "old", mad_old, flush=True)
            assert mad_src <= 0.04, (tag, mad_src)
            assert mad_new <= 2.0 / 255.0 + 0.012, (tag, mad_new)
            if abs(float(start_t) * fps - round(float(start_t) * fps)) < 1e-6:
                assert mad_old > mad_new + 0.05, (tag, mad_old, mad_new)
    finally:
        eng.close()


def _qimg_rgb(img):
    from PySide6.QtGui import QImage
    import numpy as np
    conv = img.convertToFormat(QImage.Format.Format_RGB888)
    w, h = conv.width(), conv.height()
    bpl = conv.bytesPerLine()
    arr = np.frombuffer(bytes(conv.constBits()), dtype=np.uint8)
    arr = arr.reshape((h, bpl))[:, :w * 3].reshape((h, w, 3))
    return arr.copy()


@pytest.mark.skipif(
    not (os.path.isfile(OLD_MP4) and os.path.isfile(NEW_MP4)),
    reason="g19 old/new expected.mp4 missing")
def test_g19_audio_unchanged(tmp_path):
    pairs = [
        (OLD_MP4, os.path.join(G19_2, "expected.mp4"), "g19"),
        (os.path.join(G19B, "expected.mp4"), os.path.join(G19B_2, "expected.mp4"), "g19b"),
    ]

    def _wav_md5(mp4, dest):
        cmd = [
            _ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
            "-i", mp4, "-vn", "-ac", "1", "-ar", "48000", dest,
        ]
        r = subprocess.run(cmd, capture_output=True, timeout=60)
        assert r.returncode == 0 and os.path.isfile(dest), (mp4, r.stderr)
        h = hashlib.md5()
        with open(dest, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 16), b""):
                h.update(chunk)
        return h.hexdigest()

    for old, new, tag in pairs:
        if not (os.path.isfile(old) and os.path.isfile(new)):
            continue
        a = _wav_md5(old, str(tmp_path / ("%s_old.wav" % tag)))
        b = _wav_md5(new, str(tmp_path / ("%s_new.wav" % tag)))
        print("G19_AUDIO", tag, a, b, flush=True)
        assert a == b, (tag, a, b)


def test_clip_occupancy_t8_belongs_to_second_clip():
    """Half-open [start, start+eff): t=8.0 is clip 2, not clip 1."""
    import json
    from cutora.model.project_io import deserialize_render_task
    from cutora.render.render_worker import RenderWorker
    proj = os.path.join(G19_2, "project.asproj.json")
    if not os.path.isfile(proj):
        proj = os.path.join(G19, "project.asproj.json")
    data = json.loads(open(proj, encoding="utf-8").read())
    task = deserialize_render_task(data["tasks"][0])
    tl = task.get("timeline")
    track = None
    for tr in getattr(tl, "tracks", []) or []:
        if getattr(tr, "type", None) == "video":
            track = tr
            break
    assert track is not None
    at8 = RenderWorker._find_active_clips_in_track_export(track, 8.0)
    starts = [float(getattr(c, "start_t", 0.0)) for c in at8]
    assert starts == [8.0], starts
    at7 = RenderWorker._find_active_clips_in_track_export(track, 7.999)
    starts7 = [float(getattr(c, "start_t", 0.0)) for c in at7]
    assert starts7 == [0.0], starts7

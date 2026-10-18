# -*- coding: utf-8 -*-
"""T24: transparent GIF becomes a looping clip; transparent pixels stay alpha."""
from __future__ import annotations

import os
import struct
import subprocess
import zlib

import numpy as np
import pytest

from cutora.core.config import FFMPEG_PATH, FFPROBE_PATH
from cutora.media.animated import (
    cache_path_for,
    classify_animated,
    import_animated_as_clip,
)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _write_rgba_png(path, rgba):
    h, w = rgba.shape[:2]
    raw = b"".join(b"\x00" + rgba[y].tobytes() for y in range(h))
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    with open(path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n")
        fh.write(chunk(b"IHDR", ihdr))
        fh.write(chunk(b"IDAT", zlib.compress(raw, 9)))
        fh.write(chunk(b"IEND", b""))


def _half_clear_rgba(shift=0):
    rgba = np.zeros((16, 16, 4), dtype=np.uint8)
    rgba[:, :, 0] = 220
    rgba[:, 8:, 3] = 0
    rgba[:, :8, 3] = 255
    if shift:
        rgba[:, :, 1] = 40
    return rgba


def _make_anim_gif(tmp_path, name="t24_alpha.gif"):
    png0 = str(tmp_path / "frame0.png")
    png1 = str(tmp_path / "frame1.png")
    _write_rgba_png(png0, _half_clear_rgba(0))
    _write_rgba_png(png1, _half_clear_rgba(1))
    pal = str(tmp_path / "pal.png")
    subprocess.run(
        [FFMPEG_PATH, "-y", "-hide_banner", "-loglevel", "error",
         "-i", png0, "-vf", "palettegen=reserve_transparent=1", pal],
        check=True, timeout=30,
    )
    list_txt = str(tmp_path / "frames.txt")
    with open(list_txt, "w", encoding="utf-8") as fh:
        fh.write("file '%s'\nduration 0.1\nfile '%s'\nduration 0.1\n" % (
            png0.replace("\\", "/"), png1.replace("\\", "/")))
    gif = str(tmp_path / name)
    r = subprocess.run(
        [FFMPEG_PATH, "-y", "-hide_banner", "-loglevel", "error",
         "-f", "concat", "-safe", "0", "-i", list_txt, "-i", pal,
         "-lavfi", "[0:v][1:v]paletteuse=alpha_threshold=128", gif],
        capture_output=True, timeout=30,
    )
    assert r.returncode == 0, r.stderr
    return gif


def _probe_nb_frames(path):
    r = subprocess.run(
        [FFPROBE_PATH, "-v", "error", "-select_streams", "v:0",
         "-count_frames", "-show_entries", "stream=nb_read_frames,nb_frames",
         "-of", "default=noprint_wrappers=1", path],
        capture_output=True, text=True, timeout=40,
    )
    n = None
    for line in (r.stdout or "").splitlines():
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if k in ("nb_read_frames", "nb_frames") and v and v.upper() != "N/A":
            try:
                n = int(float(v))
            except ValueError:
                continue
            if k == "nb_read_frames":
                break
    return n


def _probe_duration(path):
    r = subprocess.run(
        [FFPROBE_PATH, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, timeout=20,
    )
    txt = (r.stdout or "").strip()
    if not txt or txt.upper() == "N/A":
        return None
    return float(txt)


def test_t24_transparent_gif_loop_and_alpha(tmp_path):
    gif = _make_anim_gif(tmp_path)
    assert classify_animated(gif) == "gif"
    clip = import_animated_as_clip(gif, ffmpeg=FFMPEG_PATH)
    assert clip["loop"] is True
    assert clip["alpha"] is True
    assert os.path.isfile(clip["path"])
    frame = str(tmp_path / "out.png")
    subprocess.run(
        [FFMPEG_PATH, "-y", "-hide_banner", "-loglevel", "error",
         "-i", clip["path"], "-frames:v", "1", "-pix_fmt", "rgba", frame],
        check=True, timeout=30,
    )
    import cv2
    im = cv2.imread(frame, cv2.IMREAD_UNCHANGED)
    assert im is not None and im.shape[2] >= 4
    px_clear = im[8, 12]
    px_solid = im[8, 4]
    assert int(px_clear[3]) < 40, px_clear
    assert int(px_solid[3]) > 200, px_solid


def test_t24_static_webp_is_still_image(tmp_path):
    png = str(tmp_path / "still.png")
    _write_rgba_png(png, _half_clear_rgba())
    webp = str(tmp_path / "still.webp")
    r = subprocess.run(
        [FFMPEG_PATH, "-y", "-hide_banner", "-loglevel", "error",
         "-i", png, "-c:v", "libwebp", "-frames:v", "1", webp],
        capture_output=True, timeout=30,
    )
    assert r.returncode == 0, r.stderr
    assert os.path.isfile(webp)
    n = _probe_nb_frames(webp)
    assert n is None or n <= 1, n
    assert classify_animated(webp) is None
    with pytest.raises(ValueError):
        import_animated_as_clip(webp, ffmpeg=FFMPEG_PATH)


def test_t24_anim_cache_uses_file_fingerprint(tmp_path):
    gif = _make_anim_gif(tmp_path, "cache.gif")
    a = cache_path_for(gif)
    b = cache_path_for(gif)
    assert a == b
    assert "._cutora_anim." in os.path.basename(a)
    clip = import_animated_as_clip(gif, ffmpeg=FFMPEG_PATH)
    assert os.path.normcase(clip["path"]) == os.path.normcase(a)
    st = os.stat(gif)
    os.utime(gif, (st.st_atime + 5, st.st_mtime + 5))
    c = cache_path_for(gif)
    assert c != a, (a, c)
    with open(gif, "ab") as fh:
        fh.write(b"\x00\x00")
    d = cache_path_for(gif)
    assert d != a and d != c


def test_t24_animated_import_error_reaches_status(tmp_path, monkeypatch):
    gif = _make_anim_gif(tmp_path, "boom.gif")

    def _boom(*_a, **_k):
        raise RuntimeError("decode exploded")

    monkeypatch.setattr("cutora.media.animated.decode_animated", _boom)
    from cutora.media.animated import try_import_animated
    info, err = try_import_animated(gif, ffmpeg=FFMPEG_PATH)
    assert info is None
    assert err
    assert "decode exploded" in err

    class _Bar:
        def __init__(self):
            self.msgs = []

        def showMessage(self, msg, timeout=0):
            self.msgs.append((msg, timeout))

    class _Host:
        def __init__(self):
            self.bar = _Bar()
            self.dialogs = []

        def statusBar(self):
            return self.bar

    host = _Host()
    from cutora.media.animated import report_animated_import_error
    report_animated_import_error(host, err)
    assert host.bar.msgs, "status bar was not notified"
    assert "decode exploded" in host.bar.msgs[0][0]

    src = open(
        os.path.join(REPO, "cutora", "ui", "main_window.py"), encoding="utf-8"
    ).read()
    chunk = src[src.find("loop = False"):src.find("if ext in AUD_EXT")]
    assert "try_import_animated" in chunk
    assert "report_animated_import_error" in chunk
    assert "fps=" in chunk
    assert "except Exception:\n            loop = False" not in src
    # Dialog lives in report_animated_import_error (one shot), not a second QMessageBox here.
    assert "QMessageBox.warning" not in chunk


def test_t24_batch_load_animated_becomes_loop_video(tmp_path):
    gif = _make_anim_gif(tmp_path, "batch.gif")
    from cutora.batch.batch_load import apply_to_task
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track

    tl = Timeline(720, 1280, 30.0)
    v = Track("video")
    v.is_main_video = True
    v.clips.append(Clip("s", 0.0, 5.0, 0.0))
    tl.add_track(v)
    task = {
        "video": "x.mp4",
        "duration": 5.0,
        "dims": (720, 1280),
        "timeline": tl,
        "fps": 30.0,
    }
    apply_to_task(task, {
        "tab": "image", "image": gif,
        "start_sec": 0.0, "end_sec": 2.0,
    }, fps=30.0)
    row = task["batch_load"][0]
    assert row["loop"] is True
    assert row["kind"] == "video"
    assert row["path"] != gif
    assert os.path.isfile(row["path"])

    from cutora.batch.script_insert_image import insert_into_tasks

    class _Host:
        def _tl_of(self, t):
            return t.get("timeline")

    task2 = {
        "video": "x.mp4",
        "duration": 5.0,
        "dims": (720, 1280),
        "timeline": Timeline(720, 1280, 30.0),
        "image_overlays": [],
    }
    task2["timeline"].add_track(Track("video"))
    insert_into_tasks(
        [task2], gif, start_sec=0.0, end_sec=2.0, host=_Host(),
    )
    video_tracks = [
        t for t in task2["timeline"].tracks
        if t.type == "video" and not getattr(t, "is_main_video", False)
    ]
    assert video_tracks, "animated gif was not placed on a video overlay track"
    clips = [c for t in video_tracks for c in (t.clips or [])]
    assert clips, "no looping video clip"
    assert clips[0].loop is True
    image_clips = [
        c for t in task2["timeline"].tracks if t.type == "image"
        for c in (t.clips or [])
    ]
    assert not image_clips, "animated gif is invisible on an image track"
    src = task2["timeline"].sources[clips[0].source_id]
    assert os.path.isfile(src.path)
    assert src.path.lower().endswith(".mov")


def test_t24_sequence_uses_project_fps(tmp_path):
    for i, shift in enumerate((0, 1), start=1):
        _write_rgba_png(str(tmp_path / ("seq_%03d.png" % i)), _half_clear_rgba(shift))
    seq = str(tmp_path / "seq_%03d.png")
    assert classify_animated(seq) == "sequence"
    clip = import_animated_as_clip(seq, ffmpeg=FFMPEG_PATH, fps=12.0)
    assert os.path.isfile(clip["path"])
    dur = _probe_duration(clip["path"])
    assert dur is not None
    # 2 frames @ 12 fps ≈ 2/12 s; ffmpeg image2 default is 25 fps → 2/25 s
    assert abs(dur - (2.0 / 12.0)) < 0.08, dur
    assert abs(dur - (2.0 / 25.0)) > 0.02, dur

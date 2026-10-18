# -*- coding: utf-8 -*-
"""T08d: sidecar flags default off; write_all honors explicit flags."""
from __future__ import annotations

import json
import os
import subprocess

from cutora.core.paths import FFMPEG
from cutora.render.export_sidecar import sidecar_flags, write_all, write_subtitles
from cutora.subtitle.model import Segment, segments_to_project_dicts


def test_default_flags_all_off():
    flags = sidecar_flags({})
    assert flags["srt"] is False
    assert flags["vtt"] is False
    assert flags["ass"] is False
    assert flags["cover"] is False
    assert flags["cover_burn"] is False
    assert flags["report"] is False
    assert flags.get("chapters") is False


def test_write_srt_vtt_ass_and_report(tmp_path):
    segs = [
        Segment(id="1", start=0.0, end=1.2, text="Hello", words=[]),
        Segment(id="2", start=1.2, end=2.0, text="World", words=[]),
    ]
    task = {
        "video": str(tmp_path / "src.mp4"),
        "segments": segments_to_project_dicts(segs),
    }
    out = str(tmp_path / "clip_FINAL.mp4")
    open(out, "wb").write(b"not-a-real-mp4")
    flags = {
        "srt": True, "vtt": True, "ass": True,
        "cover": False, "cover_burn": True, "report": True,
    }
    meta = {
        "profile": "social-vertical",
        "encoder": "libx264",
        "elapsed_sec": 1.5,
        "loudness": {
            "mode": "lufs14", "target_lufs": -14.0,
            "applied": True, "measured_lufs": -14.1,
        },
    }
    result = write_all(out, task, meta, flags)
    assert os.path.isfile(tmp_path / "clip_FINAL.srt")
    assert os.path.isfile(tmp_path / "clip_FINAL.vtt")
    assert os.path.isfile(tmp_path / "clip_FINAL.ass")
    srt = (tmp_path / "clip_FINAL.srt").read_text(encoding="utf-8")
    assert "Hello" in srt and "World" in srt
    report = json.loads((tmp_path / "clip_FINAL.export.json").read_text(encoding="utf-8"))
    assert report["profile"] == "social-vertical"
    assert report["encoder"] == "libx264"
    assert report["subtitle_count"] == 2
    assert report["loudness"]["mode"] == "lufs14"
    assert report["loudness"]["measured_lufs"] == -14.1
    assert report["loudness"]["target_lufs"] == -14.0
    assert result["cover"] is None


def test_cover_from_finished_not_opencv_source(tmp_path):
    src = str(tmp_path / "src.mp4")
    out = str(tmp_path / "done.mp4")
    # Distinct colours: source red, finished blue — cover must match finished.
    subprocess.check_call([
        FFMPEG, "-y", "-f", "lavfi", "-i", "color=c=red:s=160x120:r=25:d=1.2",
        "-pix_fmt", "yuv420p", src,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.check_call([
        FFMPEG, "-y", "-f", "lavfi", "-i", "color=c=blue:s=160x120:r=25:d=1.2",
        "-pix_fmt", "yuv420p", out,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    task = {"video": src, "segments": []}
    flags = {"srt": False, "vtt": False, "ass": False, "cover": True, "cover_burn": True}
    result = write_all(out, task, {"encoder": "libx264"}, flags)
    cover = result["cover"]
    assert cover and os.path.isfile(cover)
    import cv2
    img = cv2.imread(cover)
    assert img is not None
    b, g, r = img[60, 80]
    assert b > 150 and r < 80, (int(b), int(g), int(r))


def test_empty_segments_skips_srt(tmp_path):
    out = str(tmp_path / "x.mp4")
    open(out, "wb").write(b"x")
    written = write_subtitles(out, {"segments": []}, {"srt": True, "vtt": True, "ass": True})
    assert written == []
    assert not os.path.isfile(tmp_path / "x.srt")

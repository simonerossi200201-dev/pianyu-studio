# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q160 · 单个视频分段并行导出：切段算法、开关、子进程只画一段只出视频、拼接后帧数 / 时长和整条导出一致。"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_plan_and_count():
    from cutora.render import segment_export as S
    assert S.plan_segments(0, 600, 3) == [(0, 200), (200, 400), (400, 600)]
    assert S.plan_segments(10, 21, 4) == [(10, 13), (13, 16), (16, 19), (19, 21)]
    assert S.plan_segments(5, 5, 3) == []
    # CPU 每 4 线程一段，2～8；短视频按每段 ≥ 4 秒砍；小机器不分
    assert S.segment_count(30 * 600, 30.0, cpu_count=24, avail_ram_gb=64) == 6
    assert S.segment_count(30 * 600, 30.0, cpu_count=8, avail_ram_gb=64) == 2
    assert S.segment_count(30 * 600, 30.0, cpu_count=4, avail_ram_gb=64) == 1
    assert S.segment_count(30 * 6, 30.0, cpu_count=24, avail_ram_gb=64) == 1        # 6 秒：不够两段
    assert S.segment_count(30 * 9, 30.0, cpu_count=24, avail_ram_gb=64) == 2
    assert S.segment_count(30 * 600, 30.0, cpu_count=64, avail_ram_gb=64) == 8       # 上限
    assert S.segment_count(30 * 600, 30.0, cpu_count=24, avail_ram_gb=6) == 1        # 内存不够


def test_enabled_switch(monkeypatch, tmp_path):
    from cutora.render import segment_export as S
    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path / "ud"))
    monkeypatch.delenv(S.ENV_KEY, raising=False)
    assert S.enabled() is True                       # 默认开
    S.set_enabled(False)
    assert S.enabled() is False
    monkeypatch.setenv(S.ENV_KEY, "1")
    assert S.enabled() is True
    monkeypatch.setenv(S.ENV_KEY, "0")
    assert S.enabled() is False
    monkeypatch.delenv(S.ENV_KEY)
    S.set_enabled(True)
    assert S.should_use({"video": "x"}, cpu_count=24) is True
    assert S.should_use({"video": "x"}, cpu_count=4) is False


def _ffmpeg():
    from cutora.core.config import FFMPEG_PATH
    ff = FFMPEG_PATH if os.path.isfile(FFMPEG_PATH) else shutil.which("ffmpeg")
    if not ff:
        pytest.skip("no ffmpeg")
    return ff


def _src(tmp_path, dur=6):
    ff = _ffmpeg()
    out = str(tmp_path / "src.mp4")
    subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x568:r=25:d=%d" % dur,
                    "-f", "lavfi", "-i", "sine=f=440:d=%d" % dur, "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", out], check=True)
    return out


def _task(src, dur=6.0):
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, MediaSource, Track
    tl = Timeline(320, 568, 25)
    tr = Track("video")
    tr.is_main_video = True
    tl.add_track(tr)
    s = MediaSource(src, duration=dur, probe=False)
    tl.sources[s.id] = s
    c = Clip(s.id, 0.0, dur, 0.0)
    c.is_main = True
    tr.clips.append(c)
    at = Track("audio")
    tl.add_track(at)
    ac = Clip(s.id, 0.0, dur, 0.0)
    ac.audio_role = "main"
    at.clips.append(ac)
    return {"timeline": tl, "video": src, "duration": dur, "trim_end_sec": dur, "dims": (320, 568),
            "segments": [{"start": 0.5, "end": 2.5, "text": "分段", "words": []}],
            "text_overlays": [{"type": "text", "id": 1, "content": "T", "start": 0.0, "end": dur, "x": 20, "y": 40,
                               "w": 200, "h": 60, "fontsize": 40, "font_family": "Arimo", "color": "#FFFFFF",
                               "opacity": 255, "rotation": 0}],
            "image_overlays": [], "style": {}}


def _ffprobe():
    """以前写的是 ffmpeg 路径 .replace("ffmpeg", "ffprobe") —— Windows 上内置的是 runtime\\ffmpeg\\ffmpeg.exe，
    目录名也被换掉，路径就错了（云端用系统 /usr/bin/ffmpeg 没暴露）。"""
    from cutora.core.config import FFPROBE_PATH
    _ffmpeg()
    p = FFPROBE_PATH if os.path.isfile(FFPROBE_PATH) else shutil.which("ffprobe")
    if not p:
        pytest.skip("no ffprobe")
    return p


def _frames(path):
    probe = _ffprobe()
    r = subprocess.run([probe, "-v", "error", "-select_streams", "v:0", "-count_frames",
                        "-show_entries", "stream=nb_read_frames", "-of", "json", path],
                       capture_output=True, text=True)
    return int(json.loads(r.stdout)["streams"][0]["nb_read_frames"])


def _run(worker):
    st = {"err": None, "ok": False}
    worker.error.connect(lambda m: st.update(err=m))
    worker.finished.connect(lambda: st.update(done=True, ok=True))
    worker.run()
    return st


def test_render_worker_video_only_segment(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path / "ud"))
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication(["q160"])
    from cutora.render.render_worker import RenderWorker
    src = _src(tmp_path, 6)
    out = str(tmp_path / "seg.mp4")
    w = RenderWorker(_task(src), out, "quality", False, export_logs=False,
                     frame_range=(50, 100), video_only=True, compose_workers=1)
    pcts = []
    w.progress.connect(pcts.append)
    st = _run(w)
    assert st["ok"] and not st["err"], st
    assert _frames(out) == 50                     # 只画了 [50, 100)
    assert pcts[-1] == 100 and all(0 <= p <= 100 for p in pcts)
    # 只出视频：没有音轨
    probe = _ffprobe()
    r = subprocess.run([probe, "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", out],
                       capture_output=True, text=True)
    assert r.stdout.strip().splitlines() == ["video"]


def test_segment_export_matches_serial_frames(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path / "ud"))
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication(["q160"])
    from cutora.render import segment_export as S
    from cutora.render.render_worker import RenderWorker
    src = _src(tmp_path, 6)
    serial = str(tmp_path / "serial.mp4")
    st = _run(RenderWorker(_task(src), serial, "quality", False, export_logs=False, compose_workers=1))
    assert st["ok"], st
    seg = str(tmp_path / "seg.mp4")
    w = S.SegmentExportWorker(_task(src), seg, "quality", False, export_logs=False, segments=2)
    st = _run(w)
    assert st["ok"] and not st["err"], (st, getattr(w, "_why", None))
    assert w.used_segments == 2 and not w.fell_back, getattr(w, "_why", None)
    assert _frames(seg) == _frames(serial) == 150
    probe = _ffprobe()
    r = subprocess.run([probe, "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", seg],
                       capture_output=True, text=True)
    assert sorted(r.stdout.strip().splitlines()) == ["audio", "video"]   # 混过音了
    assert w.produced_paths == [seg] and not os.path.isdir(w._tmpdir or "/nonexistent")
    # 画面一致（编码噪声级别）。整条导出的顺序解码在几十帧后比源落后一帧（老问题，改它会动金样例，
    # 没动）；分段开头是精确 seek，所以段首那一两帧和整条导出差一帧 —— 按 ±1 帧比。
    import cv2
    import numpy as np

    def _all(path):
        c = cv2.VideoCapture(path)
        out = []
        while True:
            ok, f = c.read()
            if not ok:
                break
            out.append(f.astype(int))
        return out
    A, B = _all(serial), _all(seg)
    assert len(A) == len(B) == 150
    for fi in (0, 40, 74, 75, 76, 77, 120, 149):
        best = min(float(np.abs(A[j] - B[fi]).mean()) for j in range(max(0, fi - 1), min(150, fi + 2)))
        assert best < 3.0, (fi, best)


def test_segment_failure_falls_back_to_serial(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path / "ud"))
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication(["q160"])
    from cutora.render import segment_export as S
    src = _src(tmp_path, 6)
    out = str(tmp_path / "fb.mp4")
    w = S.SegmentExportWorker(_task(src), out, "quality", False, export_logs=False, segments=2)
    # 子进程命令指向一个必败的脚本
    bad = tmp_path / "bad.py"
    bad.write_text("import sys; sys.exit(9)\n", encoding="utf-8")
    monkeypatch.setattr(w, "_child_cmd", lambda job: ([sys.executable, str(bad)], str(tmp_path)))
    st = _run(w)
    assert st["ok"] and not st["err"], st
    assert w.fell_back and _frames(out) == 150

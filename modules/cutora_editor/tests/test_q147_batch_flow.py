# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""批量加载：短窗口时间戳、图片接在标题后面、拆分工程的自动重启。"""
from __future__ import annotations

import ast
import os

import pytest

from cutora.batch import text_locate_asr as LA
from cutora.batch.text_batch import follow_title_span
from cutora.batch import queue_relaunch as qr
from cutora.render.export_guard import guard_should_close


def test_clamp_window_never_sends_a_long_clip():
    assert LA.clamp_window(0, 15) == (0.0, 15.0)
    assert LA.clamp_window(0, 12) == (0.0, 12.0)
    start, end = LA.clamp_window(0, 240)
    assert end - start <= 30.0
    assert end <= 60.0
    start, end = LA.clamp_window(40, 240)
    assert end <= 60.0
    assert end - start <= 30.0
    assert LA.clamp_window(None, None) == (0.0, 15.0)


def test_words_become_one_segment_each():
    segs = LA.words_to_segments([
        ("希望", 0.4, 1.1),
        ("", 1.1, 1.2),
        ("你能看完", 1.2, 2.8),
        ("坏", 3.0, 1.0),
    ])
    assert segs == [
        {"start": 0.4, "end": 1.1, "text": "希望"},
        {"start": 1.2, "end": 2.8, "text": "你能看完"},
    ]


def test_locate_windows_do_not_share_a_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path / "ud"))
    LA.reset_cache()
    vid = tmp_path / "a.mp4"
    vid.write_bytes(b"not really a video")
    seen = []

    def _fake(path, language="auto"):
        seen.append((LA._ctx["start"], LA._ctx["end"], language))
        return [{"start": 0.2, "end": 1.4, "text": "标题"}]

    monkeypatch.setattr(LA, "_transcribe_uncached", _fake)
    assert LA.segments_for_locate(str(vid), start=0, end=12)[0]["text"] == "标题"
    assert LA.segments_for_locate(str(vid), start=0, end=15)[0]["text"] == "标题"
    assert LA.segments_for_locate(str(vid), start=0, end=12)[0]["end"] == 1.4
    assert seen == [(0.0, 12.0, "auto"), (0.0, 15.0, "auto")]


def test_locate_batch_is_serial_and_stops_when_memory_runs_out(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path / "ud2"))
    LA.reset_cache()
    ok1 = tmp_path / "ok1.mp4"
    boom = tmp_path / "boom.mp4"
    ok2 = tmp_path / "ok2.mp4"
    for p in (ok1, boom, ok2):
        p.write_bytes(b"x" + os.path.basename(p).encode())
    made = []

    class _Client:
        def __init__(self):
            made.append(self)
            self.calls = []
            self.closed = 0

        def start(self, lang):
            self.lang = lang

        def transcribe(self, path, dur, language, timeout_sec=120.0,
                       win_start=None, win_end=None):
            self.calls.append((path, win_start, win_end))
            if os.path.basename(path).startswith("boom"):
                raise RuntimeError("CUDA out of memory")
            return [("hello", 0.3, 1.6)], False, 2

        def close(self):
            self.closed += 1

    monkeypatch.setattr("cutora.asr.clip_worker.AsrClient", _Client)
    got = []

    def _one(path, segs, err):
        got.append((os.path.basename(path), list(segs or []), err))

    LA.run_locate_batch(
        [str(ok1), str(boom), str(ok2)], _one, start=0, end=12)
    assert len(made) == 1
    assert [row[0] for row in got] == ["ok1.mp4", "boom.mp4", "ok2.mp4"]
    assert got[0][1] == [{"start": 0.3, "end": 1.6, "text": "hello"}]
    assert got[0][2] == ""
    assert "out of memory" in got[1][2].lower()
    assert "已停止" in got[2][2]
    assert [os.path.basename(p) for p, _s, _e in made[0].calls] == [
        "ok1.mp4", "boom.mp4"]
    assert made[0].calls[0][1:] == (0.0, 12.0)
    assert made[0].closed >= 1
    # 已经记下的不再占显卡。
    LA.run_locate_batch([str(ok1)], _one, start=0, end=12)
    assert len(made) == 1
    assert LA.lookup(str(ok1), 0, 12)[0]["text"] == "hello"


def test_model_start_oom_does_not_transcribe_the_batch(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path / "ud3"))
    LA.reset_cache()
    vid = tmp_path / "a.mp4"
    vid.write_bytes(b"x")

    class _Client:
        def start(self, lang):
            raise RuntimeError("CUDA out of memory")

        def transcribe(self, *args, **kwargs):
            raise AssertionError("模型都没起来，不能再转写")

        def close(self):
            return None

    monkeypatch.setattr("cutora.asr.clip_worker.AsrClient", _Client)
    got = []
    LA.run_locate_batch([str(vid)], lambda p, s, e: got.append(e), start=0, end=15)
    assert got and "已停止" in got[0]


def test_image_follows_the_title_until_the_video_ends():
    segs = [
        {"start": 0.4, "end": 1.2, "text": "希望"},
        {"start": 1.2, "end": 2.8, "text": "你能坚持看完"},
    ]
    task = {"text_overlays": []}
    got = follow_title_span(task, segs, "希望你能坚持看完", 180)
    assert got[0] == pytest.approx(2.8)
    assert got[1] == pytest.approx(180.0)
    assert got[2] == "title"
    assert follow_title_span(task, segs, "完全另一句", 180) is None
    assert follow_title_span(task, segs, "希望你能坚持看完", 2.8) is None
    titled = {"text_overlays": [
        {"start": 8.0, "end": 10.0},
        {"start": 1.0, "end": 4.0},
    ]}
    got = follow_title_span(titled, [], "", 100)
    assert got[0] == pytest.approx(4.0)
    assert got[1] == pytest.approx(100.0)
    assert got[2] == "overlay"


def test_guard_stays_open_after_a_normal_export():
    assert guard_should_close({"auto_close": True, "status": "finished"})
    assert guard_should_close({"status": "handoff"})
    assert guard_should_close({"status": "closed"})
    assert not guard_should_close({"status": "finished"})
    assert not guard_should_close(None)


def test_relaunch_plan_does_not_import_cutora():
    tree = ast.parse(open(qr.__file__, encoding="utf-8").read())
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            assert "cutora" not in (node.module or "")
        elif isinstance(node, ast.Import):
            assert all("cutora" not in a.name for a in node.names)
    py = r"D:\runtime\python.exe"
    cmd, cwd, env = qr.plan_relaunch(
        ["queue_relaunch.py", "99", py, "-m", "cutora", "--queue-continue"],
        r"D:\0_Dev\CUTORA",
    )
    assert cmd == [py, "-u", "-m", "cutora", "--queue-continue"]
    assert os.path.normcase(cwd) == os.path.normcase(r"D:\0_Dev\CUTORA")
    assert env["PYTHONPATH"].split(os.pathsep)[0] == os.path.abspath(r"D:\0_Dev\CUTORA")
    root = qr.repo_root_from_here()
    assert os.path.isfile(os.path.join(root, "cutora", "batch", "queue_relaunch.py"))

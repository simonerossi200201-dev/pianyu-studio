# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q140 · 轨道共享「自动匹配目标视频时长」不勾也照样拉伸。

对话框读了勾选，但只把轨道类型传给 batch.share_tracks，命令体里写死
auto_fit_duration=True。现在逐条轨道传 keep_duration_track_ids。"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _host():
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, MediaSource, Track
    from cutora.ui.command_bodies import CommandBodies
    from cutora.ui.main_window import MainWindow

    def task(dur, with_text):
        tl = Timeline(1080, 1920, 30)
        v = Track("video")
        tl.add_track(v)
        tl.sources["v"] = MediaSource("x.mp4", duration=dur, probe=False)
        v.clips.append(Clip("v", 0.0, dur, 0.0))
        t = {"timeline": tl, "video": "x.mp4", "text_overlays": [], "image_overlays": []}
        if with_text:
            tr = Track("text")
            tr.enable_batch = True
            tl.add_track(tr)
            t["text_overlays"].append({"type": "text", "content": "Hi", "_track_id": tr.id,
                                       "start": 2.0, "end": 7.5, "x": 10, "y": 10, "w": 300, "h": 80})
        return t

    class Host(CommandBodies):
        pass

    h = Host.__new__(Host)
    h.tasks = [task(20.0, True), task(60.0, False), task(45.0, False)]
    for name in ("share_tracks", "_collect_overlays_for_track", "_get_first_track_id_of_type"):
        setattr(Host, name, getattr(MainWindow, name))
    return h


def _text_ov(task):
    return task["text_overlays"][0]


def test_keep_duration_leaves_times_alone():
    h = _host()
    src_tr = [t for t in h.tasks[0]["timeline"].tracks if t.type == "text"][0]
    out = h.command_share_tracks(0, [1, 2], track_ids=[src_tr.id],
                                 keep_duration_track_ids=[src_tr.id])
    assert out["applied"] == 2
    for i in (1, 2):
        ov = _text_ov(h.tasks[i])
        assert (ov["start"], ov["end"]) == (2.0, 7.5)


def test_auto_fit_still_stretches_by_default():
    h = _host()
    h.command_share_tracks(0, [1, 2])
    assert (_text_ov(h.tasks[1])["start"], _text_ov(h.tasks[1])["end"]) == (0.0, 60.0)
    assert _text_ov(h.tasks[2])["end"] == 45.0


def test_global_flag_off_keeps_all():
    h = _host()
    h.command_share_tracks(0, [1], auto_fit_duration=False)
    assert _text_ov(h.tasks[1])["end"] == 7.5


def test_dialog_passes_keep_ids_to_the_command():
    import inspect
    from cutora.ui.main_window import MainWindow
    src = inspect.getsource(MainWindow._share_tracks_from_source)
    assert "keep_duration_track_ids" in src and "track_ids" in src
    from cutora.core.cmds.gui import cmd_batch_share_tracks
    assert "keep_duration_track_ids" in inspect.getsource(cmd_batch_share_tracks)

# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q176 · 批量删空轨道（当前视频 / 全部视频，绝不删错）+ 有素材的轨道确认后整条删除 + 调整条「匹配时间线时长」。"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")
os.environ.setdefault("CUTORA_USER_DATA", tempfile.mkdtemp(prefix="cutora_q176_"))
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _rich_task():
    """各种轨道：哪些空、哪些有东西、哪些受保护。"""
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track, TransitionItem
    from cutora.render import adjust_layer as AL
    from cutora.subtitle.track_source import write_events
    tl = Timeline(320, 180, 25)
    main = Track("video")
    main.is_main_video = True                       # 主视频轨：空的也不删
    v_full = Track("video")
    v_full.clips.append(Clip("s", 0.0, 2.0, 0.0))
    v_trans = Track("video")                        # 只剩转场：也算有东西
    v_trans.transitions.append(TransitionItem(1.0, 0.5, "fade"))
    v_empty = Track("video")
    v_cell = Track("video")
    v_cell.split_cell = "A"                         # 分屏格子：空的也不删
    t_legacy = Track("text")                        # 第一条文字轨：老覆层没写 _track_id 也算它的
    t_empty = Track("text")
    i_full = Track("image")
    i_empty = Track("image")
    i_locked = Track("image")
    i_locked.locked = True
    a_full = Track("adjust")
    a_empty = Track("adjust")
    s_first = Track("subs")
    s_empty = Track("subs")
    au_empty = Track("audio")
    au_full = Track("audio")
    au_full.clips.append(Clip("s", 0.0, 1.0, 0.0))
    tl.tracks[:] = [main, v_full, v_trans, v_empty, v_cell, t_legacy, t_empty, i_full, i_empty, i_locked,
                    a_full, a_empty, s_first, s_empty, au_empty, au_full]
    segs = [{"start": 0.0, "end": 1.0, "text": "一", "words": []}]
    write_events(s_first, segs)
    ad = AL.adjust_defaults(320, 180, start=0.0, end=2.0, track_id=a_full.id)
    task = {"timeline": tl, "segments": s_first.subtitle_events if hasattr(s_first, "subtitle_events") else segs,
            "text_overlays": [{"type": "text", "content": "老", "start": 0, "end": 1}],
            "image_overlays": [{"type": "image", "_track_id": i_full.id, "start": 0, "end": 1, "elements": []}, ad],
            "duration": 6.0, "dims": [320, 180], "style": {}}
    names = dict(main=main, v_full=v_full, v_trans=v_trans, v_empty=v_empty, v_cell=v_cell, t_legacy=t_legacy,
                 t_empty=t_empty, i_full=i_full, i_empty=i_empty, i_locked=i_locked, a_full=a_full,
                 a_empty=a_empty, s_first=s_first, s_empty=s_empty, au_empty=au_empty, au_full=au_full)
    return task, names


def test_only_truly_empty_unprotected_tracks_are_removed():
    from cutora.model import track_cleanup as TC
    task, n = _rich_task()
    want = {"v_empty", "t_empty", "i_empty", "a_empty", "s_empty", "au_empty"}
    got = {k for k, tr in n.items() if tr in TC.empty_tracks(task)}
    assert got == want
    removed = TC.remove_empty_tracks(task)
    assert {k for k, tr in n.items() if tr in removed} == want
    left = task["timeline"].tracks
    for k in ("main", "v_full", "v_trans", "v_cell", "t_legacy", "i_full", "i_locked", "a_full", "s_first", "au_full"):
        assert n[k] in left, k
    assert TC.remove_empty_tracks(task) == []                    # 再来一次：没有可删的
    assert len(task["image_overlays"]) == 2 and len(task["text_overlays"]) == 1 and task["segments"]


def test_delete_track_with_content():
    from cutora.model import track_cleanup as TC
    from cutora.render import adjust_layer as AL
    task, n = _rich_task()
    assert TC.describe_items(task, n["i_full"]) == "1 张图片"
    out = TC.delete_track_with_content(task, n["i_full"])
    assert out["removed"] and out["overlays"] == 1 and n["i_full"] not in task["timeline"].tracks
    assert [o for o in task["image_overlays"] if not AL.is_adjust(o)] == []
    assert len([o for o in task["image_overlays"] if AL.is_adjust(o)]) == 1      # 调整条不受影响
    out = TC.delete_track_with_content(task, n["a_full"])
    assert out["overlays"] == 1 and task["image_overlays"] == []
    out = TC.delete_track_with_content(task, n["t_legacy"])                      # 老覆层跟着第一条文字轨走
    assert out["overlays"] == 1 and task["text_overlays"] == []
    out = TC.delete_track_with_content(task, n["s_first"])
    assert out["subs"] == 1 and task["segments"] == []
    assert not TC.delete_track_with_content(task, n["main"])["removed"]          # 主视频轨不许整条删
    assert not TC.delete_track_with_content(task, n["i_locked"])["removed"]      # 锁住的不许删
    assert TC.delete_track_with_content(task, n["v_full"])["removed"]


def test_main_window_menu_actions_and_undo(monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from test_q145b_timeline_shortcuts import _load
    from cutora.model.timeline_model import Track
    win, _tl, task = _load()
    tl = task["timeline"]
    tl.add_track(Track("image"))
    tl.add_track(Track("adjust"))
    n0 = len(tl.tracks)
    other = _rich_task()[0]
    win.tasks.append(other)
    try:
        monkeypatch.setattr(QMessageBox, "question",
                            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
        assert win.remove_empty_tracks(all_videos=False) == 2
        cur = win.tasks[win.curr_idx]
        assert len(cur["timeline"].tracks) == n0 - 2 and len(other["timeline"].tracks) == 16  # 别的视频没动
        win.undo()
        assert len(win.tasks[win.curr_idx]["timeline"].tracks) == n0
        assert win.remove_empty_tracks(all_videos=True) == 2 + 6
        assert len(win.tasks[1]["timeline"].tracks) == 10
        win.undo()
        assert len(win.tasks[1]["timeline"].tracks) == 16
        # 有素材的轨道：确认后整条删；取消就不动
        cur = win.tasks[win.curr_idx]
        txt = next(t for t in cur["timeline"].tracks if t.type == "text")
        monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))
        assert not win.delete_track_with_content(txt)
        assert txt in win.tasks[win.curr_idx]["timeline"].tracks
        monkeypatch.setattr(QMessageBox, "question",
                            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
        assert win.delete_track_with_content(txt)
        cur = win.tasks[win.curr_idx]
        assert not [t for t in cur["timeline"].tracks if t.type == "text"] and not cur["text_overlays"]
        win.undo()
        cur = win.tasks[win.curr_idx]
        assert [t for t in cur["timeline"].tracks if t.type == "text"] and cur["text_overlays"]
    finally:
        win.tasks[:] = win.tasks[:1]


def test_track_menu_lists_new_actions():
    from PySide6.QtCore import QPoint, QTimer
    from PySide6.QtWidgets import QApplication, QMenu
    from test_q145b_timeline_shortcuts import _load
    win, _tl, task = _load()
    txt = next(t for t in task["timeline"].tracks if t.type == "text")
    seen = []

    def grab():
        for m in QApplication.topLevelWidgets():
            if isinstance(m, QMenu) and m.isVisible():
                seen.extend(a.text() for a in m.findChildren(type(m.menuAction())))
                m.close()
    QTimer.singleShot(200, grab)
    win.popup_track_menu(QPoint(10, 10), txt)
    assert any("连同上面的素材" in s for s in seen) and any("删除空轨道" in s for s in seen)
    assert any("仅当前视频" in s for s in seen) and any("项目内全部视频" in s for s in seen)


def test_adjust_match_timeline_duration():
    from PySide6.QtWidgets import QApplication
    from test_q145b_timeline_shortcuts import _load
    from cutora.render import adjust_layer as AL
    from cutora.render.keyframe import set_point
    win, _tl, _task = _load()
    win.timer.stop()
    ov = win.add_adjust_item(start=2.0, dur=1.0)
    set_point(ov, "effect.amount", 0.5, 30.0)                  # 本地 0.5 秒 = 时间线 2.5 秒
    for _ in range(5):
        QApplication.processEvents()
    pp = win.prop_panel
    assert pp.ov_adjust._ov is ov
    assert pp.ov_adjust.match_timeline()
    cur = win.tasks[win.curr_idx]["image_overlays"][-1]
    assert (cur["start"], cur["end"]) == (0.0, pytest.approx(8.0))     # q145b 的时间线到 8 秒
    assert cur["keyframes"]["effect.amount"][0]["t"] == pytest.approx(2.5)
    assert not pp.ov_adjust.match_timeline()                           # 已经一样长：不动
    win.undo()
    cur = win.tasks[win.curr_idx]["image_overlays"][-1]
    assert (cur["start"], cur["end"]) == (2.0, 3.0)
    # 不算调整条自己：它比时间线还长时会缩回来
    o2 = AL.adjust_defaults(320, 180, start=0.0, end=50.0)
    assert AL.match_timeline_duration(o2, win.tasks[win.curr_idx]) and o2["end"] == pytest.approx(8.0)

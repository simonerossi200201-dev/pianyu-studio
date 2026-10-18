# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q159 · 撤销快照按视频存：一步只深拷动到的那条；撤销 / 重做原地写回、任务身份不变；
跨视频 / 增删视频的老入口仍整工程；大工程里一步撤销不再按整工程深拷。"""
from __future__ import annotations

import copy
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_scope_for_command():
    from cutora.core.undo_snapshot import scope_for_command
    assert scope_for_command("timeline.move_clip", {"track_id": "a"}) == "task"
    assert scope_for_command("subtitle.set_text", {"seg_id": 1, "media_id": 3}) == 3
    assert scope_for_command("subtitle.set_text", {"seg_id": 1, "media": "/x.mp4"}) == "all"
    assert scope_for_command("audio.volume_master", {}) == "all"
    assert scope_for_command("style.apply_subtitle_preset", {"name": "x"}) == "all"
    assert scope_for_command("media.remove", {}) == "all"
    assert scope_for_command(None, {}) == "all"
    assert scope_for_command("timeline.move_clips", {"targets": [1, 2]}) == "all"


def test_snapshot_copies_only_scoped_tasks_and_restores_in_place():
    from cutora.core.undo_snapshot import UndoSnapshot
    tasks = [{"video": "a", "segments": [{"text": "a1"}]}, {"video": "b", "segments": [{"text": "b1"}]},
             {"video": "c", "segments": []}]
    a, b, c = tasks
    snap = UndoSnapshot(tasks, 1, ["a", "b", "c"], scope="task")
    assert list(snap.copies) == [1] and snap["curr_idx"] == 1 and snap["tasks"] == tasks
    b["segments"][0]["text"] = "changed"
    a["segments"][0]["text"] = "a-changed-elsewhere"       # 这步没动 a（范围外），恢复时不碰它
    out = snap.restore_into(tasks)
    assert out is tasks and tasks[1] is b and b["segments"][0]["text"] == "b1"
    assert a["segments"][0]["text"] == "a-changed-elsewhere"
    full = UndoSnapshot(tasks, 0, [], scope="all")
    assert full.covers_all()
    del tasks[2]
    full.restore_into(tasks)
    assert len(tasks) == 3 and tasks[2] is c


def test_history_manager_keeps_snapshot_objects_and_sequential_walk():
    from cutora.core.history import HistoryManager
    from cutora.core.undo_snapshot import UndoSnapshot
    tasks = [{"v": 0, "n": 0}, {"v": 1, "n": 0}]
    mgr = HistoryManager()
    # 第一步：改 task0
    mgr.push("改0", UndoSnapshot(tasks, 0, [], "task"))
    tasks[0]["n"] = 1
    mgr.set_after(UndoSnapshot(tasks, 0, [], "task"))
    # 第二步：改 task1
    mgr.push("改1", UndoSnapshot(tasks, 1, [], "task"))
    tasks[1]["n"] = 7
    mgr.set_after(UndoSnapshot(tasks, 1, [], "task"))
    assert mgr.tail_scope() == "task"
    s = mgr.undo()
    assert isinstance(s, UndoSnapshot)
    s.restore_into(tasks)
    assert tasks[1]["n"] == 0 and tasks[0]["n"] == 1
    mgr.undo().restore_into(tasks)
    assert tasks[0]["n"] == 0
    mgr.redo().restore_into(tasks)
    assert tasks[0]["n"] == 1 and tasks[1]["n"] == 0
    mgr.redo().restore_into(tasks)
    assert tasks[1]["n"] == 7
    # 老式整工程字典仍深拷
    mgr.push("old", {"tasks": copy.deepcopy(tasks), "curr_idx": 0})
    tasks[0]["n"] = 99
    st = mgr.undo()
    assert isinstance(st, dict) and st["tasks"][0]["n"] == 1


def test_window_undo_is_task_scoped_and_fast(monkeypatch):
    from test_q145b_timeline_shortcuts import _load, _click, _key, _cur, _clips, _hist, Qt, QApplication
    from cutora.core.undo_snapshot import UndoSnapshot
    win, tl, task = _load()
    # 媒体库里再塞 60 条「别的视频」，每条带不少字幕 —— 以前每一步都把它们全深拷一遍
    import copy as _c
    heavy = []
    for i in range(60):
        t = _c.deepcopy(task)
        t["video"] = "/heavy/%d.mp4" % i
        t["segments"] = [{"start": s * 1.0, "end": s * 1.0 + 0.9, "text": "字幕" * 10,
                          "words": [{"w": "字", "start": s + k * 0.05, "end": s + k * 0.05 + 0.04} for k in range(12)]}
                         for s in range(200)]
        heavy.append(t)
    win.tasks = win.tasks + heavy
    win.file_list.blockSignals(True)
    for i in range(60):
        win.file_list.addItem("h%d.mp4" % i)
    win.file_list.blockSignals(False)
    other = win.tasks[5]
    other_id = id(other)
    other_before = _c.deepcopy(other["segments"][:3])
    win.history_mgr.history = []
    win.history_mgr.current_index = -1
    _click(tl, 1.5, "video")
    t0 = time.perf_counter()
    _key(tl, Qt.Key.Key_Delete)
    dt_cmd = time.perf_counter() - t0
    entry = win.history_mgr.history[-1]
    st = entry["state"]
    assert isinstance(st, UndoSnapshot) and list(st.copies) == [0], st.describe()
    assert isinstance(entry["after"], UndoSnapshot) and list(entry["after"].copies) == [0]
    assert len(_clips(_cur(win))[1]) == 1
    item0 = win.file_list.item(0)
    t0 = time.perf_counter()
    win.undo()
    dt_undo = time.perf_counter() - t0
    assert len(_clips(_cur(win))[1]) == 2
    assert win.file_list.item(0) is item0                               # 媒体列表没有清空重建
    assert win.tasks[5] is other and id(win.tasks[5]) == other_id          # 别的视频没被换掉
    assert other["segments"][:3] == other_before
    win.redo()
    assert len(_clips(_cur(win))[1]) == 1
    # 时间上限宽松：目标是「不随视频数量线性变慢」。61 条视频整工程深拷一遍就要秒级。
    assert dt_cmd < 1.5 and dt_undo < 2.5, (dt_cmd, dt_undo)
    win.undo()
    # 收拾：恢复成单任务，别影响同进程里别的用例
    win.tasks = win.tasks[:1]
    win.file_list.blockSignals(True)
    while win.file_list.count() > 1:
        win.file_list.takeItem(win.file_list.count() - 1)
    win.file_list.blockSignals(False)


# ───────────────────────────── Q161 历史记录：写清楚、去噪、合并、预算 ─────────────────────────────

def test_describe_and_identity():
    from cutora.core.undo_labels import describe_command, identity_of
    assert describe_command("timeline.move_clip", {"track_id": "a", "clip_index": 0, "start_t": 12.3}) == "移动片段 → 12.30 s"
    assert describe_command("subtitle.set_text", {"seg_id": 3, "text": "你好世界，这是一句很长很长的字幕"}).startswith("改字幕「你好世界")
    assert describe_command("keyframe.set", {"path": "x", "t": 1.2, "value": 5}) == "关键帧 x @ 1.20 s"
    assert describe_command("split.set_layout", {"layout": "left_right"}) == "分屏布局：左右裁切"
    assert describe_command("nope.cmd", {}, "回退标题") == "回退标题"
    assert identity_of("keyframe.set", {"path": "x", "t": 1, "value": 2}) == identity_of("keyframe.set", {"path": "x", "t": 1, "value": 9})
    assert identity_of("keyframe.set", {"path": "x"}) != identity_of("keyframe.set", {"path": "y"})


def test_history_drops_noop_and_merges_gestures():
    from cutora.core.history import HistoryManager
    from cutora.core.undo_snapshot import UndoSnapshot
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track
    tl = Timeline(320, 180, 25)
    tr = Track("video")
    tl.add_track(tr)
    tr.clips.append(Clip("s", 0.0, 2.0, 0.0))
    tasks = [{"video": "a", "timeline": tl, "segments": []}]
    mgr = HistoryManager()
    # 1) 什么都没改 → 不留
    mgr.push("点了一下", UndoSnapshot(tasks, 0, ["a"], "task"), extra={"command": "track.set_props", "params": {"track_id": tr.id, "patch": {}}})
    mgr.set_after(UndoSnapshot(tasks, 0, ["a"], "task"))
    assert mgr.history == [] and mgr.dropped_noop == 1
    # 2) 同一条轨道连拖三下 → 合成一步，撤销一次回到最初
    for x in (10.0, 20.0, 30.0):
        mgr.push("设置轨道属性", UndoSnapshot(tasks, 0, ["a"], "task"),
                 extra={"command": "track.set_props", "params": {"track_id": tr.id, "patch": {"x": x}}})
        tr.props["x"] = x
        mgr.set_after(UndoSnapshot(tasks, 0, ["a"], "task"))
    assert len(mgr.history) == 1 and mgr.merged == 2
    assert mgr.history[0]["desc"] == "轨道 x=30"
    mgr.undo().restore_into(tasks)
    assert tasks[0]["timeline"].tracks[0].props["x"] == 0.0
    mgr.redo().restore_into(tasks)
    assert tasks[0]["timeline"].tracks[0].props["x"] == 30.0
    # 3) 不同轨道 / 不同命令不合并（撤销 / 重做是原地写回：时间线对象换了，重新从任务里取）
    tl = tasks[0]["timeline"]
    tr = tl.tracks[0]
    tr2 = Track("video")
    tl.add_track(tr2)
    mgr.push("设置轨道属性", UndoSnapshot(tasks, 0, ["a"], "task"),
             extra={"command": "track.set_props", "params": {"track_id": tr2.id, "patch": {"x": 5.0}}})
    tr2.props["x"] = 5.0
    mgr.set_after(UndoSnapshot(tasks, 0, ["a"], "task"))
    assert len(mgr.history) == 2
    # 4) 内存预算：超了从最老的丢
    small = HistoryManager(budget_mb=0.001)
    for i in range(5):
        small.push("s%d" % i, UndoSnapshot(tasks, 0, ["a"], "task"))
        tr.props["x"] = float(i)
        small.set_after(UndoSnapshot(tasks, 0, ["a"], "task"))
    assert len(small.history) <= 2 and small.current_index == len(small.history) - 1
    assert small.approx_memory_mb() > 0

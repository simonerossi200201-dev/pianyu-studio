# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q154 · 预合成（原位分组）：模型、循环铺满重复份、命令 + 撤销、工程往返、时间线选中 / 菜单、检查器。"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


def _app():
    from test_q145_canvas_shortcuts import _app as app
    return app()


def _tl(n=3, dur=2.0, gap=0.0, tracks=1):
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track
    tl = Timeline(160, 90, 30.0)
    out = []
    for ti in range(tracks):
        tr = Track("video")
        tr.id = "v%d" % ti
        for i in range(n):
            c = Clip("s", 0.0, dur, i * (dur + gap))
            c.x = 10.0 * i
            tr.clips.append(c)
            out.append((tr, c))
        tl.add_track(tr)
    return tl, out


def test_create_meta_sync_and_detach():
    from cutora.model import clip_groups as CG
    tl, rows = _tl(4)
    clips = [c for _t, c in rows]
    assert all(getattr(c, "uid", "") for c in clips) and len({c.uid for c in clips}) == 4
    gid = CG.create(tl, clips[:3])
    assert CG.gid_of(clips[0]) == gid and CG.gid_of(clips[3]) == ""
    g = CG.meta(tl, gid)
    assert g["locked"] and not g["loop_fill"] and g["color"] == CG.GROUP_COLORS[0]
    assert [c for _t, c in CG.members(tl, gid)] == clips[:3]
    assert CG.span(tl, gid) == (0.0, 6.0)
    # 锁住：外观同步；时间不同步
    clips[1].x = 99.0
    clips[1].start_t = 2.5
    assert CG.sync_props(tl, clips[1]) == 2
    assert clips[0].x == 99.0 and clips[2].x == 99.0 and clips[0].start_t == 0.0
    CG.set_meta(tl, gid, locked=False, name="镜头 A", color="#4FC3F7")
    clips[0].x = 1.0
    assert CG.sync_props(tl, clips[0]) == 0                   # 解锁不同步
    assert CG.meta(tl, gid)["name"] == "镜头 A" and CG.display_name(CG.meta(tl, gid)) == "镜头 A"
    # 第二个组自动换色；加入 / 分离
    gid2 = CG.create(tl, [clips[3]])
    assert CG.meta(tl, gid2)["color"] not in ("", CG.meta(tl, gid)["color"])
    CG.join(tl, gid2, [clips[2]])
    assert CG.gid_of(clips[2]) == gid2 and len(CG.members(tl, gid)) == 2
    CG.detach(tl, [clips[0], clips[1]])
    assert CG.meta(tl, gid) is None and CG.gid_of(clips[0]) == ""
    assert CG.ungroup(tl, gid2) == 2 and not CG.groups(tl)
    with pytest.raises(ValueError):
        CG.create(tl, [])


def test_loop_fill_repeats_resync_and_trim():
    from cutora.model import clip_groups as CG
    tl, rows = _tl(2, dur=1.5)                     # 组：0–3 s
    clips = [c for _t, c in rows]
    gid = CG.create(tl, clips, loop_fill=True)
    assert CG.resync(tl, 10.0)
    tr = rows[0][0]
    reps = [c for c in tr.clips if CG.is_repeat(c)]
    assert len(reps) == 2 * 2 + 1                  # 3–6、6–9 两整轮 + 9–10 半轮（第二条 10.5 超了被裁掉）
    starts = sorted(round(c.start_t, 3) for c in reps)
    assert starts == [3.0, 4.5, 6.0, 7.5, 9.0]
    last = max(reps, key=lambda c: c.start_t)
    assert last.start_t + last.effective_duration == pytest.approx(10.0)
    assert all(c.group["repeat_of"] in {clips[0].uid, clips[1].uid} for c in reps)
    assert len({c.uid for c in tr.clips}) == len(tr.clips)
    # 幂等；真身一改就重算；外观同步也覆盖重复份
    assert not CG.resync(tl, 10.0)
    clips[1].out_t = 1.0                           # 组变成 0–2.5
    assert CG.resync(tl, 10.0)
    reps = [c for c in tr.clips if CG.is_repeat(c)]
    assert min(c.start_t for c in reps) == pytest.approx(2.5)
    clips[0].scale = 2.0
    CG.sync_props(tl, clips[0])
    assert all(c.scale == 2.0 for c in tr.clips)
    # 关掉循环：重复份全没；解除预合成也一样
    CG.set_meta(tl, gid, loop_fill=False)
    CG.resync(tl, 10.0)
    assert not any(CG.is_repeat(c) for c in tr.clips) and len(tr.clips) == 2
    CG.set_meta(tl, gid, loop_fill=True)
    CG.resync(tl, 10.0)
    assert any(CG.is_repeat(c) for c in tr.clips)
    CG.ungroup(tl, gid)
    assert len(tr.clips) == 2 and not CG.groups(tl)
    # 目标比组还短：不复制
    gid = CG.create(tl, clips, loop_fill=True)
    assert not CG.resync(tl, 2.0) and len(tr.clips) == 2


def test_commands_undo_and_project_roundtrip(tmp_path):
    _app()
    from test_q152a_keyframe_editor import _bus_window
    from cutora.model import clip_groups as CG
    from cutora.model.project_io import _rj_deserialize_timeline, _rj_serialize_timeline
    from cutora.model.timeline_model import Clip, Track
    tl, rows = _tl(3, dur=1.0)
    task = {"timeline": tl, "duration": 6.0, "text_overlays": [], "image_overlays": [], "video": str(tmp_path / "a.mp4")}
    win, bus = _bus_window(task)
    refs = [{"track_id": "v0", "clip_index": i} for i in range(3)]
    res = bus.execute("group.create", {"clips": refs[:2], "name": "开头"}, source="ui")
    gid = res["group"]
    c0, c1, c2 = tl.tracks[0].clips[:3]
    assert CG.gid_of(c0) == gid == CG.gid_of(c1) and not CG.gid_of(c2)
    bus.execute("group.join", {"group": gid, "clips": [{"uid": c2.uid}]}, source="ui")
    assert CG.gid_of(c2) == gid
    bus.execute("group.set", {"group": gid, "patch": {"loop_fill": True, "color": "#81C784"}}, source="ui")
    assert any(CG.is_repeat(c) for c in tl.tracks[0].clips) and CG.meta(tl, gid)["color"] == "#81C784"
    n_hist = len(win.history_mgr.history)
    bus.execute("group.detach", {"clips": [refs[2]]}, source="ui")
    assert not CG.gid_of(c2) and len(win.history_mgr.history) == n_hist + 1
    win.undo()
    tl2 = win.tasks[0]["timeline"]
    assert CG.gid_of(tl2.tracks[0].clips[2]) == gid
    with pytest.raises(Exception):
        bus.execute("group.ungroup", {"group": "nope"}, source="ui")
    # 重复份当目标 → 落到真身上
    rep_i = next(i for i, c in enumerate(tl2.tracks[0].clips) if CG.is_repeat(c))
    bus.execute("group.detach", {"clips": [{"track_id": "v0", "clip_index": rep_i}]}, source="ui")
    tl3 = win.tasks[0]["timeline"]
    assert len([c for c in tl3.tracks[0].clips if CG.gid_of(c) and not CG.is_repeat(c)]) == 2
    # 工程往返：uid / group 都在
    d = _rj_serialize_timeline(tl3)
    back = _rj_deserialize_timeline(d)
    assert [CG.gid_of(c) for c in back.tracks[0].clips] == [CG.gid_of(c) for c in tl3.tracks[0].clips]
    assert [c.uid for c in back.tracks[0].clips] == [c.uid for c in tl3.tracks[0].clips]
    bus.execute("group.ungroup", {"group": gid}, source="ui")
    assert not CG.groups(win.tasks[0]["timeline"])
    # 老工程没有 uid：切开 / 克隆后 id 不撞
    old = Clip("s", 0.0, 2.0, 0.0)
    right = old.split_at(1.0)
    assert old.uid != right.uid


def _timeline_env(n=3, tracks=1):
    _app()
    from test_q62_keyframes import _win_for_task
    from cutora.core.commands import get_bus, reset_bus_for_tests
    from cutora.ui.panels.property import PropertyPanel
    from cutora.ui.timeline.widget import TimelineWidget
    tl, rows = _tl(n, dur=1.0, tracks=tracks)
    task = {"timeline": tl, "duration": 8.0, "text_overlays": [], "image_overlays": [], "segments": [],
            "video": "x.mp4", "style": {}}
    reset_bus_for_tests()
    win = _win_for_task(task)
    get_bus().bind_window(win)
    tlw = TimelineWidget()
    tlw.set_data(8000, task)
    tlw.setParent(win)
    win.timeline = tlw
    panel = PropertyPanel()
    panel.setParent(win)
    win.prop_panel = panel
    win.show()
    tlw.resize(900, 300)
    QApplication.processEvents()
    return win, tlw, panel, tl, rows


def test_timeline_group_select_drag_keys_and_inspector(monkeypatch):
    from PySide6.QtGui import QKeyEvent
    from cutora.model import clip_groups as CG
    win, tlw, panel, tl, rows = _timeline_env(3)
    try:
        tr = rows[0][0]
        c0, c1, c2 = [c for _t, c in rows]
        # 框选两条 → Ctrl+G
        tlw._clip_sel = [("v0", 0), ("v0", 1)]
        tlw.selected_video_clip = (tr, c0)
        tlw._publish_selection()
        assert tlw.group_selection()
        gid = CG.gid_of(c0)
        assert gid and CG.gid_of(c1) == gid and not CG.gid_of(c2)
        assert sorted(tlw._clip_sel) == [("v0", 0), ("v0", 1)]
        # 点组里一条 = 整组选上；Ctrl+点第三条 = 单独加进来；Ctrl+点组里一条 = 整组移出
        tlw._select_video_clip(tr, c1)
        assert sorted(tlw._clip_sel) == [("v0", 0), ("v0", 1)]
        tlw._select_video_clip(tr, c2, ctrl=True)
        assert sorted(tlw._clip_sel) == [("v0", 0), ("v0", 1), ("v0", 2)]
        tlw._select_video_clip(tr, c0, ctrl=True)
        assert tlw._clip_sel == [("v0", 2)]
        # 整组拖：主片段挪 0.5 s，组员一起
        tlw._select_video_clip(tr, c0)
        tlw._clip_group_begin(c0)
        assert len(tlw._clip_group_orig) == 1 and tlw._clip_group_orig[0][1] is c1
        tlw._clip_group_apply(0.5)
        assert c1.start_t == pytest.approx(1.5) and c2.start_t == 2.0       # 组员跟着走，组外不动
        tlw._clip_group_apply(0.0)
        tlw._clip_group_orig = None
        # 解锁后点一条只选一条
        tlw.set_group_meta(gid, locked=False)
        tlw._select_video_clip(tr, c1)
        assert tlw._clip_sel == [("v0", 1)]
        tlw.set_group_meta(gid, locked=True)
        # 循环铺满 → 重复份出现，画得出来，点重复份选整组，Delete 之外不可单独裁
        tlw.set_group_meta(gid, loop_fill=True)
        reps = [c for c in tr.clips if CG.is_repeat(c)]
        assert reps and max(c.start_t + c.effective_duration for c in reps) == pytest.approx(8.0)
        tlw._select_video_clip(tr, reps[0])
        assert len(tlw._clip_sel) == 2 + len(reps)
        tlw.repaint()
        QApplication.processEvents()
        # 检查器：组一节出现；改 x 整组同步（含重复份）；解除预合成按钮
        # 主窗口把 param_changed 接到 group_sync（这里的替身窗口手动接一下，逻辑同一份）
        panel.param_changed.connect(lambda _c=None: (CG.sync_props(tl, panel._video_clip), CG.resync_task(win.tasks[0])))
        panel.load_data("video_clip", (tr, c0))
        assert panel.vid_group.isVisibleTo(panel) and "预合成组" in panel.vid_group.sec.section_title
        panel.vid_x.setValue(33.0)
        assert c1.x == 33.0 and all(c.x == 33.0 for c in reps)
        # Ctrl+Shift+G
        ev = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_G, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
        assert tlw._ops_key(ev)
        assert not CG.groups(tl) and len(tr.clips) == 3
        panel.load_data("video_clip", (tr, c0))
        assert not panel.vid_group.isVisibleTo(panel)
        # 右键菜单：有「预合成片段」；在组里时有子菜单
        from PySide6.QtWidgets import QMenu
        tlw._clip_sel = [("v0", 1), ("v0", 2)]
        m = QMenu()
        acts = tlw._group_menu(m, tr, c1)
        assert any("预合成片段" in a.text() for a in acts)
        tlw.group_selection()
        m = QMenu()
        acts = tlw._group_menu(m, tr, c1)
        texts = [a.text() for a in acts]
        assert any("解除预合成" in t for t in texts) and any("循环播放铺满" in t for t in texts)
        # 第二组 + 加入群组
        tlw._clip_sel = [("v0", 0)]
        tlw.selected_video_clip = (tr, c0)
        tlw._publish_selection()
        tlw.group_selection()
        gids = list(CG.groups(tl))
        assert len(gids) == 2 and CG.meta(tl, gids[1])["color"] != CG.meta(tl, gids[0])["color"]
        other = CG.gid_of(c1)
        tlw.join_group(other)
        assert len(CG.groups(tl)) == 1 and len(CG.members(tl, other)) == 3
    finally:
        win.close()


def test_shortcut_rows():
    from cutora.ui.shortcuts import GESTURE_ROWS
    assert any("Ctrl+G" in k for _a, k, _l in GESTURE_ROWS)

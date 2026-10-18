# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q154 · 连线导入第二代：分组逻辑、画布手势（拖段 / 连线 / 框选 / 刀）、对话框只存计划、
按计划导入成预合成、一对二自动「上 1 下 2」。"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from cutora.batch import link_groups as LG  # noqa: E402


def _app():
    from test_q145_canvas_shortcuts import _app as app
    return app()


# ───────────────────────────── 纯逻辑 ─────────────────────────────

def test_prefix_and_grouping_rules():
    assert LG.prefix_of("video01_a.mp4") == "video01"
    assert LG.prefix_of("video01-b.mov") == "video01"
    assert LG.prefix_of("clip 02.mp4") == "clip"
    assert LG.prefix_of("scene3_part12.mp4") == "scene3"
    assert LG.prefix_of("alone.mp4") == "alone"
    assert LG.prefix_of("A.mp4") == "A"
    paths = ["v01_a.mp4", "v01_b.mp4", "v02_a.mp4", "x.mp4", "v02_b.mp4"]
    assert LG.rows_by_prefix(paths) == [[0, 1], [2, 4], [3]]
    assert LG.rows_by_columns(7, 3) == [[0, 1, 2], [3, 4, 5], [6]]
    assert LG.rows_by_columns(3, 1) == [[0], [1], [2]]
    assert LG.rows_by_columns(0, 3) == []
    assert LG.normalize([[0, 0, 9], [], [2]], 4) == [[0], [2], [1], [3]]
    assert LG.group_label(paths, [0, 1], 0) == "v01" and LG.group_label(paths, [0, 2], 3) == "组 4"


def test_pattern_from_user_example():
    pat, desc = LG.pattern_from_example("video01")
    assert desc == "「video」 + 2 位数字" and pat.match("Video07_a") and not pat.match("video7_a") and not pat.match("vid01")
    assert LG.pattern_from_example("")[0] is None
    pat, desc = LG.pattern_from_example("2024-01 clip")
    assert desc == "4 位数字 + 「-」 + 2 位数字 + 「 clip」" and pat.match("2024-03 clip_b")
    ps = ["video01_a.mp4", "video01_b.mp4", "video02_a.mp4", "video02_b.mp4", "intro.mp4", "video3.mp4"]
    assert LG.rows_by_pattern(ps, "video01") == [[0, 1], [2, 3], [4], [5]]
    assert LG.rows_by_pattern(ps, "video1") == [[5], [0], [1], [2], [3], [4]]
    assert LG.rows_by_pattern(ps, "intro") == [[4], [0], [1], [2], [3], [5]]
    assert LG.rows_by_pattern(ps, "") == LG.rows_by_prefix(ps)
    assert LG.suggest_example(ps) == "video01" and LG.suggest_example(["intro_a.mp4"]) == "intro" and LG.suggest_example([]) == ""
    assert LG.suggest_example(["ending.mp4"] + ps) == "video01"        # 不会因为 ending 排最前就选它
    assert LG.group_label(ps, [2, 3], 1, "video01") == "video02" and LG.group_label(ps, [4], 7, "video01") == "组 8"
    txt = LG.describe_grouping(ps, "video01")
    assert "「video」 + 2 位数字" in txt and "4 组" in txt and "2 段" in txt
    assert "没有一段对得上" in LG.describe_grouping(ps, "zzz9")
    assert LG.describe_grouping(ps, "").startswith("自动")


def test_move_merge_split_and_link_remap():
    rows = [[0, 1, 2], [3, 4, 5], [6]]
    # 同行换位：把 2 挪到 0 前面
    assert LG.move(rows, [2], 0, 0) == [[2, 0, 1], [3, 4, 5], [6]]
    # 同行往后挪：把 0 挪到 2 后面（插入点在被搬走的段之后要往前算）
    assert LG.move(rows, [0], 0, 3) == [[1, 2, 0], [3, 4, 5], [6]]
    # 跨行追加：4 的 c 段追加到第 3 组后面
    assert LG.move(rows, [5], 2, 1) == [[0, 1, 2], [3, 4], [6, 5]]
    # 跨行插入到中间
    assert LG.move(rows, [6], 0, 1) == [[0, 6, 1, 2], [3, 4, 5]]
    # 多选一起搬，保持原先后；另起一组
    assert LG.move(rows, [4, 1], 3, 0) == [[0, 2], [3, 5], [6], [1, 4]]
    # 搬空一行 → 行消失
    assert LG.move(rows, [6], 1, 0) == [[0, 1, 2], [6, 3, 4, 5]]
    assert LG.merge(rows, [1, 6]) == [[1, 6], [0, 2], [3, 4, 5]]
    assert LG.split_out(rows, [1, 4]) == [[0, 2], [1], [3, 5], [4], [6]]
    assert LG.move(rows, [], 0, 0) == rows
    # 连线跟着「行里第一段」走
    new = LG.move(rows, [6], 0, 0)          # [[6,0,1,2],[3,4,5]]
    assert LG.remap_links([(0, 0), (1, 1), (2, 2)], rows, new) == [(0, 0), (1, 1), (2, 0)]
    assert LG.auto_links(3, 5) == [(0, 0), (1, 1), (2, 2)]
    tasks = [{"video": "a"}, {"video": "b"}]
    srcs = ["v01_a.mp4", "v01_b.mp4", "v02_a.mp4"]
    plan = LG.plan_from_rows([(1, 0), (0, 1), (0, 0), (5, 0), (0, 9)], tasks, srcs, [[0, 1], [2]])
    assert plan == [
        {"task_index": 0, "groups": [["v01_a.mp4", "v01_b.mp4"], ["v02_a.mp4"]], "names": ["v01", "v02"]},
        {"task_index": 1, "groups": [["v01_a.mp4", "v01_b.mp4"]], "names": ["v01"]},
    ]


# ───────────────────────────── 画布 ─────────────────────────────

def _view(n_left=3, n_src=6, size="medium"):
    _app()
    from cutora.ui.link_group_graph import GroupLinkView
    v = GroupLinkView()
    v.resize(1200, 700)
    v.show()
    v.set_node_size(size)
    left = [{"title": "媒体 %d" % i, "key_path": "m%d.mp4" % i} for i in range(n_left)]
    src = [{"title": "v%02d_%s.mp4" % (i // 3 + 1, "abc"[i % 3]), "key_path": "/x/v%02d_%s.mp4" % (i // 3 + 1, "abc"[i % 3])}
           for i in range(n_src)]
    v.set_items(left, src, rows=LG.rows_by_columns(n_src, 3))
    QApplication.processEvents()
    return v


def _vp(v, scene_pt):
    return v.mapFromScene(scene_pt)


def _drag(v, a, b, mods=Qt.KeyboardModifier.NoModifier):
    vp = v.viewport()
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, mods, _vp(v, a))
    for f in (0.2, 0.5, 0.8, 1.0):
        p = QPointF(a.x() + (b.x() - a.x()) * f, a.y() + (b.y() - a.y()) * f)
        QTest.mouseMove(vp, _vp(v, p))
        QApplication.processEvents()
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, mods, _vp(v, b))
    QApplication.processEvents()


def _click(v, pt, mods=Qt.KeyboardModifier.NoModifier):
    QTest.mouseClick(v.viewport(), Qt.MouseButton.LeftButton, mods, _vp(v, pt))
    QApplication.processEvents()


def test_view_rows_links_regroup_and_sizes():
    v = _view()
    try:
        assert v.rows() == [[0, 1, 2], [3, 4, 5]] and len(v._row_items) == 2 and len(v._cells) == 6
        assert v.row_label(0) == "v01" and v.row_label(1) == "v02"
        assert v.auto_link() == 2 and v.links() == [(0, 0), (1, 1)]
        assert not v.add_link(0, 0) and not v.add_link(9, 0) and v.add_link(2, 0)
        assert v._media[2].linked == 1 and v._row_items[0].linked == 2
        v.regroup_columns(2)
        assert v.rows() == [[0, 1], [2, 3], [4, 5]]
        # 连线跟着行首走：原第 1 行(0,1,2) 的首段 0 还在第 0 行；原第 2 行首段 3 现在在第 1 行
        assert sorted(v.links()) == [(0, 0), (1, 1), (2, 0)]
        v.regroup_prefix()
        assert v.rows() == [[0, 1, 2], [3, 4, 5]]
        v.regroup_columns(1)
        assert len(v.rows()) == 6 and all(len(r) == 1 for r in v.rows())
        colors = v.row_colors()
        assert len(set(colors[:6])) == 6
        for size in ("large", "small", "medium"):
            v.set_node_size(size)
            assert v.node_size() == size and len(v._cells) == 6
        v.clear_links()
        assert v.links() == []
        assert v.set_thumb("/x/v01_b.mp4", None) == 1 and v.set_thumb("m0.mp4", None) == 1
    finally:
        v.close()


def test_view_drag_cells_wire_band_knife_and_keys():
    v = _view()
    try:
        v.auto_link()
        rows0 = v._row_items
        # 拖第 1 组的 c 段(2) 到第 2 组末尾
        c2 = v._cells[2]
        a = c2.mapToScene(QPointF(c2.width() / 2, c2.height() / 2))
        r1 = rows0[1]
        b = r1.mapToScene(QPointF(r1.width() - 2, r1.height() / 2))
        _drag(v, a, b)
        assert v.rows() == [[0, 1], [3, 4, 5, 2]] and v.selected_cells() == [2]
        assert sorted(v.links()) == [(0, 0), (1, 1)]
        # 框选第 2 组的后两段，拖到最下面空白 = 另起一组
        c5, c2 = v._cells[5], v._cells[2]
        tl = c5.mapToScene(QPointF(-2, 4))
        br = c2.mapToScene(QPointF(c2.width() + 2, c2.height() + 40))     # 行下面的空白处起笔，往左上框
        _drag(v, br, tl)
        assert sorted(v.selected_cells()) == [2, 5]
        zone = v.new_row_zone()
        a = c5.mapToScene(QPointF(c5.width() / 2, c5.height() / 2))
        _drag(v, a, zone.center())
        assert v.rows() == [[0, 1], [3, 4], [5, 2]]
        # 同行换位：把 2 拖到 5 前面
        c2 = v._cells[2]
        c5 = v._cells[5]
        _click(v, c2.mapToScene(QPointF(c2.width() / 2, c2.height() / 2)))     # 多选里点一下 → 收成单选
        assert v.selected_cells() == [2]
        _drag(v, c2.mapToScene(QPointF(c2.width() / 2, c2.height() / 2)), c5.mapToScene(QPointF(2, c5.height() / 2)))
        assert v.rows() == [[0, 1], [3, 4], [2, 5]]
        # 拖线：媒体 2 的端口 → 第 3 行身上
        m2 = v._media[2]
        row2 = v._row_items[2]
        _drag(v, m2.port_pos(), row2.mapToScene(QPointF(row2.width() / 2, row2.height() / 2)))
        assert (2, 2) in v.links()
        # 反向：第 1 行端口 → 媒体 1 卡片上
        row0 = v._row_items[0]
        m1 = v._media[1]
        _drag(v, row0.port_pos(), m1.mapToScene(QPointF(m1.width() / 2, m1.height() / 2)))
        assert (1, 0) in v.links()
        n = len(v.links())
        # 刀：Ctrl 从中间空白竖着划一刀，全割断
        mid_x = (v._media[0].port_pos().x() + v._row_items[0].port_pos().x()) / 2
        _drag(v, QPointF(mid_x, 0), QPointF(mid_x, v.sceneRect().bottom() - 10), Qt.KeyboardModifier.ControlModifier)
        assert v.links() == [] and n >= 3
        # 双击删线 / Delete 删选中的线
        v.add_links([(0, 0), (1, 1)])
        lk = v._links[0]
        pt = lk.path().pointAtPercent(0.5)
        QTest.mouseDClick(v.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, _vp(v, pt))
        QApplication.processEvents()
        assert v.links() == [(1, 1)]
        _click(v, v._links[0].path().pointAtPercent(0.5))
        assert v._links[0].isSelected()
        QTest.keyClick(v.viewport(), Qt.Key.Key_Delete)
        assert v.links() == []
        # Ctrl+点 加减选；Shift 连选；点组标题 = 选整组；右键菜单能建出来
        c0 = v._cells[0]
        _click(v, c0.mapToScene(QPointF(c0.width() / 2, c0.height() / 2)))
        c4 = v._cells[4]
        _click(v, c4.mapToScene(QPointF(c4.width() / 2, c4.height() / 2)), Qt.KeyboardModifier.ControlModifier)
        assert sorted(v.selected_cells()) == [0, 4]
        _click(v, c0.mapToScene(QPointF(c0.width() / 2, c0.height() / 2)), Qt.KeyboardModifier.ShiftModifier)
        assert sorted(v.selected_cells()) == [0, 1, 3, 4]
        hdr = v._row_items[2]
        _click(v, hdr.mapToScene(QPointF(hdr.width() / 2, 8)))
        assert sorted(v.selected_cells()) == [2, 5]
        v.merge_selected()
        v._select_cells([2, 5])
        v.split_selected()
        assert v.rows() == [[0, 1], [3, 4], [2], [5]]
        v._select_cells([2, 5])
        v.merge_selected()
        assert v.rows() == [[0, 1], [3, 4], [2, 5]]
        v.select_media([0, 1])
        v.connect_rows_to_media([2], [0, 1])
        assert sorted(v.links()) == [(0, 2), (1, 2)]
        from PySide6.QtWidgets import QMenu
        import cutora.ui.link_group_graph as mod
        shown = []
        orig = mod._exec_menu
        mod._exec_menu = lambda menu, *a, **k: shown.append([x.text() for x in menu.actions()])
        try:
            from PySide6.QtGui import QContextMenuEvent
            c0 = v._cells[0]
            for pt in (c0.mapToScene(QPointF(5, 5)), v._media[0].mapToScene(QPointF(10, 10)), QPointF(5, v.sceneRect().bottom() - 5)):
                v.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Reason.Mouse, _vp(v, pt)))
        finally:
            mod._exec_menu = orig
        assert len(shown) == 3 and any("合并成一组" in t for t in shown[0]) and any("连到组" in t for t in shown[1])
        _ = mod
    finally:
        v.close()


# ───────────────────────────── 对话框 + 导入 ─────────────────────────────

def _touch(path):
    with open(path, "wb") as fh:
        fh.write(b"\x00" * 64)
    return path


def test_dialog_saves_plan_only_and_restores(tmp_path, monkeypatch):
    _app()
    from cutora.ui.link_import_dialog import LinkImportDialog
    srcs = [_touch(str(tmp_path / ("v%02d_%s.mp4" % (i // 3 + 1, "abc"[i % 3])))) for i in range(6)]
    tasks = [{"video": str(tmp_path / "host1.mp4"), "duration": 20.0}, {"video": str(tmp_path / "host2.mp4"), "duration": 20.0}]
    monkeypatch.setattr("cutora.ui.timeline.thumbs.request_clip_thumb", lambda *a, **k: None)
    dlg = LinkImportDialog(tasks, None, host=None)
    try:
        dlg._entries = [str(tmp_path)]
        dlg._reload()
        assert len(dlg._sources) == 6 and len(dlg.graph.rows()) == 6     # 默认一段一行
        dlg.sp_cols.setValue(3)
        assert dlg.graph.rows() == [[0, 1, 2], [3, 4, 5]]
        dlg.chk_prefix.setChecked(True)
        assert dlg.ed_prefix.isVisibleTo(dlg) and dlg.ed_prefix.text() == "v01"      # 自动给的例子
        assert dlg.graph.rows() == [[0, 1, 2], [3, 4, 5]] and "2 组" in dlg.lbl_prefix.text()
        dlg.ed_prefix.setText("v01_a")                                              # 更细：每段自己一组
        dlg._apply_prefix_example()
        assert len(dlg.graph.rows()) == 6 and "6 组" in dlg.lbl_prefix.text()
        dlg.ed_prefix.setText("v01")
        dlg._apply_prefix_example()
        assert dlg.graph.rows() == [[0, 1, 2], [3, 4, 5]]
        dlg._auto()
        assert dlg.graph.links() == [(0, 0), (1, 1)] and dlg.btn_run.isEnabled()
        # 再加素材：原来的分组 / 连线按路径留着，新的按前缀分
        _touch(str(tmp_path / "zz_a.mp4"))
        _touch(str(tmp_path / "zz_b.mp4"))
        dlg._reload()
        assert len(dlg._sources) == 8 and dlg.graph.rows()[:2] == [[0, 1, 2], [3, 4, 5]] and len(dlg.graph.rows()) == 4   # zz_a / zz_b 对不上 v01 的规律，各自一组
        assert dlg.graph.links() == [(0, 0), (1, 1)]
        plan = dlg.current_plan()
        assert plan["plan"][0]["groups"][0] == srcs[:3] and plan["plan"][0]["names"] == ["v01"]
        assert plan["plan"][0]["colors"] and plan["by_prefix"] and plan["cols"] == 3 and plan["prefix_example"] == "v01"
        dlg._save()
        assert dlg.link_plan == plan and dlg.result() == 1
    finally:
        dlg.close()
    # 重开：恢复分组 / 连线 / 设置
    dlg2 = LinkImportDialog(tasks, None, host=None, plan=plan)
    try:
        assert dlg2.graph.rows() == plan["rows"] and dlg2.graph.links() == [tuple(x) for x in plan["links"]]
        assert dlg2.sp_cols.value() == 3 and dlg2.chk_prefix.isChecked() and dlg2.ed_prefix.text() == "v01"
    finally:
        dlg2.close()


def test_apply_plan_builds_precomp_groups_and_three_way_split(tmp_path, monkeypatch):
    _app()
    from cutora.batch import link_import as biz
    from cutora.model import clip_groups as CG
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Track
    monkeypatch.setattr("cutora.batch.script_broll_scatter.probe_duration", lambda p: 4.0)
    monkeypatch.setattr("cutora.batch.script_broll_scatter.task_duration", lambda t, h=None: 30.0)
    a = [_touch(str(tmp_path / ("a_%s.mp4" % s))) for s in "abc"]
    b = [_touch(str(tmp_path / ("b_%s.mp4" % s))) for s in "ab"]
    tl = Timeline(1080, 1920, 30.0)
    main = Track("video")
    main.is_main_video = True
    tl.add_track(main)
    task = {"video": "host.mp4", "duration": 30.0, "timeline": tl}
    plan = [{"task_index": 0, "groups": [a, b], "names": ["a", "b"], "colors": ["#4FC3F7", "#81C784"]}]
    stats = biz.apply_plan(plan, [task], host=None, loop=True, match_duration=True, mute=True,
                           split_layout="top_bottom")
    assert stats["tracks"] == 2 and stats["clips"] == 5 and stats["groups"] == 2 and stats["split_skipped"] == 0
    tr_a, tr_b = tl.tracks[-2:]
    real_a = [c for c in tr_a.clips if not CG.is_repeat(c)]
    assert [c.start_t for c in real_a] == [0.0, 4.0, 8.0]
    g = CG.group_of(real_a[0])
    assert g["name"] == "a" and g["color"] == "#4FC3F7" and g["locked"] and g["loop_fill"]
    reps = [c for c in tr_a.clips if CG.is_repeat(c)]
    assert reps and max(c.start_t + c.effective_duration for c in reps) == pytest.approx(30.0)
    assert not any(c.loop or c.match_duration for c in tr_a.clips)
    # 一对二 + 默认上下 → 自动「上 1 下 2」
    assert task["split"]["layout"] == "top1_bottom2"
    assert main.split_cell == "A" and tr_a.split_cell == "B" and tr_b.split_cell == "C"
    # 不循环：多段裁到视频时长；一段的老行为不变
    tl2 = Timeline(1080, 1920, 30.0)
    task2 = {"video": "host.mp4", "duration": 30.0, "timeline": tl2}
    monkeypatch.setattr("cutora.batch.script_broll_scatter.task_duration", lambda t, h=None: 6.0)
    biz.apply_plan([{"task_index": 0, "groups": [a, [b[0]]]}], [task2], host=None, loop=False, match_duration=True)
    ga = tl2.tracks[-2].clips
    assert len(ga) == 2 and ga[1].start_t + ga[1].effective_duration == pytest.approx(6.0)
    single = tl2.tracks[-1].clips[0]
    assert single.match_duration and single._match_target_dur == pytest.approx(6.0) and not CG.group_of(single)
    # 布局不是默认的就不自动换；多组自动升为上1下N / 左1右N
    tl3 = Timeline(1080, 1920, 30.0)
    task3 = {"video": "host.mp4", "duration": 30.0, "timeline": tl3}
    biz.apply_plan([{"task_index": 0, "groups": [a, b]}], [task3], host=None, split_layout="left_right")
    assert task3["split"]["layout"] == "left_right"
    assert biz.pick_split_layout("top_bottom", 3) == "top1_bottom3" and biz.pick_split_layout(None, 2) == "top1_bottom2"
    assert biz.pick_split_layout("top_bottom", 5) == "top1_bottom5"
    assert biz.pick_split_layout("left1_right2", 4) == "left1_right4"
    assert biz.pick_split_layout("top_bottom", 2, auto_three=False) == "top_bottom"


# ───────────────────────────── 中央监视器：每格播放 + 进度条 ─────────────────────────────

def test_monitor_per_tile_play_and_scrub(tmp_path):
    from test_q144_central_monitor import _app as mon_app, _clip, _dialog, _task, _wait
    app = mon_app()
    from PySide6.QtCore import Qt
    tasks = [_task(_clip(tmp_path, dur=3)), _task(_clip(tmp_path, "0x30C030", dur=3))]
    dlg = _dialog(tasks)
    g = dlg.grid
    try:
        assert _wait(app, lambda: all(t.video is not None and not t.comp_dirty for t in g.tiles))
        t0, t1 = g.tiles
        bar = g.bar_rect(t0)
        assert not bar.isEmpty() and bar.bottom() == pytest.approx(t0.rect.bottom())
        btn, track, _tw = g._bar_parts(t0)
        # 点进度条中间 → 这一格跳到一半，其它格不动，不改选中
        g.selected = {1}
        mid = QPointF(track.center().x(), track.center().y())
        QTest.mousePress(g, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, mid.toPoint())
        QTest.mouseMove(g, (mid + QPointF(track.width() * 0.25, 0)).toPoint())
        QTest.mouseRelease(g, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                           (mid + QPointF(track.width() * 0.25, 0)).toPoint())
        assert t0.own_t and 0.6 * 3 < t0.t < 0.9 * 3 and t1.t == 0.0 and g.selected == {1}
        assert _wait(app, lambda: t0.video is not None and not t0.comp_dirty)
        # 顶部总滑块一动，全部跟过去
        dlg.sl_time.setValue(10)
        dlg._apply_time()
        assert not t0.own_t and t0.t == pytest.approx(1.0) and t1.t == pytest.approx(1.0)
        # ▶：独立播放，时间往前走并循环；只播这一格
        QTest.mouseClick(g, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, btn.center().toPoint())
        assert t0.playing and not t1.playing and g._play_timer.isActive()
        start = t0.t
        assert _wait(app, lambda: t0.t > start + 0.15, sec=5.0)
        assert t1.t == pytest.approx(1.0)
        # 全部播放 / 暂停；关进度条 = 停
        dlg.btn_play_all.setChecked(True)
        assert t1.playing
        dlg.btn_play_all.setChecked(False)
        assert not t0.playing and not t1.playing and not g._play_timer.isActive()
        dlg.btn_play_all.setChecked(True)
        dlg.ck_bars.setChecked(False)
        assert g.bar_rect(t0).isEmpty() and not any(t.playing for t in g.tiles) and not dlg.btn_play_all.isChecked()
        assert g.hit(mid)[1] in ("cell", "overlay")
        dlg.ck_bars.setChecked(True)
        assert g.hit(mid)[1] == "scrub" and g.hit(btn.center())[1] == "playbtn"
        g.repaint()
        app.processEvents()
    finally:
        dlg.close()


def test_view_columns_scroll_independently():
    """整页滚动照旧；左右两列各自还能单独滚（细滚动条 / Alt+滚轮），连线跟着端口走。"""
    from PySide6.QtGui import QWheelEvent
    v = _view(n_left=30, n_src=3)
    try:
        v.resize(1000, 400)
        QApplication.processEvents()
        v._fit_scene_rect()
        assert v._sb["left"].isVisible() and not v._sb["right"].isVisible()      # 左列比视口高，右列不高
        y0 = v._media[5].pos().y()
        r0 = v._row_items[0].pos().y()
        v.auto_link()
        p_before = v._links[0].path().pointAtPercent(0.0)
        v.set_column_offset("left", 200)
        assert v._media[5].pos().y() == pytest.approx(y0 - 200) and v._row_items[0].pos().y() == r0
        assert v._links[0].path().pointAtPercent(0.0).y() == pytest.approx(p_before.y() - 200)
        assert v._sb["left"].value() == 200
        # 右列没得滚：偏移被钳到 0
        v.set_column_offset("right", 100)
        assert v.column_offset("right") == 0.0
        # Alt + 滚轮只滚鼠标所在那一列；普通滚轮不改列偏移（走整页滚动）
        m = v._media[0]
        pt = v.mapFromScene(m.mapToScene(QPointF(10, 10)))
        ev = QWheelEvent(QPointF(pt), v.mapToGlobal(pt), QPoint(0, 0), QPoint(0, 120), Qt.MouseButton.NoButton,
                         Qt.KeyboardModifier.AltModifier, Qt.ScrollPhase.NoScrollPhase, False)
        v.wheelEvent(ev)
        assert v.column_offset("left") < 200
        off = v.column_offset("left")
        ev2 = QWheelEvent(QPointF(pt), v.mapToGlobal(pt), QPoint(0, 0), QPoint(0, -120), Qt.MouseButton.NoButton,
                          Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        v.wheelEvent(ev2)
        assert v.column_offset("left") == off
        # 场景高不随单列偏移缩水；拖滚动条到底不越界
        h = v.sceneRect().height()
        v._sb["left"].setValue(v._sb["left"].maximum())
        assert v.sceneRect().height() == h and v.column_offset("left") == pytest.approx(v._sb["left"].maximum(), abs=1)
        assert v._media[-1].pos().y() + v._media[-1].height() <= v.viewport().height() / v.transform().m11() + 1
    finally:
        v.close()


def test_unlimited_one_to_n_split_and_batch_dividers(tmp_path, monkeypatch):
    """一对多无限制分屏（主视频占最大格 A，外部视频占 B/C/D/E…）+ 中央监视器跨不同 N 的批量分界线控制。"""
    import numpy as np
    from cutora.batch import link_import as biz
    from cutora.batch import monitor_model as mm
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track
    from cutora.render.split_screen import _build_masks

    monkeypatch.setattr("cutora.batch.script_broll_scatter.probe_duration", lambda p: 5.0)
    monkeypatch.setattr("cutora.batch.script_broll_scatter.task_duration", lambda t, h=None: 15.0)

    def _make_task(n_ext: int):
        tl = Timeline(1080, 1920, 30.0)
        main = Track("video")
        main.is_main_video = True
        main.clips.append(Clip("s0", 0.0, 15.0, 0.0))
        tl.add_track(main)
        task = {"video": "host.mp4", "duration": 15.0, "timeline": tl}
        groups = [[_touch(str(tmp_path / ("ext_%d_%d.mp4" % (n_ext, i))))] for i in range(n_ext)]
        stats = biz.apply_plan([{"task_index": 0, "groups": groups}], [task], host=None, split_layout="top_bottom")
        assert stats["split_skipped"] == 0
        return task

    t3 = _make_task(3)   # 1 + 3 = 4 屏 (top1_bottom3)
    t4 = _make_task(4)   # 1 + 4 = 5 屏 (top1_bottom4)
    t6 = _make_task(6)   # 1 + 6 = 7 屏 (top1_bottom6)

    assert t3["split"]["layout"] == "top1_bottom3"
    assert t4["split"]["layout"] == "top1_bottom4"
    assert t6["split"]["layout"] == "top1_bottom6"
    assert [tr.split_cell for tr in mm.video_tracks(t4)] == ["A", "B", "C", "D", "E"]
    assert [tr.split_cell for tr in mm.video_tracks(t6)] == ["A", "B", "C", "D", "E", "F", "G"]

    # 遮罩无死角且主视频 A 占比最大
    _, masks4 = _build_masks(t4["split"], 200, 300)
    total_m = sum(m.astype(np.float32) for m in masks4.values())
    assert np.allclose(total_m, 255.0, atol=2.0)
    assert masks4["A"].astype(np.float64).sum() > masks4["B"].astype(np.float64).sum() * 3.5

    # 批量挪主分界线 d1 与副屏分界线 d2，并验证「副屏均分」保持主分界线 d1 不变
    assert mm.divider_compatible(t3["split"], t4["split"], "d1")
    assert mm.divider_compatible(t3["split"], t4["split"], "d2")
    mm.set_divider_pos(t4, "d1", 0.65)
    mm.set_divider_pos(t4, "d2", 0.15)
    rects = mm.cell_rects(t4)
    assert rects["A"][3] == pytest.approx(1920 * 0.65)
    mm.reset_secondary_dividers(t4)
    divs = {d["id"]: d["pos"] for d in t4["split"]["dividers"]}
    assert divs["d1"] == pytest.approx(0.65)
    assert divs["d2"] == pytest.approx(0.25) and divs["d3"] == pytest.approx(0.50) and divs["d4"] == pytest.approx(0.75)


def test_audio_link_dialog_uses_group_link_view(tmp_path):
    """音频分页的节点连线面板使用同款 GroupLinkView 画布、支持编组与少重复连线。"""
    _app()
    from cutora.ui.audio_link_dialog import AudioLinkDialog

    a1 = _touch(str(tmp_path / "bgm01_a.mp3"))
    a2 = _touch(str(tmp_path / "bgm01_b.mp3"))
    a3 = _touch(str(tmp_path / "bgm02_a.mp3"))
    videos = [(0, "v01.mp4"), (1, "v02.mp4"), (2, "v03.mp4")]
    dlg = AudioLinkDialog(videos, [a1, a2, a3])
    try:
        assert dlg.property("cutora_sheet") == "fill"
        assert len(dlg.graph.rows()) == 3
        mp = dlg.mapping()
        assert set(mp.keys()) == {0, 1, 2}
        assert set(mp.values()) == {a1, a2, a3}   # 少重复连线：3 首各用一次
        # 按前缀分组：bgm01_a 与 bgm01_b 归为同一组
        dlg.chk_prefix.setChecked(True)
        dlg.ed_prefix.setText("bgm01")
        dlg._apply_prefix_example()
        assert dlg.graph.rows() == [[0, 1], [2]]
        dlg._auto_least_repeat()
        mp2 = dlg.mapping()
        assert mp2[0] == [[a1, a2]] and mp2[1] == a3
        dlg.accept()
        assert dlg.link_plan and len(dlg.link_plan["plan"]) == 3
    finally:
        dlg.close()


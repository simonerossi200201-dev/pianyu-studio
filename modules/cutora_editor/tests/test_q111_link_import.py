# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q111 连线导入：画布、连线数据、以及「一条连线 = 一条新视频轨道」。"""
import os

import pytest

from PySide6.QtWidgets import QApplication

from cutora.batch import link_import as biz
from cutora.ui.link_graph import LinkGraphView


@pytest.fixture(scope="module")
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    yield QApplication.instance() or QApplication([])


def _touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"0" * 32)
    return path


def _rows(names):
    return [{"title": n, "key_path": n} for n in names]


# ------------------------------------------------------------ 纯逻辑
def test_list_videos_natural_order(tmp_path):
    d = tmp_path / "src"
    for n in ("a10.mp4", "a2.mp4", "b.mov", "note.txt"):
        _touch(str(d / n))
    got = [os.path.basename(p) for p in biz.list_videos(str(d))]
    assert got == ["a2.mp4", "a10.mp4", "b.mov"]


def test_resolve_sources_mixed_and_dedup(tmp_path):
    d = tmp_path / "src"
    one = _touch(str(d / "one.mp4"))
    _touch(str(d / "two.mp4"))
    got = biz.resolve_sources([one, str(d), one])
    assert [os.path.basename(p) for p in got] == ["one.mp4", "two.mp4"]


def test_auto_pairs_takes_the_shorter_side():
    assert biz.auto_pairs(5, 3) == [(0, 0), (1, 1), (2, 2)]
    assert biz.auto_pairs(0, 4) == []


def test_plan_groups_by_task_and_keeps_order():
    tasks = [{"video": "a.mp4"}, {"video": "b.mp4"}]
    srcs = ["x1.mp4", "x2.mp4", "x3.mp4"]
    plan = biz.plan_from_links([(0, 2), (0, 0), (1, 1)], tasks, srcs)
    assert plan == [
        {"task_index": 0, "paths": ["x1.mp4", "x3.mp4"]},
        {"task_index": 1, "paths": ["x2.mp4"]},
    ]


def test_plan_drops_out_of_range():
    assert biz.plan_from_links([(9, 0), (0, 9)], [{"video": "a"}], ["x"]) == []


# ------------------------------------------------------------ 画布
def test_graph_links_and_auto(app):
    g = LinkGraphView()
    g.set_items(_rows(["L1", "L2", "L3"]), _rows(["R1", "R2"]))
    assert g.links() == []
    assert g.auto_link() == 2
    assert g.links() == [(0, 0), (1, 1)]


def test_graph_one_left_to_many_rights(app):
    g = LinkGraphView()
    g.set_items(_rows(["L1"]), _rows(["R1", "R2", "R3"]))
    for ri in range(3):
        assert g._add_link(0, ri)
    assert g.links() == [(0, 0), (0, 1), (0, 2)]


def test_graph_refuses_duplicate_and_out_of_range(app):
    g = LinkGraphView()
    g.set_items(_rows(["L1"]), _rows(["R1"]))
    assert g._add_link(0, 0)
    assert not g._add_link(0, 0)
    assert not g._add_link(5, 0)
    assert g.links() == [(0, 0)]


def test_graph_clear_and_set_links(app):
    g = LinkGraphView()
    g.set_items(_rows(["L1", "L2"]), _rows(["R1", "R2"]))
    g.set_links([(1, 0), (0, 1)])
    assert g.links() == [(0, 1), (1, 0)]
    g.clear_links()
    assert g.links() == []


def test_set_items_drops_stale_links(app):
    """连线是按下标存的，换了内容还留着就是错连。"""
    g = LinkGraphView()
    g.set_items(_rows(["L1"]), _rows(["R1"]))
    g.auto_link()
    g.set_items(_rows(["L1", "L2"]), _rows(["R9"]))
    assert g.links() == []


def test_compact_mode_changes_row_height(app):
    g = LinkGraphView()
    g.set_items(_rows(["L1"]), _rows(["R1"]))
    tall = g._left[0].height()
    g.set_compact(True)
    assert g.is_compact()
    assert g._left[0].height() < tall


def test_thumb_lands_on_matching_nodes(app):
    from PySide6.QtGui import QPixmap
    g = LinkGraphView()
    g.set_items(_rows(["same.mp4"]), _rows(["same.mp4", "other.mp4"]))
    pix = QPixmap(4, 4)
    assert g.set_thumb("same.mp4", pix) == 2
    assert g.set_thumb("nope.mp4", pix) == 0


def test_port_follows_card_width(app):
    """卡片宽度是按视口分配的，端口必须跟着走，否则连线会脱节。"""
    g = LinkGraphView()
    g.resize(1200, 600)
    g.set_items(_rows(["L1"]), _rows(["R1"]))
    g.auto_link()
    node = g._left[0]
    assert abs(node.port_pos().x() - (node.pos().x() + node.width)) < 0.01


# ------------------------------------------------------------ 落地
def test_apply_plan_makes_one_track_per_link(app, tmp_path, monkeypatch):
    from cutora.model.timeline import Timeline
    v1 = _touch(str(tmp_path / "ext1.mp4"))
    v2 = _touch(str(tmp_path / "ext2.mp4"))
    tl = Timeline(1080, 1920, 30.0)
    before = len(getattr(tl, "tracks", []) or [])
    task = {"video": "host.mp4", "duration": 30.0, "timeline": tl}
    monkeypatch.setattr(
        "cutora.batch.script_broll_scatter.probe_duration", lambda p: 4.0)
    monkeypatch.setattr(
        "cutora.batch.script_broll_scatter.task_duration", lambda t, h=None: 30.0)
    plan = biz.plan_from_links([(0, 0), (0, 1)], [task], [v1, v2])
    stats = biz.apply_plan(plan, [task], host=None,
                           loop=True, match_duration=True)
    assert stats == {"tasks": 1, "tracks": 2, "clips": 2, "groups": 0, "split_skipped": 0}
    assert len(tl.tracks) == before + 2
    added = tl.tracks[-2:]
    for tr in added:
        assert tr.type == "video"
        assert not tr.is_main_video
        assert len(tr.clips) == 1
        clip = tr.clips[0]
        assert clip.loop is True
        assert clip.match_duration is True
        assert clip._match_target_dur == pytest.approx(30.0)
        assert clip.start_t == 0.0
        assert not clip.is_main


def test_apply_plan_reports_missing_duration(app, tmp_path, monkeypatch):
    from cutora.model.timeline import Timeline
    v = _touch(str(tmp_path / "bad.mp4"))
    task = {"video": "host.mp4", "duration": 10.0, "timeline": Timeline(1080, 1920, 30.0)}
    monkeypatch.setattr(
        "cutora.batch.script_broll_scatter.probe_duration", lambda p: 0.0)
    monkeypatch.setattr(
        "cutora.batch.script_broll_scatter.task_duration", lambda t, h=None: 10.0)
    with pytest.raises(ValueError):
        biz.apply_plan(biz.plan_from_links([(0, 0)], [task], [v]), [task])


# ------------------------------------------------------------ 分屏
def _split_task(tmp_path, monkeypatch, n_ext=1):
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Track
    tl = Timeline(1080, 1920, 30.0)
    main = Track("video")
    main.is_main_video = True
    tl.add_track(main)
    task = {"video": "host.mp4", "duration": 30.0, "timeline": tl}
    exts = [_touch(str(tmp_path / ("e%d.mp4" % i))) for i in range(n_ext)]
    monkeypatch.setattr(
        "cutora.batch.script_broll_scatter.probe_duration", lambda p: 4.0)
    monkeypatch.setattr(
        "cutora.batch.script_broll_scatter.task_duration", lambda t, h=None: 30.0)
    return task, tl, main, exts


def test_split_b_puts_original_on_a_and_import_on_b(app, tmp_path, monkeypatch):
    task, tl, main, exts = _split_task(tmp_path, monkeypatch, 1)
    plan = biz.plan_from_links([(0, 0)], [task], exts)
    stats = biz.apply_plan(plan, [task], split_b=True)
    assert task["split"]["enabled"] is True
    assert task["split"]["layout"] == "top_bottom"
    assert main.split_cell == "A"
    assert tl.tracks[-1].split_cell == "B"
    assert stats["split_skipped"] == 0


def test_split_off_leaves_cells_alone(app, tmp_path, monkeypatch):
    task, tl, main, exts = _split_task(tmp_path, monkeypatch, 1)
    plan = biz.plan_from_links([(0, 0)], [task], exts)
    biz.apply_plan(plan, [task], split_b=False)
    assert not (task.get("split") or {}).get("enabled")
    assert main.split_cell is None
    assert tl.tracks[-1].split_cell is None


def test_split_reports_extra_tracks_without_a_cell(app, tmp_path, monkeypatch):
    """上下分屏只有两格：一条视频连三条外部视频，只有第一条进 B。"""
    task, tl, main, exts = _split_task(tmp_path, monkeypatch, 3)
    plan = biz.plan_from_links([(0, 0), (0, 1), (0, 2)], [task], exts)
    stats = biz.apply_plan(plan, [task], split_b=True)
    assert stats["tracks"] == 3
    assert stats["split_skipped"] == 2
    cells = [getattr(t, "split_cell", None) for t in tl.tracks]
    assert cells.count("B") == 1
    assert cells.count("A") == 1


def test_split_takes_the_cell_from_a_previous_occupant(app, tmp_path, monkeypatch):
    """B 格子已经被别的轨占着时不许抛错，直接让给新导入的那条。"""
    from cutora.model.timeline_model import Track
    task, tl, main, exts = _split_task(tmp_path, monkeypatch, 1)
    squatter = Track("video")
    tl.add_track(squatter)
    squatter.split_cell = "B"
    plan = biz.plan_from_links([(0, 0)], [task], exts)
    biz.apply_plan(plan, [task], split_b=True)
    assert squatter.split_cell is None
    assert tl.tracks[-1].split_cell == "B"


def test_main_video_track_falls_back_to_first_video_track(app):
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Track
    tl = Timeline(1080, 1920, 30.0)
    assert biz.main_video_track(tl) is None
    a = Track("video")
    tl.add_track(a)
    b = Track("video")
    tl.add_track(b)
    assert biz.main_video_track(tl) is a
    b.is_main_video = True
    assert biz.main_video_track(tl) is b


def test_card_width_follows_the_longest_name(app):
    """Luca：「只有一条连线的话，不用这么宽」。宽度跟文字走，不跟窗口走。"""
    from cutora.ui.link_graph import NODE_MAX_W, NODE_MIN_W
    short = LinkGraphView()
    short.resize(1600, 600)
    short.set_items(_rows(["a.mp4"]), _rows(["b.mp4"]))
    long = LinkGraphView()
    long.resize(1600, 600)
    long.set_items(
        _rows(["01  一个非常非常长的文件名-20260924-v01-final.mp4"]),
        _rows(["b.mp4"]))
    assert short._left[0].width < long._left[0].width
    assert NODE_MIN_W <= short._left[0].width <= NODE_MAX_W
    assert long._left[0].width <= NODE_MAX_W


def test_wider_window_does_not_fatten_the_cards(app):
    """窗口变宽，多出来的宽度全给中间的连线区，卡片不跟着长胖。"""
    g = LinkGraphView()
    g.resize(1200, 600)
    g.set_items(_rows(["a.mp4"]), _rows(["b.mp4"]))
    narrow = g._left[0].width
    g.resize(2400, 600)
    assert g._left[0].width == pytest.approx(narrow)
    # 但两列仍然贴着左右边
    assert g._right[0].pos().x() > g._left[0].pos().x() + narrow


# ------------------------------------------------------------ 断线的几种方式
def _linked_graph(n=3):
    g = LinkGraphView()
    g.resize(1200, 600)
    g.set_items(_rows(["L%d" % i for i in range(n)]),
                _rows(["R%d" % i for i in range(n)]))
    g.auto_link()
    return g


def test_knife_cuts_every_link_it_crosses(app):
    """按住 Ctrl 划一刀，划过的线全断。"""
    from PySide6.QtCore import QPointF
    g = _linked_graph(3)
    assert len(g.links()) == 3
    # 从第一条线上方划到第三条线下方，正好穿过三条
    mid_x = g._left[0].pos().x() + g._left[0].width + 40
    top = g._left[0].port_pos().y() - 20
    bottom = g._left[2].port_pos().y() + 20
    g._knife = [QPointF(mid_x, top), QPointF(mid_x, bottom)]
    assert g._cut_with_knife() == 3
    assert g.links() == []


def test_knife_ignores_a_stray_click(app):
    """轨迹太短当成误点，一条都不许断。"""
    from PySide6.QtCore import QPointF
    g = _linked_graph(2)
    p = g._left[0].port_pos()
    g._knife = [QPointF(p.x() + 30, p.y()), QPointF(p.x() + 31, p.y())]
    assert g._cut_with_knife() == 0
    assert len(g.links()) == 2


def test_knife_spares_links_it_misses(app):
    from PySide6.QtCore import QPointF
    g = _linked_graph(3)
    mid_x = g._left[0].pos().x() + g._left[0].width + 40
    y = g._left[0].port_pos().y()
    # 只在第一条线上下很小一段里划
    g._knife = [QPointF(mid_x, y - 8), QPointF(mid_x, y + 8)]
    assert g._cut_with_knife() == 1
    assert len(g.links()) == 2


def test_port_hit_test(app):
    g = _linked_graph(1)
    node = g._left[0]
    assert g._on_port(node, node.port_pos())
    far = node.port_pos()
    far.setX(far.x() - node.width / 2)
    assert not g._on_port(node, far)


def test_link_at_node_picks_the_nearest_one(app):
    g = LinkGraphView()
    g.resize(1200, 600)
    g.set_items(_rows(["L0"]), _rows(["R0", "R1", "R2"]))
    for ri in range(3):
        g._add_link(0, ri)
    left = g._left[0]
    near = g._link_at_node(left, g._right[2].port_pos())
    assert near is not None and near.key() == (0, 2)


def test_node_size_switches_three_heights(app):
    from cutora.ui.link_graph import NODE_H_CARD, NODE_H_LIST, NODE_H_POSTER
    g = LinkGraphView()
    g.set_items(_rows(["L0"]), _rows(["R0"]))
    g.set_node_size("small")
    assert g.node_size() == "small"
    assert g._left[0].height() == NODE_H_LIST
    g.set_node_size("large")
    assert g.node_size() == "large"
    assert g._left[0].height() == NODE_H_POSTER
    assert not g._left[0].compact
    g.set_node_size("medium")
    assert g.node_size() == "medium"
    assert g._left[0].height() == NODE_H_CARD


def test_port_snap_reaches_a_small_gap_and_not_across_the_canvas(app):
    from PySide6.QtCore import QPointF
    g = LinkGraphView()
    g.resize(1200, 600)
    g.set_items(_rows(["L0", "L1"]), _rows(["R0"]))
    left = g._left[0]
    port = left.port_pos()
    near = QPointF(port.x() + 12, port.y())
    far = QPointF(port.x() + 400, port.y())
    assert g._node_at(near) is None
    assert g._nearest_port(near) is left
    assert g._nearest_port(far) is None
    # 端口那半个圆要落在可点范围内，不然看得见却点不中。
    local = left.mapFromScene(port)
    assert left.boundingRect().contains(local)


def _release_at(g, scene_pos):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    view_pos = g.mapFromScene(scene_pos)
    ev = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        QPointF(float(view_pos.x()), float(view_pos.y())),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    g.mouseReleaseEvent(ev)


def test_drop_snaps_across_a_small_gap(app):
    from PySide6.QtCore import QPointF
    from PySide6.QtWidgets import QGraphicsView
    g = LinkGraphView()
    g.resize(1200, 600)
    g.set_items(_rows(["L0"]), _rows(["R0"]))
    right = g._right[0]
    port = right.port_pos()
    g._start_drag(g._left[0])
    assert g.viewportUpdateMode() == QGraphicsView.ViewportUpdateMode.FullViewportUpdate
    _release_at(g, QPointF(port.x() - 12, port.y()))
    assert g.links() == [(0, 0)]
    assert g.viewportUpdateMode() == (
        QGraphicsView.ViewportUpdateMode.BoundingRectViewportUpdate)


def test_drop_far_from_every_port_does_not_link(app):
    from PySide6.QtCore import QPointF
    g = LinkGraphView()
    g.resize(1200, 600)
    g.set_items(_rows(["L0"]), _rows(["R0"]))
    port = g._right[0].port_pos()
    g._start_drag(g._left[0])
    _release_at(g, QPointF(port.x() - 400, port.y()))
    assert g.links() == []


def test_wire_drag_paints_on_the_theme_backdrop(app):
    g = LinkGraphView()
    color = g.backgroundBrush().color()
    assert (color.red(), color.green(), color.blue()) != (255, 255, 255)


def test_link_color_follows_the_right_node(app):
    from cutora.ui.link_graph import material_tint
    right = material_tint(0)
    left = material_tint(6)
    g = LinkGraphView()
    g.set_items(
        [{"title": "L", "key_path": "L", "tint": left}],
        [{"title": "R", "key_path": "R", "tint": right}],
    )
    g._add_link(0, 0)
    assert g._links[0].color().name().lower() == right.name().lower()
    assert material_tint(0).name() != material_tint(1).name()
    assert g._left[0].tint == left

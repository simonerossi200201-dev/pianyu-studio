# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""图片分页的节点连线：一张图指定给多条视频，节点上要有文件名和清楚的缩略图。"""
import os

import pytest

from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QCheckBox, QWidget

from cutora.batch import link_image as biz
from cutora.batch.script_insert_image import is_insert_overlay
from cutora.ui.link_graph import (
    NODE_GAP, NODE_H_CARD, NODE_H_POSTER, POSTER_W, LinkGraphView,
)


@pytest.fixture(scope="module")
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    yield QApplication.instance() or QApplication([])


def _touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"\x89PNG\r\n" + b"0" * 24)
    return path


def _task(name):
    return {
        "video": name,
        "duration": 10.0,
        "dims": (720, 1280),
        "image_overlays": [],
    }


def _rows(names):
    return [{"title": n, "key_path": n} for n in names]


def test_resolve_images_natural_order_and_dedup(tmp_path):
    d = tmp_path / "pics"
    for n in ("a10.png", "a2.jpg", "note.txt", "b.webp"):
        _touch(str(d / n))
    lone = _touch(str(tmp_path / "lone.png"))
    got = [os.path.basename(p) for p in biz.resolve_images([str(d), lone, lone])]
    assert got == ["a2.jpg", "a10.png", "b.webp", "lone.png"]


def test_one_image_lands_on_many_videos(tmp_path):
    cover = _touch(str(tmp_path / "cover.png"))
    other = _touch(str(tmp_path / "other.png"))
    tasks = [_task("v1.mp4"), _task("v2.mp4"), _task("v3.mp4")]
    plan = biz.plan_from_links([(0, 0), (2, 1), (1, 0)], tasks, [cover, other])
    stats = biz.apply_plan(
        plan, tasks, start_sec=0.0, end_sec=4.0, replace=True,
        anim_in="fade", anim_out="fade", blend="normal")
    assert stats == {"tasks": 3, "images": 3}

    def used(task):
        ovs = [ov for ov in task["image_overlays"] if is_insert_overlay(ov)]
        assert len(ovs) == 1
        assert ovs[0]["anim_in"] == "fade"
        return os.path.basename(ovs[0]["elements"][0]["path"])

    assert used(tasks[0]) == "cover.png"
    assert used(tasks[1]) == "cover.png"
    assert used(tasks[2]) == "other.png"


def test_two_images_on_one_video_keep_both_and_a_manual_overlay(tmp_path):
    a = _touch(str(tmp_path / "a.png"))
    b = _touch(str(tmp_path / "b.png"))
    task = _task("v1.mp4")
    task["image_overlays"] = [
        {"id": "ov_insert_old", "_script": "insert_image",
         "elements": [{"kind": "image", "path": "old.png"}]},
        {"id": "manual", "elements": [{"kind": "image", "path": "keep.png"}]},
    ]
    plan = [{"task_index": 0, "paths": [a, b]}]
    biz.apply_plan(plan, [task], start_sec=0.0, end_sec=4.0, replace=True)
    manual = [ov for ov in task["image_overlays"] if ov.get("id") == "manual"]
    inserted = [ov for ov in task["image_overlays"] if is_insert_overlay(ov)]
    assert len(manual) == 1
    assert [os.path.basename(ov["elements"][0]["path"]) for ov in inserted] == [
        "a.png", "b.png"]


def test_span_of_starts_the_image_when_the_title_ends(tmp_path):
    cover = _touch(str(tmp_path / "cover.png"))
    task = _task("v1.mp4")
    task["duration"] = 180.0

    def _after_title(_task, dur):
        return 4.2, dur

    biz.apply_plan(
        [{"task_index": 0, "paths": [cover]}], [task],
        start_sec=0.0, end_sec=2.0, replace=True, span_of=_after_title)
    ov = [ov for ov in task["image_overlays"] if is_insert_overlay(ov)]
    assert len(ov) == 1
    assert ov[0]["start"] == pytest.approx(4.2)
    assert ov[0]["end"] == pytest.approx(180.0)


def test_assign_one_image_replaces_other_links_on_those_videos(app):
    g = LinkGraphView()
    g.set_items(_rows(["v1", "v2", "v3"]), _rows(["cover.png", "other.png"]))
    g._add_link(0, 1)
    g._add_link(1, 1)
    n = g.assign_right_to_lefts(0, [0, 1, 2])
    assert n == 3
    assert g.links() == [(0, 0), (1, 0), (2, 0)]
    g._add_link(0, 1)
    assert (0, 0) in g.links() and (0, 1) in g.links()


def test_choose_one_column_does_not_clear_the_other(app):
    g = LinkGraphView()
    g.set_items(_rows(["v1"]), _rows(["cover.png"]))
    g.choose("right", 0)
    g.choose("left", 0)
    assert g.chosen("right") == 0
    assert g.chosen("left") == 0
    assert g._right[0].chosen and g._left[0].chosen


def test_poster_paints_the_whole_thumb_and_keeps_the_filename(app):
    g = LinkGraphView()
    g.show()
    g.resize(1600, 900)
    app.processEvents()
    g.set_poster(True)
    pix = QPixmap(80, 40)
    pix.fill(QColor("#00E05A"))
    g.set_items(
        [{"title": "01  v01.mp4", "key_path": r"D:\v\v01.mp4",
          "tooltip": r"D:\v\v01.mp4", "thumb": pix}],
        [{"title": "cover_long_name.png", "key_path": r"D:\pics\cover_long_name.png",
          "tooltip": r"D:\pics\cover_long_name.png", "thumb": pix}],
    )
    plain = LinkGraphView()
    plain.set_items(_rows(["a.mp4"]), _rows(["b.mp4"]))
    assert plain._left[0].height() == NODE_H_CARD
    assert not plain.is_poster()

    node = g._right[0]
    assert g.is_poster()
    assert node.poster
    assert node.height() == NODE_H_POSTER
    assert node.height() > NODE_H_CARD
    assert node.width == pytest.approx(POSTER_W)
    assert node.title == "cover_long_name.png"
    assert node.toolTip() == r"D:\pics\cover_long_name.png"
    assert g._left[0].title == "01  v01.mp4"

    img = QImage(int(node.width), int(node.height()), QImage.Format.Format_ARGB32)
    img.fill(QColor("#101010"))
    painter = QPainter(img)
    node.paint(painter, None)
    painter.end()

    def is_green(x, y):
        c = img.pixelColor(int(x), int(y))
        return c.green() > 180 and c.red() < 40 and c.blue() < 130

    mid_x, mid_y = node.width / 2.0, node.height() * 0.35
    assert is_green(mid_x, mid_y)
    # 左边留白：整张图放进去，不裁成铺满
    assert not is_green(18, mid_y)
    bottom_greens = sum(
        1 for x in range(0, int(node.width), 4) if is_green(x, node.height() - 6))
    assert bottom_greens == 0


def test_image_tab_button_and_dialog_link_all(app, tmp_path):
    _touch(str(tmp_path / "cover.png"))
    _touch(str(tmp_path / "other.png"))
    from cutora.batch.batch_dialog import build_batch_dialog
    from cutora.ui.link_image_dialog import LinkImageDialog

    host = QWidget()
    host.tasks = [_task("v1.mp4"), _task("v2.mp4")]
    host.curr_idx = 0
    host.timeline = QWidget()
    dlg = build_batch_dialog(host)
    assert "节点连线" in dlg.btn_link_image.text()
    assert "缩略图" in dlg.btn_link_image.toolTip()
    multi = next(b for b in dlg.findChildren(QCheckBox) if "多平台" in b.text())
    multi.setChecked(True)
    assert not dlg.btn_link_image.isEnabled()
    multi.setChecked(False)
    assert dlg.btn_link_image.isEnabled()

    sub = LinkImageDialog(
        host.tasks, folder=str(tmp_path), checked=[0])
    assert sub.graph.is_poster()
    assert [n.title for n in sub.graph._right] == ["cover.png", "other.png"]
    sub.graph.choose("right", 0)
    assert sub._link_all() == 2
    assert sub.graph.links() == [(0, 0), (1, 0)]
    sub.graph.choose("right", 1)
    assert sub._link_checked() == 1
    assert sub.graph.links() == [(0, 1), (1, 0)]
    dlg.deleteLater()
    sub.deleteLater()


def test_hide_image_keeps_the_other_links(app, tmp_path):
    _touch(str(tmp_path / "a.png"))
    _touch(str(tmp_path / "b.png"))
    _touch(str(tmp_path / "m.png"))
    from cutora.ui.link_image_dialog import LinkImageDialog

    sub = LinkImageDialog(
        [_task("v1.mp4"), _task("v2.mp4")], folder=str(tmp_path))
    assert sub.btn_hide.text() == "隐藏这张图"
    assert [n.title for n in sub.graph._right] == ["a.png", "b.png", "m.png"]
    sub.graph._add_link(0, 0)
    sub.graph._add_link(1, 1)
    assert sub._hide_index(0)
    assert [n.title for n in sub.graph._right] == ["b.png", "m.png"]
    assert sub.graph.links() == [(1, 0)]
    assert os.path.basename(sub._visible_sources()[0]) == "b.png"
    plan = biz.plan_from_links(
        sub.graph.links(), sub._tasks, sub._visible_sources())
    assert [os.path.basename(p) for p in plan[0]["paths"]] == ["b.png"]
    assert sub._show_hidden() == 1
    assert [n.title for n in sub.graph._right] == ["a.png", "b.png", "m.png"]
    assert sub.graph.links() == [(0, 0), (1, 1)]
    sub.deleteLater()


def test_columns_scroll_on_their_own(app):
    g = LinkGraphView()
    g.show()
    g.resize(1400, 220)
    app.processEvents()
    g.set_compact(True)
    app.processEvents()
    step = g._row_height() + NODE_GAP
    n = int(g.viewport().height() / step) + 4
    g.set_items(
        _rows(["L%d" % i for i in range(n)]),
        _rows(["R%d" % i for i in range(n)]))
    assert g._sb_left.isVisible()
    assert g._sb_right.isVisible()
    g.auto_link()
    step = g._left[1].pos().y() - g._left[0].pos().y()
    left_y = g._left[0].pos().y()
    right_y = g._right[0].pos().y()
    g.scroll_column("right", step)
    assert g._left[0].pos().y() == pytest.approx(left_y)
    assert g._right[0].pos().y() == pytest.approx(right_y - step)
    assert g.links() == [(i, i) for i in range(n)]
    g.scroll_column("left", step * 2)
    assert g._left[0].pos().y() == pytest.approx(left_y - step * 2)
    assert g._right[0].pos().y() == pytest.approx(right_y - step)
    g.deleteLater()


def test_shift_range_and_box_select_stay_in_one_column(app):
    g = LinkGraphView()
    g.set_items(_rows(["L0", "L1", "L2", "L3"]), _rows(["R0", "R1", "R2"]))
    g.choose("left", 1)
    g.select_range("left", 3)
    assert g.selected_indices("left") == [1, 2, 3]
    g.select_range("left", 0)
    assert g.selected_indices("left") == [0, 1]
    assert g._anchor["left"] == 1
    g.choose("right", 2)
    g.toggle_in_selection("left", 3)
    assert g.selected_indices("left") == [0, 1, 3]
    assert g.selected_indices("right") == [2]
    band = g._left[0].sceneBoundingRect().united(g._left[2].sceneBoundingRect())
    hits = g.select_band("left", band.adjusted(-10, 0, 400, 0))
    assert hits == [0, 1, 2]
    assert g.selected_indices("right") == [2]
    g.deleteLater()


def test_batch_assign_skips_hidden_images(app, tmp_path):
    _touch(str(tmp_path / "a.png"))
    _touch(str(tmp_path / "b.png"))
    _touch(str(tmp_path / "m.png"))
    from cutora.ui.link_image_dialog import LinkImageDialog

    sub = LinkImageDialog(
        [_task("v1.mp4"), _task("v2.mp4")], folder=str(tmp_path))
    assert sub._hide_index(0)
    assert [name for _, name in sub.target_entries("left")] == ["b.png", "m.png"]
    assert sub.target_entries("right") == [
        (0, "01  v1.mp4"), (1, "02  v2.mp4")]
    sub.graph.choose("left", 0)
    sub.graph.select_range("left", 1)
    assert sub.assign_selected_to("left", 0) == 2
    assert sub.graph.links() == [(0, 0), (1, 0)]
    assert sub.assign_selected_to("left", 1) == 2
    assert sub.graph.links() == [(0, 0), (0, 1), (1, 0), (1, 1)]
    assert sub.assign_selected_to("left", 1) == 0
    sub.graph.choose("right", 0)
    sub.graph.select_range("right", 1)
    assert sub._hide_selected() == 2
    assert sub._visible_sources() == []
    assert sub.target_entries("left") == []
    assert sub.graph.links() == []
    assert sub._show_hidden() == 3
    assert [n.title for n in sub.graph._right] == ["a.png", "b.png", "m.png"]
    assert sub.graph.links() == [(0, 1), (0, 2), (1, 1), (1, 2)]
    sub.deleteLater()


def test_confirm_saves_the_map_without_touching_the_timeline(app, tmp_path):
    _touch(str(tmp_path / "a.png"))
    _touch(str(tmp_path / "b.png"))
    _touch(str(tmp_path / "m.png"))
    from cutora.ui.link_image_dialog import LinkImageDialog

    tasks = [_task("v1.mp4"), _task("v2.mp4")]
    sub = LinkImageDialog(tasks, folder=str(tmp_path))
    assert sub.btn_run.text() == "保存对应关系"
    assert sub.graph._right[0].tint is not None
    assert sub.graph._right[0].tint.name() != sub.graph._right[1].tint.name()
    sub.graph._add_link(0, 0)
    sub.graph._add_link(1, 1)
    assert sub.graph._links[0].color().name() == sub.graph._right[0].tint.name()
    assert sub.graph._links[1].color().name() == sub.graph._right[1].tint.name()
    plan = sub.commit_plan()
    assert tasks[0]["image_overlays"] == []
    assert tasks[1]["image_overlays"] == []
    assert [os.path.basename(p) for p in plan[0]["paths"]] == ["a.png"]
    assert [os.path.basename(p) for p in plan[1]["paths"]] == ["b.png"]
    again = LinkImageDialog(tasks, folder=str(tmp_path), plan=plan)
    assert again.graph.links() == [(0, 0), (1, 1)]
    sub.deleteLater()
    again.deleteLater()


def test_saved_links_preview_from_the_batch_window(app, tmp_path):
    cover = _touch(str(tmp_path / "cover.png"))
    other = _touch(str(tmp_path / "other.png"))
    from cutora.batch.batch_dialog import build_batch_dialog

    host = QWidget()
    host.tasks = [_task("v1.mp4"), _task("v2.mp4")]
    host.curr_idx = 0
    host.timeline = QWidget()
    dlg = build_batch_dialog(host)
    dlg.remember_image_links([
        {"task_index": 0, "paths": [cover]},
        {"task_index": 1, "paths": [other, cover]},
    ])
    assert host.tasks[0]["image_overlays"] == []
    assert host.tasks[1]["image_overlays"] == []
    assert dlg.picker.selected_indices() == [0, 1]
    assert "已记住" in dlg.btn_link_image.text()
    assert os.path.basename(
        dlg._preview_task["image_overlays"][0]["elements"][0]["path"]) == "cover.png"
    dlg.picker._last_row = 1
    dlg.refresh_image_preview()
    paths = [
        os.path.basename(ov["elements"][0]["path"])
        for ov in dlg._preview_task["image_overlays"]
    ]
    assert paths == ["other.png", "cover.png"]
    x0 = dlg._preview_task["image_overlays"][0]["elements"][0]["x"]
    dlg.sp_x.setValue(float(dlg.sp_x.value()) + 36)
    dlg.refresh_image_preview()
    assert dlg._preview_task["image_overlays"][0]["elements"][0]["x"] != x0
    assert host.tasks[1]["image_overlays"] == []
    dlg.remember_image_links([{"task_index": 1, "paths": [other]}])
    assert dlg.picker.selected_indices() == [1]
    dlg.deleteLater()

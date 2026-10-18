# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q152-G · 中央监视器图层面板专业化。

数据层（layer_model）：行序、显隐 / 锁定 / 颜色 / 改名 / 独奏 / 拖序 / 删除，老工程零改动。
渲染：hidden 的层不画（预览 + 导出同一条路）；locked 的层画布上点不中。
面板：按钮点击、键盘、搜索过滤、选择同步、撤销。
"""
from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_q149_layer_panel import _app, _task  # noqa: E402


def _rich_task(name="a"):
    task = _task(name)
    task["text_overlays"].append({
        "id": 8, "content": "第二条", "x": 100, "y": 400, "w": 800, "h": 200,
        "font_family": "Arimo", "fontsize": 60, "start": 0.0, "end": 4.0,
    })
    task["image_overlays"].append({
        "id": 9, "type": "image", "path": "nope.png", "x": 100, "y": 900, "w": 400, "h": 260,
        "start": 0.5, "end": 3.5,
    })
    from cutora.model.timeline_model import Clip, MediaSource, Track
    tl = task["timeline"]
    au = Track("audio")
    tl.add_track(au)
    src = MediaSource("bgm.mp3", duration=4, probe=False)
    tl.sources[src.id] = src
    au.clips.append(Clip(src.id, 0, 4, 0))
    return task


def _titles(task):
    from cutora.batch import layer_model as lm
    return [r["title"] for r in lm.rows(task)]


# ───────────────────────────── 数据层 ─────────────────────────────

def test_rows_top_first_and_defaults():
    from cutora.batch import layer_model as lm
    task = _rich_task()
    rows = lm.rows(task)
    # 文本在上（后画的在前），然后图片、视频、音频
    assert [r["kind"] for r in rows] == ["text", "text", "image", "video", "audio"]
    assert rows[0]["title"] == "第二条" and rows[1]["title"] == "标题"
    for r in rows:
        assert r["hidden"] is False and r["locked"] is False and r["color"] == ""
        assert r["opacity"] == 255 and r["blend"] == "normal"
    assert rows[-1]["is_audio"]


def test_flags_color_rename_round_trip_and_no_keys_when_off():
    from cutora.batch import layer_model as lm
    task = _rich_task()
    loc = lm.rows(task)[1]["locator"]            # 「标题」
    ov = lm.resolve(task, loc)
    assert lm.set_flag(task, loc, "hidden", True) and ov["hidden"] is True
    assert not lm.set_flag(task, loc, "hidden", True)        # 没变化 → False
    assert lm.set_flag(task, loc, "hidden", False) and "hidden" not in ov
    assert lm.set_color(task, loc, "teal") and ov["label_color"] == "teal"
    assert not lm.set_color(task, loc, "螺旋")
    assert lm.set_color(task, loc, "") and "label_color" not in ov
    assert lm.rename(task, loc, "  开场标题 ") and ov["name"] == "开场标题"
    assert lm.rows(task)[1]["title"] == "开场标题"
    assert lm.rename(task, loc, "") and "name" not in ov
    # 片段：写属性
    vloc = next(r["locator"] for r in lm.rows(task) if r["kind"] == "video")
    clip = lm.resolve(task, vloc)
    assert lm.set_flag(task, vloc, "locked", True) and clip.locked is True
    assert lm.set_color(task, vloc, "red") and clip.label_color == "red"
    with pytest.raises(ValueError):
        lm.set_flag(task, vloc, "frozen", True)


def test_clip_flags_persist_through_project_schema():
    from cutora.model.project_schema import CLIP_KEYS
    from cutora.model.timeline_model import Clip
    for key in ("hidden", "locked", "label_color", "name"):
        assert key in CLIP_KEYS
    c = Clip("s", 0.0, 1.0, 0.0)
    assert c.hidden is False and c.locked is False and c.label_color == "" and c.name == ""


def test_solo_toggles_and_restores():
    from cutora.batch import layer_model as lm
    task = _rich_task()
    rows = lm.rows(task)
    me = rows[2]["locator"]                    # 图片
    assert lm.solo(task, me) == 4
    after = {r["title"]: r["hidden"] for r in lm.rows(task)}
    assert after["nope.png"] is False and all(v for k, v in after.items() if k != "nope.png")
    assert lm.solo(task, me) == 4              # 再按一次：全部恢复
    assert not any(r["hidden"] for r in lm.rows(task))


def test_move_overlay_reorders_z_and_only_within_same_list():
    from cutora.batch import layer_model as lm
    task = _rich_task()
    assert _titles(task)[:2] == ["第二条", "标题"]
    second = lm.rows(task)[0]["locator"]
    title = lm.rows(task)[1]["locator"]
    # 把「第二条」放到「标题」下面 = 挪到最底
    assert lm.move_overlay(task, second, None)
    assert _titles(task)[:2] == ["标题", "第二条"]
    assert [ov["content"] for ov in task["text_overlays"]] == ["第二条", "标题"]
    # 再放回「标题」上面
    second = lm.rows(task)[1]["locator"]
    title = lm.rows(task)[0]["locator"]
    assert lm.move_overlay(task, second, title)
    assert _titles(task)[:2] == ["第二条", "标题"]
    # 文本不能插进图片列表
    img = next(r["locator"] for r in lm.rows(task) if r["kind"] == "image")
    assert not lm.move_overlay(task, lm.rows(task)[0]["locator"], img)
    # Q177：视频图层也能拖（拖的是它所在的轨道）；声音不行
    assert lm.can_reorder(next(r for r in lm.rows(task) if r["kind"] == "video"))
    assert not lm.can_reorder(next(r for r in lm.rows(task) if r["kind"] == "audio"))


def test_delete_overlays_and_clips():
    from cutora.batch import layer_model as lm
    task = _rich_task()
    rows = lm.rows(task)
    locs = [rows[0]["locator"], next(r["locator"] for r in rows if r["kind"] == "audio")]
    assert lm.delete(task, locs) == 2
    assert [ov["content"] for ov in task["text_overlays"]] == ["标题"]
    assert all(not tr.clips for tr in task["timeline"].tracks if tr.type == "audio")
    assert lm.delete(task, [{"where": "overlay", "list": "text_overlays", "index": 99}]) == 0


def test_filter_rows():
    from cutora.batch import layer_model as lm
    task = _rich_task()
    rows = lm.rows(task)
    assert [r["title"] for r in lm.filter_rows(rows, "标题")] == ["标题"]
    assert [r["kind"] for r in lm.filter_rows(rows, "", "audio")] == ["audio"]
    assert [r["kind"] for r in lm.filter_rows(rows, "", "text")] == ["text", "text"]
    assert [r["title"] for r in lm.filter_rows(rows, "mp3")] == ["bgm.mp3"]
    assert lm.filter_rows(rows, "图片")[0]["kind"] == "image"      # 按类型词也能搜


# ───────────────────────────── 渲染 / 命中 ─────────────────────────────

def test_hidden_clip_skipped_in_preview_export_and_mixer():
    from cutora.model.timeline_model import active_clips_at
    from cutora.model.track_guard import compute_audio_tracks
    task = _rich_task()
    tl = task["timeline"]
    vtr = next(tr for tr in tl.tracks if tr.type == "video")
    assert len(active_clips_at(vtr, 1.0)) == 1
    vtr.clips[0].hidden = True
    assert active_clips_at(vtr, 1.0) == []
    atr = next(tr for tr in tl.tracks if tr.type == "audio")
    assert len(compute_audio_tracks(task)) == 1
    atr.clips[0].hidden = True
    assert compute_audio_tracks(task) == []


def test_hidden_overlay_not_painted_locked_overlay_not_hit():
    _app()
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QImage, QPainter
    from cutora.render.frame_renderer import FrameRenderer, _overlay_hit
    from cutora.render.hitmask import hits_record
    task = _rich_task()
    from cutora.model.timeline_model import Track
    task["timeline"].add_track(Track("text"))
    task["text_overlays"] = [{
        "id": 1, "type": "text", "content": "HELLO", "x": 100, "y": 100, "w": 600, "h": 200,
        "font_family": "Arimo", "fontsize": 120, "start": 0.0, "end": 4.0, "color": "#FFFFFF",
    }]
    task["image_overlays"] = []

    def _render():
        img = QImage(1080, 1920, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(0xFF000000)
        p = QPainter(img)
        try:
            FrameRenderer.draw_layers(p, task, 1080, 1920, 1.0, render_order=["text"])
        finally:
            p.end()
        return img

    def _lit(img):
        n = 0
        for y in range(100, 300, 10):
            for x in range(100, 700, 10):
                if img.pixelColor(x, y).lightness() > 60:
                    n += 1
        return n

    assert _lit(_render()) > 0
    task["text_overlays"][0]["hidden"] = True
    assert _lit(_render()) == 0
    task["text_overlays"][0].pop("hidden")
    rec = _overlay_hit(task["text_overlays"][0], 0, QRectF(100, 100, 600, 200), 1.0, "text_overlays")
    assert "locked" not in rec
    task["text_overlays"][0]["locked"] = True
    rec = _overlay_hit(task["text_overlays"][0], 0, QRectF(100, 100, 600, 200), 1.0, "text_overlays")
    assert rec["locked"] is True
    assert not hits_record(rec, 150, 150, alt_bbox=True)


# ───────────────────────────── 面板 ─────────────────────────────

def _dialog(task):
    from cutora.ui.central_monitor import CentralMonitorDialog

    class _Host:
        pass
    host = _Host()
    host.tasks = [task]
    host.curr_idx = 0
    dlg = CentralMonitorDialog(host, [task])
    g = dlg.grid
    g._sched.stop()
    g._compose_timer.stop()
    g._edited_timer.stop()
    g._renderer.cancel_all()
    g._request = lambda *args, **kwargs: None
    dlg.show()
    g.active = 0
    g.selected = {0}
    dlg.layers.sync()
    return dlg


def _select(panel, i):
    from PySide6.QtCore import QItemSelectionModel
    panel.lst.selectionModel().select(panel.model.index(i, 0),
                                      QItemSelectionModel.SelectionFlag.ClearAndSelect)


def test_panel_buttons_keys_undo_and_selection_sync():
    app = _app()
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from cutora.batch import layer_model as lm
    task = _rich_task()
    dlg = _dialog(task)
    try:
        p = dlg.layers
        assert p.lst.count() == 5
        assert p.count_lbl.text() == "5"
        row = p._layers[1]                       # 「标题」
        p.click_button(row, "eye")
        assert task["text_overlays"][0]["hidden"] is True
        assert dlg.grid.history.can_undo()
        dlg.grid.undo()
        p.sync()
        assert "hidden" not in task["text_overlays"][0]
        # 锁定 + 独奏
        p.click_button(p._layers[1], "lock")
        assert task["text_overlays"][0]["locked"] is True
        p.click_button(p._layers[2], "solo")
        assert p.is_solo(p._layers[2])
        assert task["text_overlays"][0]["hidden"] is True
        p.click_button(p._layers[2], "solo")
        assert not any(r["hidden"] for r in lm.rows(task))
        # 键盘：选中后 Space 隐藏、Delete 删除
        _select(p, 0)
        ev = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier)
        assert p.handle_key(ev)
        assert task["text_overlays"][1]["hidden"] is True
        # 面板选中覆层 → 监视器 ov_sel 跟着；监视器选中 → 面板跟着
        _select(p, 2)
        app.processEvents()
        assert [ov["id"] for _i, ov in dlg.grid.ov_sel] == [9]
        dlg.grid.ov_sel = [(0, task["text_overlays"][0])]
        p.sync()
        assert [i.row() for i in p.lst.selectionModel().selectedRows()] == [1]
        _select(p, 2)
        ev = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier)
        assert p.handle_key(ev)
        assert task["image_overlays"] == []
        dlg.grid.undo()
        p.sync()
        assert len(task["image_overlays"]) == 1
        # 搜索过滤
        p.search.setText("标题")
        assert p.lst.count() == 1 and p.count_lbl.text() == "1 / 5"
        p.set_filter("audio")
        assert p.lst.count() == 0
        p.search.clear()
        assert p.lst.count() == 1 and p._layers[0]["kind"] == "audio"
        p.set_filter("all")
        assert p.lst.count() == 5
        # 改名 + 颜色
        p.rename_layer(p._layers[1], "开场")
        assert task["text_overlays"][0]["name"] == "开场" and p._layers[1]["title"] == "开场"
        _select(p, 1)
        p.set_color_selected("red")
        assert task["text_overlays"][0]["label_color"] == "red"
    finally:
        dlg.close()


def test_panel_drag_reorder_via_model():
    _app()
    task = _rich_task()
    dlg = _dialog(task)
    try:
        p = dlg.layers
        m = p.model
        assert [r["title"] for r in p._layers][:2] == ["第二条", "标题"]
        md = m.mimeData([m.index(0, 0)])
        from PySide6.QtCore import QModelIndex, Qt
        # Q177：拖到别的种类的图层上面也接（挪的是轨道）；拖到声音行上不接
        assert m.canDropMimeData(md, Qt.DropAction.MoveAction, 2, 0, QModelIndex())
        audio_row = next(i for i, r in enumerate(p._layers) if r.get("is_audio"))
        assert not m.canDropMimeData(md, Qt.DropAction.MoveAction, audio_row, 0, QModelIndex())
        # 拖到最后 = 最底
        assert m.dropMimeData(md, Qt.DropAction.MoveAction, m.rowCount(), 0, QModelIndex())
        assert [r["title"] for r in p._layers][:2] == ["标题", "第二条"]
        dlg.grid.undo()
        p.sync()
        assert [r["title"] for r in p._layers][:2] == ["第二条", "标题"]
        # 拖回「标题」上面
        md = m.mimeData([m.index(1, 0)])
        assert m.dropMimeData(md, Qt.DropAction.MoveAction, 0, 0, QModelIndex())
        assert [r["title"] for r in p._layers][:2] == ["标题", "第二条"]
    finally:
        dlg.close()

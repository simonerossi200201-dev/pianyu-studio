# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""中央监视器图层面板：列表、右键复制，以及文字按画布比例缩放。"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _app():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(["q149"])
    from cutora.ui.panels.fonts import load_embedded_fonts
    load_embedded_fonts()
    return app


def _task(name, text=True, w=1080, h=1920):
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, MediaSource, Track
    tl = Timeline(w, h, 30)
    tr = Track("video")
    tr.is_main_video = True
    tl.add_track(tr)
    src = MediaSource(name + ".mp4", duration=4, probe=False)
    tl.sources[src.id] = src
    tr.clips.append(Clip(src.id, 0, 4, 0))
    task = {
        "timeline": tl, "video": name + ".mp4", "dims": (w, h),
        "text_overlays": [], "image_overlays": [],
    }
    if text:
        task["text_overlays"].append({
            "id": 7, "content": "标题", "x": 200, "y": 80, "w": 400, "h": 60,
            "font_family": "Arimo", "fontsize": 40, "start": 1.0, "end": 3.0,
            "fade_top_offset": 0.2,
        })
    return task


def test_text_material_and_all_scale_with_canvas():
    from cutora.batch import layer_transfer as lt
    src = _task("a")
    src["text_overlays"][0]["end"] = 9.0
    layers = [row for row in lt.list_layers(src) if row["kind"] == "text"]
    dst = _task("b", text=False, w=540, h=960)
    assert lt.apply_pack(dst, lt.pack_layers(src, layers, "material"))["added"] == 1
    ov = dst["text_overlays"][0]
    assert abs(ov["x"] - 100) < 1e-6
    assert abs(float(ov["fontsize"]) - 20) < 1e-6
    assert ov["end"] == 9.0 and ov["content"] == "标题"
    assert ov["fade_top_offset"] == 0.2 and ov["id"] != 7
    host = _task("c", text=True, w=540, h=960)
    host["text_overlays"][0].update({"id": 9, "content": "正文", "_track_id": "stay"})
    assert lt.apply_pack(host, lt.pack_layers(src, layers, "all"))["styled"] == 1
    got = host["text_overlays"][0]
    assert got["id"] == 9 and got["_track_id"] == "stay" and got["content"] == "标题"
    assert abs(got["x"] - 100) < 1e-6 and abs(float(got["fontsize"]) - 20) < 1e-6
    other = _task("d", text=True, w=540, h=960)
    other["text_overlays"][0].update({"x": 15, "font_family": "Other", "content": "正文"})
    lt.apply_pack(other, lt.pack_layers(src, layers, "style"))
    kept = other["text_overlays"][0]
    assert kept["x"] == 15 and kept["content"] == "正文" and kept["font_family"] == "Arimo"
    assert float(kept["fontsize"]) == 40


def test_layer_panel_menu_pastes_and_skips_empty():
    app = _app()
    from PySide6.QtWidgets import QMenu, QPushButton
    from cutora.batch import layer_transfer as lt
    from cutora.batch import monitor_model as mm
    from cutora.ui.central_monitor import CentralMonitorDialog
    a = _task("a")
    b = _task("b", text=False)

    class _Host:
        pass
    host = _Host()
    host.tasks = [a, b]
    dlg = CentralMonitorDialog(host, [a, b])
    g = dlg.grid
    g._sched.stop()
    g._compose_timer.stop()
    g._edited_timer.stop()
    g._renderer.cancel_all()
    g._request = lambda *args, **kwargs: None
    try:
        dlg.show()
        assert "点一条视频" in dlg.layers.hint.text()
        g.active = 0
        g.selected = {0}
        dlg.layers.sync()
        assert dlg.layers.lst.count() == 2
        assert "a" in dlg.layers.title.text()
        from PySide6.QtWidgets import QCheckBox
        assert dlg.layers.findChildren(QPushButton) == []
        assert dlg.layers.findChildren(QCheckBox) == []
        xfer = QMenu(dlg)
        dlg.layers._add_transfer_menu(xfer)
        labels = [act.text() for act in xfer.actions()]
        assert "复制到其他视频" in labels
        assert "粘贴到选中的视频" in labels
        assert "粘贴到全部视频" in labels
        paste_all = next(act.menu() for act in xfer.actions() if act.text() == "粘贴到全部视频")
        assert [act.text() for act in paste_all.actions()] == ["粘贴素材", "粘贴属性", "粘贴全部"]
        menu = QMenu(dlg)
        g._add_layer_menus(menu, g.tiles[0])
        actions = list(menu.actions())
        copy_act = next(act for act in actions if act.menu() is not None and act.text() == "复制图层")
        sub = copy_act.menu()
        labels = [act.text() for act in list(sub.actions())]
        assert "复制全部素材" in labels
        assert "复制全部属性" in labels
        assert "复制全部图层的全部属性" in labels
        assert not any("全部的全部" in text for text in labels)
        assert any(text.startswith("文本") for text in labels)
        assert any(text.startswith("粘贴图层到全部") for text in [act.text() for act in actions])
        next(act for act in list(sub.actions()) if act.text() == "复制全部素材").trigger()
        assert g._clipboard["kind"] == "layers" and g._clipboard["mode"] == "material"
        g.selected = {1}
        paste_act = next(act for act in actions if act.menu() is not None and "粘贴图层到选中的" in act.text())
        paste_sub = paste_act.menu()
        next(act for act in list(paste_sub.actions()) if act.text() == "粘贴素材").trigger()
        assert b["text_overlays"][0]["content"] == "标题"
        assert b["text_overlays"][0]["start"] == 1.0 and b["text_overlays"][0]["end"] == 3.0
        assert len([tr for tr in b["timeline"].tracks if tr.type == "video"]) == 2
        g.undo()
        assert b["text_overlays"] == []
        assert [tr.type for tr in b["timeline"].tracks] == ["video"]
        b["text_overlays"].append({
            "id": 9, "content": "正文", "x": 15, "font_family": "Other",
            "fontsize": 40, "start": 0.0, "end": 1.0,
        })
        g.copy_layers(g.tiles[0], [row for row in lt.list_layers(a) if row["kind"] == "text"], "material")
        assert g._clipboard["mode"] == "material"
        g.paste_layers("style")
        assert g._clipboard["mode"] == "material"
        assert b["text_overlays"][0]["content"] == "正文"
        assert b["text_overlays"][0]["font_family"] == "Arimo"
        assert b["text_overlays"][0]["x"] == 15 and b["text_overlays"][0]["id"] == 9
        g.undo()
        assert b["text_overlays"][0]["font_family"] == "Other"
        b["text_overlays"].clear()
        assert not g.history.can_undo()
        g.paste_layers("style")
        assert not g.history.can_undo()
        assert b["text_overlays"] == []
        row0 = dlg.layers.model.rows()[0]
        assert dlg.layers.set_hidden_span([row0], True)
        assert dlg.layers.model.rows()[0].get("hidden") is True
    finally:
        mm.set_clipboard(None)
        g.shutdown()
        dlg.close()
        app.processEvents()


def test_batch_paint_decodes_one_visible_tile_and_skips_text_only():
    """一批格子只补屏幕里的一格；只改文字时不重新解码视频。"""
    app = _app()
    from PySide6.QtCore import QRectF
    from cutora.batch import layer_transfer as lt
    from cutora.batch import monitor_model as mm
    from cutora.ui.central_monitor import CentralMonitorDialog

    tasks = [_task("v%d" % i) for i in range(4)]
    tasks[1]["text_overlays"][0]["font_family"] = "Other"

    class _Host:
        pass

    host = _Host()
    host.tasks = tasks
    dlg = CentralMonitorDialog(host, tasks)
    g = dlg.grid
    seen = []

    def spy(tile, prio=0):
        # 记下谁被送去解码，并替真实请求写上 req_gen，这样下一拍会轮到下一格。
        seen.append(tile.idx)
        tile.req_gen = tile.gen

    g._sched.stop()
    g._compose_timer.stop()
    g._edited_timer.stop()
    g._renderer.cancel_all()
    g._request = spy
    try:
        dlg.show()
        for _ in range(4):
            app.processEvents()
        g.relayout = lambda *a, **k: None
        g.viewport_rect = lambda: QRectF(0, 0, 480, 900)
        for i, tile in enumerate(g.tiles):
            y = 20000 if i >= 2 else 40 + i * 140
            tile.head = QRectF(16, y, 200, 24)
            tile.rect = QRectF(16, y + 24, 200, 80)
        g._sched.stop()
        g._compose_timer.stop()
        g._renderer.cancel_all()
        seen.clear()
        assert g._in_view(g.tiles[0]) and g._in_view(g.tiles[1])
        assert not g._in_view(g.tiles[2]) and not g._in_view(g.tiles[3])

        text = [row for row in lt.list_layers(tasks[0]) if row["kind"] == "text"]
        g.copy_layers(g.tiles[0], text, "style")
        g.selected = {1}
        g.paste_layers("style")
        app.processEvents()
        assert tasks[1]["text_overlays"][0]["font_family"] == "Arimo"
        assert seen == []

        g._op = {"kind": "pan"}
        g._changed(list(g.tiles), prio=0)
        g._schedule_now()
        assert seen == []
        g._changed([g.tiles[0]], prio=0)
        assert seen == [0]
        g._op = None
        g._sched.stop()

        seen.clear()
        g.active = 0
        g._changed(list(g.tiles), prio=0)
        assert seen == []
        g._sched.stop()
        g._renderer.cancel_all()
        g._schedule_now()
        assert seen == [0]
        g._schedule_now()
        assert seen == [0, 1]
        g._schedule_now()
        assert seen == [0, 1]

        g._renderer.pending_count = lambda: 1
        g.tiles[0].gen += 1
        g._schedule_now()
        assert seen == [0, 1]
    finally:
        mm.set_clipboard(None)
        g.shutdown()
        dlg.close()
        app.processEvents()

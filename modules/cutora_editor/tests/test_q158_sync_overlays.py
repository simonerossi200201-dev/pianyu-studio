# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q158 · 中央监视器同步编辑连同类素材一起动；批量加载图片的开头 / 结尾淡入淡出真正生效；
粘贴图片属性带上转场和元素几何。"""
from __future__ import annotations

import copy
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_batch_anims_become_a_real_transition():
    from cutora.batch.script_insert_image import make_overlay, stamp_transition, transition_from_anims
    assert transition_from_anims("fade", "fade")[0] == "fade_both"
    assert transition_from_anims("fade", "none")[0] == "fade_in"
    assert transition_from_anims("none", "fade")[0] == "fade_out"
    assert transition_from_anims("none", "none")[0] == "none"
    assert transition_from_anims("scale_in", "fade")[0] == "pop_both"
    assert transition_from_anims("slide+left", None)[0] == "slide_left_in"
    assert transition_from_anims("weird_thing", None)[0] == "fade_in"
    ov = make_overlay("/x/a.png", 0.0, 3.0, 1080, 1920, 400, 300, "fit", 1.0, 0.0, 0.0, "tr", 1,
                      anim_in="fade", anim_out="none")
    assert ov["transition_preset"] == "fade_in" and ov["transition_duration"] > 0
    stamp_transition(ov, "none", "fade")
    assert ov["transition_preset"] == "fade_out" and ov["anim_out"] == "fade"


def test_image_transition_renders_as_fade(tmp_path):
    """开头淡入：第 0 帧图片几乎看不见，转场结束后实打实画出来。"""
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication(["q158"])
    from cutora.batch.script_insert_image import make_overlay
    from cutora.render.frame_renderer import FrameRenderer
    png = tmp_path / "red.png"
    img = QImage(64, 64, QImage.Format.Format_RGB32)
    img.fill(QColor("#FF0000"))
    img.save(str(png))
    ov = make_overlay(str(png), 0.0, 3.0, 200, 200, 64, 64, "fit", 1.0, 0.0, 0.0, "tr", 1,
                      anim_in="fade", anim_out="none")
    task = {"image_overlays": [ov], "text_overlays": [], "segments": [], "dims": (200, 200)}

    def red_at(t):
        canvas = QImage(200, 200, QImage.Format.Format_ARGB32_Premultiplied)
        canvas.fill(QColor("#000000"))
        p = QPainter(canvas)
        try:
            FrameRenderer.draw_layers(p, task, 200, 200, t, [])
        finally:
            p.end()
        return canvas.pixelColor(100, 100).red()
    assert red_at(0.02) < 80 < red_at(1.5), (red_at(0.02), red_at(1.5))


def test_paste_image_style_carries_transition_and_element_geometry():
    from cutora.batch import layer_transfer as lt
    from cutora.model.timeline import Timeline

    def box(w, h):
        return {"timeline": Timeline(w, h, 30), "dims": (w, h), "text_overlays": [], "image_overlays": []}
    src = box(1080, 1920)
    src["image_overlays"].append({
        "id": 1, "x": 0, "y": 0, "w": 1080, "h": 1920, "opacity": 255, "start": 0.0, "end": 2.0,
        "transition_preset": "fade_in", "transition_duration": 0.6, "flip_x": True,
        "elements": [{"path": "a.png", "x": 100, "y": 200, "w": 400, "h": 300, "blend_mode": "screen"}],
    })
    dst = box(540, 960)
    dest = {"id": 7, "x": 0, "y": 0, "w": 540, "h": 960, "opacity": 255, "start": 1.0, "end": 3.0,
            "_track_id": "img", "transition_preset": "none",
            "elements": [{"path": "b.png", "x": 0, "y": 0, "w": 50, "h": 50, "blend_mode": "normal"}]}
    dst["image_overlays"].append(dest)
    out = lt.apply_pack(dst, lt.pack_layers(src, lt.list_layers(src), "style"))
    assert out["styled"] == 1
    assert dest["transition_preset"] == "fade_in" and abs(dest["transition_duration"] - 0.6) < 1e-9
    assert dest["flip_x"] is True and dest["start"] == 1.0 and dest["id"] == 7
    el = dest["elements"][0]
    assert el["path"] == "b.png" and el["blend_mode"] == "screen"
    assert abs(el["x"] - 50) < 1e-6 and abs(el["w"] - 200) < 1e-6      # 按画布比例 0.5


def test_sibling_overlay_matching():
    from cutora.batch import monitor_model as mm
    from cutora.model.timeline import Timeline

    def task(w=1080, h=1920):
        return {"timeline": Timeline(w, h, 30), "dims": (w, h), "text_overlays": [], "image_overlays": []}
    a, b = task(), task(540, 960)
    img_a = {"id": "ov_insert_1", "_script": "insert_image", "elements": [{"path": "P/x.png"}], "x": 0, "y": 0}
    img_b = {"id": "ov_insert_2", "_script": "insert_image", "elements": [{"path": "P/x.png"}], "x": 0, "y": 0}
    other_b = {"id": "ov_insert_3", "_script": "insert_image", "elements": [{"path": "P/y.png"}]}
    a["image_overlays"] = [img_a]
    b["image_overlays"] = [other_b, img_b]
    assert mm.sibling_overlay(a, img_a, b) is img_b                       # 同一张图，不管排第几
    t_a = {"id": 11, "_script": "batch_load", "content": "第一集", "_preset_name": "标题", "x": 0, "y": 0}
    t_b1 = {"id": 12, "_script": "batch_load", "content": "第二集", "_preset_name": "标题"}
    t_b2 = {"id": 13, "_script": "batch_load", "content": "第一集", "_preset_name": "标题"}
    a["text_overlays"] = [t_a]
    b["text_overlays"] = [t_b1, t_b2]
    assert mm.sibling_overlay(a, t_a, b) is t_b2                          # 同内容优先
    b["text_overlays"] = [t_b1]
    assert mm.sibling_overlay(a, t_a, b) is t_b1                          # 不同内容、同预设 → 同类第 N 条
    assert mm.sibling_overlay(a, t_a, {"text_overlays": [], "image_overlays": []}) is None
    loose = {"id": 99, "content": "手写的"}
    b["text_overlays"] = [{"id": 5, "_script": "batch_load", "content": "x"}]
    assert mm.sibling_overlay(a, loose, b) is None                        # 批次不同 → 不乱配
    assert abs(mm.sibling_ratio(a, b) - 0.5) < 1e-9


def test_monitor_sync_drag_moves_same_text_on_selected_tiles(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path / "ud"))
    from test_q144_central_monitor import _app, _clip, _dialog, _task, _text_ov, _wait
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from cutora.batch import monitor_model as mm
    app = _app()
    tasks = [_task(_clip(tmp_path, dur=2)), _task(_clip(tmp_path, "0x30C030", dur=2)),
             _task(_clip(tmp_path, "0xC03030", dur=2))]
    ovs = [_text_ov(t, content="同一条标题") for t in tasks]
    for o in ovs:
        o["_script"] = "batch_load"
    dlg = _dialog(tasks)
    g = dlg.grid
    try:
        assert _wait(app, lambda: all(t.video is not None for t in g.tiles))
        dlg.ck_link.setChecked(True)
        g.selected = {0, 1}                       # 第 3 条没选：不该动
        g.active = 0
        before = [(float(o["x"]), float(o["y"])) for o in ovs]
        tile = g.tiles[0]
        ax, ay = mm.overlay_anchor(tile.task, ovs[0])
        bx, by, bw, bh = mm.overlay_bounds(tile.task, ovs[0])
        p0 = tile.canvas_to_widget(bx + bw / 2.0, by + bh / 2.0) if hasattr(tile, "canvas_to_widget") else None
        if p0 is None:
            r = tile.canvas_rect_to_widget(bx, by, bw, bh)
            p0 = r.center()
        k = tile.k
        p1 = QPointF(p0.x() + 30 * k, p0.y() + 20 * k)

        def ev(typ, pos, buttons, button=Qt.MouseButton.LeftButton):
            return QMouseEvent(typ, pos, pos, button, buttons, Qt.KeyboardModifier.NoModifier)
        g.mousePressEvent(ev(QEvent.Type.MouseButtonPress, p0, Qt.MouseButton.LeftButton))
        for i in range(1, 6):
            q = QPointF(p0.x() + (p1.x() - p0.x()) * i / 5.0, p0.y() + (p1.y() - p0.y()) * i / 5.0)
            g.mouseMoveEvent(ev(QEvent.Type.MouseMove, q, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton))
        g.mouseReleaseEvent(ev(QEvent.Type.MouseButtonRelease, p1, Qt.MouseButton.NoButton))
        app.processEvents()
        d0 = (ovs[0]["x"] - before[0][0], ovs[0]["y"] - before[0][1])
        d1 = (ovs[1]["x"] - before[1][0], ovs[1]["y"] - before[1][1])
        assert abs(d0[0] - 30) < 1.5 and abs(d0[1] - 20) < 1.5, d0
        assert abs(d1[0] - d0[0]) < 1e-6 and abs(d1[1] - d0[1]) < 1e-6, (d0, d1)
        assert (ovs[2]["x"], ovs[2]["y"]) == before[2]
        g.undo()
        assert all((o["x"], o["y"]) == b for o, b in zip(ovs, before))
        # 微移也一起
        g.ov_sel = [(0, ovs[0])]
        g.nudge(5, 0)
        assert ovs[0]["x"] - before[0][0] == 5 and ovs[1]["x"] - before[1][0] == 5
        assert ovs[2]["x"] == before[2][0]
    finally:
        dlg.close()

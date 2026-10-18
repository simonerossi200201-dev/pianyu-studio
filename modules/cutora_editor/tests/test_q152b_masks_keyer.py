# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q152-B/C · 形状遮罩 + 蓝绿幕抠像。

- masks：规范化、栅格化（矩形 / 椭圆 / 多边形 / 圆角 / 旋转）、羽化 / 扩展、叠加运算、编辑函数。
- keyer：绿幕透明、前景不透明、溢色抑制、Matte 整理、视图、吸管。
- layer_fx：没有效果原样返回同一对象；有效果出 ARGB 预乘；缓存。
- 渲染：视频片段预览 / 导出同一条路；覆层遮罩整幅层路径；图片抠像。
- 命令：fx.mask_add / update / delete / reorder、fx.keyer_set，撤销。
- 检查器 LayerFxPanel；画布手柄拖动、多边形绘制、吸管。
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


def _app():
    from test_q145_canvas_shortcuts import _app as app
    return app()


def _green_scene(w=320, h=240):
    """绿幕 + 肤色圆 + 白方块 + 蓝方块（RGB）。"""
    import cv2
    img = np.zeros((h, w, 3), np.uint8)
    img[:] = (30, 200, 30)
    cv2.circle(img, (160, 80), 40, (225, 180, 150), -1)
    cv2.rectangle(img, (110, 120), (210, 220), (240, 240, 240), -1)
    cv2.rectangle(img, (20, 20), (60, 60), (60, 60, 220), -1)
    return img


def _qimg(rgb):
    h, w = rgb.shape[:2]
    return QImage(np.ascontiguousarray(rgb).data, w, h, w * 3, QImage.Format.Format_RGB888).copy() \
        .convertToFormat(QImage.Format.Format_BGR888)


# ───────────────────────────── masks ─────────────────────────────

def test_mask_normalize_and_defaults():
    from cutora.fx import masks as M
    m = M.normalize({"type": "rect"})
    assert m["rect"] == [0.25, 0.25, 0.5, 0.5] and m["mode"] == "add" and m["enabled"] and m["id"]
    assert M.normalize({"type": "polygon", "points": [[0, 0], [1, 0]]}) is None
    assert M.normalize({"type": "螺旋", "mode": "x", "feather": -5, "opacity": 3})["type"] == "rect"
    n = M.normalize({"type": "ellipse", "feather": -5, "opacity": 3, "radius": 9})
    assert n["feather"] == 0.0 and n["opacity"] == 1.0 and n["radius"] == 0.5
    assert M.normalize("nope") is None
    assert not M.has_masks({"masks": [{"type": "rect", "enabled": False}]})
    assert M.has_masks({"masks": [{"type": "rect"}]})


def test_rasterize_shapes_and_combine():
    from cutora.fx import masks as M
    w, h = 200, 100
    r = M.rasterize(M.normalize({"type": "rect", "rect": [0, 0, 0.5, 1]}), w, h)
    assert r.shape == (h, w) and r[50, 20] == 1.0 and r[50, 150] == 0.0
    e = M.rasterize(M.normalize({"type": "ellipse", "rect": [0, 0, 1, 1]}), w, h)
    assert e[50, 100] == 1.0 and e[2, 2] == 0.0 and e[50, 3] > 0.5
    p = M.rasterize(M.normalize({"type": "polygon", "points": [[0, 0], [1, 0], [0, 1]]}), w, h)
    assert p[10, 10] == 1.0 and p[90, 190] == 0.0
    rr = M.rasterize(M.normalize({"type": "rect", "rect": [0, 0, 1, 1], "radius": 0.5}), w, h)
    assert rr[1, 1] < 0.1 and rr[50, 100] == 1.0           # 圆角切掉了角
    up = M.rasterize(M.normalize({"type": "rect", "rect": [0.4, 0, 0.2, 1]}), w, h)          # 竖条 x 80–120
    rot = M.rasterize(M.normalize({"type": "rect", "rect": [0.4, 0, 0.2, 1], "rotation": 90}), w, h)
    assert up[50, 60] == 0.0 and rot[50, 60] == 1.0 and rot[5, 100] == 0.0   # 转 90° 后变成横条 y 30–70
    # 羽化：边上渐变；扩展：变大
    f = M.rasterize(M.normalize({"type": "rect", "rect": [0, 0, 0.5, 1], "feather": 20}), w, h)
    assert 0.1 < f[50, 100] < 0.9 and f[50, 20] > 0.95
    x = M.rasterize(M.normalize({"type": "rect", "rect": [0, 0, 0.5, 1], "expand": 20}), w, h)
    assert x[50, 110] == 1.0
    # 叠加：加 / 减 / 交 / 反转
    a = M.normalize({"type": "rect", "rect": [0, 0, 0.5, 1]})
    b = M.normalize({"type": "rect", "rect": [0.25, 0, 0.5, 1], "mode": "subtract"})
    c = M.normalize({"type": "rect", "rect": [0.25, 0, 0.5, 1], "mode": "intersect"})
    tot = M.combine([a, b], w, h)
    assert tot[50, 20] == 1.0 and tot[50, 70] == 0.0 and tot[50, 150] == 0.0
    tot = M.combine([a, c], w, h)
    assert tot[50, 20] == 0.0 and tot[50, 70] == 1.0 and tot[50, 150] == 0.0
    inv = M.combine([M.normalize({"type": "rect", "rect": [0, 0, 0.5, 1], "invert": True})], w, h)
    assert inv[50, 20] == 0.0 and inv[50, 150] == 1.0
    assert M.combine([], w, h) is None
    # 第一个就是「相减」= 挖洞
    hole = M.combine([b], w, h)
    assert hole[50, 20] == 1.0 and hole[50, 100] == 0.0


def test_mask_edit_helpers():
    from cutora.fx import masks as M
    m = M.normalize({"type": "polygon", "points": [[0, 0], [1, 0], [1, 1], [0, 1]]})
    assert M.bounds(m) == (0, 0, 1, 1)
    assert M.moved(m, 0.1, 0.2)["points"][0] == [0.1, 0.2]
    assert M.with_point(m, 1, 0.5, 0.5)["points"][1] == [0.5, 0.5]
    assert len(M.insert_point(m, 0, 0.5, 0.0)["points"]) == 5
    assert M.remove_point(m, 0)["points"][0] == [1, 0]
    tri = M.normalize({"type": "polygon", "points": [[0, 0], [1, 0], [0, 1]]})
    assert M.remove_point(tri, 0) is None
    r = M.with_rect(M.default_mask("rect"), 0.1, 0.1, 0.0, 0.3)
    assert r["rect"][2] > 0


# ───────────────────────────── keyer ─────────────────────────────

def test_keyer_green_screen_matte_and_despill():
    from cutora.fx import keyer as K
    img = _green_scene()
    # Q165：只抠像不消除溢色（默认关）—— 颜色原样，只出 alpha
    out0, a = K.apply(img, {"enabled": True, "color": "#1EC81E", "gain": 1.2})
    assert a[5, 5] == 0 and a[80, 160] == 255 and a[170, 160] == 255 and a[40, 40] == 255
    assert tuple(out0[5, 5]) == tuple(img[5, 5])
    out, a = K.apply(img, {"enabled": True, "color": "#1EC81E", "gain": 1.2,
                           "despill_on": True, "despill_scope": "all"})
    assert tuple(out[80, 160]) == (225, 180, 150)                 # 肤色没被动
    # 带绿的像素：溢色被压成灰
    g = out[5, 5]
    assert abs(int(g[0]) - int(g[1])) < 6 and abs(int(g[1]) - int(g[2])) < 6
    # 打开但强度 0：背景颜色原样
    out2, _a2 = K.apply(img, {"enabled": True, "despill_on": True, "despill": 0.0})
    assert tuple(out2[5, 5]) == tuple(img[5, 5])
    # 蓝幕：换屏幕色就行
    blue = img.copy()
    blue[(img[..., 1] == 200)] = (30, 30, 220)
    _o, ab = K.apply(blue, {"enabled": True, "color": "#1E1EDC"})
    assert ab[5, 5] == 0 and ab[80, 160] == 255


def test_keyer_matte_controls_and_views():
    from cutora.fx import keyer as K
    img = _green_scene()
    base = {"enabled": True, "color": "#1EC81E"}
    _o, a0 = K.apply(img, base)
    _o, a_er = K.apply(img, dict(base, erode=4))
    assert a_er.astype(int).sum() < a0.astype(int).sum()
    _o, a_di = K.apply(img, dict(base, erode=-4))
    assert a_di.astype(int).sum() > a0.astype(int).sum()
    _o, a_bl = K.apply(img, dict(base, blur=6))
    edge = a_bl[80, 160 + 40]                                     # 圆边上
    assert 0 < edge < 255
    _o, a_wh = K.apply(img, dict(base, black=0.2, white=0.4))
    assert a_wh[80, 160] == 255 and a_wh[5, 5] == 0
    m, am = K.apply(img, dict(base, view="matte"))
    assert am is None and tuple(m[5, 5]) == (0, 0, 0) and tuple(m[80, 160]) == (255, 255, 255)
    s, as_ = K.apply(img, dict(base, view="source"))
    assert as_ is None and s is img
    sp, asp = K.apply(img, dict(base, view="spill"))
    assert asp is None and tuple(sp[80, 160]) == (225, 180, 150)
    assert K.sample_color(img, 5, 5) == "#1EC81E"
    assert K.normalize({"gain": 99, "view": "x", "white": 0.1, "black": 0.5})["gain"] == 8.0
    assert K.normalize({"white": 0.1, "black": 0.5})["white"] > 0.5
    assert K.PRESETS["phone_green"]["preblur"] > 0


# ───────────────────────────── layer_fx ─────────────────────────────

def test_apply_layer_fx_identity_and_output(app=None):
    _app()
    from cutora.fx.layer_fx import apply_layer_fx, clear_cache, fx_signature
    from cutora.model.timeline_model import Clip
    clear_cache()
    q = _qimg(_green_scene())
    clip = Clip("s", 0, 1, 0)
    assert fx_signature(clip) is None
    assert apply_layer_fx(q, clip) is q                           # 老工程：同一个对象
    assert apply_layer_fx(q, {"masks": [], "keyer": {}}) is q
    clip.keyer = {"enabled": True, "color": "#1EC81E"}
    out = apply_layer_fx(q, clip)
    assert out is not q and out.format() == QImage.Format.Format_ARGB32_Premultiplied
    assert out.pixelColor(5, 5).alpha() == 0 and out.pixelColor(160, 80).alpha() == 255
    assert apply_layer_fx(q, clip) is out                         # 缓存
    clip.masks = [{"type": "rect", "rect": [0, 0, 0.5, 1]}]
    out2 = apply_layer_fx(q, clip)
    assert out2.pixelColor(170, 80).alpha() == 0 and out2.pixelColor(130, 170).alpha() == 255
    # 只有遮罩：alpha 来自遮罩，颜色不变
    only = {"masks": [{"type": "rect", "rect": [0, 0, 0.5, 1], "feather": 0}]}
    o3 = apply_layer_fx(q, only)
    assert o3.pixelColor(20, 20).alpha() == 255 and o3.pixelColor(300, 20).alpha() == 0
    c = o3.pixelColor(40, 40)
    assert (c.red(), c.green(), c.blue()) == (60, 60, 220)
    # ARGB 输入（带 alpha）：原 alpha 和遮罩相乘
    argb = QImage(100, 100, QImage.Format.Format_ARGB32_Premultiplied)
    argb.fill(QColor(255, 0, 0, 128))
    o4 = apply_layer_fx(argb, only)
    assert o4.pixelColor(10, 10).alpha() == 128 and o4.pixelColor(90, 10).alpha() == 0


def test_preview_and_export_video_paths_share_fx(tmp_path):
    """同一个 clip 带遮罩 + 抠像：PreviewEngine 和 ExportRenderer 出一样的 alpha 形状。"""
    _app()
    from test_q31_keyframes import _color_mp4, _export_frame, _task_with_clip
    from test_q62_keyframes import _preview_composited
    media = str(tmp_path / "green.mp4")
    _color_mp4(media, "0x1EC81E", frames=30, size="160x90")
    task, _tr, clip = _task_with_clip(media, start_t=0.0, duration=1.0, canvas=(160, 90))
    clip.fit_mode = "stretch"

    def _lum(img, x, y):
        return img.pixelColor(x, y).lightness()

    a = _preview_composited(task, 0.5)
    assert _lum(a, 80, 45) > 60                                   # 绿底画出来了
    clip.keyer = {"enabled": True, "color": "#1EC81E", "gain": 1.2}
    b = _preview_composited(task, 0.5)
    assert _lum(b, 80, 45) < 20                                   # 全抠掉 → 黑底
    clip.keyer = {}
    clip.masks = [{"type": "rect", "rect": [0, 0, 0.5, 1]}]
    c = _preview_composited(task, 0.5)
    d = _export_frame(task, 0.5)                                  # 导出的视频路径（RenderWorker）
    for img in (c, d):
        assert _lum(img, 20, 45) > 60 and _lum(img, 140, 45) < 20
    clip.keyer = {"enabled": True, "color": "#1EC81E", "gain": 1.2}
    assert _lum(_export_frame(task, 0.5), 20, 45) < 20


def test_overlay_mask_and_image_keyer_render(tmp_path):
    _app()
    from cutora.render.frame_renderer import FrameRenderer, get_cached_image
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Track
    import cv2
    path = str(tmp_path / "gs.png")
    cv2.imwrite(path, cv2.cvtColor(_green_scene(), cv2.COLOR_RGB2BGR))
    tl = Timeline(640, 480, 30.0)
    tl.add_track(Track("image"))
    tl.add_track(Track("text"))
    task = {"timeline": tl, "segments": [], "style": {}, "text_overlays": [], "image_overlays": [], "dims": (640, 480)}
    ov = {"id": 1, "type": "image", "content": path, "x": 160, "y": 120, "w": 320, "h": 240,
          "start": 0.0, "end": 4.0, "opacity": 255}
    task["image_overlays"].append(ov)

    def _render():
        img = QImage(640, 480, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(0xFF000000)
        p = QPainter(img)
        try:
            FrameRenderer.draw_layers(p, task, 640, 480, 1.0, render_order=["image", "text"])
        finally:
            p.end()
        return img

    r0 = _render()
    assert r0.pixelColor(170, 130).green() > 150                 # 绿底
    ov["keyer"] = {"enabled": True, "color": "#1EC81E"}
    r1 = _render()
    assert r1.pixelColor(170, 130).lightness() < 20               # 抠掉了
    assert r1.pixelColor(160 + 160, 120 + 80).red() > 150         # 肤色还在
    ov.pop("keyer")
    ov["masks"] = [{"type": "rect", "rect": [0, 0, 0.5, 1]}]
    r2 = _render()
    assert r2.pixelColor(170, 130).green() > 150 and r2.pixelColor(470, 130).lightness() < 20
    # 文本层遮罩
    task["image_overlays"].clear()
    tov = {"id": 2, "type": "text", "content": "HELLO", "x": 20, "y": 100, "w": 600, "h": 200,
           "font_family": "Arimo", "fontsize": 150, "start": 0.0, "end": 4.0, "color": "#FFFFFF",
           "fit_mode": "none"}
    task["text_overlays"].append(tov)

    def _lit(img, x0, x1):
        return sum(1 for y in range(100, 300, 8) for x in range(x0, x1, 8) if img.pixelColor(x, y).lightness() > 60)

    t0 = _render()
    assert _lit(t0, 20, 320) > 0 and _lit(t0, 320, 620) > 0
    tov["masks"] = [{"type": "rect", "rect": [0, 0, 0.5, 1]}]
    t1 = _render()
    assert _lit(t1, 20, 320) > 0 and _lit(t1, 330, 620) == 0
    assert get_cached_image(path) is not None


# ───────────────────────────── 命令 ─────────────────────────────

def test_fx_commands_and_undo():
    _app()
    from test_q152a_keyframe_editor import _bus_window, _clip_task
    task, _tr, clip = _clip_task()
    win, bus = _bus_window(task)
    loc = {"target": "clip", "track_id": "v0", "clip_index": 0}
    r = bus.execute("fx.mask_add", dict(loc, mask={"type": "ellipse", "name": "脸"}), source="ui")
    mid = r["mask_id"]
    assert clip.masks[0]["type"] == "ellipse" and clip.masks[0]["name"] == "脸"
    bus.execute("fx.mask_update", dict(loc, mask_id=mid, patch={"feather": 12, "mode": "subtract"}), source="ui")
    assert clip.masks[0]["feather"] == 12.0 and clip.masks[0]["mode"] == "subtract"
    r2 = bus.execute("fx.mask_add", dict(loc, mask={"type": "rect"}), source="ui")
    bus.execute("fx.mask_reorder", dict(loc, order=[r2["mask_id"], mid]), source="ui")
    assert [m["id"] for m in clip.masks] == [r2["mask_id"], mid]
    with pytest.raises(Exception):
        bus.execute("fx.mask_reorder", dict(loc, order=[mid]), source="ui")
    bus.execute("fx.mask_delete", dict(loc, mask_id=mid), source="ui")
    assert [m["id"] for m in clip.masks] == [r2["mask_id"]]
    with pytest.raises(Exception):
        bus.execute("fx.mask_update", dict(loc, mask_id="nope", patch={}), source="ui")
    with pytest.raises(Exception):
        bus.execute("fx.mask_add", dict(loc, mask={"type": "polygon", "points": [[0, 0]]}), source="ui")
    bus.execute("fx.keyer_set", dict(loc, patch={"enabled": True, "color": "#00FF00", "gain": 2.0}), source="ui")
    assert clip.keyer["enabled"] and clip.keyer["color"] == "#00FF00" and clip.keyer["gain"] == 2.0
    bus.execute("fx.keyer_set", dict(loc, patch={"enabled": False, "color": "#1EC81E", "gain": 1.2}), source="ui")
    assert clip.keyer == {}
    n = len(win.history_mgr.history)
    assert n == 7
    win.undo()
    clip2 = win.tasks[0]["timeline"].tracks[0].clips[0]
    assert clip2.keyer["enabled"] is True
    # 覆层也行
    cur = win.tasks[0]                                            # undo 之后 task 对象换了
    cur.setdefault("text_overlays", []).append({"id": 1, "type": "text", "content": "A", "x": 0, "y": 0,
                                                "w": 100, "h": 50, "start": 0, "end": 1})
    r = bus.execute("fx.mask_add", {"target": "overlay", "list": "text_overlays", "overlay_index": 0,
                                    "mask": {"type": "rect"}}, source="ui")
    assert win.tasks[0]["text_overlays"][0]["masks"][0]["id"] == r["mask_id"]
    bus.execute("fx.mask_delete", {"target": "overlay", "list": "text_overlays", "overlay_index": 0,
                                   "mask_id": r["mask_id"]}, source="ui")
    assert "masks" not in win.tasks[0]["text_overlays"][0]


def test_clip_schema_has_fx_keys():
    from cutora.model.project_schema import CLIP_KEYS
    from cutora.model.timeline_model import Clip
    assert "masks" in CLIP_KEYS and "keyer" in CLIP_KEYS
    c = Clip("s", 0, 1, 0)
    assert c.masks == [] and c.keyer == {}


# ───────────────────────────── 检查器 ─────────────────────────────

def test_inspector_fx_panel_video_and_overlay():
    from test_q152a2_keyable_motion import _panel_env
    win, panel, task, ov, clip = _panel_env("clip")
    try:
        fx = panel.vid_fx
        assert fx.sec_mask.isVisibleTo(panel) and fx.sec_key.isVisibleTo(panel)
        mid = fx.add_mask("rect")
        clip2 = task["timeline"].tracks[0].clips[0]
        assert clip2.masks and clip2.masks[0]["id"] == mid and fx.active_mask_id == mid
        assert fx.mask_list.count() == 1
        fx.mask_feather.setValue(30)
        assert clip2.masks[0]["feather"] == 30.0
        fx.mask_mode.setCurrentText("subtract")
        assert clip2.masks[0]["mode"] == "subtract"
        fx.mask_list.item(0).setCheckState(Qt.CheckState.Unchecked)
        assert clip2.masks[0]["enabled"] is False
        fx.delete_mask()
        assert clip2.masks == [] and fx.mask_list.count() == 0
        # 抠像
        fx.key_enable.setChecked(True)
        assert clip2.keyer["enabled"] is True and fx.key_gain.isEnabled()
        fx.key_preset.setCurrentText("blue")
        assert clip2.keyer["color"] == "#1E3CDC"
        fx.key_view.setCurrentText("matte")
        assert clip2.keyer["view"] == "matte"
        fx.set_picked_color("#20C020")
        assert clip2.keyer["color"] == "#20C020" and not fx.btn_pick.isChecked()
        assert len(win.history_mgr.history) >= 8
    finally:
        win.close()
    win, panel, task, ov, clip = _panel_env("overlay")
    try:
        fx = panel.ov_fx
        assert fx.sec_mask.isVisibleTo(panel) and not fx.sec_key.isVisibleTo(panel)   # 文本没有抠像
        fx.add_mask("ellipse")
        assert task["text_overlays"][0]["masks"][0]["type"] == "ellipse"
        fx.btn_poly.click()
        assert panel.fx_action == ("draw_polygon", None)
    finally:
        win.close()


# ───────────────────────────── 画布 ─────────────────────────────

def _canvas_env():
    _app()
    from test_q145_canvas_shortcuts import _canvas_texts, _click, _glyph_point
    from test_q62_keyframes import _win_for_task
    from cutora.core.commands import get_bus, reset_bus_for_tests
    from cutora.ui.panels.property import PropertyPanel
    c, task, ovs = _canvas_texts(1)
    ov = ovs[0]
    reset_bus_for_tests()
    win = _win_for_task(task)
    get_bus().bind_window(win)
    panel = PropertyPanel()
    panel.setParent(win)
    win.prop_panel = panel
    win.video_canvas = c
    c.setParent(win)
    win.show()
    c.set_data(task, 1.0)
    c.repaint()
    QApplication.processEvents()
    gx, gy = _glyph_point(c, 0)
    _click(c, gx, gy)
    panel.load_data("overlay", ov)
    c.repaint()
    QApplication.processEvents()
    return c, task, ov, win, panel


def _to_widget(c, p):
    from test_q145_canvas_shortcuts import VH, VW
    t = c._preview_target_rect()
    return t.x() + p.x() * t.width() / VW, t.y() + p.y() * t.height() / VH


def test_canvas_mask_handles_drag_and_polygon_draw():
    from test_q145_canvas_shortcuts import _drag, _mouse
    c, task, ov, win, panel = _canvas_env()
    try:
        fx = panel.ov_fx
        mid = fx.add_mask("rect")
        c.repaint()
        QApplication.processEvents()
        geom = c._mask_geom
        assert geom and geom["mask"]["id"] == mid and set(geom["handles"]) >= {"nw", "se", "e"}
        # 拖右边中点往右：宽度变大，左边不动
        e = geom["handles"]["e"]
        x, y = _to_widget(c, e)
        n_hist = len(win.history_mgr.history)
        _drag(c, x, y, x + 30, y)
        r = ov["masks"][0]["rect"]
        assert r[0] == pytest.approx(0.25) and r[2] > 0.5
        assert len(win.history_mgr.history) == n_hist + 1
        # 拖中间：整体平移
        c.repaint()
        QApplication.processEvents()
        body = c._mask_geom["outline"].boundingRect().center()
        x, y = _to_widget(c, body)
        _drag(c, x, y, x + 20, y + 10)
        r2 = ov["masks"][0]["rect"]
        assert r2[0] > r[0] and r2[1] > r[1] and r2[2] == pytest.approx(r[2])
        # 多边形：点三下 + 双击闭合
        fx.btn_poly.click()
        assert c._mask_mode and c._mask_mode["kind"] == "poly"
        from cutora.ui.canvas_mask import Frame, _host_and_rec
        host, rec, _k = _host_and_rec(c)
        frame = Frame.of(host, rec)
        for nx, ny in ((0.1, 0.1), (0.9, 0.1), (0.5, 0.9)):
            p = frame.to_canvas(nx, ny)
            x, y = _to_widget(c, p)
            c.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, x, y))
            c.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, x, y, buttons=Qt.MouseButton.NoButton))
        c.mouseDoubleClickEvent(_mouse(QEvent.Type.MouseButtonDblClick, x, y))
        assert c._mask_mode is None
        polys = [m for m in ov["masks"] if m["type"] == "polygon"]
        assert len(polys) == 1 and len(polys[0]["points"]) == 3
        assert polys[0]["points"][0] == pytest.approx([0.1, 0.1], abs=0.02)
        # Esc 取消绘制
        fx.btn_poly.click()
        from PySide6.QtGui import QKeyEvent
        c.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
        assert c._mask_mode is None and len(ov["masks"]) == 2
    finally:
        c.close()
        win.close()


def test_canvas_eyedropper_on_video_clip(tmp_path):
    _app()
    from test_q145_canvas_shortcuts import _click, _mouse, _w
    from test_q31_keyframes import _color_mp4, _task_with_clip
    from test_q62_keyframes import _win_for_task
    from cutora.core.commands import get_bus, reset_bus_for_tests
    from cutora.ui.canvas import VideoCanvas
    from cutora.ui.panels.property import PropertyPanel
    media = str(tmp_path / "green.mp4")
    _color_mp4(media, "0x1EC81E", frames=30, size="160x90")
    task, tr, clip = _task_with_clip(media, start_t=0.0, duration=1.0, canvas=(160, 90))
    clip.fit_mode = "stretch"
    reset_bus_for_tests()
    win = _win_for_task(task)
    get_bus().bind_window(win)
    c = VideoCanvas()
    c.setParent(win)
    c.setFixedSize(480, 270)
    panel = PropertyPanel()
    panel.setParent(win)
    win.prop_panel = panel
    win.video_canvas = c
    win.show()
    try:
        c.set_data(task, 0.5)
        from cutora.preview.engine import PreviewEngine
        c.set_program_frame(PreviewEngine().video_frame_at(task, 0.5))   # 和程序里一样：预览线程合成好交给画布
        c.repaint()
        QApplication.processEvents()
        _click(c, *_w(c, 80, 45))
        c.repaint()
        QApplication.processEvents()
        assert c._selected_item is not None and c._selected_item.get("type") == "video_clip"
        panel.load_data("video_clip", (tr, clip))
        fx = panel.vid_fx
        fx.btn_pick.setChecked(True)
        assert c._mask_mode and c._mask_mode["kind"] == "pick"
        c.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, *_w(c, 80, 45)))
        assert c._mask_mode is None and not fx.btn_pick.isChecked()
        col = QColor(clip.keyer["color"])
        assert clip.keyer["enabled"] and col.green() > 150 and col.red() < 80
    finally:
        c.close()
        win.close()

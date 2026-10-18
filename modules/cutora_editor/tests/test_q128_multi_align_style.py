# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q128 · 多选 + 对齐 / 等距分布 + 复制粘贴样式（文本工具改造第五批）。"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

VW, VH = 1080, 1920
SHIFT = Qt.KeyboardModifier.ShiftModifier
NONE = Qt.KeyboardModifier.NoModifier


# ─────────────────────────── 纯几何 ───────────────────────────

def test_align_relative_to_each_other():
    from cutora.ui.align_ops import align
    boxes = [(10, 10, 100, 50), (200, 80, 60, 30), (50, 300, 300, 20)]
    d = align(boxes, "left")
    assert [round(x) for x, _ in d] == [0, -190, -40]
    d = align(boxes, "right")           # 最右边缘 = 350
    assert [round(x) for x, _ in d] == [240, 90, 0]
    d = align(boxes, "hcenter")         # 包围盒 10..350 → 中线 180
    assert [round(x + 0) for x, _ in d] == [120, -50, -20]
    d = align(boxes, "top")
    assert [round(y) for _, y in d] == [0, -70, -290]
    d = align(boxes, "bottom")          # 最底 = 320
    assert [round(y) for _, y in d] == [260, 210, 0]
    d = align(boxes, "vcenter")         # 10..320 → 165
    assert [round(y) for _, y in d] == [130, 70, -145]


def test_align_single_goes_to_canvas():
    from cutora.ui.align_ops import align
    d = align([(100, 100, 200, 50)], "hcenter", bounds=(0, 0, VW, VH))
    assert d[0][0] == pytest.approx(VW / 2 - 200)
    d = align([(100, 100, 200, 50)], "bottom", bounds=(0, 0, VW, VH))
    assert d[0][1] == pytest.approx(VH - 150)
    # 多个 + to_bounds 也是相对画布
    d = align([(0, 0, 10, 10), (500, 0, 10, 10)], "right", bounds=(0, 0, VW, VH), to_bounds=True)
    assert [round(x) for x, _ in d] == [VW - 10, VW - 510]


def test_distribute_equal_gaps_first_last_fixed():
    from cutora.ui.align_ops import distribute
    boxes = [(0, 0, 100, 10), (130, 0, 50, 10), (400, 0, 100, 10), (900, 0, 100, 10)]
    d = distribute(boxes, "h")
    assert d[0] == (0.0, 0.0) and d[3] == (0.0, 0.0)
    xs = [b[0] + dx for b, (dx, _) in zip(boxes, d)]
    # 间隙：总跨度 1000，框宽和 350，三段间隙各 216.67
    gaps = [xs[1] - (xs[0] + 100), xs[2] - (xs[1] + 50), xs[3] - (xs[2] + 100)]
    assert max(gaps) - min(gaps) < 1e-6
    assert distribute(boxes[:2], "h") == [(0.0, 0.0), (0.0, 0.0)]
    # 竖向
    vb = [(0, 0, 10, 100), (0, 700, 10, 100), (0, 200, 10, 100)]
    dv = distribute(vb, "v")
    ys = sorted(b[1] + dy for b, (_, dy) in zip(vb, dv))
    assert ys == pytest.approx([0, 350, 700])


def test_align_rejects_bad_mode():
    from cutora.ui.align_ops import align, distribute
    with pytest.raises(ValueError):
        align([(0, 0, 1, 1)], "diagonal")
    with pytest.raises(ValueError):
        distribute([(0, 0, 1, 1)] * 3, "z")


# ─────────────────────────── 样式剪贴板 ───────────────────────────

def _text(**kw):
    ov = {"type": "text", "content": "A", "x": 1, "y": 2, "w": 300, "h": 100, "rotation": 5,
          "start": 0.0, "end": 3.0, "id": 7, "_track_id": 9,
          "font_family": "Arimo", "fontsize": 80, "bold": True, "italic": False,
          "fx": {"fill": {"type": "solid", "colors": ["#FFFFFF"]},
                 "stroke": {"enabled": True, "color": "#000000", "width_em": 0.05},
                 "anim": {"in": "fade"}},
          "text_layout": {"align": "center", "valign": "top", "left_pad": 0, "right_pad": 0,
                          "top_pad": 0, "bottom_pad": 0, "line_spacing": 0, "letter_spacing": 0,
                          "first_indent": 0, "para_spacing": 0},
          "writing_mode": "horizontal", "fit_mode": "auto_height", "opacity": 255,
          "runs": [{"start": 0, "end": 1, "overrides": {"color": "#FF0000"}}],
          "keyframes": {"x": [[0, 1]]}}
    ov.update(kw)
    return ov


def test_copy_style_excludes_content_geometry_time_anim():
    from cutora.ui.text_style_clipboard import copy_style
    src = _text()
    st = copy_style(src)
    for key in ("content", "x", "y", "w", "h", "rotation", "start", "end", "id",
                "_track_id", "runs", "keyframes"):
        assert key not in st, key
    assert st["font_family"] == "Arimo" and st["fontsize"] == 80 and st["bold"] is True
    assert "anim" not in st["fx"]
    assert st["fx"]["stroke"]["enabled"] is True
    # 深拷贝：改剪贴板不影响源
    st["fx"]["stroke"]["enabled"] = False
    assert src["fx"]["stroke"]["enabled"] is True


def test_paste_style_keeps_target_content_geometry_and_anim():
    from cutora.ui.text_style_clipboard import copy_style, paste_style
    src = _text(fontsize=120, font_family="Roboto", fx={"fill": {"type": "solid", "colors": ["#FF00FF"]},
                                                        "anim": {"in": "slide"}})
    dst = _text(content="B", x=500, y=600, fontsize=40, fx={"fill": {"type": "solid", "colors": ["#00FF00"]},
                                                            "anim": {"in": "pop_in"}})
    assert paste_style(dst, copy_style(src)) is True
    assert dst["content"] == "B" and dst["x"] == 500 and dst["y"] == 600
    assert dst["fontsize"] == 120 and dst["font_family"] == "Roboto"
    assert dst["fx"]["fill"]["colors"] == ["#FF00FF"]
    assert dst["fx"]["anim"] == {"in": "pop_in"}          # 目标的动画留着
    # 再贴一次：没变化
    assert paste_style(dst, copy_style(src)) is False


def test_clipboard_roundtrip_and_paste_to_many():
    from cutora.ui import text_style_clipboard as C
    C.set_clipboard(None)
    assert not C.has_clipboard()
    assert C.paste_to([_text()]) == 0
    src = _text(fontsize=99)
    C.copy_from(src)
    assert C.has_clipboard()
    targets = [_text(fontsize=10, content=str(i)) for i in range(4)]
    targets.append({"type": "image", "fontsize": 1})     # 不是文本，跳过
    assert C.paste_to(targets) == 4
    assert all(t["fontsize"] == 99 for t in targets[:4])
    assert targets[4]["fontsize"] == 1
    C.set_clipboard(None)


# ─────────────────────────── 画布 ───────────────────────────

def _app():
    app = QApplication.instance() or QApplication(["q128"])
    from cutora.ui.panels.fonts import load_embedded_fonts
    load_embedded_fonts()
    return app


def _mouse(typ, x, y, mods=NONE, buttons=Qt.MouseButton.LeftButton):
    return QMouseEvent(typ, QPointF(float(x), float(y)), QPointF(float(x), float(y)),
                       Qt.MouseButton.LeftButton, buttons, mods)


def _canvas_three_texts():
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Track
    from cutora.model.factory_defaults import overlay_defaults
    from cutora.ui.canvas import VideoCanvas
    tl = Timeline(VW, VH, 30.0)
    tr = Track("text")
    tl.tracks.append(tr)
    ovs = []
    for i, (x, y) in enumerate(((60, 200), (300, 700), (500, 1300))):
        ov = overlay_defaults("text", canvas_w=VW, canvas_h=VH, track_id=tr.id)
        ov.update({"content": "T%d" % i, "x": x, "y": y, "w": 400, "h": 120, "fontsize": 60,
                   "fit_mode": "none"})
        ovs.append(ov)
    task = {"timeline": tl, "segments": [], "style": {}, "text_overlays": ovs,
            "image_overlays": [], "duration": 5.0, "dims": [VW, VH]}
    c = VideoCanvas()
    c.setFixedSize(540, 960)
    c.set_data(task, 0.5)
    c.show()
    QApplication.processEvents()
    c.repaint()
    return c, ovs


def _widget_center(c, ov):
    """一个落在这条文本**字形上**的控件坐标（画布按 alpha 命中，点框里空白处选不中）。"""
    from PySide6.QtCore import QRectF
    from cutora.render.hitmask import hits_record
    idx = c._task_data["text_overlays"].index(ov)
    rec = next(r for r in c._hit_records if r.get("type") == "overlay" and r.get("idx") == idx)
    rect = rec["rect"]
    target = c._preview_target_rect()
    sx = target.width() / VW
    sy = target.height() / VH
    for j in range(1, 40):
        for i in range(1, 80):
            vx = rect.x() + rect.width() * i / 80.0
            vy = rect.y() + rect.height() * j / 40.0
            if hits_record(rec, vx, vy):
                return target.x() + vx * sx, target.y() + vy * sy
    r, _ = c._video_rect_to_widget(QRectF(ov["x"], ov["y"], ov["w"], ov["h"]))
    return r.center().x(), r.center().y()


def _click(c, x, y, mods=NONE):
    c.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, x, y, mods))
    c.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, x, y, mods, Qt.MouseButton.NoButton))


def test_shift_click_builds_multi_and_plain_click_outside_group_clears():
    _app()
    c, ovs = _canvas_three_texts()
    try:
        counts = []
        c.multi_selection_changed.connect(counts.append)
        _click(c, *_widget_center(c, ovs[0]))
        assert c._multi == []
        _click(c, *_widget_center(c, ovs[1]), SHIFT)
        assert len(c._multi) == 2
        assert {r["idx"] for r in c._multi} == {0, 1}
        assert c._selected_item["idx"] == 1
        _click(c, *_widget_center(c, ovs[1]), SHIFT)     # 再点一次移出
        assert [r["idx"] for r in c._multi] == [0]
        _click(c, *_widget_center(c, ovs[2]), SHIFT)
        assert {r["idx"] for r in c._multi} == {0, 2}
        assert len(c.selected_text_overlays()) == 2
        # 点组外的 → 散伙
        _click(c, *_widget_center(c, ovs[1]))
        assert c._multi == [] and c._selected_item["idx"] == 1
        assert counts and counts[-1] == 0
    finally:
        c.close()


def test_group_move_drags_every_member():
    _app()
    c, ovs = _canvas_three_texts()
    try:
        _click(c, *_widget_center(c, ovs[0]))
        _click(c, *_widget_center(c, ovs[2]), SHIFT)
        before = [(o["x"], o["y"]) for o in ovs]
        x, y = _widget_center(c, ovs[2])
        c.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, x, y))
        c.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, x + 60, y + 30))
        c.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, x + 60, y + 30, NONE, Qt.MouseButton.NoButton))
        d0 = (ovs[0]["x"] - before[0][0], ovs[0]["y"] - before[0][1])
        d2 = (ovs[2]["x"] - before[2][0], ovs[2]["y"] - before[2][1])
        assert abs(d2[0]) > 50 and abs(d2[1]) > 20
        assert d0 == pytest.approx(d2, abs=1e-6)           # 组员同步
        assert (ovs[1]["x"], ovs[1]["y"]) == before[1]      # 组外不动
        assert len(c._multi) == 2                            # 拖完组还在
    finally:
        c.close()


def test_align_and_distribute_selection_with_undo_labels():
    _app()
    c, ovs = _canvas_three_texts()
    try:
        labels = []
        c.gesture_began.connect(lambda: labels.append(c._gesture_label))
        _click(c, *_widget_center(c, ovs[0]))
        _click(c, *_widget_center(c, ovs[1]), SHIFT)
        _click(c, *_widget_center(c, ovs[2]), SHIFT)
        assert c.align_selection("left") is True
        assert {o["x"] for o in ovs} == {60}
        assert c.distribute_selection("v") is True
        ys = sorted(o["y"] for o in ovs)
        assert ys[0] == 200 and ys[-1] == 1300
        assert ys[1] == pytest.approx((200 + 120 + 1300) / 2 - 60)   # 中间那条居中
        assert [l for l in labels if l] == ["左对齐", "垂直等距"]   # 点选的 gesture 没标签
        # 已经对齐的再对一次：没动，不推撤销
        c.item_dragged.connect(lambda: labels.append("dragged"))
        assert c.align_selection("left") is False
        assert "dragged" not in labels
        # 单选相对画布（先重画一次，让命中记录跟上刚才的位移）
        c.repaint()
        _click(c, *_widget_center(c, ovs[0]))
        assert c.align_selection("hcenter") is True
        assert ovs[0]["x"] == pytest.approx(VW / 2 - 200)
    finally:
        c.close()


def test_band_select_picks_intersecting_texts():
    _app()
    c, ovs = _canvas_three_texts()
    try:
        # 从画布空白拖一个框，盖住前两条
        from PySide6.QtCore import QRectF
        r0, _ = c._video_rect_to_widget(QRectF(ovs[0]["x"], ovs[0]["y"], ovs[0]["w"], ovs[0]["h"]))
        r1, _ = c._video_rect_to_widget(QRectF(ovs[1]["x"], ovs[1]["y"], ovs[1]["w"], ovs[1]["h"]))
        x0, y0 = r0.left() - 8, r0.top() - 8
        x1, y1 = r1.right() + 8, r1.bottom() + 8
        c.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, x0, y0))
        assert c._band is not None
        c.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, x1, y1))
        c.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, x1, y1, NONE, Qt.MouseButton.NoButton))
        assert c._band is None
        assert {r["idx"] for r in c._multi} == {0, 1}
        # 太小的框 = 普通点空白，不选
        c.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, 5, 5))
        c.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, 6, 6, NONE, Qt.MouseButton.NoButton))
        assert c._multi == []
    finally:
        c.close()


def test_copy_paste_style_across_selection():
    from cutora.ui import text_style_clipboard as C
    _app()
    c, ovs = _canvas_three_texts()
    try:
        C.set_clipboard(None)
        ovs[0]["fontsize"] = 96
        ovs[0]["font_family"] = "Roboto"
        _click(c, *_widget_center(c, ovs[0]))
        assert c.copy_text_style()["fontsize"] == 96
        _click(c, *_widget_center(c, ovs[1]))
        _click(c, *_widget_center(c, ovs[2]), SHIFT)
        assert c.paste_text_style() == 2
        assert ovs[1]["fontsize"] == 96 and ovs[2]["fontsize"] == 96
        assert ovs[1]["content"] == "T1" and ovs[2]["x"] == 500      # 正文 / 位置不变
        C.set_clipboard(None)
        assert c.paste_text_style() == 0
    finally:
        c.close()


def test_shortcuts_registered_without_collision():
    from cutora.ui.shortcuts import SHORTCUTS
    ids = {r[0]: r for r in SHORTCUTS}
    assert ids["text_style_copy"][1] == "Ctrl+Alt+C"
    assert ids["text_style_paste"][1] == "Ctrl+Alt+V"
    keys = [r[1] for r in SHORTCUTS]
    assert len(keys) == len(set(keys)), "快捷键有重复"


# ─────────────────────────── 样式画廊 ───────────────────────────

def _preset_row(name="金", **fx):
    from cutora.model.factory_defaults import overlay_defaults
    from cutora.subtitle import text_preset as store
    from cutora.subtitle.text_layer import ensure_overlay_fx
    ov = overlay_defaults("text", canvas_w=VW, canvas_h=VH)
    ov["content"] = "预设正文"
    ov["fontsize"] = 96
    ensure_overlay_fx(ov)
    ov["fx"]["fill"] = {"type": "gradient", "colors": ["#FFE066", "#FF8C42"], "angle": 90.0}
    ov["fx"]["anim"] = {"in": "fade"}
    for k, v in fx.items():
        ov["fx"][k] = v
    return store.preset_from_overlay(ov, VW, VH, name=name)


def test_gallery_style_from_preset_keeps_text_and_geometry():
    from cutora.ui.text_style_gallery import apply_preset_style, style_from_preset
    row = _preset_row()
    st = style_from_preset(row)
    assert "content" not in st and "x" not in st and "w" not in st
    assert st["fontsize"] == 96
    assert st["fx"]["fill"]["colors"][0] == "#FFE066"
    assert "anim" not in st["fx"]
    target = _text(content="我的字", x=333, fontsize=20, fx={"fill": {"type": "solid", "colors": ["#00FF00"]},
                                                          "anim": {"in": "pop_in"}})
    assert apply_preset_style(target, row) is True
    assert target["content"] == "我的字" and target["x"] == 333
    assert target["fontsize"] == 96
    assert target["fx"]["fill"]["colors"][0] == "#FFE066"
    assert target["fx"]["anim"] == {"in": "pop_in"}
    assert target["source_preset_id"] == row["id"]
    assert apply_preset_style(target, row) is False


def test_gallery_thumb_renders_styled_pixels_and_caches():
    from cutora.ui import text_style_gallery as G
    _app()
    G.invalidate_thumbs()
    row = _preset_row()
    pm = G.render_preset_thumb(row)
    assert pm.width() == G.THUMB_W and pm.height() == G.THUMB_H
    img = pm.toImage()
    # 有暖色（金色渐变）像素，说明真的按预设画了
    warm = 0
    for y in range(0, img.height(), 4):
        for x in range(0, img.width(), 4):
            c = img.pixelColor(x, y)
            if c.red() > 180 and c.blue() < 120:
                warm += 1
    assert warm > 10, warm
    assert G.render_preset_thumb(row) is pm      # 缓存命中
    G.invalidate_thumbs()
    assert G.render_preset_thumb(row) is not pm


def test_gallery_widget_lists_presets_and_emits_pick(tmp_path, monkeypatch):
    from cutora.core.paths import refresh_cached_paths
    from cutora.subtitle import text_preset as store
    from cutora.ui.text_style_gallery import TextStyleGallery
    _app()
    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path))
    refresh_cached_paths()
    store.reset_runtime_state_for_tests()
    saved = store.save_preset(_preset_row("画廊测试"))
    g = TextStyleGallery()
    try:
        n = g.reload(saved["id"])
        assert n >= 2                                   # 内置 + 刚存的
        names = [g.list.item(i).text() for i in range(g.list.count())]
        assert "画廊测试" in names
        assert g.list.currentItem() is not None
        assert g.list.currentItem().data(Qt.ItemDataRole.UserRole) == saved["id"]
        got = []
        g.style_picked.connect(got.append)
        g._on_item(g.list.currentItem())
        assert got == [saved["id"]]
    finally:
        g.close()
        store.reset_runtime_state_for_tests()

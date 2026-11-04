# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Comprehensive verification suite for 205 parametric borders & 300 adaptive lower-thirds."""
from __future__ import annotations

import os


def _ensure_app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_205_border_styles_and_families():
    _ensure_app()
    from cutora.assets.library import items_for_category
    from cutora.render.border_engine import BORDER_STYLES

    assert len(BORDER_STYLES) >= 205
    families = {st["family"] for st in BORDER_STYLES}
    assert families == {
        "western_classic",
        "eastern_guofeng",
        "floral_botanical",
        "luxury_texture",
        "modern_minimal",
        "cinematic_film",
        "cyber_neon",
        "sacred_ceremonial",
    }
    lib_frames = items_for_category("frame")
    assert len(lib_frames) >= 205


def test_border_edge_modes_length_color_and_auto_border():
    _ensure_app()
    from PySide6.QtGui import QImage, QPainter
    from cutora.render.border_engine import (
        EDGE_MODES,
        create_auto_border_for_canvas,
        create_auto_border_for_overlay,
        default_border_spec,
        render_border,
        render_border_preview,
    )

    img = QImage(640, 360, QImage.Format.Format_ARGB32_Premultiplied)
    for mode_key, _label in EDGE_MODES:
        img.fill(0)
        spec = default_border_spec("bd_west_001")
        spec["edge_mode"] = mode_key
        spec["length_ratio"] = 0.65
        spec["thickness"] = 14
        spec["primary_color"] = "#00F5D4"
        spec["secondary_color"] = "#FF007F"
        spec["color_mode"] = "dual_gradient"
        ov = {"x": 20, "y": 20, "w": 600, "h": 320, "border_spec": spec}
        p = QPainter(img)
        rect = render_border(p, ov, current_time=1.0, video_w=640, video_h=360)
        p.end()
        assert rect.width() == 600 and rect.height() == 320
        assert spec["thickness"] == 14

    target_layer = {"id": "ov_target_1", "name": "产品特写", "x": 120, "y": 180, "w": 420, "h": 300, "start": 1.0, "end": 4.5}
    bd_layer = create_auto_border_for_overlay(target_layer, style_id="bd_east_001")
    assert bd_layer["x"] == 120 and bd_layer["w"] == 420
    assert bd_layer["border_spec"]["target_link"] == "ov_target_1"

    bd_canvas = create_auto_border_for_canvas(style_id="bd_sacr_001", canvas_w=1080, canvas_h=1920)
    assert bd_canvas["w"] == 1080 and bd_canvas["h"] == 1920
    assert bd_canvas["border_spec"]["target_link"] == "canvas"

    prev = render_border_preview("bd_flor_001", 160, 160)
    assert not prev.isNull()


def test_300_lower_thirds_and_100_icon_badges():
    _ensure_app()
    from cutora.assets.library import items_for_category
    from cutora.render.lower_third_engine import LOWER_THIRD_STYLES

    assert len(LOWER_THIRD_STYLES) >= 300
    icon_styles = [st for st in LOWER_THIRD_STYLES if st["family"] == "icon_badge" and st["lower_third_spec"].get("show_icon")]
    assert len(icon_styles) >= 100
    lib_lt = items_for_category("lower_third")
    assert len(lib_lt) >= 300


def test_lower_third_adaptive_multiline_and_paragraph_wrapping():
    _ensure_app()
    from PySide6.QtGui import QImage, QPainter
    from cutora.render.lower_third_engine import (
        default_lower_third_spec,
        layout_lower_third_text,
        render_lower_third,
        render_lower_third_preview,
    )

    w1, h1 = layout_lower_third_text("单行精简标题", "Microsoft YaHei", 360.0, 120.0)
    w2, h2 = layout_lower_third_text("第一行主标题\n第二行副标题说明", "Microsoft YaHei", 360.0, 120.0)
    w4, h4 = layout_lower_third_text("第一行标题\n第二行内容\n第三行重点\n第四行总结补充", "Microsoft YaHei", 360.0, 120.0)
    long_para = "这是一段连续输入的不换行长段落测试文字，字幕条排版引擎会自动根据当前模板的可用列宽进行像素级智能折行排版，并自动纵向延展底板卡片与装饰边线高度，绝不裁切任何文字。"
    wp, hp = layout_lower_third_text(long_para, "Microsoft YaHei", 280.0, 120.0)

    assert len(w1) == 1
    assert len(w2) == 2 and h2 > h1
    assert len(w4) == 4 and h4 > h2
    assert len(wp) >= 3 and hp > h2

    spec = default_lower_third_spec("lt_icon_001")
    ov = {
        "x": 60,
        "y": 1200,
        "w": 420,
        "h": 110,
        "content": long_para,
        "lower_third_spec": spec,
        "start": 0.0,
        "end": 5.0,
    }
    img = QImage(1080, 1920, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    p = QPainter(img)
    rect_in = render_lower_third(p, ov, current_time=0.1, video_w=1080, video_h=1920)
    rect_mid = render_lower_third(p, ov, current_time=2.5, video_w=1080, video_h=1920)
    p.end()
    assert rect_mid.height() > 110
    assert ov["h"] >= int(rect_mid.height())
    assert rect_in.width() == 420

    prev = render_lower_third_preview("lt_graf_001", 240, 140)
    assert not prev.isNull()

# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q124 · 文本框成为真正的框：贴着文字、能垂直对齐、高度跟着文字走。"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from cutora.model.factory_defaults import default_text_box, overlay_defaults


def _app():
    return QApplication.instance() or QApplication(["q124"])


# ------------------------------------------------------------ 默认的框

def test_default_box_is_not_the_whole_canvas():
    """老毛病：新建文本 x=0,y=0,w=画布宽,h=画布高，八个手柄挂在画面四角。"""
    x, y, w, h = default_text_box(1080, 1920)
    assert 0 < x and x + w < 1080
    assert 0 < y and y + h < 1920
    assert w == pytest.approx(1080 * 0.78, abs=1)
    assert abs((x + w / 2) - 540) <= 1, "水平居中"
    assert 1200 < y + h / 2 < 1450, "框中心落在下三分之一"


def test_default_box_never_leaves_the_canvas():
    for cw, ch in ((1080, 1920), (1920, 1080), (720, 720), (200, 100)):
        x, y, w, h = default_text_box(cw, ch, fontsize=200, lines=6)
        assert 0 <= x and x + w <= cw
        assert 0 <= y and y + h <= ch


def test_default_box_estimates_height_from_lines():
    _, _, _, h1 = default_text_box(1080, 1920, fontsize=80, lines=1)
    _, _, _, h3 = default_text_box(1080, 1920, fontsize=80, lines=3)
    assert h3 > h1 * 2.5


def test_overlay_defaults_text_uses_the_box():
    ov = overlay_defaults("text", canvas_w=1080, canvas_h=1920)
    assert (ov["x"], ov["y"], ov["w"], ov["h"]) == default_text_box(1080, 1920)
    assert ov["fit_mode"] == "auto_height"
    assert ov["lock_aspect"] is False
    assert ov["text_layout"]["align"] == "center"
    assert ov["text_layout"]["valign"] == "top"


def test_make_text_overlay_uses_the_box_too():
    """批量加载 / 批量加文本走的是这一条，三个入口必须同一份几何。"""
    from cutora.batch.batch_load import make_text_overlay
    ov = make_text_overlay(None, content="一行字", start=1.0, end=3.0,
                           dims=(1080, 1920))
    assert ov["w"] < 1080 and ov["h"] < 1920 and ov["x"] > 0
    assert ov["fit_mode"] == "auto_height"
    assert ov["lock_aspect"] is False
    assert ov["content"] == "一行字"
    tall = make_text_overlay(None, content="一\n二\n三", dims=(1080, 1920))
    assert tall["h"] > ov["h"]


def test_image_defaults_are_untouched():
    """只动文本。图片的框还是画布，别顺手改了。"""
    ov = overlay_defaults("image", canvas_w=320, canvas_h=180, image_wh=(80, 40))
    assert (ov["x"], ov["y"], ov["w"], ov["h"]) == (0, 0, 320, 180)
    assert ov["lock_aspect"] is True


# ------------------------------------------------------- 画出来的位置

def _paint(ov, w=400, h=400):
    _app()
    from cutora.render.text_layer_paint import paint_text_overlay
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(0, 0, 0, 255))
    p = QPainter(img)
    rect = paint_text_overlay(p, ov, video_w=w, video_h=h)
    p.end()
    return img, rect


def _ink_rows(img):
    rows = []
    for y in range(img.height()):
        for x in range(0, img.width(), 4):
            if QColor(img.pixel(x, y)).value() > 90:
                rows.append(y)
                break
    return rows


def _ov(**kw):
    ov = {
        "type": "text", "content": "Hi", "start": 0.0, "end": 5.0,
        "x": 0, "y": 0, "w": 400, "h": 400,
        "fontsize": 60, "font_family": "Arimo", "bold": True,
        "color": "#FFFFFF", "use_stroke": False, "bg_enabled": False,
        "opacity": 255, "rotation": 0, "blend_mode": "normal",
        "content_format": "plain", "fit_mode": "none",
        "text_layout": {"left_pad": 0, "right_pad": 0, "top_pad": 0,
                        "bottom_pad": 0, "line_spacing": 0,
                        "letter_spacing": 0, "align": "center"},
    }
    ov.update(kw)
    return ov


def test_valign_top_middle_bottom_move_the_text():
    """老 bug：_place_in_box 的 dy 是常量，垂直永远顶对齐。"""
    tops = {}
    for va in ("top", "middle", "bottom"):
        ov = _ov()
        ov["text_layout"]["valign"] = va
        img, _ = _paint(ov)
        rows = _ink_rows(img)
        assert rows, "%s 没画出字" % va
        tops[va] = (rows[0], rows[-1])
    assert tops["top"][0] < tops["middle"][0] < tops["bottom"][0]
    assert tops["top"][0] < 80, tops
    assert tops["bottom"][1] > 320, tops
    mid_c = (tops["middle"][0] + tops["middle"][1]) / 2.0
    assert abs(mid_c - 200) < 40, tops


def test_missing_valign_means_top_like_before():
    """老工程没存 valign → 和以前画得一模一样（顶对齐）。"""
    ov = _ov()
    ov["text_layout"].pop("valign", None)
    img, _ = _paint(ov)
    ov2 = _ov()
    ov2["text_layout"]["valign"] = "top"
    img2, _ = _paint(ov2)
    assert _ink_rows(img)[:3] == _ink_rows(img2)[:3]


# ------------------------------------------------------- 高度跟着文字走

def test_auto_height_follows_the_text_both_ways():
    """Canva 的默认：宽度你定，高度自动。打到三行框长高，删回一行框变矮。"""
    ov = _ov(fit_mode="auto_height", h=10)
    _paint(ov)
    h1 = ov["h"]
    assert 40 < h1 < 160, h1              # 一行 60px 的字，不是 10 也不是 400
    ov["content"] = "Hi\nHi\nHi"
    _paint(ov)
    h3 = ov["h"]
    assert h3 > h1 * 2.2, (h1, h3)
    ov["content"] = "Hi"      # 同一种字，别拿中英文不同的行高来比
    _paint(ov)
    assert abs(ov["h"] - h1) < 12, ("删回一行框要缩回去", h1, ov["h"])


def test_auto_height_returns_the_new_rect():
    ov = _ov(fit_mode="auto_height", h=10)
    _, rect = _paint(ov)
    assert abs(rect.height() - ov["h"]) < 0.01


def test_fixed_box_stays_put():
    """fit_mode=none 的老 overlay：框高一个像素都不许动。"""
    ov = _ov(fit_mode="none", h=333)
    _paint(ov)
    assert ov["h"] == 333


def test_grow_box_still_only_grows():
    ov = _ov(fit_mode="grow_box", h=500)
    _paint(ov)
    assert ov["h"] == 500


def test_measure_text_block_matches_auto_height():
    from cutora.render.text_layer_paint import measure_text_block
    ov = _ov(fit_mode="auto_height", h=10)
    w, h = measure_text_block(ov, 400, 400)
    _paint(ov)
    assert abs(h - ov["h"]) < 0.01
    assert 0 < w <= 400


# --------------------------------------------------- 各个入口都走同一份

def test_dropped_txt_gets_a_real_box(tmp_path):
    from types import SimpleNamespace
    from cutora.model.timeline import Timeline
    from cutora.ui.main_window import MainWindow
    tl = Timeline(1080, 1920, 30.0)
    task = {"timeline": tl}
    f = tmp_path / "w.txt"
    f.write_text("愿你平安", encoding="utf-8")

    class Host:
        def statusBar(self):
            return SimpleNamespace(showMessage=lambda *a, **k: None)

    MainWindow._drop_text_file(Host(), task, tl, str(f), 0.0)
    ov = task["text_overlays"][0]
    assert ov["content"] == "愿你平安"
    assert (ov["x"], ov["y"], ov["w"], ov["h"]) == default_text_box(1080, 1920)
    assert ov["fit_mode"] == "auto_height" and ov["lock_aspect"] is False
    assert ov["_track_id"] is not None

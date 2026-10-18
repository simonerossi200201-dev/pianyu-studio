# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q117 · 画面没铺满时的背景填充：模糊 / 镜像 / 平铺 / 拉伸。"""
from __future__ import annotations

import pytest

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from cutora.render.fill_bg import (
    DEFAULTS, FILL_MODES, blur_image, contain_rect, cover_rect, covers,
    fill_spec, needs_fill, paint_fill, set_fill,
)


def _app():
    return QApplication.instance() or QApplication([])


def _frame(w=64, h=64, top=(220, 40, 40), bottom=(40, 40, 220)):
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    p = QPainter(img)
    p.fillRect(QRectF(0, 0, w, h / 2), QColor(*top))
    p.fillRect(QRectF(0, h / 2, w, h / 2), QColor(*bottom))
    p.end()
    return img


def _canvas(w, h):
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(0, 0, 0, 255))
    return img


# ------------------------------------------------------------------ 设置

def test_defaults_are_off():
    """默认不填充 —— 不能悄悄改掉现有工程的画面。"""
    assert DEFAULTS["mode"] == "none"
    assert fill_spec(None)["mode"] == "none"
    assert fill_spec({})["mode"] == "none"


def test_fill_spec_fills_in_the_gaps():
    spec = fill_spec({"bg_fill": {"mode": "blur"}})
    assert spec["mode"] == "blur"
    assert spec["blur"] == DEFAULTS["blur"]
    assert spec["scale"] == DEFAULTS["scale"]


def test_fill_spec_clamps_and_rejects_junk():
    spec = fill_spec({"bg_fill": {"mode": "螺旋", "blur": 999,
                                  "dim": -3, "scale": 0.0001,
                                  "x": "nope"}})
    assert spec["mode"] == "none"
    assert spec["blur"] == 100.0
    assert spec["dim"] == 0.0
    assert spec["scale"] == pytest.approx(0.05)
    assert spec["x"] == 0.0


def test_set_fill_writes_back_to_the_clip():
    from types import SimpleNamespace
    clip = SimpleNamespace()
    set_fill(clip, mode="blur", blur=70)
    assert clip.bg_fill["mode"] == "blur"
    assert clip.bg_fill["blur"] == 70.0
    set_fill(clip, x=12.0)
    assert clip.bg_fill["mode"] == "blur", "改一项不该把其它项冲掉"
    assert clip.bg_fill["x"] == 12.0
    d = {}
    set_fill(d, mode="mirror")
    assert d["bg_fill"]["mode"] == "mirror"


def test_every_mode_is_reachable():
    for mode in FILL_MODES:
        assert fill_spec({"bg_fill": {"mode": mode}})["mode"] == mode


# ------------------------------------------------------ 铺满了就不画背景

def test_covers_and_needs_fill():
    full = QRectF(0, 0, 1080, 1920)
    assert covers(full, 1080, 1920)
    assert not needs_fill({"mode": "blur"}, full, 1080, 1920)
    short = QRectF(0, 400, 1080, 1120)          # 上下露黑
    assert not covers(short, 1080, 1920)
    assert needs_fill({"mode": "blur"}, short, 1080, 1920)
    assert not needs_fill({"mode": "none"}, short, 1080, 1920), "关着就别画"
    assert not needs_fill({"mode": "blur"}, None, 0, 0)


def test_oversized_foreground_counts_as_covering():
    assert covers(QRectF(-50, -80, 1200, 2100), 1080, 1920)


# ------------------------------------------------------------------ 几何

def test_cover_rect_fills_the_whole_area():
    r = cover_rect(100, 100, 400, 200, scale=1.0, rotation=0.0)
    assert r.width() >= 400 - 1e-6 and r.height() >= 200 - 1e-6
    assert r.center().x() == pytest.approx(0.0)


def test_cover_rect_grows_for_rotation():
    """转过角度还按原尺寸铺就会露角。"""
    flat = cover_rect(100, 100, 400, 200, 1.0, 0.0)
    tilted = cover_rect(100, 100, 400, 200, 1.0, 30.0)
    assert tilted.width() > flat.width()
    assert tilted.height() > flat.height()


def test_contain_rect_keeps_the_aspect():
    r = contain_rect(100, 50, 400, 400)
    assert r.width() == pytest.approx(400.0)
    assert r.height() == pytest.approx(200.0)


# ------------------------------------------------------------------ 模糊

def test_blur_keeps_the_size_and_softens():
    _app()
    img = _frame(64, 64)
    from cutora.render.fill_bg import blur_image_full
    out = blur_image_full(img, 60)          # Q130：blur_image 现在返回小图，铺满交给 painter
    assert (out.width(), out.height()) == (64, 64)
    small = blur_image(img, 60)
    assert small.width() < 64 and small.height() < 64
    # 交界处原来是硬边，模糊之后应该出现中间色
    mid = QColor(out.pixel(32, 32))
    assert 20 < mid.red() < 235, mid.red()
    assert 20 < mid.blue() < 235, mid.blue()


def test_blur_zero_is_a_no_op():
    _app()
    img = _frame(32, 32)
    assert blur_image(img, 0) is img


# ------------------------------------------------------------------ 绘制

def _paint(mode, dest_w=200, dest_h=200, target=None, **kw):
    _app()
    canvas = _canvas(dest_w, dest_h)
    painter = QPainter(canvas)
    spec = dict(DEFAULTS)
    spec["mode"] = mode
    spec["dim"] = 0.0
    spec.update(kw)
    target = target if target is not None else QRectF(0, 60, dest_w, 80)
    drawn = paint_fill(painter, _frame(40, 40), spec, dest_w, dest_h, target)
    painter.end()
    return drawn, canvas


def _corner_is_black(img):
    return QColor(img.pixel(2, 2)).value() < 8


@pytest.mark.parametrize("mode", ["blur", "mirror", "tile", "stretch"])
def test_every_mode_covers_the_black_corners(mode):
    """老毛病：竖屏视频放进横幅里，四角是纯黑。填充之后不许还是黑的。"""
    drawn, canvas = _paint(mode)
    assert drawn is True
    assert not _corner_is_black(canvas), "%s 没盖住左上角" % mode
    for x, y in ((2, 2), (197, 2), (2, 197), (197, 197)):
        assert QColor(canvas.pixel(x, y)).value() >= 8, (mode, x, y)


def test_none_paints_nothing():
    drawn, canvas = _paint("none")
    assert drawn is False
    assert _corner_is_black(canvas)


def test_a_covered_frame_paints_nothing():
    drawn, canvas = _paint("blur", target=QRectF(0, 0, 200, 200))
    assert drawn is False
    assert _corner_is_black(canvas)


def test_dim_darkens_the_background():
    _app()
    canvas = _canvas(200, 200)
    painter = QPainter(canvas)
    spec = dict(DEFAULTS); spec["mode"] = "stretch"; spec["dim"] = 0.0
    paint_fill(painter, _frame(40, 40), spec, 200, 200, QRectF(0, 60, 200, 80))
    bright = QColor(canvas.pixel(4, 4)).value()
    painter.end()

    canvas2 = _canvas(200, 200)
    painter2 = QPainter(canvas2)
    spec2 = dict(spec); spec2["dim"] = 0.6
    paint_fill(painter2, _frame(40, 40), spec2, 200, 200, QRectF(0, 60, 200, 80))
    dark = QColor(canvas2.pixel(4, 4)).value()
    painter2.end()
    assert dark < bright, (dark, bright)


def test_fill_does_not_escape_its_area():
    """分屏时背景只能待在自己那一格里。"""
    _app()
    canvas = _canvas(200, 200)
    painter = QPainter(canvas)
    painter.translate(0, 100)          # 模拟分屏：画笔已经挪到下半格
    spec = dict(DEFAULTS); spec["mode"] = "stretch"; spec["dim"] = 0.0
    paint_fill(painter, _frame(40, 40), spec, 200, 100, QRectF(0, 20, 200, 60))
    painter.end()
    assert _corner_is_black(canvas), "背景漏到上半格去了"
    assert QColor(canvas.pixel(4, 104)).value() >= 8, "下半格没画上"


def test_mirror_seam_matches():
    """镜像的意义就在接缝两边像素是连着的 —— 镜像块贴边那一列要对上。"""
    _app()
    canvas = _canvas(120, 200)
    painter = QPainter(canvas)
    spec = dict(DEFAULTS)
    spec.update({"mode": "mirror", "dim": 0.0, "scale": 1.0})
    frame = _frame(60, 60, top=(240, 0, 0), bottom=(0, 0, 240))
    paint_fill(painter, frame, spec, 120, 200, QRectF(0, 70, 120, 60))
    painter.end()
    base_top = 100 - 60          # 中心块顶边 = 100 - 120/2? 见下
    # 中心块高 120（contain 到 120 宽），纵向中心在 100
    seam = 100 - 60              # 上接缝
    above = QColor(canvas.pixel(60, max(0, seam - 2)))
    below = QColor(canvas.pixel(60, min(199, seam + 2)))
    assert abs(above.red() - below.red()) < 90, (above.red(), below.red())


# ------------------------------------------------------------------ 存盘

def test_bg_fill_survives_a_save():
    from cutora.model.project_io import deserialize_render_task, serialize_render_task
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track
    tl = Timeline(1080, 1920, 30.0)
    tr = Track("video")
    tl.add_track(tr)
    clip = Clip("s1", 0.0, 3.0, 0.0)
    set_fill(clip, mode="blur", blur=72.0, scale=1.4, x=-8.0, rotation=6.0,
             dim=0.3)
    tr.clips.append(clip)
    back = deserialize_render_task(serialize_render_task({"timeline": tl}))
    got = back["timeline"].tracks[0].clips[0]
    spec = fill_spec(got)
    assert spec["mode"] == "blur"
    assert spec["blur"] == pytest.approx(72.0)
    assert spec["scale"] == pytest.approx(1.4)
    assert spec["x"] == pytest.approx(-8.0)
    assert spec["rotation"] == pytest.approx(6.0)
    assert spec["dim"] == pytest.approx(0.3)


def test_clip_defaults_expose_bg_fill():
    from cutora.model.factory_defaults import clip_defaults
    assert clip_defaults()["bg_fill"]["mode"] == "none"

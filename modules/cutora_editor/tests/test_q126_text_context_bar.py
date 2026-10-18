# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q126 · 选中文本就浮出来的工具条：它只是属性面板控件的代理，一份数据。"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(["q126"])


@pytest.fixture()
def rig(qapp):
    from cutora.model.factory_defaults import overlay_defaults
    from cutora.ui.panels.property import PropertyPanel
    from cutora.ui.text_context_bar import TextContextBar
    panel = PropertyPanel()
    host = QWidget()
    host.resize(1000, 300)
    bar = TextContextBar(panel, host)
    panel.data_loaded.connect(bar.show_for)
    panel.param_changed.connect(lambda _p=None: bar.refresh() if bar.isVisible() else None)
    ov = overlay_defaults("text", canvas_w=1080, canvas_h=1920)
    ov["content"] = "Hi"
    host.show()
    qapp.processEvents()
    yield panel, host, bar, ov
    host.close()
    panel.close()


def test_bar_shows_for_text_and_hides_for_the_rest(rig, qapp):
    panel, host, bar, ov = rig
    assert not bar.isVisible()
    panel.load_data("overlay", ov)
    qapp.processEvents()
    assert bar.isVisible()
    panel.load_data("overlay", {"type": "image", "x": 0, "y": 0, "w": 10, "h": 10,
                                "elements": []})
    qapp.processEvents()
    assert not bar.isVisible()
    panel.load_data("overlay", ov)
    qapp.processEvents()
    assert bar.isVisible()
    panel.load_data(None, None)
    qapp.processEvents()
    assert not bar.isVisible()


def test_bar_mirrors_the_panel_on_load(rig, qapp):
    panel, host, bar, ov = rig
    ov["fontsize"] = 96
    ov["bold"] = True
    ov["italic"] = True
    ov["text_layout"]["align"] = "right"
    ov["text_layout"]["valign"] = "bottom"
    ov["text_layout"]["line_spacing"] = 12
    panel.load_data("overlay", ov)
    qapp.processEvents()
    assert bar.sp_size.value() == 96
    assert bar.bt_bold.isChecked() and bar.bt_italic.isChecked()
    assert bar.sg_halign.currentText() == "right"
    assert bar.sg_valign.currentText() == "bottom"
    assert bar.sp_line.value() == 12
    assert bar.cb_font.currentText() == panel.ov_font.currentText()


def test_bar_writes_through_the_panel_only(rig, qapp):
    """改工具条 = 改面板控件 = 走 on_change_ov。overlay 上的值必须跟着变。

    顺带锁住：面板字号框的上限以前是 QSpinBox 默认的 99，120 根本输不进去。
    """
    panel, host, bar, ov = rig
    panel.load_data("overlay", ov)
    qapp.processEvents()
    bar.sp_size.setValue(120)
    qapp.processEvents()
    assert panel.ov_size.value() == 120
    assert ov["fontsize"] == 120
    bar.bt_bold.setChecked(False)
    qapp.processEvents()
    assert panel.ov_bold.isChecked() is False
    assert ov["bold"] is False
    bar.sg_halign.setCurrentText("left")
    bar.sg_valign.setCurrentText("middle")
    qapp.processEvents()
    assert ov["text_layout"]["align"] == "left"
    assert ov["text_layout"]["valign"] == "middle"
    bar.sp_line.setValue(30)
    qapp.processEvents()
    assert ov["text_layout"]["line_spacing"] == 30


def test_bar_colour_goes_into_fill_colors(rig, qapp):
    panel, host, bar, ov = rig
    panel.load_data("overlay", ov)
    qapp.processEvents()
    bar.sw_color.set_hex("#FFCC00", emit=True)
    qapp.processEvents()
    from cutora.subtitle.text_layer import overlay_fill_color
    assert overlay_fill_color(ov).upper() == "#FFCC00"


def test_panel_edits_show_up_on_the_bar(rig, qapp):
    """反过来也一样：面板改了，工具条跟着变。"""
    panel, host, bar, ov = rig
    panel.load_data("overlay", ov)
    qapp.processEvents()
    panel.ov_size.setValue(64)
    qapp.processEvents()
    assert bar.sp_size.value() == 64
    panel.ov_text_valign.setCurrentText("bottom")
    qapp.processEvents()
    assert bar.sg_valign.currentText() == "bottom"


def test_bar_sits_centred_on_top_of_the_host(rig, qapp):
    panel, host, bar, ov = rig
    panel.load_data("overlay", ov)
    qapp.processEvents()
    cx = bar.x() + bar.width() / 2.0
    assert abs(cx - host.width() / 2.0) <= 2
    assert bar.y() <= 12
    host.resize(1400, 300)
    qapp.processEvents()
    cx = bar.x() + bar.width() / 2.0
    assert abs(cx - host.width() / 2.0) <= 2
    # 预览比工具条还窄：贴左边，不许跑到负坐标去
    host.resize(300, 300)
    qapp.processEvents()
    assert bar.x() == 0


def test_attribute_names_do_not_shadow_qwidget(rig):
    """font() / size() 是 QWidget 自己的方法，控件不能叫这个名。"""
    _panel, _host, bar, _ov = rig
    assert callable(bar.font) and callable(bar.size)
    assert bar.font().family() != ""


def test_rich_default_layout_keeps_every_key():
    """老 bug：面板加载时手写的键表只有七个键，valign / 首行缩进 / 段间距

    一进面板就丢，再一保存就把用户设过的值冲成默认。
    """
    from cutora.subtitle.text_utils import _rich_default_layout
    lay = _rich_default_layout({"valign": "middle", "first_indent": 24,
                                "para_spacing": 8, "align": "right"})
    assert lay["valign"] == "middle"
    assert lay["first_indent"] == 24
    assert lay["para_spacing"] == 8
    assert lay["align"] == "right"
    assert _rich_default_layout(None)["valign"] == "top"


def test_valign_survives_a_round_trip_through_the_panel(rig, qapp):
    panel, host, bar, ov = rig
    ov["text_layout"]["valign"] = "middle"
    panel.load_data("overlay", ov)
    qapp.processEvents()
    panel.on_change_ov()          # 面板原样写回
    assert ov["text_layout"]["valign"] == "middle"


def test_bar_folds_spacing_controls_when_the_preview_is_narrow(rig, qapp):
    """竖屏预览那一栏不宽，放不下就先收行距 / 字距，字体框最后再压。"""
    panel, host, bar, ov = rig
    host.resize(1400, 300)
    panel.load_data("overlay", ov)
    qapp.processEvents()
    assert bar._compact == 0 and bar.sp_line.isVisible()
    host.resize(640, 300)
    qapp.processEvents()
    assert bar._compact >= 1
    assert not bar.sp_line.isVisible() and not bar.sp_tracking.isVisible()
    assert bar.width() <= host.width()
    host.resize(1400, 300)
    qapp.processEvents()
    assert bar._compact == 0 and bar.sp_line.isVisible()

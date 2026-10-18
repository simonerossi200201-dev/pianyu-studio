# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q145 文字修复（Luca 2026-09-29 截图）：

1. 预设的字体存成了覆盖全文的 run（「选中文字」工具条设的）：排版按本层字体（Arimo）
   折行，画的时候才换成 Barlow Condensed 55 —— 折行宽度对不上，字伸出框被光栅范围切掉；
   面板字体框显示 Arimo，改一下字号字体就回到默认。
2. 「缩小适配」一触发（比如行距加大放不下），字号变小、折行宽度不变 → 换行位置全变。

修法：覆盖全文的 run 格式提升成本层格式（排版、面板、预设三处）；局部富文本换了字宽
以后按对齐重摆并更新光栅范围；缩小适配改成整块等比缩小，折行不变。"""
from __future__ import annotations

import copy
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

VW, VH = 1080, 1920
TXT = "YOU DIDN'T SEE THIS PRAYER BY ACCIDENT—JESUS KNEW YOU NEEDED IT TODAY"
FAM = "Barlow Condensed"


def _app():
    app = QApplication.instance() or QApplication(["q145c"])
    from cutora.ui.panels.fonts import load_embedded_fonts
    load_embedded_fonts()
    return app


def _need_font():
    _app()
    from PySide6.QtGui import QFontDatabase
    if FAM not in QFontDatabase.families():
        pytest.skip("%s 没注册" % FAM)


def _ov(**kw):
    from cutora.model.factory_defaults import overlay_defaults
    ov = overlay_defaults("text", canvas_w=VW, canvas_h=VH, track_id="t")
    ov.update({"content": TXT, "x": 100.0, "y": 300.0, "w": 880.0, "h": 260.0,
               "font_family": "Arimo", "fontsize": 57, "bold": True,
               "start": 0.0, "end": 5.0})
    ov.update(kw)
    return ov


def _whole_run(end=95):
    return [{"start": 0, "end": end, "overrides": {"font_family": FAM, "fontsize": 55}}]


# ───────────────────────────── 提升规则 ─────────────────────────────

def test_promote_whole_run_to_layer_font_and_fx_body():
    from cutora.subtitle.text_layer import promote_whole_runs
    ov = _ov(runs=_whole_run(), fx={"fonts": {"body": "Arimo", "emphasis": "Arimo"}})
    assert promote_whole_runs(ov)
    assert ov["font_family"] == FAM and ov["fontsize"] == 55
    assert ov["fx"]["fonts"] == {"body": FAM, "emphasis": FAM}
    assert "runs" not in ov


def test_partial_and_mixed_runs_are_kept():
    from cutora.subtitle.text_layer import promote_whole_runs
    partial = [{"start": 0, "end": 5, "overrides": {"font_family": FAM}}]
    ov = _ov(runs=copy.deepcopy(partial))
    assert not promote_whole_runs(ov)
    assert ov["runs"] == partial and ov["font_family"] == "Arimo"
    n = len(TXT)
    mixed = [{"start": 0, "end": 10, "overrides": {"fontsize": 50}},
             {"start": 10, "end": n, "overrides": {"fontsize": 60, "color": "#FF0000"}}]
    ov = _ov(runs=copy.deepcopy(mixed))
    assert not promote_whole_runs(ov)
    # 全文颜色不提升（不影响排版），字体提升后颜色 run 留着
    ov = _ov(runs=[{"start": 0, "end": n, "overrides": {"font_family": FAM, "color": "#FF0000"}}])
    assert promote_whole_runs(ov)
    assert ov["font_family"] == FAM
    assert ov["runs"] == [{"start": 0, "end": n, "overrides": {"color": "#FF0000"}}]


def test_preset_template_promotes_its_own_runs():
    from cutora.subtitle import text_preset as tp
    row = {"id": "p1", "name": "标题一", "text": "When God is leading a woman toward success",
           "style": {"font_family": "Arimo", "fontsize": 57, "runs": _whole_run(),
                     "fx": {"fonts": {"body": "Arimo", "emphasis": "Arimo"}}}}
    tmpl = tp.template_from_preset(row)
    assert tmpl["font_family"] == FAM and tmpl["fontsize"] == 55
    assert not tmpl.get("runs")
    assert tmpl["fx"]["fonts"]["body"] == FAM


# ───────────────────────────── 排版 / 光栅 ─────────────────────────────

def _layout(ov):
    from cutora.render.text_layer_paint import layout_for_overlay
    return layout_for_overlay(copy.deepcopy(ov), VW, VH)


def _line_texts(layout):
    return ["".join(r.text for r in ln.runs).strip() for ln in layout.lines]


def test_whole_run_font_lays_out_like_real_font_and_fits_box():
    _need_font()
    via_run = _ov(runs=_whole_run())
    direct = _ov(font_family=FAM, fontsize=55)
    la, _s, _st, _e, box = _layout(via_run)
    lb = _layout(direct)[0]
    assert _line_texts(la) == _line_texts(lb)
    x, _y, w, _h = box
    for ln in la.lines:
        assert ln.x >= x - 1.0 and ln.x + ln.w <= x + w + 1.0, (ln.x, ln.w, box)
    # 光栅范围盖住所有字
    ox, _oy = la.origin
    for ln in la.lines:
        for r in ln.runs:
            assert r.x >= ox - 0.5 and r.x + r.w <= ox + la.roi_w + 0.5


def test_partial_bigger_run_is_inside_raster_roi():
    _need_font()
    n = len(TXT)
    ov = _ov(runs=[{"start": n - 5, "end": n, "overrides": {"fontsize": 110}}],
             text_layout={"align": "center"})
    la = _layout(ov)[0]
    ox, _oy = la.origin
    right = max(r.x + r.w for ln in la.lines for r in ln.runs)
    left = min(r.x for ln in la.lines for r in ln.runs)
    assert left >= ox - 0.5 and right <= ox + la.roi_w + 0.5
    assert la.text_x <= left + 0.5 and la.text_x + la.text_w >= right - 0.5


def test_shrink_keeps_line_breaks_when_line_spacing_grows():
    _need_font()
    base = _ov(font_family=FAM, fontsize=55, fit_mode="shrink")
    got = []
    for ls in (0, 22, 60, 120):
        ov = copy.deepcopy(base)
        ov["text_layout"] = {"align": "center", "line_spacing": ls}
        la, _s, _st, _e, (x, y, w, h) = _layout(ov)
        got.append(_line_texts(la))
        assert la.text_w <= w + 1.0 and la.text_h <= h + 1.0, (ls, la.text_w, la.text_h)
    assert all(g == got[0] for g in got), got


def test_overlay_without_runs_uses_same_dict():
    from cutora.render.text_layer_paint import _layout_view
    ov = _ov()
    assert _layout_view(ov) is ov


# ───────────────────────────── 属性面板 ─────────────────────────────

def test_panel_shows_real_font_and_size_change_keeps_it():
    _need_font()
    app = _app()
    from cutora.ui.panels.property import PropertyPanel
    ov = _ov(content="YOU DIDN'T SEE THIS PRAYER", runs=_whole_run(),
             fx={"fonts": {"body": "Arimo", "emphasis": "Arimo"}})
    p = PropertyPanel()
    p.load_data("overlay", ov)
    assert p.ov_font.currentText() == FAM
    assert p.ov_size.value() == 55
    p.ov_size.setValue(49)
    app.processEvents()
    assert ov["font_family"] == FAM and ov["fontsize"] == 49
    assert ov["fx"]["fonts"]["body"] == FAM
    assert not ov.get("runs")


def test_rich_bar_select_all_font_becomes_layer_font():
    _need_font()
    app = _app()
    from PySide6.QtGui import QTextCursor
    from cutora.ui.panels.property import PropertyPanel
    ov = _ov(content="HELLO WORLD",
             runs=[{"start": 0, "end": 5, "overrides": {"color": "#FF0000"}}])
    p = PropertyPanel()
    p.load_data("overlay", ov)
    ed = p.ov_rich.edit
    cur = ed.textCursor()
    cur.select(QTextCursor.SelectionType.Document)
    ed.setTextCursor(cur)
    p.ov_rich.bar._set_family(FAM)
    app.processEvents()
    assert ov["font_family"] == FAM
    assert p.ov_font.currentText() == FAM
    assert ov["runs"] == [{"start": 0, "end": 5, "overrides": {"color": "#FF0000"}}]
    p.ov_size.setValue(44)
    app.processEvents()
    assert ov["font_family"] == FAM
    assert ov["fx"]["fonts"]["body"] == FAM and ov["fx"]["fonts"]["emphasis"] == FAM


def test_editor_applies_run_longer_than_text():
    _app()
    from cutora.ui.panels.text_rich import RichTextEditor, document_to_runs
    ed = RichTextEditor()
    ed.set_text_runs("SHORT", [{"start": 0, "end": 95, "overrides": {"color": "#FF0000"}}],
                     {"font_family": "Arimo", "fontsize": 40})
    _text, runs = document_to_runs(ed.edit)
    assert runs and runs[0]["start"] == 0 and runs[0]["end"] == 5
    assert runs[0]["overrides"].get("color") == "#FF0000"

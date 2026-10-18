# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q153 · 批量加载 → 文本分页：「套用模板」一列 + 电子表格手感 + 放大编辑。

- 每一行可以单独指定文本预设；空 = 跟随上方模板。预览、落地都按行走。
- 复制 / 粘贴（一格贴一片、多格贴一块、外部多列按能改的列对上、模板名不认识跳过）。
- 填充柄：向下 / 向上按顺序重复，双击填到底；Ctrl+D 向下填。
- Ctrl+Z / Ctrl+Y 撤销重做；整表刷新后不留历史。
- 放大：同一张表搬进大窗口，收起后放回原处，数据和撤销都还在。
"""
from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent, QKeySequence, QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from cutora.ui.sheet_table import parse_grid, to_tsv  # noqa: E402

SEGS = [
    {"start": 0.0, "end": 2.0, "text": "大家好。"},
    {"start": 5.5, "end": 9.0, "text": "希望你能坚持看完。"},
]


@pytest.fixture()
def presets(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTORA_USER_DATA", str(tmp_path / "ud"))
    from cutora.core.paths import refresh_cached_paths
    refresh_cached_paths()
    from cutora.subtitle import text_preset as TP
    TP.reset_runtime_state_for_tests()
    TP.save_preset({"id": "p_gold", "name": "金色大标题", "text": "标题",
                    "style": {"font_family": "Arimo", "fontsize": 120, "color": "#E6C04A", "bold": True}})
    TP.save_preset({"id": "p_small", "name": "白色小字", "text": "小字",
                    "style": {"font_family": "Arimo", "fontsize": 40, "color": "#FFFFFF"}})
    yield ["金色大标题", "白色小字"]
    TP.reset_runtime_state_for_tests()
    refresh_cached_paths()


def _task(segs=None):
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Track
    tl = Timeline(1080, 1920, 30.0)
    tl.add_track(Track("video"))
    return {"timeline": tl, "segments": list(segs or []), "dims": (1080, 1920)}


def _page(n=4):
    from test_q120_text_batch import _page as page
    return page([_task(SEGS) for _ in range(n)])


def _key(table, key, mods=Qt.KeyboardModifier.NoModifier):
    table.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, key, mods))


def _col(page, c):
    t = page.table
    return [t.cell_value(r, c) for r in range(t.rowCount())]


# ───────────────────────────── 剪贴板格式 ─────────────────────────────

def test_tsv_round_trip_keeps_tabs_newlines_quotes():
    grid = [["一", "两\n行", 'he said "hi"'], ["", "a\tb", "c"]]
    assert parse_grid(to_tsv(grid)) == grid
    assert parse_grid("") == []
    assert parse_grid("甲\t乙\n丙\t丁\n") == [["甲", "乙"], ["丙", "丁"]]


# ───────────────────────────── 套用模板一列 ─────────────────────────────

def test_template_column_exists_after_state(presets):
    from cutora.ui.batch_text_tab import COL_SAID, COL_STATE, COL_TPL, FOLLOW_LABEL
    page, _host = _page(2)
    t = page.table
    # Q190：状态后面先是「视频原话」，再是套用模板
    assert COL_STATE == 2 and COL_SAID == 3 and COL_TPL == 4
    assert t.horizontalHeaderItem(COL_TPL).text() == "套用模板"
    assert t.item(0, COL_TPL).text() == FOLLOW_LABEL and t.cell_value(0, COL_TPL) == ""
    t.apply_values({(0, COL_TPL): "金色大标题"})
    assert t.item(0, COL_TPL).text() == "金色大标题"


def test_delegate_lists_presets_and_commits(presets):
    from cutora.ui.batch_text_tab import COL_TPL, FOLLOW_LABEL
    page, _host = _page(2)
    t = page.table
    t.show()
    d = t.itemDelegateForColumn(COL_TPL)
    idx = t.model().index(1, COL_TPL)
    combo = d.createEditor(t.viewport(), None, idx)
    items = [combo.itemText(i) for i in range(combo.count())]
    assert items[0] == FOLLOW_LABEL and set(presets) <= set(items)
    d.setEditorData(combo, idx)
    combo.setCurrentIndex(combo.findData("白色小字"))
    d.setModelData(combo, t.model(), idx)
    assert t.cell_value(1, COL_TPL) == "白色小字"
    _key(t, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert t.cell_value(1, COL_TPL) == ""
    combo.deleteLater()


def test_per_row_template_goes_into_preview_and_apply(presets):
    from cutora.ui.batch_text_tab import COL_TPL
    page, host = _page(3)
    t = page.table
    t.fill_from(0, ["希望你能坚持看完。"] * 3)
    t.apply_values({(1, COL_TPL): "金色大标题", (2, COL_TPL): "白色小字"})
    plans = page.preview_plans()
    assert [p["tpl"] for p in plans] == ["", "金色大标题", "白色小字"]
    page._preview_row = 1
    ov = page.current_preview()["overlay"]
    assert ov["color"].upper() == "#E6C04A"
    # 和在上方模板里选它得到的一模一样（字号按预设的比例框换算）
    page.cb_preset.setCurrentIndex(page.cb_preset.findData("金色大标题"))
    page._preview_row = 0
    ov0 = page.current_preview()["overlay"]
    assert float(ov0["fontsize"]) == pytest.approx(float(ov["fontsize"]))
    page.cb_preset.setCurrentIndex(0)
    stats = page.apply()
    assert stats["overlays"] == 3
    got = [host.tasks[i]["text_overlays"][0] for i in range(3)]
    assert got[1]["source_preset_id"] == "p_gold"
    assert got[2]["source_preset_id"] == "p_small"
    assert got[2]["color"].upper() == "#FFFFFF" and got[1]["color"].upper() == "#E6C04A"
    assert got[0].get("source_preset_id") != "p_gold"


def test_missing_template_falls_back_and_warns(presets):
    from cutora.subtitle import text_preset as TP
    from cutora.ui.batch_text_tab import COL_STATE, COL_TPL
    page, _host = _page(1)
    t = page.table
    t.fill_from(0, ["希望你能坚持看完。"])
    t.apply_values({(0, COL_TPL): "白色小字"})
    TP.delete_preset("p_small")
    page.refill_presets("")
    plans = page.preview_plans()
    assert plans[0]["tpl"] == ""
    assert "不存在" in t.item(0, COL_STATE).text()


def test_refresh_keeps_template_choices(presets):
    from cutora.ui.batch_text_tab import COL_TPL
    page, _host = _page(2)
    page.table.apply_values({(1, COL_TPL): "金色大标题"})
    page.refresh()
    assert _col(page, COL_TPL) == ["", "金色大标题"]
    assert not page.table.undo()                 # 刷新后不留历史


# ───────────────────────────── 复制 / 粘贴 ─────────────────────────────

def test_copy_one_cell_paste_into_a_range(presets):
    from cutora.ui.batch_text_tab import COL_TPL
    page, _host = _page(5)
    t = page.table
    t.apply_values({(0, COL_TPL): "金色大标题"})
    t.select_box(0, 0, COL_TPL, COL_TPL)
    t.setCurrentCell(0, COL_TPL)
    _key(t, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    t.select_box(2, 4, COL_TPL, COL_TPL)
    _key(t, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    assert _col(page, COL_TPL) == ["金色大标题", "", "金色大标题", "金色大标题", "金色大标题"]
    _key(t, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)      # 一次粘贴一步撤销
    assert _col(page, COL_TPL) == ["金色大标题", "", "", "", ""]
    _key(t, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
    assert _col(page, COL_TPL)[2:] == ["金色大标题"] * 3


def test_external_paste_maps_to_editable_columns_and_validates(presets):
    from cutora.ui.batch_text_tab import COL_DUR, COL_START, COL_TEXT, COL_TPL
    page, _host = _page(3)
    t = page.table
    QApplication.clipboard().setText("第一句\t金色大标题\t1.5\t3\n第二句\t不存在的模板\tabc\t2\n")
    t.select_box(0, 0, COL_TEXT, COL_TEXT)
    t.setCurrentCell(0, COL_TEXT)
    n = t.paste_clipboard()
    assert n == 6
    assert _col(page, COL_TEXT)[:2] == ["第一句", "第二句"]
    assert _col(page, COL_TPL)[:2] == ["金色大标题", ""]
    assert _col(page, COL_START)[:2] == ["1.5", ""]
    assert _col(page, COL_DUR)[:2] == ["3", "2"]
    # 模板名大小写 / 空白宽松匹配
    QApplication.clipboard().setText("  白色小字 \n")
    t.select_box(2, 2, COL_TPL, COL_TPL)
    t.setCurrentCell(2, COL_TPL)
    t.paste_clipboard()
    assert t.cell_value(2, COL_TPL) == "白色小字"


def test_paste_text_column_from_name_column_goes_to_text(presets):
    from cutora.ui.batch_text_tab import COL_NAME, COL_TEXT
    page, _host = _page(2)
    t = page.table
    QApplication.clipboard().setText("甲\n乙\n")
    t.setCurrentCell(0, COL_NAME)
    t.paste_from_clipboard()
    assert _col(page, COL_TEXT) == ["甲", "乙"]


def test_delete_clears_only_editable_cells(presets):
    from cutora.ui.batch_text_tab import COL_NAME, COL_TEXT, COL_TPL
    page, _host = _page(2)
    t = page.table
    t.fill_from(0, ["甲", "乙"])
    t.apply_values({(0, COL_TPL): "金色大标题"})
    names = _col(page, COL_NAME)
    t.select_box(0, 1, COL_NAME, COL_TPL)
    _key(t, Qt.Key.Key_Delete)
    assert _col(page, COL_TEXT) == ["", ""] and _col(page, COL_TPL) == ["", ""]
    assert [t.item(r, COL_NAME).text() for r in range(2)] == [t.item(r, COL_NAME).text() for r in range(2)]
    assert names == _col(page, COL_NAME)
    _key(t, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert _col(page, COL_TEXT) == ["甲", "乙"]


# ───────────────────────────── 填充柄 ─────────────────────────────

def _mouse(t, typ, pt, button=Qt.MouseButton.LeftButton):
    buttons = button if typ != QEvent.Type.MouseButtonRelease else Qt.MouseButton.NoButton
    return QMouseEvent(typ, QPointF(pt), QPointF(pt), button, buttons, Qt.KeyboardModifier.NoModifier)


def test_fill_handle_drag_repeats_pattern_down(presets):
    from cutora.ui.batch_text_tab import COL_TPL
    page, _host = _page(6)
    t = page.table
    t.resize(900, 400)
    t.show()
    QApplication.processEvents()
    t.apply_values({(0, COL_TPL): "金色大标题", (1, COL_TPL): "白色小字"})
    t.select_box(0, 1, COL_TPL, COL_TPL)
    h = t.handle_rect()
    assert h is not None
    start = h.center()
    end = t.visualRect(t.model().index(5, COL_TPL)).center()
    t.mousePressEvent(_mouse(t, QEvent.Type.MouseButtonPress, start))
    t.mouseMoveEvent(_mouse(t, QEvent.Type.MouseMove, end))
    t.mouseReleaseEvent(_mouse(t, QEvent.Type.MouseButtonRelease, end))
    assert _col(page, COL_TPL) == ["金色大标题", "白色小字"] * 3
    assert t.selection_box() == (0, 5, COL_TPL, COL_TPL)
    _key(t, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert _col(page, COL_TPL) == ["金色大标题", "白色小字", "", "", "", ""]


def test_fill_up_double_click_and_ctrl_d(presets):
    from cutora.ui.batch_text_tab import COL_TEXT, COL_TPL
    page, _host = _page(5)
    t = page.table
    t.resize(900, 400)
    t.show()
    QApplication.processEvents()
    t.apply_values({(3, COL_TPL): "白色小字"})
    t.select_box(3, 3, COL_TPL, COL_TPL)
    t.fill_to(1)                                 # 向上
    assert _col(page, COL_TPL) == ["", "白色小字", "白色小字", "白色小字", ""]
    t.select_box(0, 0, COL_TEXT, COL_TEXT)
    t.apply_values({(0, COL_TEXT): "同一句"})
    h = t.handle_rect()
    t.mouseDoubleClickEvent(_mouse(t, QEvent.Type.MouseButtonDblClick, h.center()))
    assert _col(page, COL_TEXT) == ["同一句"] * 5
    t.apply_values({(0, COL_TPL): "金色大标题"})
    t.select_box(0, 2, COL_TPL, COL_TPL)
    _key(t, Qt.Key.Key_D, Qt.KeyboardModifier.ControlModifier)
    assert _col(page, COL_TPL)[:3] == ["金色大标题"] * 3


def test_fill_handle_hidden_for_readonly_selection(presets):
    from cutora.ui.batch_text_tab import COL_NAME, COL_STATE
    page, _host = _page(2)
    t = page.table
    t.show()
    t.select_box(0, 1, COL_NAME, COL_NAME)
    assert t.handle_rect() is None
    t.select_box(0, 1, COL_STATE, COL_STATE)
    assert t.handle_rect() is None


# ───────────────────────────── 放大 ─────────────────────────────

def test_zoom_dialog_moves_table_and_back(presets):
    from PySide6.QtCore import QTimer
    from cutora.ui.batch_text_tab import COL_TPL, TableZoomDialog
    page, _host = _page(3)
    t = page.table
    page.show()
    t.fill_from(0, ["甲", "乙", "丙"])
    from PySide6.QtWidgets import QWidget
    lay = QWidget.layout(page)          # page.layout 被模板比例框占用了
    home = lay.indexOf(t)
    dlg = TableZoomDialog(t, lay, home, parent=page)
    assert t.parent() is not page and t.window() is dlg
    assert dlg.cb_tpl.findData("金色大标题") >= 0
    t.select_box(0, 2, COL_TPL, COL_TPL)
    dlg.cb_tpl.setCurrentIndex(dlg.cb_tpl.findData("金色大标题"))
    dlg.btn_tpl.click()
    assert _col(page, COL_TPL) == ["金色大标题"] * 3
    assert "3 行" in dlg.msg.text()
    dlg.accept()
    assert lay.indexOf(t) == home and t.window() is page.window()
    assert _col(page, COL_TPL) == ["金色大标题"] * 3
    assert t.undo() and _col(page, COL_TPL) == ["", "", ""]
    # 按钮走 exec：用定时器收起
    QTimer.singleShot(50, lambda: page.zoom_dialog.accept() if page.zoom_dialog else None)
    page.zoom()
    assert lay.indexOf(t) == home and page.zoom_dialog is None

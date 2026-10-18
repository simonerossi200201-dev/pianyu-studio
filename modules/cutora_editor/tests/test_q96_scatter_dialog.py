# SPDX-License-Identifier: GPL-3.0-or-later
"""Q96 片段散落 / 视频勾选器：界面层。"""
import os

import pytest

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from cutora.batch import script_broll_scatter as sc
from cutora.batch.script_ui import TaskPicker


@pytest.fixture(scope="module")
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    a = QApplication.instance() or QApplication([])
    yield a


def _touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"0" * 32)
    return path


def _tasks(n):
    return [{"video": "C:/x/v%02d.mp4" % i, "duration": 60.0 + i}
            for i in range(n)]


def test_picker_row_click_toggles(app):
    """整行点一下就切换 —— 不用瞄准那个小方框。"""
    p = TaskPicker(_tasks(4))
    assert p.selected_count() == 4
    p._on_click(p.list.item(1))
    assert p.selected_indices() == [0, 2, 3]
    p._on_click(p.list.item(1))
    assert p.selected_count() == 4


def test_picker_has_no_selection_state(app):
    """没有「选中但没勾」这第二种状态。"""
    p = TaskPicker(_tasks(3))
    from PySide6.QtWidgets import QListWidget
    assert p.list.selectionMode() == QListWidget.SelectionMode.NoSelection
    for i in range(p.list.count()):
        assert not (p.list.item(i).flags() & Qt.ItemFlag.ItemIsUserCheckable)


def test_picker_filter_scopes_buttons(app):
    p = TaskPicker(_tasks(5))
    p.ed_filter.setText("v03")
    assert len(p._visible_items()) == 1
    p._set_all(False)
    # 只取消了筛出来那一行，其余四行原样留着
    assert p.selected_count() == 4
    assert 3 not in p.selected_indices()
    p.ed_filter.setText("")
    assert len(p._visible_items()) == 5


def test_picker_shift_click_range(app):
    p = TaskPicker(_tasks(6))
    p._on_click(p.list.item(1))          # 取消第 2 行
    assert p.selected_count() == 5
    p._last_row = 1
    from unittest import mock
    with mock.patch("PySide6.QtWidgets.QApplication.keyboardModifiers",
                    return_value=Qt.KeyboardModifier.ShiftModifier):
        p._on_click(p.list.item(4))      # 1..4 一起取消
    assert p.selected_indices() == [0, 5]


def test_dialog_accepts_folder_and_files(app, tmp_path, monkeypatch):
    d = tmp_path / "clips"
    a = _touch(str(d / "a.mp4"))
    _touch(str(d / "b.mp4"))
    solo = _touch(str(tmp_path / "solo" / "s.mov"))
    monkeypatch.setattr(sc, "load_last_sources", lambda: [])

    class _Host:
        tasks = _tasks(3)

    dlg = sc.build_dialog(None)
    try:
        dlg.picker = TaskPicker(_Host.tasks)
        assert dlg.entries() == []
        assert not dlg.btn_run.isEnabled()
        dlg.add_paths([str(d)])
        assert dlg.btn_run.isEnabled()
        assert "共 2 个视频" in dlg.lbl_src_sum.text()
        dlg.add_paths([solo])
        assert "共 3 个视频" in dlg.lbl_src_sum.text()
        dlg.add_paths([str(d)])                 # 重复加不翻倍
        assert dlg.lst_src.count() == 2
        assert "共 3 个视频" in dlg.lbl_src_sum.text()
        dlg.clear_sources()
        assert not dlg.btn_run.isEnabled()
        assert a  # 用到
    finally:
        dlg.deleteLater()


def test_dialog_seeds_last_sources(app, tmp_path, monkeypatch):
    d = tmp_path / "clips"
    _touch(str(d / "a.mp4"))
    monkeypatch.setattr(sc, "load_last_sources", lambda: [str(d)])
    dlg = sc.build_dialog(None)
    try:
        assert dlg.entries() == [str(d)]
        assert dlg.btn_run.isEnabled()
    finally:
        dlg.deleteLater()

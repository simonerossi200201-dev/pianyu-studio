# SPDX-License-Identifier: GPL-3.0-or-later
"""Q96：素材右键菜单里，两条共享动作必须在最顶上。"""
import os

import pytest

from PySide6.QtCore import Qt, QPoint
from PySide6.QtWidgets import QApplication, QMenu, QWidget, QVBoxLayout

from cutora.ui.panels import media_list as ml


@pytest.fixture(scope="module")
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    yield QApplication.instance() or QApplication([])


class _Host(QWidget):
    """只提供菜单需要的那两个钩子。"""

    def __init__(self):
        super().__init__()
        self.lst = ml.DraggableListWidget(self)
        lay = QVBoxLayout(self)
        lay.addWidget(self.lst)

    def add_bin_batch_menu_actions(self, menu, source_item):
        a = menu.addAction("共享字幕风格（2 次共享）")
        b = menu.addAction("共享轨道（2 次共享）…")
        a.setObjectName("act_bin_copy_style")
        b.setObjectName("act_bin_share_tracks")
        return a, b


def _open_menu(monkeypatch, host, pos):
    seen = []

    class _Menu(QMenu):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            seen.append(self)

        def exec(self, *a, **k):      # 不要真的弹出来
            return None

    monkeypatch.setattr(ml, "QMenu", _Menu)
    host.lst.show_context_menu(pos)
    assert seen, "没有建出菜单"
    return seen[0]


def test_share_actions_are_first(app, monkeypatch, tmp_path):
    host = _Host()
    from PySide6.QtWidgets import QTreeWidgetItem
    it = QTreeWidgetItem(["T0072-李良-0923-v01.mp4"])
    it.setData(0, Qt.ItemDataRole.UserRole, "clip")
    host.lst.addTopLevelItem(it)
    host.show()
    app.processEvents()
    rect = host.lst.visualItemRect(it)
    menu = _open_menu(monkeypatch, host, rect.center() or QPoint(8, 8))
    acts = [a for a in menu.actions() if not a.isSeparator()]
    assert acts[0].objectName() == "act_bin_copy_style", [
        a.text() for a in acts[:4]]
    assert acts[1].objectName() == "act_bin_share_tracks"
    assert acts[2].text() == "导出选中视频首帧图片"
    # 紧跟着一条分隔线，和下面的常规项分开
    assert menu.actions()[2].isSeparator()
    host.deleteLater()


def test_menu_without_batch_hook_is_unchanged(app, monkeypatch):
    class _Bare(QWidget):
        def __init__(self):
            super().__init__()
            self.lst = ml.DraggableListWidget(self)
            lay = QVBoxLayout(self)
            lay.addWidget(self.lst)

    host = _Bare()
    host.show()
    app.processEvents()
    menu = _open_menu(monkeypatch, host, QPoint(8, 8))
    acts = [a for a in menu.actions() if not a.isSeparator()]
    assert acts[0].text() == "导出选中视频首帧图片"
    host.deleteLater()

# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q192 · 弹窗都在主窗口里（浮层 / 侧滑面板），主窗口任何时候都能拖动；调用方代码不用改。"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

if "PySide6" not in sys.modules:
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")
os.environ.setdefault("CUTORA_GPU_FX", "0")


def _pump(app, ms=60):
    from PySide6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()
    app.processEvents()


@pytest.fixture(scope="module")
def win():
    os.environ["CUTORA_USER_DATA"] = tempfile.mkdtemp(prefix="cutora_q192_")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(["cutora-q192"])
    from cutora.ui.main_window import MainWindow
    w = MainWindow()
    w.resize(1300, 800)
    w.show()
    _pump(app)
    w.show_editor()
    _pump(app)
    w._q192_app = app
    yield w
    from cutora.ui import inwindow
    inwindow.uninstall()


def _later(fn, ms=120):
    from PySide6.QtCore import QTimer
    QTimer.singleShot(ms, fn)


def _top(win):
    host = win._overlay_host
    return host.layers[-1] if host.layers else None


def test_message_box_is_inside_main_window(win):
    from PySide6.QtWidgets import QMessageBox
    seen = {}

    def check():
        layer = _top(win)
        seen["inside"] = layer is not None and not layer.dlg.isWindow()
        seen["modal"] = layer.modal
        seen["gate"] = win.room_host.gate.count
        seen["label"] = layer.dlg.button(QMessageBox.StandardButton.No).text()
        layer.dlg.button(QMessageBox.StandardButton.No).click()
    _later(check)
    r = QMessageBox.question(win, "未保存的修改", "是否保存？",
                             QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    assert r == QMessageBox.StandardButton.No
    assert seen == {"inside": True, "modal": True, "gate": 1, "label": "否"}
    assert win.room_host.gate.count == 0 and not win._overlay_host.layers
    assert not win._overlay_host.isVisible()


def test_warning_and_information_return_ok(win):
    from PySide6.QtWidgets import QMessageBox
    _later(lambda: _top(win).dlg.accept())
    assert QMessageBox.warning(win, "提示", "请先选择视频") == QMessageBox.StandardButton.Ok


def test_input_dialogs(win):
    from PySide6.QtWidgets import QInputDialog
    _later(lambda: (_top(win).dlg.setTextValue("1080x1350"), _top(win).dlg.accept()))
    assert QInputDialog.getText(win, "自定义画布", "尺寸", text="720x1280") == ("1080x1350", True)
    _later(lambda: (_top(win).dlg.setIntValue(5), _top(win).dlg.accept()))
    assert QInputDialog.getInt(win, "并行进程数", "数量", 3, 1, 12, 1) == (5, True)
    _later(lambda: _top(win).dlg.reject())
    assert QInputDialog.getDouble(win, "时长", "秒", 2.5, 0.1, 60.0, 2) == (2.5, False)
    _later(lambda: _top(win).dlg.accept())
    item, ok = QInputDialog.getItem(win, "导入字幕", "方式", ["覆盖", "追加"], 1, False)
    assert ok and item == "追加"


def test_color_dialog(win):
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QColorDialog

    def pick():
        d = _top(win).dlg
        d.setCurrentColor(QColor("#336699"))
        d.accept()
    _later(pick)
    c = QColorDialog.getColor(QColor("#ffffff"), win, "标记颜色")
    assert c.isValid() and c.name() == "#336699"


def test_nested_dialog_stacks_and_custom_qdialog_exec(win):
    from PySide6.QtWidgets import QDialog, QMessageBox, QVBoxLayout, QPushButton
    dlg = QDialog(win)
    dlg.setWindowTitle("设置")
    dlg.setObjectName("SettingsDialog")
    QVBoxLayout(dlg).addWidget(QPushButton("x"))
    seen = {}

    def inner():
        seen["layers"] = len(win._overlay_host.layers)
        seen["gate"] = win.room_host.gate.count
        _top(win).dlg.accept()

    def outer():
        _later(inner, 80)
        QMessageBox.information(dlg, "提示", "里面再弹一个")
        dlg.accept()
    _later(outer)
    assert dlg.exec() == QDialog.DialogCode.Accepted
    assert seen == {"layers": 2, "gate": 2}
    assert not dlg.isVisible() and dlg.parentWidget() is win
    assert win.room_host.gate.count == 0


def test_settings_style_dialogs_slide_in_from_the_right(win):
    from PySide6.QtWidgets import QDialog
    from cutora.ui import inwindow

    class SettingsDialog(QDialog):
        pass
    d = SettingsDialog(win)
    assert inwindow.placement_for(d) == "right"
    seen = {}

    def chk():
        layer = _top(win)
        g = layer.card.geometry()
        seen["right_edge"] = win._overlay_host.width() - g.right()
        seen["tall"] = g.height() >= win._overlay_host.height() - 20
        d.reject()
    _later(chk)
    d.exec()
    assert seen["right_edge"] < 20 and seen["tall"]


def test_file_dialog_and_foreign_windows_stay_native(win):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QDialog, QFileDialog, QWidget
    from cutora.ui import inwindow
    assert inwindow.host_window_for(QFileDialog(win)) is None
    other = QWidget()                     # 比如独立导出监视器
    other.show()
    assert inwindow.host_window_for(QDialog(other)) is None
    top = QDialog(win)
    top.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    assert inwindow.host_window_for(top) is None
    other.close()


def test_modeless_console_does_not_block(win):
    win.open_console()
    _pump(win._q192_app)
    host = win._overlay_host
    layer = host.layers[-1]
    assert not layer.modal and win.room_host.gate.count == 0
    assert not host.mask().isEmpty(), "不阻塞的面板旁边要能点到主界面"
    win._console.reject()
    _pump(win._q192_app)
    assert not host.layers


def test_stitch_and_scatter_are_rooms(win):
    rh = win.room_host
    assert "stitch" in rh.bar.buttons
    assert "scatter" not in rh.bar.buttons
    win.open_script_stitcher()
    _pump(win._q192_app)
    st = rh.widget("stitch")
    assert rh.current == "stitch" and st is not None and not st.isWindow()
    assert st.btn_room_exit.text() == "完成并退出"
    win.open_script_broll_scatter()
    _pump(win._q192_app)
    assert rh.current == "batch"
    batch = rh.widget("batch")
    assert batch is not None and not batch.isWindow()
    assert batch.tabs.currentWidget() is batch.scatter_tab
    assert rh.widget("stitch") is st          # 拼接切走时后台照样跑：房间留着
    st.btn_room_exit.click()
    _pump(win._q192_app)
    assert rh.widget("stitch") is None
    rh.enter("edit")

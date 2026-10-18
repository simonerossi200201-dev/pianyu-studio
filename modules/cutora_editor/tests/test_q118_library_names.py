# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q119 · 重开工程每行还是它自己的视频 + 开机不再问「上次未正常退出」。"""
from __future__ import annotations

import os

import pytest

from cutora.media.import_probe import base_name, library_item_text


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["q118"])


# ------------------------------------------- 重开工程后每一行还是它自己

def _mini_task(path, dur=1.0):
    """够 apply_project_data 用的最小 task：路径 + 一条视频轨。"""
    sid = "s_" + os.path.basename(path)
    return {
        "path": path,
        "video": path,
        "duration": dur,
        "dims": [1080, 1920],
        "segments": [],
        "text_overlays": [],
        "image_overlays": [],
        "audio_clips": [],
        "style": {},
        "timeline": {
            "width": 1080, "height": 1920, "fps": 30.0,
            "sources": {sid: {"id": sid, "path": path, "duration": dur,
                              "has_video": True, "has_audio": False}},
            "tracks": [{
                "id": "tr_" + sid, "type": "video", "is_main_video": True,
                "clips": [{"source_id": sid, "in_t": 0.0, "out_t": dur,
                           "start_t": 0.0}],
            }],
        },
    }


def test_reopen_keeps_every_task_on_its_own_video(qapp, monkeypatch):
    """老 bug：拖进 v01…v22 存盘重开，22 行全叫 v22.mp4。

    apply_project_data 的主循环里根本没有 ``path = ...``，用的是上面那个
    「查缺失文件」循环留下的变量 —— 循环跑完它固定等于最后一个 task 的
    路径，于是 ``task["video"] = path`` 把每一条的视频都写成了最后那一个。
    不止是名字显示错，task 的数据本身就是错的。
    """
    from PySide6.QtWidgets import QMessageBox
    from cutora.ui.main_window import MainWindow
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *a, **k: QMessageBox.StandardButton.No)
    names = ["v%02d.mp4" % i for i in range(1, 7)]
    paths = [r"D:\P\A\视频\%s" % n for n in names]
    win = MainWindow()
    win._suppress_close_prompt = True
    win._trigger_waveform_generation = lambda *a, **k: None
    win._rebuild_preview_wav = lambda *a, **k: None
    win._present_preview_frame = lambda *a, **k: None
    try:
        win.show_editor()
        qapp.processEvents()
        win.apply_project_data({"version": 2,
                                "tasks": [_mini_task(p) for p in paths]})
        qapp.processEvents()
        got = [os.path.basename((t.get("video") or "").replace("\\", "/"))
               for t in win.tasks]
        assert got == names, "每条任务必须还是它自己那个视频"
        rows = [win.file_list.item(i).text(0)
                for i in range(win.file_list.count())]
        assert [r.split()[0] for r in rows] == names
    finally:
        win.close()


def test_rows_read_the_same_before_and_after_a_reopen(qapp, monkeypatch):
    """导入时和重开工程后，这一行的字必须一模一样。

    以前是两套算法：导入走「文件名 时长」，打开工程只给文件名。
    """
    from cutora.ui.main_window import MainWindow
    task = _mini_task(r"D:\P\A\视频\v07.mp4", dur=83.0)
    on_import = MainWindow._library_label_for(None, dict(task, probe_pending=False))
    reopened = MainWindow._library_label_for(None, dict(task))
    assert on_import == reopened == "v07.mp4  1:23"


def test_named_rows_are_left_alone():
    """拼接工程那种自带名字的条目原样显示。"""
    from cutora.ui.main_window import MainWindow
    label = MainWindow._library_label_for(
        None, {"name": "T023", "video": r"D:\x\stitched.mp4", "duration": 8.0})
    assert label == "T023"


# ------------------------------------------------------------ 行上的字

def test_label_is_only_the_file_name():
    """Luca 明确要求：只要文件名，不要文件夹。"""
    txt = library_item_text(r"D:\P\A\视频\v22.mp4", 83.0)
    assert txt == "v22.mp4  1:23"


def test_label_without_a_folder_is_just_the_file():
    assert library_item_text(r"D:\P\solo.mp4", 5.0).startswith("solo.mp4")


def test_label_shows_the_error_instead_of_a_duration():
    txt = library_item_text("x.mp4", None, error="不是视频")
    assert "无法导入" in txt and "读取中" not in txt


def test_old_project_without_a_duration_does_not_say_loading():
    """存盘里没时长、又不在探测中，就别一直挂着「读取中…」。"""
    assert library_item_text("x.mp4", None, pending=False) == "x.mp4"
    assert "读取中" in library_item_text("x.mp4", None, pending=True)


# --------------------------------------------------- 开机不再弹恢复窗口

def test_startup_never_shows_the_restore_dialog(monkeypatch, tmp_path):
    """改之前：启动 500ms 后必弹一次，而且那会儿还没打开任何工程，

    leftover_for(None) 退回去读全局指针，指着上个会话的快照 ——
    跟这次是不是崩了根本没关系。
    """
    import cutora.ui.command_bodies as cb

    class _Boom:
        def __init__(self, *a, **k):
            raise AssertionError("开机不许再弹恢复窗口")

    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox", _Boom, raising=False)
    left = tmp_path / "p.autosave.json"
    left.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("cutora.core.autosave.leftover_for",
                        lambda _p: str(left))

    class Act:
        enabled = None

        def setEnabled(self, v):
            self.enabled = bool(v)

    class Host:
        project_path = None
        act_autosave_restore = Act()

    host = Host()
    cb.CommandBodies._offer_autosave_restore(host)
    assert host._autosave_leftover == str(left)
    assert host.act_autosave_restore.enabled is True, "菜单项该亮起来"


def test_menu_entry_stays_grey_without_a_leftover(monkeypatch):
    import cutora.ui.command_bodies as cb
    monkeypatch.setattr("cutora.core.autosave.leftover_for", lambda _p: None)

    class Act:
        enabled = True

        def setEnabled(self, v):
            self.enabled = bool(v)

    class Host:
        project_path = None
        act_autosave_restore = Act()

    host = Host()
    cb.CommandBodies._offer_autosave_restore(host)
    assert host._autosave_leftover is None
    assert host.act_autosave_restore.enabled is False


def test_clean_exit_drops_the_pointer(tmp_path, monkeypatch):
    """干净退出必须连指针一起清，否则下次开机又被当成崩溃。"""
    from cutora.core import autosave
    ptr = tmp_path / "last_autosave.json"
    ptr.write_text('{"sidecar": "whatever"}', encoding="utf-8")
    monkeypatch.setattr(autosave, "_pointer_path", lambda: str(ptr))
    assert ptr.exists()
    autosave.clear_pointer()
    assert not ptr.exists()
    autosave.clear_pointer()          # 再来一次也不许炸


def test_leftover_falls_back_to_the_pointer(tmp_path, monkeypatch):
    """这就是「每次都问」的来源：没工程路径时它去读指针。"""
    from cutora.core import autosave
    side = tmp_path / "old.autosave.json"
    side.write_text("{}", encoding="utf-8")
    ptr = tmp_path / "last_autosave.json"
    ptr.write_text('{"sidecar": "%s"}' % str(side).replace("\\", "\\\\"),
                   encoding="utf-8")
    monkeypatch.setattr(autosave, "_pointer_path", lambda: str(ptr))
    assert autosave.leftover_for(None) == str(side)
    autosave.clear_pointer()
    assert autosave.leftover_for(None) is None

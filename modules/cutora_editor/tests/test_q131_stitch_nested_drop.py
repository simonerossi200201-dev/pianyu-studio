# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q131 · 拖 / 选一个总目录（B\\T0081-…\\clips\\*.mp4），两种分组方式都要自动往下找素材。

空镜头 / 成品 / _弃用 / _stitched 下的视频不算素材。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["q131"])


def _touch(folder, name):
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name)
    with open(path, "wb") as fh:
        fh.write(b"x")
    return path


def _project_root(tmp_path):
    root = tmp_path / "B"
    for tid, n in (("T0081", 3), ("T0082", 2)):
        proj = root / ("%s-认真-0924-v1" % tid)
        for i in range(1, n + 1):
            _touch(str(proj / "clips"), "%s-s%03d-p%02d.mp4" % (tid, i, i))
        _touch(str(proj / "clips" / "_stitched"), "%s.mp4" % tid)
        _touch(str(proj / "clips" / "_弃用"), "%s-s009-p01.mp4" % tid)
        _touch(str(proj / "空镜头" / "视频"), "broll.mp4")
        _touch(str(proj / "images"), "p01.jpeg")
    _touch(str(root / "成品"), "v01.mp4")
    return root


def _drop(widget, paths):
    from PySide6.QtCore import QMimeData, QPointF, QUrl, Qt
    from PySide6.QtGui import QDropEvent
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
    ev = QDropEvent(QPointF(12, 12), Qt.DropAction.CopyAction, mime,
                    Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    widget.dropEvent(ev)


def _band():
    from cutora.ui.home_stitch import HomeStitchBand
    band = HomeStitchBand()
    band.resize(700, 700)
    band.show()
    return band


def _all_names(band):
    return sorted(os.path.basename(p) for ps in band.groups().values() for p in ps)


def test_find_material_dirs_skips_broll_outputs_and_underscore(tmp_path):
    from cutora.batch.script_stitcher import find_material_dirs
    root = _project_root(tmp_path)
    found = [os.path.relpath(d, str(root)) for d in find_material_dirs(str(root), max_depth=4)]
    assert sorted(found) == [
        os.path.join("T0081-认真-0924-v1", "clips"),
        os.path.join("T0082-认真-0924-v1", "clips"),
    ]


def test_drop_root_folder_mode_finds_clips_and_names_groups_by_project(tmp_path):
    app = _app()
    root = _project_root(tmp_path)
    band = _band()
    band.btn_mode_folder.click()
    _drop(band, [root])
    app.processEvents()
    assert list(band.groups()) == ["T0081-认真-0924-v1", "T0082-认真-0924-v1"]
    assert band.clip_count() == 5
    assert "broll.mp4" not in _all_names(band)
    assert "v01.mp4" not in _all_names(band)
    assert band._base_of["T0081-认真-0924-v1"] == "T0081-认真-0924-v1"
    assert os.path.basename(band._folder_of["T0081-认真-0924-v1"]) == "clips"


def test_choose_root_prefix_mode_finds_clips(tmp_path, monkeypatch):
    """Luca 实测：选「按文件名前缀」+ 选 D:\\…\\B —— 以前一点反应都没有。"""
    app = _app()
    root = _project_root(tmp_path)
    band = _band()
    band.btn_mode_prefix.click()
    from PySide6.QtWidgets import QFileDialog
    monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *a, **k: str(root)))
    band._choose_folder()
    app.processEvents()
    assert band.group_count() == 2, (band.groups(), band._notes)
    assert band.clip_count() == 5
    assert "broll.mp4" not in _all_names(band)


def test_drop_video_files_and_folder_with_videos_still_work(tmp_path):
    app = _app()
    flat = tmp_path / "组甲"
    _touch(str(flat), "01a.mp4")
    _touch(str(flat), "01b.mp4")
    band = _band()
    band.btn_mode_folder.click()
    _drop(band, [flat])
    app.processEvents()
    assert list(band.groups()) == ["组甲"]
    loose = _touch(str(tmp_path / "散"), "x.mp4")
    _drop(band, [loose])
    app.processEvents()
    assert band.clip_count() == 3


def test_redrop_same_folder_gives_feedback(tmp_path):
    app = _app()
    root = _project_root(tmp_path)
    band = _band()
    _drop(band, [root])
    app.processEvents()
    _drop(band, [root])
    app.processEvents()
    assert "已经在列表里" in band.lbl_status.text()


def test_property_panel_builds_without_signal_errors():
    """Luca 09:25 日志：建属性面板时「缩放」行 setRange 触发 on_change_ov，
    block_signals 还没定义 → AttributeError。"""
    import sys
    app = _app()
    errs = []
    old = sys.excepthook
    sys.excepthook = lambda *a: errs.append(a[1])
    try:
        from cutora.ui.panels.property import PropertyPanel
        panel = PropertyPanel()
        app.processEvents()
    finally:
        sys.excepthook = old
    assert errs == [], errs
    assert panel.block_signals is False

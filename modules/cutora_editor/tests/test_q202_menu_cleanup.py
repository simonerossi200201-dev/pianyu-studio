# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q202: remove Audio menu and duplicate batch volume/BGM entries."""
from __future__ import annotations

import inspect
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

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["cutora-q202"])


def _pump(app, ms=80):
    from PySide6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()
    app.processEvents()


def _mw_src():
    return open(
        os.path.join(REPO, "cutora", "ui", "main_window.py"), encoding="utf-8"
    ).read()


def test_source_audio_menu_and_duplicates_gone():
    src = _mw_src()
    assert 'addMenu(tr("音频"))' not in src
    assert "audio_fx_menu" not in src
    assert "open_volume_master_dialog" not in src
    assert "btnAddAudio" not in src
    assert "_on_toggle_preview_fx" not in src
    chunk_start = src.find('batch_menu = menu.addMenu(tr("批量"))')
    assert chunk_start != -1
    chunk = src[chunk_start:chunk_start + 4000]
    assert "片段散落…" in chunk
    assert "批量配乐…" not in chunk
    assert "音量总控…" not in chunk
    assert "open_script_broll_scatter" in chunk


def test_command_apis_and_audio_fx_dialog_kept():
    from cutora.core.commands import get_registry
    from cutora.ui.inwindow import RIGHT_CLASSES
    from cutora.ui.panels.audio_fx import AudioFxDialog
    from cutora.ui.batch_audio_tab import BatchAudioTab
    from cutora.ui.main_window import MainWindow

    names = {s.name for s in get_registry().list()}
    for cmd in (
        "audio.volume_master",
        "batch.add_bgm",
        "batch.bgm_assign",
        "audio.spatial.set",
    ):
        assert cmd in names, cmd
    assert AudioFxDialog.__name__ == "AudioFxDialog"
    assert "AudioFxDialog" in RIGHT_CLASSES
    more = inspect.getsource(BatchAudioTab.open_more)
    assert "AudioFxDialog" in more or "open_audio_fx_dialog" in more
    assert hasattr(MainWindow, "open_audio_fx_dialog")
    assert not hasattr(MainWindow, "open_volume_master_dialog")
    assert hasattr(MainWindow, "add_audio_bgm")
    assert hasattr(MainWindow, "batch_assign_bgm")


def test_mixer_keeps_sfx_preview_and_add_bgm():
    from PySide6.QtWidgets import QComboBox
    from cutora.ui.audio_mixer_bar import AudioMixerBar

    _app()
    bar = AudioMixerBar()
    try:
        assert bar.findChildren(QComboBox) == []
        menu = bar.menu_btn.menu()
        assert menu is not None
        titles = [a.text() for a in menu.actions() if not a.isSeparator()]
        assert "添加配乐…" in titles
        assert "铺音效…" in titles
        assert "预览时播放音效" in titles
        assert bar.act_sfx_preview.isCheckable()
        assert bar.act_sfx_preview.isChecked()
        seen = []
        bar.sfx_preview_toggled.connect(lambda on: seen.append(bool(on)))
        bar.act_sfx_preview.setChecked(False)
        assert seen == [False]
        bar.set_sfx_preview(True)
        assert bar.act_sfx_preview.isChecked()
        assert seen == [False]
        from cutora.model.timeline import Timeline
        from cutora.model.timeline_model import Clip, Track
        tl = Timeline(1080, 1920, 30)
        track = Track("audio")
        track.audio_role = "main"
        cl = Clip("src", 0.0, 2.0, 0.0)
        cl.audio_role = "main"
        cl.is_main = True
        track.clips.append(cl)
        tl.add_track(track)
        bar.set_from_task({"timeline": tl})
        assert "原声" in (bar.faders["main"].slider.toolTip() or "")
    finally:
        bar.close()


@pytest.fixture(scope="module")
def win():
    os.environ["CUTORA_USER_DATA"] = tempfile.mkdtemp(prefix="cutora_q202_")
    app = _app()
    from cutora.ui.main_window import MainWindow

    w = MainWindow()
    w.show()
    _pump(app)
    w.show_editor()
    _pump(app)
    w._q202_app = app
    yield w
    try:
        w.room_host.enter("edit")
    except Exception:
        pass


def test_live_menus_no_audio_or_volume_master(win):
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QPushButton

    bar_titles = [(a.text() or "").strip() for a in win.menuBar().actions()]
    assert "音频" not in bar_titles
    assert "批量" in bar_titles
    batch = None
    for act in win.menuBar().actions():
        if act.text() == "批量":
            batch = act.menu()
            break
    assert batch is not None
    titles = ["---" if a.isSeparator() else a.text() for a in batch.actions()]
    assert "片段散落…" in titles
    assert "批量配乐…" not in titles
    assert "音量总控…" not in titles
    have = {(a.text() or "").strip() for a in win.findChildren(QAction)}
    assert "添加配乐…" in have
    assert "预览时播放音效" in have
    assert "添加背景音乐" not in have
    assert getattr(win, "btnAddAudio", None) is None
    assert getattr(win, "act_batch_bgm", None) is None
    assert getattr(win, "act_volume_master", None) is None
    assert win.mixer_bar is not None and win.mixer_bar.isVisible()

    win.room_host.enter("batch")
    _pump(win._q202_app, 150)
    dlg = win.room_host.widget("batch")
    tab = dlg.audio_tab
    assert tab is not None
    more = [b for b in tab.findChildren(QPushButton) if b.text() == "更多…"]
    assert more, "batch audio tab must keep 更多… for AudioFxDialog"
    win.room_host.enter("edit")

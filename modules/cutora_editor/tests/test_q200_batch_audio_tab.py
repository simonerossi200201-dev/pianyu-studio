# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q200: transport-row mixer and batch-load Audio tab."""
from __future__ import annotations

import inspect
import os
import sys
import tempfile

import numpy as np
import pytest

if "PySide6" not in sys.modules:
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")
os.environ.setdefault("CUTORA_GPU_FX", "0")


def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["cutora-q200"])


def _pump(app, ms=80):
    from PySide6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()
    app.processEvents()


class _FakeSink:
    def __init__(self):
        self.starts = 0
        self.stops = 0
        self.running = False

    def start(self, *a, **k):
        self.starts += 1
        self.running = True

    def stop(self):
        self.stops += 1
        self.running = False


def test_mixer_bar_has_no_combo_and_fader_width():
    _app()
    from PySide6.QtWidgets import QComboBox
    from cutora.ui.audio_mixer_bar import AudioMixerBar, FADER_MAX_W, FADER_MIN_W

    src = inspect.getsource(AudioMixerBar)
    assert "QComboBox" not in src
    bar = AudioMixerBar()
    assert bar.findChildren(QComboBox) == []
    assert bar.height() <= 40
    for role, fd in bar.faders.items():
        w = fd.maximumWidth()
        assert FADER_MIN_W <= fd.minimumWidth() <= FADER_MAX_W
        assert FADER_MIN_W <= w <= FADER_MAX_W
        assert fd.mute_btn is not None
        assert fd.slider.objectName() == "roleFader_%s" % role
    assert bar.spatial_btn.isCheckable()
    assert bar.menu_btn.menu() is not None
    bar.close()


def test_batch_audio_tab_has_width_mic_import_default():
    from PySide6.QtWidgets import QWidget
    from cutora.ui.batch_audio_tab import BatchAudioTab

    _app()
    src = inspect.getsource(BatchAudioTab)
    assert "PreviewEngine" not in src
    assert "close_room" not in src
    assert "cutora_sheet" not in src
    host = QWidget()
    tab = BatchAudioTab(host, None, None, parent=host)
    assert tab.sp_width is not None
    assert tab.chk_mic is not None
    assert tab.chk_import_default is not None
    assert tab.btn_more is not None
    assert tab.btn_audition is not None
    assert tab.property("cutora_sheet") in (None, "")
    assert not tab.isWindow()
    assert tab.minimumWidth() < 400
    assert tab.minimumHeight() < 500
    tab.close()
    host.close()


def test_audio_tab_does_not_break_rooms():
    from cutora.ui.batch_audio_tab import BatchAudioTab
    from cutora.ui.inwindow import placement_for
    from cutora.ui.panels.audio_fx import AudioFxDialog

    from PySide6.QtWidgets import QWidget

    _app()
    host = QWidget()
    tab = BatchAudioTab(host, None, None, parent=host)
    assert placement_for(tab) != "fill"
    assert AudioFxDialog.__name__ == "AudioFxDialog"
    src = inspect.getsource(BatchAudioTab.open_more)
    assert "AudioFxDialog" in src
    tab.close()
    host.close()


def test_audition_other_picker_row_disables_button():
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QListWidget, QListWidgetItem, QWidget
    from cutora.ui.batch_audio_tab import BatchAudioTab

    _app()

    class Host:
        curr_idx = 0
        tasks = [{}, {}, {}]
        bus = None

        def start_batch_audition(self):
            return True

        def stop_batch_audition(self):
            return None

    class Picker(QWidget):
        def __init__(self):
            super().__init__()
            self._last_row = 0
            self.list = QListWidget()
            for i in range(3):
                it = QListWidgetItem("v%d" % i)
                it.setData(Qt.ItemDataRole.UserRole, i)
                self.list.addItem(it)

        def selected_indices(self):
            return [self._last_row]

    host = Host()
    picker = Picker()
    tab = BatchAudioTab(None, host, picker)
    tab._sync_audition_btn()
    assert tab.btn_audition.isEnabled()
    picker._last_row = 2
    tab._sync_audition_btn()
    assert not tab.btn_audition.isEnabled()
    tab.close()


def test_batch_audition_one_sink_media_player_paused():
    from types import SimpleNamespace
    from cutora.audio.stem_cache import reset_stem_mixer
    from cutora.ui.main_window import MainWindow

    mixer = reset_stem_mixer()
    mixer.cache.stems["main"] = np.ones((480, 2), dtype=np.float32)
    sink = _FakeSink()

    class Host:
        _sync_stem_sink = MainWindow._sync_stem_sink
        _stop_stem_sink = MainWindow._stop_stem_sink
        start_batch_audition = MainWindow.start_batch_audition
        stop_batch_audition = MainWindow.stop_batch_audition
        _stem_clock_ms = MainWindow._stem_clock_ms

    host = Host()
    host._timeline_playing = False
    host._jkl_rate = 0
    host._stem_mixer = mixer
    host._stem_sink = sink
    host._stem_pull_io = object()
    host._stem_sink_running = False
    host._batch_audition = False
    host._audition_wall0 = None
    host._audition_origin_ms = 0.0
    host._timeline_ms = 0
    host.curr_idx = 0
    host.tasks = [{}]
    host._muted = []
    host.audio_output = SimpleNamespace(
        setMuted=lambda on, h=host: h._muted.append(bool(on)))
    plays = []

    class MP:
        def play(self):
            plays.append("play")

        def pause(self):
            plays.append("pause")

        def playbackState(self):
            return 0

    host.media_player = MP()
    pe_calls = []

    def _no_engine(*_a, **_k):
        pe_calls.append(1)
        raise AssertionError("PreviewEngine must not run during batch audition")

    host._preview = SimpleNamespace(frame_at=_no_engine)
    src = inspect.getsource(MainWindow.start_batch_audition)
    assert "media_player.play" not in src
    assert "frame_at" not in src
    assert "PreviewEngine(" not in src
    ok = MainWindow.start_batch_audition(host)
    assert ok is True
    assert sink.starts == 1
    assert host._stem_sink_running is True
    assert plays == []
    assert pe_calls == []
    MainWindow.stop_batch_audition(host)
    assert sink.stops >= 1
    assert host._batch_audition is False


@pytest.fixture(scope="module")
def win():
    os.environ["CUTORA_USER_DATA"] = tempfile.mkdtemp(prefix="cutora_q200_")
    app = _app()
    from cutora.ui.main_window import MainWindow
    from cutora.model.timeline import Timeline

    w = MainWindow()
    w.resize(1366, 728)
    w.show()
    _pump(app)
    w.show_editor()
    _pump(app)
    w.tasks = [{
        "video": "/nonexistent/v%d.mp4" % i,
        "name": "v%d.mp4" % i,
        "segments": [],
        "timeline": Timeline(1080, 1920, 30.0),
        "text_overlays": [],
        "image_overlays": [],
        "dims": [1080, 1920],
        "style": {},
        "duration": 12.0,
        "text": "",
    } for i in range(3)]
    w.curr_idx = 0
    w._q200_app = app
    yield w
    try:
        w.room_host.enter("edit")
    except Exception:
        pass


def test_mixer_replaces_btn_add_audio(win):
    from PySide6.QtWidgets import QComboBox

    bar = getattr(win, "mixer_bar", None)
    assert bar is not None
    assert bar.objectName() == "audioMixerBar"
    assert bar.isVisible()
    btn = getattr(win, "btnAddAudio", None)
    assert btn is None or not btn.isVisible()
    assert bar.findChildren(QComboBox) == []
    assert win.preview_tools.height() == 40
    assert bar.height() <= 40


def test_q193_edit_page_with_strip_does_not_grow(win):
    h0 = win.height()
    hint = win.minimumSizeHint().height()
    assert win.preview_tools.height() == 40
    assert hint <= 728
    assert win.height() == h0
    win.room_host.enter("edit")
    _pump(win._q200_app, 80)
    assert win.height() == h0
    assert win.minimumSizeHint().height() <= 728


def test_1366_play_and_timecode_geometry_visible(win):
    from PySide6.QtCore import QRect

    win.resize(1366, 728)
    win.show_editor()
    _pump(win._q200_app, 120)
    assert win.preview_tools.height() == 40

    def mapped(widget):
        r = widget.rect()
        tl = widget.mapTo(win, r.topLeft())
        return QRect(tl, r.size())

    play = mapped(win.btn_start)
    tc = mapped(win.lbl_timecode)
    wr = win.rect()
    assert play.width() > 0 and play.height() > 0
    assert wr.contains(play)
    assert win.lbl_timecode.isVisible()
    assert tc.width() > 0 and tc.height() > 0
    assert wr.contains(tc)
    for role, fd in win.mixer_bar.faders.items():
        assert 72 <= fd.width() <= 90 or fd.minimumWidth() >= 72


def test_batch_tabs_include_audio(win):
    win.room_host.enter("batch")
    _pump(win._q200_app, 150)
    dlg = win.room_host.widget("batch")
    names = [dlg.tabs.tabText(i) for i in range(dlg.tabs.count())]
    assert "音频" in names
    tab = dlg.audio_tab
    assert tab is not None
    assert tab.sp_width is not None
    assert tab.chk_mic is not None
    assert tab.chk_import_default is not None
    dlg.tabs.setCurrentWidget(tab)
    _pump(win._q200_app, 80)
    tab.picker._last_row = 1
    win.curr_idx = 0
    tab._sync_audition_btn()
    assert not tab.btn_audition.isEnabled()
    tab.picker._last_row = 0
    tab._sync_audition_btn()
    assert tab.btn_audition.isEnabled()
    win.room_host.enter("edit")

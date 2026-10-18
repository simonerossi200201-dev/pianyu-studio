# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q204b: fade handles inside trim; apply_new_bgm_defaults."""
from __future__ import annotations

import hashlib
import inspect
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

if "PySide6" not in sys.modules:
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WIND = os.path.join(REPO, "resources", "audio", "sfx", "wind_soft.wav")


def _pair(dur=4.0):
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track

    tl = Timeline(1080, 1920, 30)
    vtr = Track("video")
    vtr.id = "v0"
    atr = Track("audio")
    atr.id = "a0"
    vc = Clip("src", 0.0, dur, 0.0)
    ac = Clip("src", 0.0, dur, 0.0)
    ac.is_main = True
    ac.audio_role = "main"
    vtr.clips.append(vc)
    atr.clips.append(ac)
    tl.add_track(vtr)
    tl.add_track(atr)
    return tl, vtr, atr, vc, ac


class _DummyTimeline:
    def update(self):
        pass


from cutora.ui.command_bodies import CommandBodies  # noqa: E402


class _Win(CommandBodies):
    def __init__(self, tl):
        self.tasks = [{"timeline": tl, "duration": 8.0}]
        self.curr_idx = 0
        self.timeline = _DummyTimeline()
        self.project_path = "mem"


def _mouse(w, kind, x, y):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    pos = QPointF(float(x), float(y))
    typ = {
        "press": QEvent.Type.MouseButtonPress,
        "move": QEvent.Type.MouseMove,
        "release": QEvent.Type.MouseButtonRelease,
    }[kind]
    btn = Qt.MouseButton.LeftButton
    pressed = Qt.MouseButton.NoButton if kind == "release" else btn
    caused = Qt.MouseButton.NoButton if kind == "move" else btn
    ev = QMouseEvent(typ, pos, pos, caused, pressed, Qt.KeyboardModifier.NoModifier)
    if kind == "press":
        w.mousePressEvent(ev)
    elif kind == "move":
        w.mouseMoveEvent(ev)
    else:
        w.mouseReleaseEvent(ev)


def test_set_clip_fades_command_writes_fields():
    tl, vtr, atr, vc, ac = _pair()
    win = _Win(tl)
    assert float(ac.fade_in_dur) == 0.0
    out = win.command_set_clip_fades(
        track_id="a0", clip_index=0, fade_in_dur=0.35, fade_out_dur=0.40)
    assert ac.fade_in_dur == pytest.approx(0.35)
    assert ac.fade_out_dur == pytest.approx(0.40)
    assert out["fade_in_dur"] == pytest.approx(0.35)


def test_command_registered():
    from cutora.core.commands import get_registry
    names = {s.name for s in get_registry().list()}
    assert "timeline.set_clip_fades" in names


def test_fade_hit_inside_trim_and_short_clip_skips():
    from PySide6.QtWidgets import QApplication
    from cutora.ui.timeline.widget import TimelineWidget

    if QApplication.instance() is None:
        QApplication([])
    w = TimelineWidget()
    half = w.HANDLE_PX / 2.0
    long_sw = w.HANDLE_PX * 2 + 16
    assert w._audio_fade_hit(0.0, long_sw, half + 1.0) == "in"
    assert w._audio_fade_hit(0.0, long_sw, long_sw - half - 1.0) == "out"
    assert w._audio_fade_hit(0.0, long_sw, half / 2.0) is None
    assert w._audio_fade_handles_ok(long_sw - 1) is False
    assert w._audio_fade_hit(0.0, long_sw - 1, half + 1.0) is None
    w.close()


def _qt_env(dur=4.0):
    from PySide6.QtWidgets import QApplication, QWidget
    from cutora.core.commands import get_bus, reset_bus_for_tests
    from cutora.core.history import HistoryManager
    from cutora.ui.panels.property import PropertyPanel
    from cutora.ui.timeline.widget import TimelineWidget

    if QApplication.instance() is None:
        QApplication([])
    tl, vtr, atr, vc, ac = _pair(dur=dur)
    task = {
        "timeline": tl, "duration": 8.0,
        "text_overlays": [], "image_overlays": [], "segments": [],
        "video": "x.mp4", "style": {},
    }

    class _GuiWin(CommandBodies, QWidget):
        def __init__(self):
            QWidget.__init__(self)
            self.tasks = [task]
            self.curr_idx = 0
            self.project_path = "mem"
            self.history_mgr = HistoryManager()
            self.timeline = None

        def push_undo(self, action_name="操作", command=None, params=None, source=None):
            self.history_mgr.push(action_name, {"tasks": self.tasks, "curr_idx": 0})

        def capture_undo_after(self):
            self.history_mgr.set_after({"tasks": self.tasks, "curr_idx": 0})

    reset_bus_for_tests()
    win = _GuiWin()
    get_bus().bind_window(win)
    tlw = TimelineWidget()
    tlw.set_data(8000, task)
    tlw.setParent(win)
    win.timeline = tlw
    panel = PropertyPanel()
    panel.setParent(win)
    win.prop_panel = panel
    win.show()
    tlw.resize(900, 300)
    tlw.pixels_per_second = 20.0
    tlw.scroll_x = 0
    QApplication.processEvents()
    return win, tlw, panel, tl, vtr, atr, vc, ac


def test_handle_writes_fade_in_dur():
    from PySide6.QtWidgets import QApplication

    win, tlw, panel, tl, vtr, atr, vc, ac = _qt_env(dur=4.0)
    try:
        tlw._select_audio_index(atr, ac, 0)
        rows = tlw._track_positions().get("rows", [])
        audio_row = None
        for t_obj, row_y, row_h in rows:
            if t_obj is atr:
                audio_row = (row_y, row_h)
                break
        assert audio_row is not None
        row_y, row_h = audio_row
        pps = float(tlw.pixels_per_second)
        sx = 0.0
        sw = 4.0 * pps
        assert tlw._audio_fade_handles_ok(sw)
        x0 = sx + tlw.HANDLE_PX / 2.0 + 4.0
        y = row_y + row_h / 2.0
        _mouse(tlw, "press", x0, y)
        assert tlw.mode == "fade_audio_in"
        x1 = sx + tlw.HANDLE_PX / 2.0 + 1.2 * pps
        _mouse(tlw, "move", x1, y)
        _mouse(tlw, "release", x1, y)
        QApplication.processEvents()
        assert ac.fade_in_dur == pytest.approx(1.2, abs=0.05)
        assert ac.fade_out_dur == pytest.approx(0.0)
    finally:
        win.close()
        QApplication.processEvents()


def test_short_clip_no_fade_hit():
    from PySide6.QtWidgets import QApplication

    win, tlw, panel, tl, vtr, atr, vc, ac = _qt_env(dur=1.0)
    try:
        tlw._select_audio_index(atr, ac, 0)
        rows = tlw._track_positions().get("rows", [])
        audio_row = None
        for t_obj, row_y, row_h in rows:
            if t_obj is atr:
                audio_row = (row_y, row_h)
                break
        assert audio_row is not None
        row_y, row_h = audio_row
        pps = float(tlw.pixels_per_second)
        sw = 1.0 * pps
        assert not tlw._audio_fade_handles_ok(sw)
        x0 = tlw.HANDLE_PX / 2.0 + 4.0
        y = row_y + row_h / 2.0
        _mouse(tlw, "press", x0, y)
        assert tlw.mode != "fade_audio_in"
        assert tlw.mode != "fade_audio_out"
        _mouse(tlw, "release", x0, y)
        assert ac.fade_in_dur == pytest.approx(0.0)
    finally:
        win.close()
        QApplication.processEvents()


def test_inspector_fade_slider_goes_through_command():
    from PySide6.QtWidgets import QApplication
    from cutora.core.commands import get_bus

    win, tlw, panel, tl, vtr, atr, vc, ac = _qt_env(dur=4.0)
    try:
        panel.load_data("audio", ac)
        calls = []
        bus = get_bus()
        orig = bus.execute

        def wrapped(name, params=None, source="ui", ctx=None):
            calls.append(name)
            return orig(name, params, source=source, ctx=ctx)

        bus.execute = wrapped
        panel.audio_fade_in_dur.setValue(0.55)
        QApplication.processEvents()
        assert "timeline.set_clip_fades" in calls
        assert ac.fade_in_dur == pytest.approx(0.55)
    finally:
        win.close()
        QApplication.processEvents()


def _make_wav(path, freq, dur=1.0):
    from cutora.core.config import FFMPEG_PATH
    cmd = [
        FFMPEG_PATH, "-y", "-v", "error",
        "-f", "lavfi",
        "-i", "sine=frequency=%s:duration=%s:sample_rate=48000" % (freq, dur),
        "-ac", "2", "-c:a", "pcm_s16le", path,
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=30)
    assert r.returncode == 0 and os.path.isfile(path), (r.stderr or b"").decode(
        "utf-8", "replace")[-300:]


def _audio_task(wav, fade_in=0.0, fade_out=0.0):
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, MediaSource, Track

    tl = Timeline(1080, 1920, 30)
    src = MediaSource.__new__(MediaSource)
    src.id = "voice"
    src.path = wav
    src.duration = 1.0
    src.has_video = False
    src.has_audio = True
    tl.sources[src.id] = src
    atr = Track("audio")
    atr.id = "a0"
    atr.audio_role = "main"
    clip = Clip(src.id, 0.0, 1.0, 0.0)
    clip.is_main = True
    clip.audio_role = "main"
    clip.spatial_enabled = False
    clip.fade_in_dur = float(fade_in)
    clip.fade_out_dur = float(fade_out)
    atr.clips.append(clip)
    tl.add_track(atr)
    return {
        "timeline": tl,
        "video": wav,
        "src_volume": 1.0,
        "audio_fx": {"spatial_enabled": False, "spatial_wet": 0.0},
    }, clip


def _pcm_md5(task, dest):
    from cutora.core.config import FFMPEG_PATH
    from cutora.render.render_worker import RenderWorker

    rw = RenderWorker.__new__(RenderWorker)
    rw.export_audio_rate = 48000
    fc, tracks = rw.export_audio_filter_complex(task, has_video=False)
    assert tracks and fc, (fc, tracks)
    cmd = [FFMPEG_PATH, "-y", "-v", "error"]
    for t in tracks:
        cmd += ["-i", t["path"]]
    cmd += [
        "-filter_complex", fc, "-map", "[aout]",
        "-t", "1", "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", dest,
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=60)
    assert r.returncode == 0 and os.path.isfile(dest), (r.stderr or b"").decode(
        "utf-8", "replace")[-400:]
    return hashlib.md5(open(dest, "rb").read()).hexdigest(), fc


def test_export_afade_contains_value_and_fade0_pcm_same(tmp_path):
    wav = WIND if os.path.isfile(WIND) else str(tmp_path / "tone.wav")
    if wav != WIND:
        _make_wav(wav, 440)
    task0, clip0 = _audio_task(wav, 0.0, 0.0)
    md5_a, fc0 = _pcm_md5(task0, str(tmp_path / "a.wav"))
    assert "afade=" not in fc0
    task0b, clip0b = _audio_task(wav, 0.0, 0.0)
    md5_b, fc0b = _pcm_md5(task0b, str(tmp_path / "b.wav"))
    assert md5_a == md5_b
    task1, clip1 = _audio_task(wav, 0.30, 0.30)
    md5_c, fc1 = _pcm_md5(task1, str(tmp_path / "c.wav"))
    assert "afade=t=in" in fc1
    assert "d=0.3" in fc1 or "d=0.30" in fc1
    assert md5_c != md5_a


def test_apply_new_bgm_defaults_and_three_entries():
    from cutora.audio.sfx_library import (
        NEW_BGM_FADE_SEC, NEW_BGM_LINEAR_VOL, apply_new_bgm_defaults,
    )
    from cutora.batch import cli as batch_cli
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track
    from cutora.ui.main_window import MainWindow

    clip = Clip("m", 0.0, 4.0, 0.0)
    apply_new_bgm_defaults(clip)
    assert clip.audio_role == "bgm"
    assert clip.loop is True
    assert clip.spatial_enabled is False
    assert clip.is_main is False
    assert clip.fade_in_dur == pytest.approx(NEW_BGM_FADE_SEC)
    assert clip.fade_out_dur == pytest.approx(NEW_BGM_FADE_SEC)
    expected_gain = 20.0 * __import__("math").log10(NEW_BGM_LINEAR_VOL)
    assert clip.gain_db == pytest.approx(expected_gain, abs=0.01)

    src = inspect.getsource(batch_cli.op_add_bgm)
    assert "apply_new_bgm_defaults" in src
    assert "fade_in = min(0.8" not in src

    wav = WIND if os.path.isfile(WIND) else os.path.join(REPO, "resources", "audio", "sfx", "birds_forest.wav")
    assert os.path.isfile(wav), wav

    tl = Timeline(1080, 1920, 30)
    main_tr = Track("audio")
    main_tr.audio_role = "main"
    main_tr.name = "原声"
    main_tr.id = "a-main"
    tl.add_track(main_tr)
    task = {"timeline": tl, "duration": 4.0, "audio_clips": []}

    class DropHost:
        def statusBar(self):
            return SimpleNamespace(showMessage=lambda *a, **k: None)

        def _pending_drop_duration(self, _task, _start_t):
            return 4.0

        def _note_drop_slot(self, slot):
            return None

        def _queue_drop_probe(self, slot):
            return None

    MainWindow._drop_audio_file(DropHost(), task, tl, wav, 0.0)
    bgm_tr = next(
        tr for tr in tl.tracks
        if getattr(tr, "audio_role", "") == "bgm" or any(
            getattr(c, "audio_role", "") == "bgm" for c in (tr.clips or []))
    )
    dropped = bgm_tr.clips[-1]
    assert dropped.fade_in_dur == pytest.approx(0.30)
    assert dropped.fade_out_dur == pytest.approx(0.30)

    tl2 = Timeline(1080, 1920, 30)
    task2 = {"timeline": tl2, "duration": 4.0, "audio_clips": []}

    class AddHost:
        def _trigger_waveform_generation(self, *a, **k):
            return None

    MainWindow._add_bgm_to_track(AddHost(), task2, wav, 4.0)
    added = None
    for tr in tl2.tracks:
        for c in (tr.clips or []):
            if getattr(c, "audio_role", "") == "bgm":
                added = c
    assert added is not None
    assert added.fade_in_dur == pytest.approx(0.30)
    assert added.fade_out_dur == pytest.approx(0.30)
    assert dropped.fade_in_dur == added.fade_in_dur == clip.fade_in_dur

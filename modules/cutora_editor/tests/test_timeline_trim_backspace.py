# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Backspace deletes any selected item (incl. subtitle pieces after a blade cut);
Shift+[ / Shift+] trim at the playhead WITHOUT ripple (gap stays, total length and
other clips unchanged) for linked video/audio and subtitles; Ctrl+Shift+[ / ] ripple.

Runs on the real MainWindow + command bus; every edit is one undo step."""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")
os.environ.setdefault("CUTORA_USER_DATA", tempfile.mkdtemp(prefix="cutora_trimbs_"))

from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

NONE = Qt.KeyboardModifier.NoModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier
CTRL = Qt.KeyboardModifier.ControlModifier
FPS = 25.0
EPS = 1e-6

_WIN = None


def _app():
    return QApplication.instance() or QApplication(["trimbs"])


def _window():
    global _WIN
    if _WIN is None:
        _app()
        from PySide6.QtWidgets import QMessageBox
        from cutora.ui.main_window import MainWindow
        QMessageBox.information = staticmethod(lambda *a, **k: None)
        QMessageBox.warning = staticmethod(lambda *a, **k: None)
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.No)
        QMessageBox.critical = staticmethod(lambda *a, **k: None)
        win = MainWindow()
        win._suppress_close_prompt = True
        win._prompt_canvas_if_sources_mismatch = lambda *a, **k: None
        win._offer_autosave_restore = lambda *a, **k: None
        win.resize(1400, 900)
        win.show()
        if callable(getattr(win, "show_editor", None)):
            win.show_editor()
        QApplication.processEvents()
        _WIN = win
    return _WIN


def _task():
    """V1: [0,4) src-a (linked to A1 [0,4)), [4,8) src-b. Subs [0.5,1.5) with words, [6,7)."""
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track
    tl = Timeline(320, 180, FPS)
    video = Track("video")
    video.is_main_video = True
    v0 = Clip("src-a", 0.0, 4.0, 0.0)
    v0.link_group_id = "g-a"
    video.clips.append(v0)
    video.clips.append(Clip("src-b", 10.0, 14.0, 4.0))
    audio = Track("audio")
    a0 = Clip("src-a", 0.0, 4.0, 0.0)
    a0.link_group_id = "g-a"
    audio.clips.append(a0)
    text_tr = Track("text")
    subs_tr = Track("subs")
    tl.tracks.extend([video, audio, text_tr, subs_tr])
    text = {"type": "text", "id": 101, "content": "hi", "start": 1.0, "end": 3.0,
            "x": 20, "y": 20, "w": 200, "h": 60, "fontsize": 40, "font_family": "Arimo",
            "color": "#FFFFFF", "opacity": 255, "rotation": 0, "_track_id": text_tr.id}
    segs = [{"start": 0.5, "end": 1.5, "text": "one two",
             "words": [{"word": "one", "start": 0.5, "end": 0.9},
                       {"word": "two", "start": 1.0, "end": 1.5}]},
            {"start": 6.0, "end": 7.0, "text": "three", "words": []}]
    task = {"video": "trimbs.mp4", "text": "", "segments": segs, "overlays": [],
            "text_overlays": [text], "image_overlays": [], "audio_clips": [], "dims": [320, 180],
            "style": {}, "src_volume": 1.0, "intro_media": None, "timeline": tl,
            "video_overlay": {}, "audio_tracks": [], "duration": 8.0, "guard_error": None}
    from cutora.subtitle.stylepack import ensure_default_pack
    from cutora.subtitle.track_source import set_segments
    ensure_default_pack(task)
    set_segments(task, segs)
    return task


def _settle(ms=150):
    from PySide6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()
    QApplication.processEvents()


def _load():
    from cutora.core.commands import get_bus
    win = _window()
    task = _task()
    win.tasks = [task]
    win.curr_idx = 0
    win.project_path = None
    win.file_list.blockSignals(True)
    win.file_list.clear()
    win.file_list.addItem("trimbs.mp4")
    win.file_list.blockSignals(False)
    get_bus().bind_window(win)
    _settle()
    win.history_mgr.history = []
    win.history_mgr.current_index = -1
    tl = win.timeline
    tl.pixels_per_second = 80.0
    tl.scroll_x = 0
    tl.scroll_y = 0
    tl.set_data(8000, task)
    tl._reset_selection()
    tl._ov_multi = []
    tl.set_tool("select")
    tl.position = 0
    QApplication.processEvents()
    return win, tl, task


def _cur(win):
    return win.tasks[win.curr_idx]


def _hist(win):
    _settle()
    return len(win.history_mgr.history)


def _key(tl, key, mods=NONE):
    tl.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, key, mods))
    QApplication.processEvents()


def _track(task, kind):
    return next(t for t in task["timeline"].tracks if t.type == kind)


def _spans(task, kind):
    from cutora.ui.timeline.snap import clip_span
    return sorted((round(s, 4), round(e, 4)) for s, e in (clip_span(c) for c in _track(task, kind).clips))


def _seg_spans(task):
    return [(round(float(s["start"]), 4), round(float(s["end"]), 4)) for s in task["segments"]]


def _subs_events_match(task):
    from cutora.subtitle.track_source import events_of, first_subs_track
    ev = events_of(first_subs_track(task))
    return ev is None or [(round(float(s["start"]), 4), round(float(s["end"]), 4)) for s in ev] == _seg_spans(task)


def _duration(task):
    from cutora.preview.engine import timeline_duration_sec
    return timeline_duration_sec(task)


# ───────────────────────────── Backspace ─────────────────────────────

def test_backspace_deletes_subtitle_piece_after_blade():
    win, tl, task = _load()
    tl.selected_indices = [0]
    tl.position = 1000
    _key(tl, Qt.Key.Key_B, CTRL)                       # blade the selected subtitle at 1.0 s
    task = _cur(win)
    assert _seg_spans(task) == [(0.5, 1.0), (1.0, 1.5), (6.0, 7.0)]
    tl = win.timeline
    tl._reset_selection()
    tl.selected_indices = [1]                          # select the extra piece
    n = _hist(win)
    _key(tl, Qt.Key.Key_Backspace)
    task = _cur(win)
    assert _seg_spans(task) == [(0.5, 1.0), (6.0, 7.0)]   # piece gone, nothing else moved
    assert _subs_events_match(task)
    assert _spans(task, "video") == [(0.0, 4.0), (4.0, 8.0)]
    assert _hist(win) == n + 1
    win.undo()
    assert _seg_spans(_cur(win)) == [(0.5, 1.0), (1.0, 1.5), (6.0, 7.0)]


def test_backspace_deletes_audio_clip_and_text():
    win, tl, task = _load()
    atr = _track(task, "audio")
    tl._clip_sel = [(str(atr.id), 0)]
    tl._ov_multi = [task["text_overlays"][0]]
    _key(tl, Qt.Key.Key_Backspace)
    task = _cur(win)
    assert _track(task, "audio").clips == []
    assert task["text_overlays"] == []


# ───────────────────────────── Shift+[ / Shift+] (no ripple) ─────────────────────────────

def test_shift_bracket_right_trims_linked_av_without_ripple():
    win, tl, task = _load()
    dur0 = _duration(task)
    tl.position = 2000                                # inside V [0,4) / A [0,4) (linked)
    n = _hist(win)
    _key(tl, Qt.Key.Key_BracketRight, SHIFT)
    task = _cur(win)
    assert _spans(task, "video") == [(0.0, 2.0), (4.0, 8.0)]   # gap [2,4) stays
    assert _spans(task, "audio") == [(0.0, 2.0)]               # linked audio trimmed too
    assert abs(_duration(task) - dur0) < EPS
    assert _seg_spans(task) == [(0.5, 1.5), (6.0, 7.0)]         # subtitles untouched
    assert abs(task["text_overlays"][0]["start"] - 1.0) < EPS
    assert _hist(win) == n + 1
    win.undo()
    task = _cur(win)
    assert _spans(task, "video") == [(0.0, 4.0), (4.0, 8.0)]
    assert _spans(task, "audio") == [(0.0, 4.0)]


def test_shift_bracket_left_trims_head_without_ripple():
    win, tl, task = _load()
    tl.position = 1000
    _key(tl, Qt.Key.Key_BracketLeft, SHIFT)
    task = _cur(win)
    vtr = _track(task, "video")
    first = min(vtr.clips, key=lambda c: c.start_t)
    assert abs(first.start_t - 1.0) < EPS and abs(first.in_t - 1.0) < EPS and abs(first.out_t - 4.0) < EPS
    assert _spans(task, "video") == [(1.0, 4.0), (4.0, 8.0)]
    a = _track(task, "audio").clips[0]
    assert abs(a.start_t - 1.0) < EPS and abs(a.in_t - 1.0) < EPS
    assert abs(_duration(task) - 8.0) < EPS


def test_brace_keycode_variant_also_trims():
    """US layout: Shift+[ arrives as Key_BraceLeft."""
    win, tl, _t = _load()
    tl.position = 5000
    _key(tl, Qt.Key.Key_BraceLeft, SHIFT)
    assert _spans(_cur(win), "video") == [(0.0, 4.0), (5.0, 8.0)]


def test_shift_brackets_on_selected_subtitle():
    win, tl, task = _load()
    tl.selected_indices = [0]
    tl.position = 1200
    _key(tl, Qt.Key.Key_BracketRight, SHIFT)          # keep [0.5,1.2)
    task = _cur(win)
    assert _seg_spans(task) == [(0.5, 1.2), (6.0, 7.0)]
    words = task["segments"][0]["words"]
    assert [(w["word"], round(w["start"], 4), round(w["end"], 4)) for w in words] == \
        [("one", 0.5, 0.9), ("two", 1.0, 1.2)]
    assert _spans(task, "video") == [(0.0, 4.0), (4.0, 8.0)]      # clips not touched
    assert _subs_events_match(task)
    tl = win.timeline
    tl._reset_selection()
    tl.selected_indices = [1]
    tl.position = 6400
    _key(tl, Qt.Key.Key_BracketLeft, SHIFT)           # keep [6.4,7)
    task = _cur(win)
    assert _seg_spans(task) == [(0.5, 1.2), (6.4, 7.0)]
    assert abs(_duration(task) - 8.0) < EPS
    win.undo()
    assert _seg_spans(_cur(win)) == [(0.5, 1.2), (6.0, 7.0)]


def test_shift_bracket_on_selected_text():
    win, tl, task = _load()
    tl._ov_multi = [task["text_overlays"][0]]
    tl.position = 2000
    _key(tl, Qt.Key.Key_BracketRight, SHIFT)
    task = _cur(win)
    ov = task["text_overlays"][0]
    assert abs(ov["start"] - 1.0) < EPS and abs(ov["end"] - 2.0) < EPS
    assert _spans(task, "video") == [(0.0, 4.0), (4.0, 8.0)]


def test_ctrl_shift_bracket_keeps_ripple_variant():
    win, tl, _t = _load()
    tl.position = 1000
    _key(tl, Qt.Key.Key_BracketLeft, CTRL | SHIFT)
    task = _cur(win)
    assert _spans(task, "video") == [(0.0, 3.0), (3.0, 7.0)]      # later clip moved left


def test_trim_then_project_roundtrip_keeps_gap_and_subs():
    from cutora.model.project_io import _rj_deserialize_timeline, _rj_serialize_timeline
    from cutora.subtitle.track_source import events_of, first_subs_track, hydrate_track
    win, tl, task = _load()
    tl.position = 2000
    _key(tl, Qt.Key.Key_BracketRight, SHIFT)
    tl = win.timeline
    tl.selected_indices = [1]
    tl.position = 6500
    _key(tl, Qt.Key.Key_BracketRight, SHIFT)
    task = _cur(win)
    back = _rj_deserialize_timeline(_rj_serialize_timeline(task["timeline"]))
    for tr in back.tracks:
        hydrate_track(tr)
    t2 = dict(task, timeline=back)
    assert _spans(t2, "video") == [(0.0, 2.0), (4.0, 8.0)]
    assert _spans(t2, "audio") == [(0.0, 2.0)]
    ev = events_of(first_subs_track(t2))
    if ev is not None:
        assert [(round(s["start"], 4), round(s["end"], 4)) for s in ev] == [(0.5, 1.5), (6.0, 6.5)]
    assert abs(_duration(t2) - 8.0) < EPS


def test_shortcut_help_mentions_new_bindings():
    from cutora.ui.shortcuts import GESTURE_ROWS
    keys = [k for _a, k, _l in GESTURE_ROWS]
    assert any("Shift+[ / Shift+]" == k for k in keys)
    assert any("Ctrl+Shift+[" in k for k in keys)

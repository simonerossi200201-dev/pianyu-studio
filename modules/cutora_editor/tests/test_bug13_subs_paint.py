# -*- coding: utf-8 -*-
"""BUG-13: subs track paints subtitle bars."""
from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
G19 = os.path.join(REPO, "tests", "golden", "g19_stitch_timeline", "project.asproj.json")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["bug13-subs"])


def test_g19_subs_row_has_bar_pixels(qapp):
    from cutora.model.project_io import deserialize_render_task
    from cutora.ui.timeline.widget import TimelineWidget

    data = json.loads(open(G19, encoding="utf-8").read())
    task = deserialize_render_task(data["tasks"][0])
    task["segments"] = [
        {"start": 0.0, "end": 2.0, "text": "hello", "role": "default"},
        {"start": 2.0, "end": 4.0, "text": "world", "role": "default"},
    ]
    tl = task["timeline"]
    types = [getattr(t, "type", None) for t in tl.tracks]
    assert "subs" in types
    w = TimelineWidget()
    w.resize(900, 280)
    w.pixels_per_second = 60.0
    w.set_data(int(float(task.get("duration") or 30) * 1000), task)
    w.show()
    qapp.processEvents()
    w.repaint()
    qapp.processEvents()
    img = w.grab().toImage()
    w.close()
    pos = w._track_positions()
    row = None
    for t_obj, y, h in pos.get("rows") or []:
        if getattr(t_obj, "type", None) in ("subs", "subtitle"):
            row = (int(y), int(h))
            break
    assert row is not None, "subs row missing from _track_positions"
    y0, h = row
    found = False
    for y in range(max(0, y0), min(img.height(), y0 + h)):
        for x in range(4, min(160, img.width())):
            c = img.pixelColor(x, y)
            # bars: purple edge (100,80,210) or white label
            if c.blue() > 150 and c.red() > 60 and c.green() < 160:
                found = True
                break
            if c.red() > 200 and c.green() > 200 and c.blue() > 200:
                found = True
                break
        if found:
            break
    assert found, "no subtitle bar pixels in subs row y=%s h=%s" % (y0, h)


def test_timeline_asr_done_calls_set_data_with_task_duration(monkeypatch):
    """Root cause: ASR finished then set_data(media_player.duration()) of clip 1.
    Not _track_positions visibility. Lock: on_align_done uses task.duration."""
    from cutora.ui.main_window import MainWindow

    calls = []

    class Timeline:
        def set_data(self, dur_ms, task):
            calls.append((int(dur_ms), task))

    class Player:
        def duration(self):
            return 8000

    class Bar:
        def showMessage(self, *a, **k):
            pass

    host = type("H", (), {})()
    host.enable_time_postprocess = False
    host.tasks = [{
        "duration": 29.26,
        "segments": [],
        "text": "hello world",
        "style": {},
        "timeline": {"tracks": [{"type": "subs", "clips": []}]},
    }]
    host.curr_idx = 0
    host.media_player = Player()
    host.timeline = Timeline()
    host.push_undo = lambda: None
    host._refresh_task_view = lambda i: None
    host.worker_ai = type("W", (), {
        "align_note": None, "approximate_words": False})()
    host.batch_queue = []
    host.statusBar = lambda: Bar()
    monkeypatch.setattr(
        "cutora.subtitle.stylepack.ensure_default_pack", lambda t: None)
    monkeypatch.setattr(
        "cutora.subtitle.layout.auto_relayout_enabled", lambda: False)
    segs = [
        {"start": 0.0, "end": 2.0, "text": "hello"},
        {"start": 25.0, "end": 28.0, "text": "world"},
    ]
    MainWindow.on_align_done(host, segs, False)
    assert calls, "timeline.set_data not called after ASR"
    dur_ms, task = calls[0]
    assert dur_ms >= 29260, dur_ms
    assert dur_ms != 8000
    assert task is host.tasks[0]
    assert task.get("segments")


def _luca_t023_asproj():
    root = os.path.join("D:\\", "1_PROJECT", "2026-09-15", "A")
    if not os.path.isdir(root):
        return None
    for dirpath, _dirs, names in os.walk(root):
        if "stitched.asproj.json" not in names:
            continue
        if os.sep + "T023" + os.sep not in (dirpath + os.sep):
            continue
        return os.path.join(dirpath, "stitched.asproj.json")
    return None


def _row_has_bar_pixels(img, y0, h):
    for y in range(max(0, y0), min(img.height(), y0 + h)):
        for x in range(4, min(220, img.width())):
            c = img.pixelColor(x, y)
            if c.blue() > 150 and c.red() > 60 and c.green() < 160:
                return True
            if c.red() > 200 and c.green() > 200 and c.blue() > 200:
                return True
    return False


def test_luca_t023_asproj_tracks_and_segments():
    """(a) Luca's stitched asproj as on disk: subs track present, segments=0."""
    path = _luca_t023_asproj()
    if not path:
        pytest.skip("Luca T023 asproj not on this machine")
    data = json.loads(open(path, encoding="utf-8").read())
    task = data["tasks"][0]
    tracks = (task.get("timeline") or {}).get("tracks") or []
    types = [tr.get("type") for tr in tracks]
    n_seg = len(task.get("segments") or [])
    print("LUCA_T023 tracks_has_subs=%s segments=%s types=%s" % (
        "subs" in types, n_seg, types), flush=True)
    assert "subs" in types
    assert n_seg == 0


def _silence_win(win, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: QMessageBox.StandardButton.No)
    win._ui_warning = lambda *a, **k: None
    win._ui_information = lambda *a, **k: None
    win._trigger_waveform_generation = lambda *a, **k: None
    win._rebuild_preview_wav = lambda *a, **k: None
    win._present_preview_frame = lambda *a, **k: None
    win._preview_fx_request = lambda *a, **k: None
    win._preview_fx_reset = lambda *a, **k: None
    win._sfx_stop_all = lambda *a, **k: None
    win._sync_bgm_state = lambda *a, **k: None
    win._suppress_close_prompt = True


def test_user_path_stitch_open_asr_paints_subs_row(qapp, monkeypatch):
    """(b) stitcher done → open → on_align_done. Test never calls set_data
    or stuffs segments except via the ASR callback."""
    path = _luca_t023_asproj()
    if not path:
        pytest.skip("Luca T023 asproj not on this machine")
    from cutora.ui.main_window import MainWindow
    win = MainWindow()
    _silence_win(win, monkeypatch)
    win.show_editor()
    qapp.processEvents()
    MainWindow._on_stitcher_done(
        win,
        {"kind": "timeline", "asproj": path, "asprojs": [path], "n": 9},
        False)
    qapp.processEvents()
    assert win.tasks
    tl = win.tasks[0].get("timeline")
    types_open = [getattr(t, "type", None) for t in getattr(tl, "tracks", [])]
    assert "subs" not in types_open, types_open
    assert not (win.tasks[0].get("segments") or [])
    segs = [
        {"start": 0.0, "end": 2.5, "text": "hello", "role": "default"},
        {"start": 2.5, "end": 5.0, "text": "world", "role": "default"},
    ]
    MainWindow.on_align_done(win, segs, False)
    qapp.processEvents()
    win.timeline.pixels_per_second = 40.0
    win.timeline.resize(900, 220)
    if hasattr(win, "timeline_container"):
        win.timeline_container.resize(1100, 240)
        win.timeline_container.show()
    win.timeline.show()
    qapp.processEvents()
    win.timeline.repaint()
    qapp.processEvents()
    pos = win.timeline._track_positions()
    row = None
    for t_obj, y, h in pos.get("rows") or []:
        if getattr(t_obj, "type", None) in ("subs", "subtitle"):
            row = (int(y), int(h))
            break
    assert row is not None, "subs row missing after ASR callback; rows=%s" % (
        [getattr(t, "type", None) for t, _, _ in pos.get("rows") or []])
    img = win.timeline.grab().toImage()
    y0, h = row
    assert _row_has_bar_pixels(img, y0, h), "no subtitle bars in subs row"
    out = os.path.join(REPO, "docs", "plan", "reports", "img")
    os.makedirs(out, exist_ok=True)
    dest = os.path.join(out, "BUG13_luca_T023_timeline.png")
    grab = win.timeline_container if hasattr(win, "timeline_container") else win.timeline
    grab.grab().save(dest)
    win.close()


def test_open_heals_missing_subs_track_when_segments_present(qapp, monkeypatch, tmp_path):
    """(c) open project: segments non-empty, no subs track → get_or_create_track."""
    src = _luca_t023_asproj() or G19
    data = json.loads(open(src, encoding="utf-8").read())
    task = data["tasks"][0]
    task["segments"] = [
        {"start": 0.0, "end": 2.0, "text": "hello", "role": "default"},
        {"start": 2.0, "end": 4.0, "text": "world", "role": "default"},
    ]
    tl = task["timeline"]
    tl["tracks"] = [tr for tr in tl.get("tracks") or [] if tr.get("type") != "subs"]
    assert all(tr.get("type") != "subs" for tr in tl["tracks"])
    dest = str(tmp_path / "no_subs.asproj.json")
    open(dest, "w", encoding="utf-8").write(json.dumps(data, ensure_ascii=False))
    from cutora.ui.main_window import MainWindow
    win = MainWindow()
    _silence_win(win, monkeypatch)
    win.show_editor()
    qapp.processEvents()
    win.apply_project_data(json.loads(open(dest, encoding="utf-8").read()))
    qapp.processEvents()
    assert win.tasks
    types = [getattr(t, "type", None) for t in win.tasks[0]["timeline"].tracks]
    assert "subs" in types, types
    pos = win.timeline._track_positions()
    assert any(getattr(t, "type", None) == "subs" for t, _, _ in pos.get("rows") or [])
    win.close()


def test_asr_done_autosave_segments_match_memory(tmp_path, monkeypatch):
    """BUG-15: on_align_done writes autosave with the same segment count."""
    from cutora.ui.main_window import MainWindow
    from cutora.core.autosave import sidecar_path, write_snapshot
    proj = str(tmp_path / "t.asproj.json")
    open(proj, "w", encoding="utf-8").write("{}")
    segs = [
        {"start": 0.0, "end": 1.5, "text": "one", "role": "default"},
        {"start": 1.5, "end": 3.0, "text": "two", "role": "default"},
    ]
    host = type("H", (), {})()
    host.enable_time_postprocess = False
    host.tasks = [{
        "duration": 10.0, "segments": [], "text": "one two",
        "style": {}, "timeline": {"tracks": [{"type": "subs", "clips": []}]},
    }]
    host.curr_idx = 0
    host.project_path = proj
    host.media_player = type("P", (), {"duration": lambda self: 8000})()
    calls = []
    host.timeline = type("T", (), {
        "set_data": lambda self, d, t: calls.append(d)})()
    host.push_undo = lambda: None
    host._refresh_task_view = lambda i: None
    host.worker_ai = None
    host.batch_queue = []
    host.statusBar = lambda: type("S", (), {"showMessage": lambda *a, **k: None})()
    host.collect_project_data = lambda: {"tasks": host.tasks}
    from cutora.core.autosave import write_snapshot
    host._autosave_now = lambda force=False: write_snapshot(
        host.project_path, host.collect_project_data())
    monkeypatch.setattr(
        "cutora.subtitle.stylepack.ensure_default_pack", lambda t: None)
    monkeypatch.setattr(
        "cutora.subtitle.layout.auto_relayout_enabled", lambda: False)
    MainWindow.on_align_done(host, list(segs), False)
    auto = sidecar_path(proj)
    assert os.path.isfile(auto), auto
    data = json.loads(open(auto, encoding="utf-8").read())
    mem = len(host.tasks[0].get("segments") or [])
    file_n = len((data.get("tasks") or [{}])[0].get("segments") or [])
    assert mem == file_n == 2, (mem, file_n)


def test_asr_done_marks_dirty_and_save_writes_segments(tmp_path, monkeypatch, qapp):
    """BUG-15: ASR done marks dirty; save_project writes segments into asproj."""
    from cutora.ui.main_window import MainWindow
    proj = str(tmp_path / "stitched.asproj.json")
    open(proj, "w", encoding="utf-8").write("{}")
    segs = [
        {"start": 0.0, "end": 1.5, "text": "one", "role": "default"},
        {"start": 1.5, "end": 3.0, "text": "two", "role": "default"},
        {"start": 3.0, "end": 4.5, "text": "three", "role": "default"},
    ]
    host = type("H", (), {})()
    host.enable_time_postprocess = False
    host.tasks = [{
        "duration": 10.0, "segments": [], "text": "one two three",
        "style": {}, "timeline": {"tracks": [{"type": "subs", "clips": []}]},
    }]
    host.curr_idx = 0
    host.project_path = proj
    host.media_player = type("P", (), {"duration": lambda self: 8000})()
    host.timeline = type("T", (), {"set_data": lambda self, d, t: None})()
    host.push_undo = lambda: None
    host._refresh_task_view = lambda i: None
    host.worker_ai = None
    host.batch_queue = []
    titles = []
    host.setWindowTitle = lambda t: titles.append(t)
    host.statusBar = lambda: type("S", (), {"showMessage": lambda *a, **k: None})()
    host.collect_project_data = lambda: {"tasks": host.tasks}
    host._autosave_now = lambda force=False: None
    host.is_dirty = lambda: bool(getattr(host, "_project_dirty", False))

    def _save():
        data = host.collect_project_data()
        json.dump(data, open(proj, "w", encoding="utf-8"), ensure_ascii=False)
        host._project_dirty = False

    host.save_project = _save
    monkeypatch.setattr(
        "cutora.subtitle.stylepack.ensure_default_pack", lambda t: None)
    monkeypatch.setattr(
        "cutora.subtitle.layout.auto_relayout_enabled", lambda: False)
    MainWindow.on_align_done(host, list(segs), False)
    assert host.is_dirty() is True
    mem = len(host.tasks[0].get("segments") or [])
    assert mem == 3, mem
    from PySide6.QtGui import QColor, QImage, QPainter
    from cutora.render.frame_renderer import FrameRenderer
    img = QImage(64, 64, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor("#000"))
    painter = QPainter(img)
    try:
        FrameRenderer.draw_subtitle(
            painter, host.tasks[0]["segments"][0],
            {"font_family": "Arimo", "fontsize": 18, "pos_x": 32, "pos_y": 32,
             "color_text": "#FFFFFF"},
            0.4, 64, 64, seg_idx=0)
    finally:
        painter.end()
    from cutora.core.autosave import sidecar_path, write_snapshot
    auto = write_snapshot(proj, host.collect_project_data())
    assert os.path.isfile(auto)
    assert sidecar_path(proj) == auto
    host.save_project()
    disk = json.loads(open(proj, encoding="utf-8").read())
    file_n = len((disk.get("tasks") or [{}])[0].get("segments") or [])
    assert file_n == mem == 3, (file_n, mem)
    assert host.is_dirty() is False

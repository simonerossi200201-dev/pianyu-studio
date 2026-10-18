# -*- coding: utf-8 -*-
"""Q29: 句子 page reads the unique subtitle source (subs track)."""
from __future__ import annotations

import json
import logging
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
G09 = os.path.join(REPO, "tests", "golden", "g09_linebreak_latin", "project.asproj.json")
G19 = os.path.join(REPO, "tests", "golden", "g19_stitch_timeline", "project.asproj.json")
MEDIA = os.path.join(REPO, "tests", "golden", "_media", "g19_stitch_timeline")
CLIPS = (
    "T0035-s001-p03.mp4",
    "T0035-s004-p02.mp4",
    "T0035-s014-p04.mp4",
    "T0035-s021-p03.mp4",
    "T0035-s027-p01.mp4",
    "T0035-s008-p08.mp4",
)

MSG_TRUE_EMPTY = "还没有字幕。先自动字幕，或在「文案」里贴原文再对齐。"
MSG_NO_MEDIA = "先在媒体库里选一个条目"
MSG_UNREAD = "检测到时间线上有"


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["t29-transcript"])


def _events(n=3, prefix="line"):
    return [
        {
            "start": float(i),
            "end": float(i) + 0.8,
            "text": "%s %d" % (prefix, i),
            "role": "default",
        }
        for i in range(n)
    ]


def _asproj_track_subs(dest, name, video, events):
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    task = {
        "name": name,
        "video": video,
        "path": video,
        "text": "script body",
        "segments": [],
        "style": {},
        "duration": max(4.0, float(len(events))),
        "timeline": {
            "width": 1080,
            "height": 1920,
            "fps": 30.0,
            "sources": {
                "s1": {
                    "id": "s1",
                    "path": video,
                    "duration": 4.0,
                    "has_video": True,
                    "has_audio": True,
                }
            },
            "tracks": [
                {
                    "type": "video",
                    "is_main_video": True,
                    "clips": [{
                        "source_id": "s1",
                        "in_t": 0.0, "out_t": 4.0, "start_t": 0.0,
                    }],
                },
                {
                    "type": "subs",
                    "clips": [],
                    "subtitle_events": list(events),
                },
                {
                    "type": "audio",
                    "clips": [{
                        "source_id": "s1",
                        "in_t": 0.0, "out_t": 4.0, "start_t": 0.0,
                    }],
                },
            ],
        },
    }
    data = {"version": 2, "tasks": [task], "curr_idx": 0}
    with open(dest, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
        fh.write("\n")
    return dest


def _silence(win, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *a, **k: QMessageBox.StandardButton.No)
    win._suppress_close_prompt = True
    win._trigger_waveform_generation = lambda *a, **k: None
    win._rebuild_preview_wav = lambda *a, **k: None
    win._present_preview_frame = lambda *a, **k: None
    win._prompt_canvas_if_sources_mismatch = lambda *a, **k: None
    win._offer_autosave_restore = lambda *a, **k: None


class _FakeWin:
    def __init__(self, task=None, idx=0):
        self.tasks = [task] if isinstance(task, dict) else list(task or [])
        self.curr_idx = idx
        self._timeline_ms = 0
        self.bus = None

    def focusWidget(self):
        return None


def _panel(qapp, task=None, idx=0):
    from cutora.ui.panels.transcript import TranscriptPanel
    win = _FakeWin(task, idx)
    panel = TranscriptPanel(win)
    panel.resize(280, 400)
    panel.show()
    qapp.processEvents()
    return panel, win


def _card_count(panel):
    return int(panel.model.rowCount())


def _make_task_with_track(events, segments=None):
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Track
    from cutora.subtitle.track_source import write_events
    tl = Timeline(1080, 1920, 30.0)
    subs = Track("subs")
    write_events(subs, list(events or []))
    tl.add_track(subs)
    task = {
        "text": "script",
        "segments": list(segments) if segments is not None else [],
        "style": {},
        "timeline": tl,
        "duration": 4.0,
    }
    return task


def test_t29_single_source(qapp):
    """A: writing the track is enough; segments() must not need task['segments']."""
    from cutora.model.timeline_model import Track
    from cutora.subtitle.track_source import write_events
    events = _events(2, "src")
    task = _make_task_with_track([])
    task["segments"] = []
    panel, _win = _panel(qapp, task, 0)
    tr = None
    for t in task["timeline"].tracks:
        if getattr(t, "type", None) == "subs":
            tr = t
            break
    assert isinstance(tr, Track)
    write_events(tr, events)
    assert task.get("segments") == []
    got = panel.segments()
    assert [s.get("text") for s in got] == ["src 0", "src 1"]
    panel.reload(force=True)
    assert _card_count(panel) == 2


def test_t29_empty_states(qapp, caplog):
    from cutora.ui.i18n import tr
    from cutora.subtitle.track_source import write_events

    panel, win = _panel(qapp, None, -1)
    win.tasks = []
    win.curr_idx = -1
    panel.reload(force=True)
    assert tr(MSG_NO_MEDIA) in panel.empty.text()
    assert panel.empty_box.isVisible()
    assert not panel.btn_empty_auto.isVisible()
    assert not panel.btn_reload.isVisible()

    task = _make_task_with_track([])
    task["segments"] = []
    win.tasks = [task]
    win.curr_idx = 0
    panel.reload(force=True)
    assert tr(MSG_TRUE_EMPTY) in panel.empty.text()
    assert panel.empty_box.isVisible()
    assert panel.btn_empty_auto.isVisible()
    assert not panel.btn_reload.isVisible()

    caplog.set_level(logging.ERROR)
    write_events(task["timeline"].tracks[0], ["broken-a", "broken-b"])
    task["segments"] = []
    with caplog.at_level(logging.ERROR):
        panel.reload(force=True)
    assert MSG_UNREAD in panel.empty.text()
    assert "2" in panel.empty.text()
    assert panel.btn_reload.isVisible()
    assert not panel.btn_empty_auto.isVisible()
    err = "\n".join(r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR)
    assert "track_subs" in err or "transcript" in err.lower() or "读取" in err or "read" in err.lower()
    assert err, caplog.text


def test_t29_reload_force(qapp):
    from cutora.subtitle.track_source import set_segments
    events = _events(1, "old")
    task = _make_task_with_track(events)
    set_segments(task, events)
    panel, win = _panel(qapp, task, 0)
    panel.reload(force=True)
    panel.inspect_cards()
    assert panel._cards
    card = panel._cards[0]
    card._dirty = True
    win.focusWidget = lambda: card.editor
    set_segments(task, _events(3, "new"))
    panel.reload()
    assert _card_count(panel) == 1
    panel.reload(force=True)
    assert _card_count(panel) == 3


def test_t29_transcript_after_open(qapp, tmp_path, monkeypatch):
    from cutora.ui.main_window import MainWindow
    vid = os.path.join(MEDIA, CLIPS[0])
    if not os.path.isfile(vid):
        pytest.skip("g19 media missing")
    events = _events(4, "open")
    dest = str(tmp_path / "with_subs.asproj.json")
    _asproj_track_subs(dest, "T029", vid, events)
    win = MainWindow()
    _silence(win, monkeypatch)
    win.show_editor()
    qapp.processEvents()
    win.command_project_open(path=dest)
    qapp.processEvents()
    panel = win.transcript_panel
    panel.reload(force=True)
    assert _card_count(panel) == len(events)
    win.close()


def test_t29_transcript_after_asr(qapp, tmp_path, monkeypatch):
    from cutora.ui.main_window import MainWindow
    monkeypatch.setattr(
        "cutora.subtitle.layout.auto_relayout_enabled", lambda: False)
    vid = os.path.join(MEDIA, CLIPS[0])
    if not os.path.isfile(vid):
        pytest.skip("g19 media missing")
    dest = str(tmp_path / "empty.asproj.json")
    _asproj_track_subs(dest, "T029a", vid, [])
    win = MainWindow()
    _silence(win, monkeypatch)
    win.show_editor()
    qapp.processEvents()
    win.command_project_open(path=dest)
    qapp.processEvents()
    panel = win.transcript_panel
    panel.reload(force=True)
    assert _card_count(panel) == 0

    class _Fake:
        align_note = None
        approximate_words = False

    injected = _events(5, "asr")
    win.worker_ai = _Fake()
    win.enable_time_postprocess = False
    win.on_align_done(list(injected), False)
    qapp.processEvents()
    panel.reload(force=True)
    assert _card_count(panel) == len(injected)
    win.close()


def test_t29_transcript_after_stitch_import(qapp, tmp_path, monkeypatch):
    from cutora.ui.main_window import MainWindow
    asprojs = []
    for i in range(6):
        vid = os.path.join(MEDIA, CLIPS[i % len(CLIPS)])
        if not os.path.isfile(vid):
            pytest.skip("g19 media missing")
        dest = str(tmp_path / ("g%d" % i) / "_stitched" / "stitched.asproj.json")
        _asproj_track_subs(dest, "T%03d" % (i + 1), vid, _events(2 + i, "st"))
        asprojs.append(dest)
    win = MainWindow()
    _silence(win, monkeypatch)
    win.show_editor()
    qapp.processEvents()
    win._import_stitch_timeline_projects(asprojs)
    qapp.processEvents()
    assert len(win.tasks) == 6
    item = win.file_list.item(0)
    assert item is not None
    win.on_click(item)
    qapp.processEvents()
    panel = win.transcript_panel
    panel.reload(force=True)
    assert _card_count(panel) > 0
    win.close()


def test_t29_measure_five_paths(qapp, tmp_path, monkeypatch, capsys):
    """Record len(segments) vs track_subs on the five user paths."""
    from cutora.subtitle.track_source import snapshot_source
    from cutora.ui.main_window import MainWindow

    rows = {}
    vid = os.path.join(MEDIA, CLIPS[0])
    if not os.path.isfile(vid) or not os.path.isfile(G09):
        pytest.skip("golden media missing")

    monkeypatch.setattr(
        "cutora.subtitle.layout.auto_relayout_enabled", lambda: False)
    win = MainWindow()
    _silence(win, monkeypatch)
    win.show_editor()
    qapp.processEvents()

    win.command_project_open(path=G09)
    qapp.processEvents()
    task = win.tasks[win.curr_idx]
    rows["project.open"] = snapshot_source(task)

    dest_a = str(tmp_path / "A" / "_stitched" / "stitched.asproj.json")
    dest_b = str(tmp_path / "B" / "_stitched" / "stitched.asproj.json")
    _asproj_track_subs(dest_a, "A", vid, _events(2, "a"))
    _asproj_track_subs(dest_b, "B", vid, _events(3, "b"))
    win._import_stitch_timeline_projects([dest_a, dest_b])
    qapp.processEvents()
    rows["stitch_import"] = snapshot_source(win.tasks[win.curr_idx])

    if win.file_list.count() >= 2:
        win.on_click(win.file_list.item(1))
        qapp.processEvents()
        rows["on_click"] = snapshot_source(win.tasks[win.curr_idx])
    else:
        rows["on_click"] = snapshot_source(win.tasks[win.curr_idx])

    class _Fake:
        align_note = None
        approximate_words = False

    win.worker_ai = _Fake()
    win.enable_time_postprocess = False
    win.on_align_done(_events(4, "asr"), False)
    qapp.processEvents()
    rows["on_align_done"] = snapshot_source(win.tasks[win.curr_idx])

    win.push_undo()
    rows["push_undo"] = snapshot_source(win.tasks[win.curr_idx])

    print("T29_FIVE_PATHS", json.dumps(rows), flush=True)
    win.close()
    assert set(rows) == {
        "project.open", "on_click", "on_align_done", "stitch_import", "push_undo",
    }

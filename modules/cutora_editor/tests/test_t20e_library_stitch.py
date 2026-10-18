# -*- coding: utf-8 -*-
"""BUG-25: stitch import library rows equal reopen (no expand-to-sources)."""
from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEDIA = os.path.join(REPO, "tests", "golden", "_media", "g19_stitch_timeline")
CLIPS = (
    "T0035-s001-p03.mp4",
    "T0035-s004-p02.mp4",
    "T0035-s014-p04.mp4",
)


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["bug25-lib"])


def _mini_asproj(dest, name, video):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    task = {
        "name": name,
        "video": video,
        "path": video,
        "text": "",
        "segments": [],
        "style": {},
        "duration": 4.0,
        "timeline": {
            "width": 1080, "height": 1920, "fps": 30.0,
            "sources": {
                "s1": {
                    "id": "s1", "path": video, "duration": 4.0,
                    "has_video": True, "has_audio": True,
                }
            },
            "tracks": [{
                "type": "video",
                "clips": [{
                    "source_id": "s1", "in_t": 0.0, "out_t": 4.0, "start_t": 0.0,
                }],
            }],
        },
    }
    data = {"version": 2, "tasks": [task], "curr_idx": 0}
    with open(dest, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
        fh.write("\n")


def _snap(win):
    from PySide6.QtCore import Qt
    fl = win.file_list
    rows = []
    for i in range(fl.topLevelItemCount()):
        it = fl.topLevelItem(i)
        rows.append({
            "name": (it.text(0) or "").strip(),
            "checked": it.checkState(0) == Qt.CheckState.Checked,
            "role": it.data(0, Qt.ItemDataRole.UserRole),
            "children": it.childCount(),
        })
    return rows


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


def test_no_add_stitch_entry():
    from cutora.ui.panels.media_list import DraggableListWidget
    assert not hasattr(DraggableListWidget, "add_stitch_entry")


def test_stitch_import_equals_reopen(qapp, tmp_path, monkeypatch):
    from cutora.ui.main_window import MainWindow
    names = ("T023", "T024", "T025")
    asprojs = []
    for i, name in enumerate(names):
        vid = os.path.join(MEDIA, CLIPS[i])
        if not os.path.isfile(vid):
            pytest.skip("g19 media missing")
        dest = str(tmp_path / name / "_stitched" / "stitched.asproj.json")
        _mini_asproj(dest, name, vid)
        asprojs.append(dest)
    win = MainWindow()
    _silence(win, monkeypatch)
    win.show_editor()
    qapp.processEvents()
    win._import_stitch_timeline_projects(asprojs)
    qapp.processEvents()
    assert [t.get("name") for t in win.tasks] == list(names)
    snap1 = _snap(win)
    assert len(snap1) == 3
    assert all(r["children"] == 0 for r in snap1)
    assert all(r["checked"] for r in snap1)
    assert [r["name"] for r in snap1] == list(names)
    assert win.resolve_bin_targets() == [0, 1, 2]
    data = win.collect_project_data()
    win2 = MainWindow()
    _silence(win2, monkeypatch)
    win2.show_editor()
    qapp.processEvents()
    win2.apply_project_data(data)
    qapp.processEvents()
    snap2 = _snap(win2)
    assert snap1 == snap2
    assert win2.resolve_bin_targets() == [0, 1, 2]
    win.close()
    win2.close()


def test_open_sanitized_luca_three_checked(qapp, monkeypatch):
    from PySide6.QtCore import Qt
    from cutora.ui.main_window import MainWindow
    src = os.path.join(REPO, "tests", "fixtures", "bug25_A认真.cutora")
    media = os.path.join(REPO, "tests", "golden", "_media", "g19_stitch_timeline")
    data = json.loads(open(src, encoding="utf-8").read())
    blob = json.dumps(data)
    blob = blob.replace("MEDIA/", media.replace("\\", "/") + "/")
    data = json.loads(blob)
    win = MainWindow()
    _silence(win, monkeypatch)
    win.show_editor()
    qapp.processEvents()
    win.apply_project_data(data)
    qapp.processEvents()
    snap = _snap(win)
    assert [r["name"] for r in snap] == ["T023", "T024", "T025"]
    assert all(r["checked"] for r in snap)
    assert all(r["children"] == 0 for r in snap)
    assert win.resolve_bin_targets() == [0, 1, 2]
    win.close()

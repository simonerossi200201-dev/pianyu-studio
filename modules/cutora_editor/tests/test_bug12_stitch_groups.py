# -*- coding: utf-8 -*-
"""BUG-12: one timeline per stitch group."""
from __future__ import annotations

import json
import os

from cutora.batch.timeline_stitch import placements_by_group, projects_from_cutlist
from cutora.model.project_io import deserialize_render_task


def test_three_groups_three_projects(tmp_path):
    cuts = {
        "T023": [
            {"path": "a.mp4", "keep": True, "segs": [[0.0, 1.5]], "name": "a"},
            {"path": "b.mp4", "keep": True, "segs": [[0.0, 2.5]], "name": "b"},
        ],
        "T024": [
            {"path": "c.mp4", "keep": True, "segs": [[0.0, 3.0]], "name": "c"},
        ],
        "T025": [
            {"path": "d.mp4", "keep": True, "segs": [[0.0, 4.0]], "name": "d"},
            {"path": "e.mp4", "keep": True, "segs": [[1.0, 2.0]], "name": "e"},
        ],
    }
    grouped = placements_by_group(cuts)
    assert set(grouped) == {"T023", "T024", "T025"}
    assert len(grouped["T023"]) == 2
    assert len(grouped["T024"]) == 1
    assert len(grouped["T025"]) == 2
    paths = projects_from_cutlist(cuts, str(tmp_path))
    assert len(paths) == 3
    for g, want_n, want_dur in (("T023", 2, 4.0), ("T024", 1, 3.0), ("T025", 2, 5.0)):
        dest = os.path.join(str(tmp_path), g, "_stitched", "stitched.asproj.json")
        assert os.path.isfile(dest), dest
        data = json.loads(open(dest, encoding="utf-8").read())
        task = deserialize_render_task(data["tasks"][0])
        tl = task["timeline"]
        v = [t for t in tl.tracks if t.type == "video"][0]
        assert len(v.clips) == want_n
        total = sum(max(0.0, c.out_t - c.in_t) for c in v.clips)
        assert abs(total - want_dur) < 1e-9
        srcs = data["tasks"][0]["timeline"]["sources"]
        names = {os.path.basename(str((s or {}).get("path") or ""))
                 for s in srcs.values()}
        if g == "T023":
            assert "c.mp4" not in names and "d.mp4" not in names
            assert "a.mp4" in names and "b.mp4" in names
        if g == "T024":
            assert "c.mp4" in names
            assert "a.mp4" not in names


_THREE_CUTS = {
    "T023": [
        {"path": "a.mp4", "keep": True, "segs": [[0.0, 1.5]], "name": "a"},
        {"path": "b.mp4", "keep": True, "segs": [[0.0, 2.5]], "name": "b"},
    ],
    "T024": [
        {"path": "c.mp4", "keep": True, "segs": [[0.0, 3.0]], "name": "c"},
    ],
    "T025": [
        {"path": "d.mp4", "keep": True, "segs": [[0.0, 4.0]], "name": "d"},
        {"path": "e.mp4", "keep": True, "segs": [[1.0, 2.0]], "name": "e"},
    ],
}


def test_finish_as_timeline_payload_has_three_asprojs(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMessageBox
    QApplication.instance() or QApplication(["bug12-finish"])
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    from cutora.batch.script_stitcher import StitcherDialog

    class Fake:
        _out_dir = str(tmp_path)
        _scripts = {"T023": "aa", "T024": "bb", "T025": "cc"}

        class _Chk:
            def isChecked(self):
                return True

        chk_auto_ai = _Chk()

        class _Btn:
            def setText(self, t):
                pass

        btn_start = _Btn()

        class _Bar:
            def setValue(self, v):
                pass

        bar = _Bar()
        payload = None
        auto = None

        def _log(self, m):
            pass

        def _on_failed(self, m):
            raise AssertionError(m)

        def on_done(self, results, auto_ai):
            self.payload = results
            self.auto = auto_ai

        def accept(self):
            self.accepted = True

        def _project_canvas_wh(self):
            # BUG-38：对话框把宿主窗口的画布传给 timeline_stitch。
            # 以前这里写死 1080x1920，用户在欢迎页点 720 也没用。
            return (720, 1280)

    fake = Fake()
    StitcherDialog._finish_as_timeline(fake, _THREE_CUTS)
    assert fake.payload is not None
    assert fake.payload["kind"] == "timeline"
    assert len(fake.payload["asprojs"]) == 3
    assert all(os.path.isfile(p) for p in fake.payload["asprojs"])
    assert fake.auto is True
    # BUG-38：多组这条路也必须把画布传下去，不是只有单组那条
    for path in fake.payload["asprojs"]:
        task = json.load(open(path, encoding="utf-8"))["tasks"][0]
        assert tuple(task["dims"]) == (720, 1280), path
        tl = task["timeline"]
        assert (int(tl["width"]), int(tl["height"])) == (720, 1280), path


def test_stitcher_done_three_library_rows_three_queue_asr(tmp_path, monkeypatch):
    """UI wiring: 3 checkable library rows, 3 batch-queue tasks, ASR count 3."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication(["bug12-ui"])
    paths = projects_from_cutlist(
        _THREE_CUTS, str(tmp_path),
        text_by_group={"T023": "hello a", "T024": "hello b", "T025": "hello c"})
    assert len(paths) == 3
    from cutora.ui.main_window import MainWindow
    from cutora.ui.panels.media_list import DraggableListWidget

    class Host:
        def __init__(self):
            self.file_list = DraggableListWidget()
            self.tasks = []
            self.curr_idx = -1
            self.project_path = None
            self.batch_queue = []
            self.asr_count = 0

        def apply_project_data(self, data):
            self.tasks = list(data.get("tasks") or [])
            self.curr_idx = int(data.get("curr_idx") or 0)
            MainWindow._populate_library(self, self.tasks)

        def start_batch_ai(self):
            self.batch_queue = [
                i for i, t in enumerate(self.tasks)
                if not t.get("segments") and (t.get("text") or "").strip()]
            self.asr_count = len(self.batch_queue)

        def _relabel_library_rows(self):
            return None

        def _announce_text_layer_migrate(self, data):
            MainWindow._announce_text_layer_migrate(self, data)

        def _import_stitch_timeline_projects(self, asprojs):
            MainWindow._import_stitch_timeline_projects(self, asprojs)

    host = Host()
    MainWindow._on_stitcher_done(
        host,
        {"kind": "timeline", "asproj": paths[0], "asprojs": paths, "n": 5},
        True)
    assert host.file_list.topLevelItemCount() == 3
    names = []
    for i in range(3):
        item = host.file_list.topLevelItem(i)
        names.append((item.text(0) or "").strip())
        assert item.childCount() == 0
        assert item.checkState(0) == Qt.CheckState.Checked
        assert item.data(0, Qt.ItemDataRole.UserRole) != "stitch"
    assert names == ["T023", "T024", "T025"], names
    assert [t.get("name") for t in host.tasks] == ["T023", "T024", "T025"]
    assert len(host.tasks) == 3
    assert host.batch_queue == [0, 1, 2]
    assert host.asr_count == 3

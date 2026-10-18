# -*- coding: utf-8 -*-
"""Q54: opening a many-clip project must not freeze the UI, and ffprobe once."""
from __future__ import annotations

import json
import os
import time
import uuid

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
G19 = os.path.join(REPO, "tests", "golden", "_media", "g19_stitch_timeline")
G01 = os.path.join(REPO, "tests", "golden", "_media", "g01", "video.mp4")

# Q71: same measurement as the open-profile report (QApplication eventFilter).
MAIN_THREAD_BLOCK_MS = 200.0


def _videos():
    import glob
    files = sorted(glob.glob(os.path.join(G19, "*.mp4")))
    if files:
        return files
    if os.path.isfile(G01):
        return [G01]
    return []


def _n_clip_project(dest, n=32, files=None):
    files = list(files or _videos())
    assert files, "need golden mp4"
    n = max(int(n), 1)
    sources = {}
    vclips = []
    aclips = []
    t = 0.0
    clip_len = 2.0
    for i in range(n):
        path = os.path.abspath(files[i % len(files)])
        sid = uuid.uuid4().hex
        sources[sid] = {
            "id": sid,
            "path": path,
            "duration": 4.0,
            "has_video": True,
            "has_audio": True,
        }
        vclips.append({
            "source_id": sid, "in_t": 0.0, "out_t": clip_len, "start_t": t,
            "fit_mode": "fill",
        })
        aclips.append({
            "source_id": sid, "in_t": 0.0, "out_t": clip_len, "start_t": t,
            "is_main": True, "audio_role": "main",
        })
        t += clip_len
    first = os.path.abspath(files[0])
    data = {
        "version": 2,
        "app": "CUTORA",
        "tasks": [{
            "name": "q54-%d" % n,
            "video": first,
            "path": first,
            "text": "",
            "segments": [],
            "style": {},
            "duration": t,
            "text_overlays": [],
            "image_overlays": [],
            "timeline": {
                "width": 1080, "height": 1920, "fps": 30.0,
                "sources": sources,
                "tracks": [
                    {"type": "video", "is_main_video": True, "clips": vclips,
                     "transitions": []},
                    {"type": "audio", "clips": aclips, "transitions": []},
                ],
            },
        }],
    }
    os.makedirs(os.path.dirname(os.path.abspath(dest)) or ".", exist_ok=True)
    with open(dest, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    return dest


def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["q54-open"])


def _silence(win):
    from PySide6.QtWidgets import QMessageBox
    QMessageBox.information = staticmethod(lambda *a, **k: None)
    QMessageBox.warning = staticmethod(lambda *a, **k: None)
    QMessageBox.question = staticmethod(
        lambda *a, **k: QMessageBox.StandardButton.No)
    QMessageBox.critical = staticmethod(lambda *a, **k: None)
    win._suppress_close_prompt = True
    win._prompt_canvas_if_sources_mismatch = lambda *a, **k: None
    win._offer_autosave_restore = lambda *a, **k: None


class _StallWatch:
    """Gap between QApplication events = main-thread stall.

    Offscreen QTimer is not a reliable heartbeat (nested loops may not
    deliver timeout). An app event filter sees every dispatched event.
    """

    def __init__(self):
        self.max_gap_ms = 0.0
        self.slow = []
        self._last = 0.0
        self._last_ev = None
        self._filter = None
        self._app = None

    def start(self, app):
        from PySide6.QtCore import QObject
        self._app = app
        self._last = time.perf_counter()
        watch = self

        class _Filt(QObject):
            def eventFilter(self, obj, event):
                now = time.perf_counter()
                gap = (now - watch._last) * 1000.0
                watch._last = now
                if gap > watch.max_gap_ms:
                    watch.max_gap_ms = gap
                if gap > 80:
                    watch.slow.append((
                        round(gap, 1),
                        watch._last_ev,
                        type(obj).__name__,
                        int(event.type()),
                    ))
                watch._last_ev = (type(obj).__name__, int(event.type()))
                return False

        self._filter = _Filt(app)
        app.installEventFilter(self._filter)
        app.processEvents()
        self._last = time.perf_counter()

    def stop(self):
        now = time.perf_counter()
        gap = (now - self._last) * 1000.0
        if gap > self.max_gap_ms:
            self.max_gap_ms = gap
        if self._app is not None and self._filter is not None:
            self._app.removeEventFilter(self._filter)


def _make_win():
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.main_window import MainWindow
    load_embedded_fonts()
    win = MainWindow()
    _silence(win)
    return win


def test_q54_open_no_main_thread_block(tmp_path):
    files = _videos()
    if len(files) < 1:
        pytest.skip("golden media missing")
    dest = str(tmp_path / "q54_32.asproj.json")
    _n_clip_project(dest, n=32, files=files)
    app = _qapp()
    win = _make_win()
    win.show()
    app.processEvents()
    watch = _StallWatch()
    watch.start(app)
    win.command_project_open(path=dest)
    app.processEvents()
    watch.stop()
    n_src = 0
    for task in win.tasks or []:
        tl = task.get("timeline")
        srcs = getattr(tl, "sources", None) or {}
        n_src += len(srcs)
    win.close()
    assert n_src >= 30, n_src
    assert watch.max_gap_ms <= MAIN_THREAD_BLOCK_MS, (
        "main-thread stall %.1f ms exceeds %.0f ms (open of %d sources) slow=%s"
        % (watch.max_gap_ms, MAIN_THREAD_BLOCK_MS, n_src, watch.slow)
    )


def test_q54_ffprobe_called_once_per_file(tmp_path, monkeypatch):
    files = _videos()
    if not files:
        pytest.skip("golden media missing")
    from cutora.media.probe_cache import reset as reset_probe_cache
    reset_probe_cache()
    one = [os.path.abspath(files[0])]
    dest = str(tmp_path / "q54_dup.asproj.json")
    # Four clips, one file — without a cache this is four (or more) ffprobes.
    _n_clip_project(dest, n=4, files=one)

    import subprocess as sp
    orig = sp.run
    counts = {}
    cmds = []

    def run_wrap(cmd, *a, **k):
        exe = ""
        path = ""
        if isinstance(cmd, (list, tuple)) and cmd:
            exe = os.path.basename(str(cmd[0])).lower()
            path = str(cmd[-1]) if len(cmd) >= 2 else ""
        if "ffprobe" in exe and any("duration" in str(x) for x in cmd):
            key = os.path.normcase(os.path.abspath(path)) if path else "?"
            counts[key] = counts.get(key, 0) + 1
            cmds.append([str(x) for x in cmd])
        return orig(cmd, *a, **k)

    monkeypatch.setattr(sp, "run", run_wrap)

    app = _qapp()
    win = _make_win()
    win.show()
    app.processEvents()
    win.command_project_open(path=dest)
    app.processEvents()
    win.close()

    assert counts, "open path never called ffprobe (counter is dead)"
    over = {p: n for p, n in counts.items() if n > 1}
    assert over == {}, (
        "same file ffprobe'd more than once during open: %s cmds=%s" % (over, cmds)
    )

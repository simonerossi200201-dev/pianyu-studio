# -*- coding: utf-8 -*-
"""Q122: timeline drop must not block, and one import must not refresh twice.

Before this change, dropping files that were never imported ran ffprobe on
the UI thread (duration + audio + alpha), laid clips out by whoever returned
a duration, and a 5-file drop called timeline.set_data once per file.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
G01 = os.path.join(REPO, "tests", "golden", "_media", "g01", "video.mp4")

MAIN_THREAD_BLOCK_MS = 150.0
_CHILD_ENV = "CUTORA_Q122_CHILD"


def _run_in_fresh_interpreter():
    """One MainWindow per process. A second window in the same interpreter hangs."""
    if os.environ.get(_CHILD_ENV) == "1":
        return False
    node = (os.environ.get("PYTEST_CURRENT_TEST") or "").split(" ")[0]
    if not node:
        return False
    env = dict(os.environ)
    env[_CHILD_ENV] = "1"
    env["CUTORA_PYTEST_ISOLATED"] = "1"
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", node, "-q", "--tb=short",
         "-p", "no:cacheprovider"],
        cwd=REPO, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=90,
    )
    assert proc.returncode == 0, (proc.stdout or "") + "\n" + (proc.stderr or "")
    return True


def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["q122-smooth"])


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
    def __init__(self):
        self.max_gap_ms = 0.0
        self.slow = []
        self._last = 0.0
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
                        type(obj).__name__,
                        int(event.type()),
                    ))
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
    win._timeline_template = {"width": 1080, "height": 1920, "fps": 30.0}
    return win


@pytest.fixture
def isolated_user_data(tmp_path, monkeypatch):
    ud = tmp_path / "ud"
    ud.mkdir()
    monkeypatch.setenv("CUTORA_USER_DATA", str(ud))
    from cutora.core.paths import refresh_cached_paths
    refresh_cached_paths()
    from cutora.media.probe_cache import reset as reset_probe_cache
    from cutora.media.import_probe import reset_import_cache
    reset_probe_cache()
    reset_import_cache()
    yield ud
    reset_probe_cache()
    reset_import_cache()


def _ok_rec(duration=1.25, width=64, height=96, has_audio=True):
    return {
        "ok": True,
        "info": {
            "duration": duration,
            "width": width,
            "height": height,
            "has_audio": has_audio,
            "has_video": True,
            "fps": 30.0,
            "pix_fmt": "yuv420p",
            "vcodec": "h264",
        },
        "issues": [],
        "fatal": [],
        "error": "",
    }


def _touch(tmp_path, names):
    files = []
    for name in names:
        dest = tmp_path / name
        if os.path.isfile(G01):
            shutil.copy2(G01, dest)
        else:
            dest.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32)
        files.append(str(dest))
    return files


def _arm_current_task(win, path):
    """A current task long enough that dropped clips are not clamped away."""
    win._append_placeholder_task(path)
    win.curr_idx = 0
    win.file_list.setCurrentRow(0)
    task = win.tasks[0]
    task["duration"] = 10000.0
    task["trim_end_sec"] = 10000.0
    task["probe_pending"] = False
    task["guard_error"] = None
    tl = task["timeline"]
    for tr in tl.tracks:
        if getattr(tr, "type", None) != "video":
            continue
        for clip in tr.clips:
            clip.out_t = 10000.0
    return task


def _pump_until(app, pred, timeout=8.0):
    t_end = time.perf_counter() + timeout
    while time.perf_counter() < t_end:
        app.processEvents()
        if pred():
            app.processEvents()
            return True
        time.sleep(0.005)
    return False


def _dropped_video_rows(task, names):
    tl = task["timeline"]
    rows = []
    for tr in getattr(tl, "tracks", []) or []:
        if getattr(tr, "type", None) != "video":
            continue
        for clip in getattr(tr, "clips", []) or []:
            src = tl.sources.get(getattr(clip, "source_id", None))
            base = os.path.basename(getattr(src, "path", "") or "")
            if base in names:
                rows.append((base, float(clip.start_t), float(clip.out_t) - float(clip.in_t)))
    rows.sort(key=lambda r: (r[1], r[0]))
    return rows


def _patch_slow_probes(monkeypatch):
    """Sleep on every probe the old drop path and the new import path can hit."""
    from cutora.media import import_probe
    from cutora.model import timeline_model
    from cutora.render import readers
    from cutora.ui import main_window as mw

    def slow_dur(path):
        time.sleep(0.200)
        return 1.5

    def slow_audio(path):
        time.sleep(0.200)
        return True

    def slow_alpha(path):
        time.sleep(0.200)
        return {"has_alpha": False, "pix_fmt": "yuv420p"}

    def slow_import(path):
        time.sleep(0.200)
        return _ok_rec(duration=1.5)

    monkeypatch.setattr(timeline_model, "probe_duration_sec", slow_dur)
    monkeypatch.setattr(mw, "probe_duration_sec", slow_dur)
    monkeypatch.setattr(mw, "has_audio_stream", slow_audio)
    monkeypatch.setattr(readers, "has_audio_stream", slow_audio)
    monkeypatch.setattr(readers, "_probe_video_has_alpha", slow_alpha)
    monkeypatch.setattr(import_probe, "probe_file_for_import", slow_import)


def test_q122_timeline_drop_does_not_block_ui(tmp_path, isolated_user_data, monkeypatch):
    """Before: 5 new files × sync probes sleep on the UI thread. Gap >> 150ms."""
    if _run_in_fresh_interpreter():
        return
    _patch_slow_probes(monkeypatch)
    names = ["a.mp4", "b.mp4", "c.mp4", "d.mp4", "e.mp4"]
    files = _touch(tmp_path, names)
    base = _touch(tmp_path, ["base.mp4"])[0]

    app = _qapp()
    win = _make_win()
    win.show()
    app.processEvents()
    task = _arm_current_task(win, base)
    tl = task["timeline"]
    vtrack = next(tr for tr in tl.tracks if tr.type == "video")

    watch = _StallWatch()
    watch.start(app)
    win._handle_timeline_drop_batch({
        "paths": list(files),
        "time": 0.0,
        "domain": "visual",
        "track": vtrack,
        "new_track": False,
    })
    def _ready():
        rows = _dropped_video_rows(task, set(names))
        return len(rows) == 5 and all(abs(r[2] - 1.5) < 0.05 for r in rows)

    assert _pump_until(app, _ready, timeout=8.0), _dropped_video_rows(task, set(names))
    watch.stop()
    win.close()
    assert watch.max_gap_ms < MAIN_THREAD_BLOCK_MS, (
        "main-thread stall %.1f ms exceeds %.0f ms slow=%s"
        % (watch.max_gap_ms, MAIN_THREAD_BLOCK_MS, watch.slow[:6])
    )


def test_q122_drop_order_follows_names_not_probe_finish(tmp_path, isolated_user_data, monkeypatch):
    """Probes finish out of order. Clip starts stay in filename order."""
    if _run_in_fresh_interpreter():
        return
    from cutora.media import import_probe

    names = ["a.mp4", "b.mp4", "c.mp4", "d.mp4", "e.mp4"]
    durs = {"a.mp4": 2.0, "b.mp4": 3.0, "c.mp4": 1.0, "d.mp4": 4.0, "e.mp4": 5.0}
    files = _touch(tmp_path, names)
    base = _touch(tmp_path, ["base.mp4"])[0]
    pending = []

    def capture(path, on_done):
        pending.append((path, on_done))

    monkeypatch.setattr(import_probe, "queue_import_probe", capture)

    app = _qapp()
    win = _make_win()
    win.show()
    app.processEvents()
    task = _arm_current_task(win, base)
    tl = task["timeline"]
    vtrack = next(tr for tr in tl.tracks if tr.type == "video")
    win._handle_timeline_drop_batch({
        "paths": list(files),
        "time": 0.0,
        "domain": "visual",
        "track": vtrack,
        "new_track": False,
    })
    app.processEvents()
    assert len(pending) == 5, pending
    # Finish c, e, a, d, b — not filename order.
    by_name = {os.path.basename(p): (p, cb) for p, cb in pending}
    for name in ("c.mp4", "e.mp4", "a.mp4", "d.mp4", "b.mp4"):
        path, cb = by_name[name]
        cb(path, _ok_rec(duration=durs[name], has_audio=False))
        app.processEvents()
    rows = _dropped_video_rows(task, set(names))
    win.close()
    assert [r[0] for r in rows] == names, rows
    cursor = 0.0
    for name, start, dur in rows:
        assert start == pytest.approx(cursor, abs=0.02), rows
        assert dur == pytest.approx(durs[name], abs=0.02), rows
        cursor += durs[name]


def test_q122_same_file_dropped_twice_probes_once(tmp_path, isolated_user_data, monkeypatch):
    """import_probe caches path+mtime+size. The second drop must not probe again."""
    if _run_in_fresh_interpreter():
        return
    from cutora.media import import_probe

    src = _touch(tmp_path, ["same.mp4"])[0]
    base = _touch(tmp_path, ["base.mp4"])[0]
    calls = {"n": 0}

    def counted(path):
        calls["n"] += 1
        return _ok_rec(duration=2.0, has_audio=False)

    monkeypatch.setattr(import_probe, "_probe_file_uncached", counted)

    app = _qapp()
    win = _make_win()
    win.show()
    app.processEvents()
    task = _arm_current_task(win, base)
    tl = task["timeline"]
    vtrack = next(tr for tr in tl.tracks if tr.type == "video")

    def _drop():
        win._handle_timeline_drop_batch({
            "paths": [src],
            "time": 0.0,
            "domain": "visual",
            "track": vtrack,
            "new_track": False,
        })

    _drop()
    assert _pump_until(
        app,
        lambda: len(_dropped_video_rows(task, {"same.mp4"})) >= 1
        and abs(_dropped_video_rows(task, {"same.mp4"})[-1][2] - 2.0) < 0.05,
    ), _dropped_video_rows(task, {"same.mp4"})
    assert calls["n"] == 1, calls
    _drop()
    app.processEvents()
    time.sleep(0.05)
    app.processEvents()
    win.close()
    assert calls["n"] == 1, "same file probed twice: %s" % calls


def test_q122_import_refreshes_ui_at_most_once(tmp_path, isolated_user_data, monkeypatch):
    """Importing 5 files calls refresh_ui at most once."""
    if _run_in_fresh_interpreter():
        return
    from cutora.media import import_probe

    def fast(path):
        return _ok_rec(duration=1.25, has_audio=False)

    monkeypatch.setattr(import_probe, "probe_file_for_import", fast)

    names = ["n0.mp4", "n1.mp4", "n2.mp4", "n3.mp4", "n4.mp4"]
    files = _touch(tmp_path, names)
    app = _qapp()
    win = _make_win()
    win.show()
    app.processEvents()

    refresh = {"n": 0}
    set_data = {"n": 0}
    orig_refresh = win.refresh_ui
    orig_set = win.timeline.set_data

    def wrapped_refresh(*a, **k):
        refresh["n"] += 1
        return orig_refresh(*a, **k)

    def wrapped_set(*a, **k):
        set_data["n"] += 1
        return orig_set(*a, **k)

    win.refresh_ui = wrapped_refresh
    win.timeline.set_data = wrapped_set

    win.on_drop(list(files))
    assert _pump_until(
        app, lambda: len(win.tasks) == 5 and not any(t.get("probe_pending") for t in win.tasks),
    )
    lib_refresh = refresh["n"]
    win.close()
    assert lib_refresh <= 1, "library import called refresh_ui %s times" % lib_refresh


def test_q122_timeline_drop_refreshes_at_most_once(tmp_path, isolated_user_data, monkeypatch):
    """Dropping 5 files must not set_data once per file."""
    if _run_in_fresh_interpreter():
        return
    from cutora.media import import_probe

    def fast(path):
        return _ok_rec(duration=1.25, has_audio=False)

    monkeypatch.setattr(import_probe, "probe_file_for_import", fast)

    app = _qapp()
    win = _make_win()
    win.show()
    app.processEvents()
    base = _touch(tmp_path, ["base2.mp4"])[0]
    task = _arm_current_task(win, base)
    tl = task["timeline"]
    vtrack = next(tr for tr in tl.tracks if tr.type == "video")
    refresh = {"n": 0}
    set_data = {"n": 0}
    orig_refresh = win.refresh_ui
    orig_set = win.timeline.set_data

    def wrapped_refresh(*a, **k):
        refresh["n"] += 1
        return orig_refresh(*a, **k)

    def wrapped_set(*a, **k):
        set_data["n"] += 1
        return orig_set(*a, **k)

    win.refresh_ui = wrapped_refresh
    win.timeline.set_data = wrapped_set
    drop_names = ["d0.mp4", "d1.mp4", "d2.mp4", "d3.mp4", "d4.mp4"]
    dropped = _touch(tmp_path, drop_names)
    win._handle_timeline_drop_batch({
        "paths": list(dropped),
        "time": 0.0,
        "domain": "visual",
        "track": vtrack,
        "new_track": False,
    })
    # Count only the drop call itself. Probe callbacks are later events.
    sync_refresh = refresh["n"]
    sync_set = set_data["n"]
    win.close()
    assert sync_refresh <= 1, sync_refresh
    assert sync_set <= 1, "timeline.set_data ran %s times for one drop of 5" % sync_set


def test_q122_fake_mp4_is_marked_and_kept_off_the_timeline(
        tmp_path, isolated_user_data, monkeypatch):
    """A JPEG renamed to .mp4 is marked and does not become a clip. Others do."""
    if _run_in_fresh_interpreter():
        return
    if not os.path.isfile(G01):
        pytest.skip("golden media missing")
    good_a = tmp_path / "ok_a.mp4"
    good_b = tmp_path / "ok_b.mp4"
    shutil.copy2(G01, good_a)
    shutil.copy2(G01, good_b)
    fake = tmp_path / "still.mp4"
    fake.write_bytes(b"\xff\xd8\xff\xe0" + b"JFIF" + b"\x00" * 64)
    base = tmp_path / "base.mp4"
    shutil.copy2(G01, base)

    app = _qapp()
    win = _make_win()
    win.show()
    app.processEvents()
    task = _arm_current_task(win, str(base))
    tl = task["timeline"]
    vtrack = next(tr for tr in tl.tracks if tr.type == "video")
    win._handle_timeline_drop_batch({
        "paths": [str(good_a), str(fake), str(good_b)],
        "time": 0.0,
        "domain": "visual",
        "track": vtrack,
        "new_track": False,
    })

    def _settled():
        rows = _dropped_video_rows(task, {"ok_a.mp4", "ok_b.mp4", "still.mp4"})
        names = {r[0] for r in rows}
        goods = [r for r in rows if r[0] in ("ok_a.mp4", "ok_b.mp4")]
        return "still.mp4" not in names and len(goods) == 2 and all(r[2] > 0.3 for r in goods)

    assert _pump_until(app, _settled, timeout=12.0), (
        _dropped_video_rows(task, {"ok_a.mp4", "ok_b.mp4", "still.mp4"}),
        getattr(win, "_drop_guard_marks", None),
    )
    rows = _dropped_video_rows(task, {"ok_a.mp4", "ok_b.mp4", "still.mp4"})
    assert all(r[0] != "still.mp4" for r in rows), rows
    marks = list(getattr(win, "_drop_guard_marks", []) or [])
    msg = ""
    try:
        msg = win.statusBar().currentMessage() or ""
    except Exception:
        msg = ""
    marked = any("still.mp4" in str(m.get("path") or "") and m.get("error") for m in marks)
    assert marked or ("无法导入" in msg and "still.mp4" in msg), (marks, msg)
    goods = [r for r in rows if r[0] in ("ok_a.mp4", "ok_b.mp4")]
    goods.sort(key=lambda r: r[1])
    assert [r[0] for r in goods] == ["ok_a.mp4", "ok_b.mp4"], goods
    assert goods[1][1] > goods[0][1], goods
    win.close()

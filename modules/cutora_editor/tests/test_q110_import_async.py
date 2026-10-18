# -*- coding: utf-8 -*-
"""Q110: import and click must not freeze the UI.

Each test is written so the pre-change product path would fail it:
sync ffprobe on the GUI thread, no placeholders, no cache, thumbs for
every clip, fake mp4 silently skipped or blocking the batch.
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

MAIN_THREAD_BLOCK_MS = 100.0
THUMB_PREFETCH_CLIPS = 1
_CHILD_ENV = "CUTORA_Q110_CHILD"


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
    return QApplication.instance() or QApplication(["q110-import"])


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
    """Gap between QApplication events = main-thread stall."""

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
    return win


def _drop_win(win):
    """Close and destroy. A live preview thread makes the next MainWindow crawl."""
    from PySide6.QtCore import QThreadPool
    from PySide6.QtWidgets import QApplication
    if win is None:
        return
    try:
        timer = getattr(win, "timer", None)
        if timer is not None:
            timer.stop()
    except Exception:
        pass
    try:
        pw = getattr(win, "_preview_worker", None)
        if pw is not None and hasattr(pw, "shutdown"):
            pw.shutdown(2000)
    except Exception:
        pass
    try:
        pool = QThreadPool.globalInstance()
        pool.clear()
        pool.waitForDone(3000)
    except Exception:
        pass
    try:
        win.close()
    except Exception:
        pass
    try:
        win.deleteLater()
    except Exception:
        pass
    app = QApplication.instance()
    if app is not None:
        for _ in range(16):
            app.processEvents()
    import gc
    gc.collect()
    if app is not None:
        for _ in range(8):
            app.processEvents()


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


def _wait_probes(win, app, timeout=12.0):
    t_end = time.perf_counter() + timeout
    while time.perf_counter() < t_end:
        app.processEvents()
        tasks = list(win.tasks or [])
        if tasks and not any(t.get("probe_pending") for t in tasks):
            app.processEvents()
            return
        time.sleep(0.005)
    pending = [t.get("video") for t in (win.tasks or []) if t.get("probe_pending")]
    raise AssertionError("probes still pending: %s" % pending)


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
        },
        "issues": [],
        "fatal": [],
        "error": "",
    }


def _touch_copies(tmp_path, n=5):
    files = []
    src = G01 if os.path.isfile(G01) else None
    for i in range(n):
        dest = tmp_path / ("q110_%02d.mp4" % i)
        if src:
            shutil.copy2(src, dest)
        else:
            dest.write_bytes(b"\x00\x00\x00\x18ftypmp42")
        files.append(str(dest))
    return files


def test_q110_import_does_not_block_ui(tmp_path, isolated_user_data, monkeypatch):
    """Before: 5 × 200ms probe on the GUI thread. After: longest gap < 100ms."""
    if _run_in_fresh_interpreter():
        return
    from cutora.media import import_probe

    files = _touch_copies(tmp_path, 5)

    def slow(path):
        time.sleep(0.200)
        return _ok_rec()

    monkeypatch.setattr(import_probe, "probe_file_for_import", slow)

    app = _qapp()
    win = _make_win()
    win.show()
    app.processEvents()
    watch = _StallWatch()
    watch.start(app)
    win.on_drop(list(files))
    _wait_probes(win, app)
    watch.stop()
    n = len(win.tasks or [])
    _drop_win(win)
    assert n == 5, n
    assert watch.max_gap_ms < MAIN_THREAD_BLOCK_MS, (
        "main-thread stall %.1f ms exceeds %.0f ms (5×200ms probes) slow=%s"
        % (watch.max_gap_ms, MAIN_THREAD_BLOCK_MS, watch.slow)
    )


def test_q110_placeholder_then_duration(tmp_path, isolated_user_data, monkeypatch):
    """Before: duration already filled when on_drop returns (probe was sync)."""
    if _run_in_fresh_interpreter():
        return
    from cutora.media import import_probe
    from cutora.media.import_probe import DURATION_PENDING_LABEL

    files = _touch_copies(tmp_path, 5)
    gate = {"go": False}

    def slow(path):
        t_end = time.perf_counter() + 2.0
        while not gate["go"] and time.perf_counter() < t_end:
            time.sleep(0.01)
        return _ok_rec(duration=2.5, width=1080, height=1920)

    monkeypatch.setattr(import_probe, "probe_file_for_import", slow)

    app = _qapp()
    win = _make_win()
    win.show()
    app.processEvents()
    win.on_drop(list(files))
    app.processEvents()
    assert len(win.tasks) == 5
    assert len([win.file_list.item(i) for i in range(win.file_list.count())]) == 5
    for i, task in enumerate(win.tasks):
        assert task.get("duration") is None, task.get("duration")
        assert task.get("probe_pending") is True
        text = win.file_list.item(i).text(0)
        assert DURATION_PENDING_LABEL in text, text
    gate["go"] = True
    _wait_probes(win, app)
    for task in win.tasks:
        assert task.get("duration") == 2.5
        assert task.get("probe_pending") is False
    _drop_win(win)


def test_q110_cache_hit_skips_second_probe(tmp_path, isolated_user_data, monkeypatch):
    """Before: every import called ffprobe, even the same file twice."""
    if _run_in_fresh_interpreter():
        return
    from cutora.media import import_probe

    src = tmp_path / "same.mp4"
    if os.path.isfile(G01):
        shutil.copy2(G01, src)
    else:
        src.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    path = str(src)
    calls = {"n": 0}

    def counted(p):
        calls["n"] += 1
        return _ok_rec(duration=3.0)

    monkeypatch.setattr(import_probe, "_probe_file_uncached", counted)

    app = _qapp()
    win = _make_win()
    win.show()
    app.processEvents()
    win.on_drop([path])
    _wait_probes(win, app)
    assert calls["n"] == 1, calls
    win.on_drop([path])
    _wait_probes(win, app)
    assert calls["n"] == 1, "same fingerprint probed twice: %s" % calls
    os.utime(path, (time.time() + 10, time.time() + 10))
    win.on_drop([path])
    _wait_probes(win, app)
    _drop_win(win)
    assert calls["n"] == 2, "mtime change did not invalidate cache: %s" % calls


def test_q110_thumbs_only_viewport(tmp_path, isolated_user_data):
    """Before: set_data requested a thumb for every clip, not just the viewport."""
    if _run_in_fresh_interpreter():
        return
    if not os.path.isfile(G01):
        pytest.skip("golden media missing")
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, MediaSource, Track
    from cutora.ui.timeline.thumbs import THUMB_PREFETCH_CLIPS, take_request_log
    from cutora.ui.timeline.widget import TimelineWidget

    assert THUMB_PREFETCH_CLIPS == 1
    app = _qapp()
    tl = Timeline(1080, 1920, 30.0)
    vtrack = Track("video")
    vtrack.is_main_video = True
    clip_len = 2.0
    n = 50
    for i in range(n):
        src = MediaSource(G01, duration=4.0, probe=False)
        tl.sources[src.id] = src
        vtrack.clips.append(Clip(src.id, 0.0, clip_len, i * clip_len))
    tl.tracks.append(vtrack)
    task = {"timeline": tl, "video": G01, "duration": n * clip_len, "segments": []}

    take_request_log()
    w = TimelineWidget()
    w.pixels_per_second = 20.0
    w.setFixedSize(240, 160)
    w.show()
    app.processEvents()
    assert w.width() == 240, w.width()
    # 240 px / 20 px/s = 12 s = 6 clips of 2 s. Prefetch 1 on each side.
    w.set_data(int(n * clip_len * 1000), task)
    app.processEvents()
    log = take_request_log()
    w.close()
    visible = 6
    budget = visible + 2 * THUMB_PREFETCH_CLIPS
    assert len(log) <= budget, (
        "requested %s thumbs for 50 clips, viewport=%s prefetch=%s budget=%s"
        % (len(log), visible, THUMB_PREFETCH_CLIPS, budget)
    )
    assert len(log) >= visible, "viewport clips got no thumbs: %s" % len(log)


def test_q110_guard_marks_fake_mp4_and_keeps_going(tmp_path, isolated_user_data):
    """Before: fake jpeg-as-mp4 was silent or blocked the whole batch."""
    if _run_in_fresh_interpreter():
        return
    if not os.path.isfile(G01):
        pytest.skip("golden media missing")
    good_a = tmp_path / "ok_a.mp4"
    good_b = tmp_path / "ok_b.mp4"
    shutil.copy2(G01, good_a)
    shutil.copy2(G01, good_b)
    fake = tmp_path / "still.mp4"
    fake.write_bytes(b"\xff\xd8\xff\xe0" + b"JFIF" + b"\x00" * 16)

    app = _qapp()
    win = _make_win()
    win.show()
    app.processEvents()
    win.on_drop([str(good_a), str(fake), str(good_b)])
    _wait_probes(win, app)

    by_name = {}
    for i, task in enumerate(win.tasks or []):
        by_name[os.path.basename(task.get("video") or "")] = (i, task)
    assert "still.mp4" in by_name
    fake_idx, fake_task = by_name["still.mp4"]
    assert fake_task.get("guard_error"), fake_task
    tl = fake_task.get("timeline")
    n_vclips = 0
    for tr in getattr(tl, "tracks", []) or []:
        if getattr(tr, "type", None) == "video":
            n_vclips += len(getattr(tr, "clips", []) or [])
    assert n_vclips == 0, "fake mp4 still has %s video clips" % n_vclips
    item = win.file_list.item(fake_idx)
    assert item is not None
    assert "无法导入" in item.text(0), item.text(0)

    for name in ("ok_a.mp4", "ok_b.mp4"):
        _i, task = by_name[name]
        assert not task.get("guard_error"), task.get("guard_error")
        assert isinstance(task.get("duration"), (int, float)) and task["duration"] > 0
        tl = task.get("timeline")
        n_v = 0
        for tr in getattr(tl, "tracks", []) or []:
            if getattr(tr, "type", None) == "video":
                n_v += len(getattr(tr, "clips", []) or [])
        assert n_v >= 1, "%s has no video clip" % name
    _drop_win(win)


def test_q110_import_fields_match_probe(tmp_path, isolated_user_data):
    """After import finishes, path / duration / size match a direct inspect_clip."""
    if _run_in_fresh_interpreter():
        return
    if not os.path.isfile(G01):
        pytest.skip("golden media missing")
    from cutora.batch.media_guard import inspect_clip

    dest = tmp_path / "reg.mp4"
    shutil.copy2(G01, dest)
    issues, info = inspect_clip(str(dest))
    assert info is not None, issues
    app = _qapp()
    win = _make_win()
    win.show()
    app.processEvents()
    win.on_drop([str(dest)])
    _wait_probes(win, app)
    assert len(win.tasks) == 1
    task = win.tasks[0]
    _drop_win(win)
    assert os.path.normcase(os.path.abspath(task.get("video") or "")) == os.path.normcase(
        os.path.abspath(str(dest)))
    assert abs(float(task.get("duration") or 0) - float(info["duration"])) < 0.05, (
        task.get("duration"), info.get("duration"))
    dims = task.get("dims") or (0, 0)
    assert int(dims[0]) == int(info["width"]), (dims, info)
    assert int(dims[1]) == int(info["height"]), (dims, info)

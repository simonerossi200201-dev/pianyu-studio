# -*- coding: utf-8 -*-
"""Q71: parallel probe, failed probes retry, alpha cache, 200ms stall gate."""
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

# Same measurement as tools/_diag/profile_open.py (QApplication eventFilter).
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
            "name": "q71-%d" % n,
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
    return QApplication.instance() or QApplication(["q71-open"])


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

    Same method as test_q54_open._StallWatch and profile_open.StallWatch.
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


@pytest.fixture
def isolated_user_data(tmp_path, monkeypatch):
    ud = tmp_path / "ud"
    ud.mkdir()
    monkeypatch.setenv("CUTORA_USER_DATA", str(ud))
    from cutora.core.paths import refresh_cached_paths
    refresh_cached_paths()
    from cutora.media.probe_cache import reset as reset_probe_cache
    reset_probe_cache()
    yield ud
    reset_probe_cache()


def _tiny_mov(dest: str) -> str:
    from cutora.core.config import FFMPEG_PATH
    import subprocess
    os.makedirs(os.path.dirname(os.path.abspath(dest)) or ".", exist_ok=True)
    cmd = [
        FFMPEG_PATH, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=red:s=64x64:d=0.3:r=10",
        "-c:v", "mpeg4", dest,
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=30)
    assert r.returncode == 0 and os.path.isfile(dest), r.stderr
    return dest


def test_q71_probe_parallel(isolated_user_data, monkeypatch):
    files = _videos()
    if len(files) < 27:
        pytest.skip("need 27 golden mp4s")
    files = [os.path.abspath(p) for p in files[:27]]
    from cutora.media import probe_cache
    from cutora.media.probe_cache import prefetch_paths, reset

    sleep_s = 0.05
    calls = {"n": 0}

    def slow_probe(path):
        calls["n"] += 1
        time.sleep(sleep_s)
        return 1.25

    monkeypatch.setattr(probe_cache, "_ffprobe_duration", slow_probe)
    reset()
    t0 = time.perf_counter()
    prefetch_paths(files, max_workers=4, include_alpha=False)
    parallel_ms = (time.perf_counter() - t0) * 1000.0
    n = calls["n"]
    assert n == 27, "prefetch must miss-probe each of 27 files, got %s" % n
    serial_ms = n * sleep_s * 1000.0
    assert parallel_ms < serial_ms * 0.55, (
        "27-file prefetch wall %.0f ms is not faster than serial sum %.0f ms "
        "(workers should overlap ffprobe)"
        % (parallel_ms, serial_ms)
    )


def test_q71_failed_probe_not_persisted(isolated_user_data, monkeypatch):
    files = _videos()
    if not files:
        pytest.skip("golden media missing")
    one = os.path.abspath(files[0])
    dest = str(isolated_user_data / "fail.asproj.json")
    _n_clip_project(dest, n=1, files=[one])

    from cutora.media import probe_cache
    from cutora.media.probe_cache import _cache_path, reset

    reset()
    calls = {"n": 0}

    def flaky(path):
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return 9.5

    monkeypatch.setattr(probe_cache, "_ffprobe_duration", flaky)

    app = _qapp()
    win = _make_win()
    win.show()
    app.processEvents()
    win.command_project_open(path=dest)
    app.processEvents()

    cache_path = _cache_path()
    if os.path.isfile(cache_path):
        data = json.loads(open(cache_path, encoding="utf-8").read() or "{}")
        items = data.get("items") or {}
        for rec in items.values():
            if isinstance(rec, dict):
                assert rec.get("duration") is not None, rec
            else:
                assert rec is not None

    first_calls = calls["n"]
    assert first_calls >= 1

    win.command_project_open(path=dest)
    app.processEvents()
    win.close()

    assert calls["n"] > first_calls, (
        "failed probe was remembered; second open did not retry ffprobe "
        "(n stayed %s)" % calls["n"]
    )


def test_q71_alpha_probe_cached(isolated_user_data, tmp_path, monkeypatch):
    mov = _tiny_mov(str(tmp_path / "q71.mov"))
    dest = str(tmp_path / "q71_alpha.asproj.json")
    _n_clip_project(dest, n=1, files=[mov])

    from cutora.media.probe_cache import reset
    reset()

    import subprocess as sp
    orig = sp.run
    alpha_cmds = []

    def run_wrap(cmd, *a, **k):
        exe = ""
        if isinstance(cmd, (list, tuple)) and cmd:
            exe = os.path.basename(str(cmd[0])).lower()
        blob = " ".join(str(x) for x in cmd) if isinstance(cmd, (list, tuple)) else str(cmd)
        if "ffprobe" in exe and "pix_fmt" in blob:
            alpha_cmds.append([str(x) for x in cmd])
        return orig(cmd, *a, **k)

    monkeypatch.setattr(sp, "run", run_wrap)
    try:
        import cutora.core.proc as proc
        monkeypatch.setattr(proc, "run", run_wrap)
    except Exception:
        pass

    app = _qapp()
    win = _make_win()
    win.show()
    app.processEvents()
    win.command_project_open(path=dest)
    app.processEvents()
    first = len(alpha_cmds)
    assert first >= 1, "opening a .mov never ran the alpha ffprobe"
    win.command_project_open(path=dest)
    app.processEvents()
    win.close()
    assert len(alpha_cmds) == first, (
        "same mov alpha-probed again on second open: first=%s total=%s cmds=%s"
        % (first, len(alpha_cmds), alpha_cmds)
    )


def test_q71_main_thread_block_under_200ms(isolated_user_data, tmp_path):
    files = _videos()
    if len(files) < 1:
        pytest.skip("golden media missing")
    dest = str(tmp_path / "q71_32.asproj.json")
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

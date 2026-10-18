#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q71: profile a real 'open recent project' run (cold + hot cache).

Buckets (exclusive, nested): disk_read / json_parse / model_build /
QWidget_build / first_frame / thumbs / media_probe.

Stall = QApplication eventFilter gaps — the same method as
test_q71_main_thread_block_under_200ms. The wall clock of
command_project_open is recorded separately and is NOT folded into
max_gap_ms (Q54 mixed the two).

Window construction is timed separately and is NOT part of open-project
time (BUG-41 lesson).
"""
from __future__ import annotations

import argparse
import cProfile
import glob
import io
import json
import os
import pstats
import subprocess
import sys
import tempfile
import threading
import time
import uuid

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

G19_MEDIA = os.path.join(REPO, "tests", "golden", "_media", "g19_stitch_timeline")
G01_VIDEO = os.path.join(REPO, "tests", "golden", "_media", "g01", "video.mp4")
LUCA_PROJECT = os.path.join("D:", os.sep, "1_PROJECT", "2026-09-07", "A", "2026-09-07A.asproj.json")
REPORT_MD = os.path.join(REPO, "docs", "plan", "reports", "Q71_open_profile.md")
REPORT_JSON = os.path.join(REPO, "docs", "plan", "reports", "Q71_open_profile.json")

BUCKETS = (
    "disk_read",
    "json_parse",
    "model_build",
    "qwidget_build",
    "first_frame",
    "thumbs",
    "media_probe",
)


class BucketClock:
    """Exclusive nested timers. Child time is not counted in the parent."""

    def __init__(self):
        self.ms = {k: 0.0 for k in BUCKETS}
        self.calls = {k: 0 for k in BUCKETS}
        self._stack: list[tuple[str, float]] = []
        self.unattributed_ms = 0.0

    def enter(self, name: str) -> None:
        now = time.perf_counter()
        if self._stack:
            parent, t0 = self._stack[-1]
            self.ms[parent] = self.ms.get(parent, 0.0) + (now - t0) * 1000.0
            self._stack[-1] = (parent, now)
        self.calls[name] = self.calls.get(name, 0) + 1
        self._stack.append((name, now))

    def leave(self, name: str) -> None:
        now = time.perf_counter()
        if not self._stack:
            return
        cur, t0 = self._stack.pop()
        if cur != name:
            # Unwind until we find *name* so a raised exception cannot
            # permanently desync the stack.
            while cur != name and self._stack:
                self.ms[cur] = self.ms.get(cur, 0.0) + (now - t0) * 1000.0
                cur, t0 = self._stack.pop()
        self.ms[name] = self.ms.get(name, 0.0) + (now - t0) * 1000.0
        if self._stack:
            parent, _ = self._stack[-1]
            self._stack[-1] = (parent, now)

    def wrap(self, name: str, fn):
        clock = self

        def inner(*a, **k):
            clock.enter(name)
            try:
                return fn(*a, **k)
            finally:
                clock.leave(name)

        return inner

    def snapshot(self) -> dict:
        return {
            "ms": {k: round(v, 3) for k, v in self.ms.items()},
            "calls": dict(self.calls),
        }


class StallWatch:
    """Gap between QApplication events = main-thread stall.

    Same method as tests/test_q54_open.py::_StallWatch and
    test_q71_main_thread_block_under_200ms. Offscreen QTimer is not a
    reliable heartbeat (nested loops may not deliver timeout).

    ``note_sync_block`` records command_project_open wall time but does
    NOT fold it into max_gap_ms — that was Q54 mixing 「慢」 into 「卡」.
    """

    def __init__(self):
        self.gaps_ms: list[float] = []
        self.max_gap_ms = 0.0
        self.slow = []
        self._last = 0.0
        self._last_ev = None
        self._filter = None
        self._app = None
        self._sync_blocks: list[tuple[str, float]] = []
        self.paint_ms: dict[str, list[float]] = {
            "TimelineWidget": [],
            "TrackHeaderPanel": [],
        }

    def start(self, app) -> None:
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
                if gap > 8.0:
                    watch.gaps_ms.append(gap)
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

    def note_sync_block(self, label: str, ms: float) -> None:
        self._sync_blocks.append((label, ms))

    def stop(self) -> None:
        now = time.perf_counter()
        gap = (now - self._last) * 1000.0
        if gap > self.max_gap_ms:
            self.max_gap_ms = gap
        if gap > 8.0:
            self.gaps_ms.append(gap)
        if self._app is not None and self._filter is not None:
            self._app.removeEventFilter(self._filter)

    def report(self) -> dict:
        gaps = sorted(self.gaps_ms, reverse=True)
        paint = {}
        for name, rows in self.paint_ms.items():
            paint[name] = {
                "n": len(rows),
                "max_ms": round(max(rows), 3) if rows else 0.0,
                "sum_ms": round(sum(rows), 3),
            }
        return {
            "max_gap_ms": round(self.max_gap_ms, 3),
            "n_gaps_over_8ms": len(self.gaps_ms),
            "top5_gaps_ms": [round(g, 3) for g in gaps[:5]],
            "sync_blocks": [
                {"label": lab, "ms": round(ms, 3)} for lab, ms in self._sync_blocks
            ],
            "paint": paint,
            "method": "QApplication.eventFilter",
        }


def list_clip_videos() -> list[str]:
    files = sorted(glob.glob(os.path.join(G19_MEDIA, "*.mp4")))
    if not files and os.path.isfile(G01_VIDEO):
        files = [G01_VIDEO]
    return files


def build_n_clip_project(n: int = 32, dest: str | None = None) -> str:
    """One task, N video clips (and matching audio clips) on the timeline.

    Cycles g19 stitch media. Duration is taken from the project JSON so the
    fixture itself does not spawn ffprobe.
    """
    files = list_clip_videos()
    if not files:
        raise FileNotFoundError("no golden mp4 under tests/golden/_media")
    n = max(int(n), 1)
    sources = {}
    vclips = []
    aclips = []
    t = 0.0
    clip_len = 2.0
    src_dur = 4.0
    for i in range(n):
        path = os.path.abspath(files[i % len(files)])
        sid = uuid.uuid4().hex
        sources[sid] = {
            "id": sid,
            "path": path,
            "duration": src_dur,
            "has_video": True,
            "has_audio": True,
        }
        vclips.append({
            "source_id": sid,
            "in_t": 0.0,
            "out_t": clip_len,
            "start_t": t,
            "fit_mode": "fill",
        })
        aclips.append({
            "source_id": sid,
            "in_t": 0.0,
            "out_t": clip_len,
            "start_t": t,
            "is_main": True,
            "audio_role": "main",
        })
        t += clip_len
    first = os.path.abspath(files[0])
    data = {
        "version": 2,
        "app": "CUTORA",
        "tasks": [{
            "name": "q54-%d-clips" % n,
            "video": first,
            "path": first,
            "text": "",
            "segments": [],
            "style": {},
            "duration": t,
            "text_overlays": [],
            "image_overlays": [],
            "audio_clips": [],
            "timeline": {
                "width": 1080,
                "height": 1920,
                "fps": 30.0,
                "sources": sources,
                "tracks": [
                    {
                        "type": "video",
                        "is_main_video": True,
                        "domain": "visual",
                        "visible": True,
                        "locked": False,
                        "clips": vclips,
                        "transitions": [],
                    },
                    {
                        "type": "audio",
                        "domain": "audio",
                        "visible": True,
                        "locked": False,
                        "clips": aclips,
                        "transitions": [],
                    },
                ],
            },
        }],
    }
    if dest is None:
        fd, dest = tempfile.mkstemp(prefix="q54_%dclips_" % n, suffix=".asproj.json")
        os.close(fd)
    os.makedirs(os.path.dirname(os.path.abspath(dest)) or ".", exist_ok=True)
    with open(dest, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    return dest


def _silence_win(win) -> None:
    from PySide6.QtWidgets import QMessageBox
    QMessageBox.information = staticmethod(lambda *a, **k: None)
    QMessageBox.warning = staticmethod(lambda *a, **k: None)
    QMessageBox.question = staticmethod(
        lambda *a, **k: QMessageBox.StandardButton.No)
    QMessageBox.critical = staticmethod(lambda *a, **k: None)
    win._suppress_close_prompt = True
    win._prompt_canvas_if_sources_mismatch = lambda *a, **k: None
    win._offer_autosave_restore = lambda *a, **k: None


def _is_ffprobe(cmd) -> bool:
    if not cmd:
        return False
    exe = str(cmd[0] if isinstance(cmd, (list, tuple)) else cmd)
    return "ffprobe" in os.path.basename(exe).lower()


def _is_ffmpeg(cmd) -> bool:
    if not cmd:
        return False
    exe = str(cmd[0] if isinstance(cmd, (list, tuple)) else cmd)
    return "ffmpeg" in os.path.basename(exe).lower() and "ffprobe" not in os.path.basename(exe).lower()


def install_hooks(clock: BucketClock) -> dict:
    """Patch product functions. Returns a dict of extra counters."""
    extra = {
        "ffprobe_calls": [],          # list of {path, ms, thread}
        "ffmpeg_calls": 0,
        "subprocess_other": 0,
        "probe_duration_calls": 0,
        "inspect_path_calls": 0,
        "classify_animated_calls": 0,
        "nb_frames_calls": 0,
        "ensure_pack_thumb_calls": 0,
        "ensure_pack_thumb_ms": 0.0,
        "extract_frame_calls": 0,
        "request_clip_thumb_calls": 0,
        "library_additem_calls": 0,
        "library_additem_ms": 0.0,
        "cv_capture_calls": 0,
        "cv_capture_ms": 0.0,
        "set_data_ms": 0.0,
        "present_frame_ms": 0.0,
        "migrate_ms": 0.0,
        "normalize_ms": 0.0,
        "apply_ms": 0.0,
        "open_ms": 0.0,
        "window_construct_ms": 0.0,
        "pump_ms": 0.0,
        "paint_timeline_ms": [],
        "paint_header_ms": [],
    }
    main_ident = threading.get_ident()

    import subprocess as sp
    orig_run = sp.run

    def run_wrap(cmd, *a, **k):
        path = ""
        if isinstance(cmd, (list, tuple)) and len(cmd) >= 2:
            path = str(cmd[-1])
        t0 = time.perf_counter()
        is_probe = _is_ffprobe(cmd)
        bucket = "media_probe" if is_probe else ("thumbs" if _is_ffmpeg(cmd) else None)
        if bucket:
            clock.enter(bucket)
        try:
            return orig_run(cmd, *a, **k)
        finally:
            ms = (time.perf_counter() - t0) * 1000.0
            if bucket:
                clock.leave(bucket)
            if is_probe:
                extra["ffprobe_calls"].append({
                    "path": path,
                    "ms": round(ms, 3),
                    "main_thread": threading.get_ident() == main_ident,
                })
            elif _is_ffmpeg(cmd):
                extra["ffmpeg_calls"] += 1
            else:
                extra["subprocess_other"] += 1

    sp.run = run_wrap
    try:
        import cutora.core.proc as proc
        orig_proc_run = proc.run

        def proc_run_wrap(cmd, *a, **k):
            return run_wrap(cmd, *a, **k)

        proc.run = proc_run_wrap
        extra["_orig_proc_run"] = orig_proc_run
    except Exception:
        extra["_orig_proc_run"] = None
    extra["_orig_sp_run"] = orig_run

    from cutora.model import timeline_model, offline
    extra["_orig_probe"] = timeline_model.probe_duration_sec
    extra["probe_duration_calls"] = 0

    def probe_wrap(media_path):
        extra["probe_duration_calls"] += 1
        return clock.wrap("media_probe", extra["_orig_probe"])(media_path)

    timeline_model.probe_duration_sec = probe_wrap
    extra["_orig_offline_probe"] = offline.probe_duration_sec
    offline.probe_duration_sec = probe_wrap
    try:
        from cutora.render import readers
        extra["_orig_readers_probe"] = readers.probe_duration_sec

        def readers_probe_wrap(media_path):
            extra["probe_duration_calls"] += 1
            return clock.wrap("media_probe", extra["_orig_readers_probe"])(media_path)

        readers.probe_duration_sec = readers_probe_wrap
        import cutora.ui.main_window as mw
        extra["_orig_mw_probe"] = getattr(mw, "probe_duration_sec", None)
        if extra["_orig_mw_probe"] is extra["_orig_readers_probe"]:
            mw.probe_duration_sec = readers_probe_wrap
    except Exception:
        extra["_orig_readers_probe"] = None
        extra["_orig_mw_probe"] = None

    from cutora.model import offline
    extra["_orig_inspect"] = offline.inspect_path

    def inspect_wrap(path, duration_json=None):
        extra["inspect_path_calls"] += 1
        return clock.wrap("media_probe", extra["_orig_inspect"])(path, duration_json)

    offline.inspect_path = inspect_wrap

    from cutora.media import animated
    extra["_orig_classify"] = animated.classify_animated
    extra["_orig_nb"] = animated._nb_frames

    def classify_wrap(path):
        extra["classify_animated_calls"] += 1
        return clock.wrap("media_probe", extra["_orig_classify"])(path)

    def nb_wrap(path):
        extra["nb_frames_calls"] += 1
        return clock.wrap("media_probe", extra["_orig_nb"])(path)

    animated.classify_animated = classify_wrap
    animated._nb_frames = nb_wrap

    from cutora.subtitle import stylepack
    extra["_orig_pack"] = stylepack.ensure_pack_thumb

    def pack_wrap(path, *a, **k):
        extra["ensure_pack_thumb_calls"] += 1
        t0 = time.perf_counter()
        try:
            return clock.wrap("thumbs", extra["_orig_pack"])(path, *a, **k)
        finally:
            extra["ensure_pack_thumb_ms"] += (time.perf_counter() - t0) * 1000.0

    stylepack.ensure_pack_thumb = pack_wrap

    from cutora.ui.timeline import thumbs as tl_thumbs
    extra["_orig_extract"] = tl_thumbs.extract_frame
    extra["_orig_request"] = tl_thumbs.request_clip_thumb

    def extract_wrap(*a, **k):
        extra["extract_frame_calls"] += 1
        return clock.wrap("thumbs", extra["_orig_extract"])(*a, **k)

    def request_wrap(*a, **k):
        extra["request_clip_thumb_calls"] += 1
        return clock.wrap("thumbs", extra["_orig_request"])(*a, **k)

    tl_thumbs.extract_frame = extract_wrap
    tl_thumbs.request_clip_thumb = request_wrap

    from cutora.ui.panels import media_list as ml
    extra["_orig_add"] = ml.DraggableListWidget.addItem

    def add_wrap(self, name, filepath=""):
        extra["library_additem_calls"] += 1
        t0 = time.perf_counter()
        # Library thumbs decode a frame with OpenCV on the calling thread.
        clock.enter("thumbs")
        try:
            return extra["_orig_add"](self, name, filepath)
        finally:
            clock.leave("thumbs")
            extra["library_additem_ms"] += (time.perf_counter() - t0) * 1000.0

    ml.DraggableListWidget.addItem = add_wrap

    try:
        from cutora.render import readers
        extra["_orig_cap"] = readers._open_video_capture

        def cap_wrap(*a, **k):
            extra["cv_capture_calls"] += 1
            t0 = time.perf_counter()
            # Stay in the caller's bucket (library thumbs vs first-frame paint).
            already = bool(clock._stack)
            if not already:
                clock.enter("first_frame")
            try:
                return extra["_orig_cap"](*a, **k)
            finally:
                if not already:
                    clock.leave("first_frame")
                extra["cv_capture_ms"] += (time.perf_counter() - t0) * 1000.0

        readers._open_video_capture = cap_wrap
        if getattr(ml, "_open_video_capture", None) is extra["_orig_cap"]:
            ml._open_video_capture = cap_wrap
            extra["_patched_ml_cap"] = True
    except Exception:
        extra["_orig_cap"] = None
        extra["_patched_ml_cap"] = False

    extra["_orig_json_load"] = json.load
    extra["_orig_open"] = open  # not wrapping builtin open globally — too noisy

    from cutora.ui.timeline.widget import TimelineWidget
    from cutora.ui.timeline.header import TrackHeaderPanel
    extra["_orig_tl_paint"] = TimelineWidget.paintEvent
    extra["_orig_hd_paint"] = TrackHeaderPanel.paintEvent

    def tl_paint(self, event):
        t0 = time.perf_counter()
        try:
            return extra["_orig_tl_paint"](self, event)
        finally:
            extra["paint_timeline_ms"].append((time.perf_counter() - t0) * 1000.0)

    def hd_paint(self, event):
        t0 = time.perf_counter()
        try:
            return extra["_orig_hd_paint"](self, event)
        finally:
            extra["paint_header_ms"].append((time.perf_counter() - t0) * 1000.0)

    TimelineWidget.paintEvent = tl_paint
    TrackHeaderPanel.paintEvent = hd_paint

    return extra


def restore_hooks(extra: dict) -> None:
    import subprocess as sp
    if extra.get("_orig_sp_run"):
        sp.run = extra["_orig_sp_run"]
    if extra.get("_orig_proc_run"):
        import cutora.core.proc as proc
        proc.run = extra["_orig_proc_run"]
    from cutora.model import timeline_model, offline
    if extra.get("_orig_probe"):
        timeline_model.probe_duration_sec = extra["_orig_probe"]
    if extra.get("_orig_offline_probe"):
        offline.probe_duration_sec = extra["_orig_offline_probe"]
    if extra.get("_orig_inspect"):
        offline.inspect_path = extra["_orig_inspect"]
    if extra.get("_orig_readers_probe"):
        from cutora.render import readers
        readers.probe_duration_sec = extra["_orig_readers_probe"]
        if extra.get("_orig_mw_probe") is extra["_orig_readers_probe"]:
            import cutora.ui.main_window as mw
            mw.probe_duration_sec = extra["_orig_mw_probe"]
    from cutora.media import animated
    if extra.get("_orig_classify"):
        animated.classify_animated = extra["_orig_classify"]
    if extra.get("_orig_nb"):
        animated._nb_frames = extra["_orig_nb"]
    from cutora.subtitle import stylepack
    if extra.get("_orig_pack"):
        stylepack.ensure_pack_thumb = extra["_orig_pack"]
    from cutora.ui.timeline import thumbs as tl_thumbs
    if extra.get("_orig_extract"):
        tl_thumbs.extract_frame = extra["_orig_extract"]
    if extra.get("_orig_request"):
        tl_thumbs.request_clip_thumb = extra["_orig_request"]
    from cutora.ui.panels import media_list as ml
    if extra.get("_orig_add"):
        ml.DraggableListWidget.addItem = extra["_orig_add"]
    if extra.get("_orig_cap"):
        from cutora.render import readers
        readers._open_video_capture = extra["_orig_cap"]
        if extra.get("_patched_ml_cap"):
            ml._open_video_capture = extra["_orig_cap"]
    if extra.get("_orig_tl_paint"):
        from cutora.ui.timeline.widget import TimelineWidget
        TimelineWidget.paintEvent = extra["_orig_tl_paint"]
    if extra.get("_orig_hd_paint"):
        from cutora.ui.timeline.header import TrackHeaderPanel
        TrackHeaderPanel.paintEvent = extra["_orig_hd_paint"]


def wrap_open_path(win, clock: BucketClock, extra: dict) -> None:
    from cutora.model import project_io
    from cutora.subtitle import text_layer
    from cutora.ui.timeline.widget import TimelineWidget

    orig_norm = project_io._normalize_project_data
    orig_mig = text_layer.migrate_open_project
    orig_apply = win.apply_project_data
    orig_set = TimelineWidget.set_data
    orig_present = getattr(win, "_present_preview_frame", None)
    orig_cmd = win.command_project_open

    def norm_wrap(data):
        t0 = time.perf_counter()
        try:
            return clock.wrap("model_build", orig_norm)(data)
        finally:
            extra["normalize_ms"] += (time.perf_counter() - t0) * 1000.0

    def mig_wrap(data):
        t0 = time.perf_counter()
        try:
            return clock.wrap("model_build", orig_mig)(data)
        finally:
            extra["migrate_ms"] += (time.perf_counter() - t0) * 1000.0

    def apply_wrap(data):
        t0 = time.perf_counter()
        try:
            return clock.wrap("model_build", orig_apply)(data)
        finally:
            extra["apply_ms"] += (time.perf_counter() - t0) * 1000.0

    def set_wrap(self, *a, **k):
        t0 = time.perf_counter()
        try:
            return clock.wrap("qwidget_build", orig_set)(self, *a, **k)
        finally:
            extra["set_data_ms"] += (time.perf_counter() - t0) * 1000.0

    def present_wrap(*a, **k):
        t0 = time.perf_counter()
        try:
            return clock.wrap("first_frame", orig_present)(*a, **k)
        finally:
            extra["present_frame_ms"] += (time.perf_counter() - t0) * 1000.0

    def cmd_wrap(path):
        # Split disk vs json inside the original body by wrapping open+load.
        orig_load = json.load

        def load_wrap(fh, *a, **k):
            clock.enter("disk_read")
            raw = fh.read()
            clock.leave("disk_read")
            clock.enter("json_parse")
            try:
                return json.loads(raw)
            finally:
                clock.leave("json_parse")

        json.load = load_wrap
        t0 = time.perf_counter()
        try:
            return orig_cmd(path)
        finally:
            extra["open_ms"] += (time.perf_counter() - t0) * 1000.0
            json.load = orig_load

    project_io._normalize_project_data = norm_wrap
    text_layer.migrate_open_project = mig_wrap
    win.apply_project_data = apply_wrap
    TimelineWidget.set_data = set_wrap
    if callable(orig_present):
        win._present_preview_frame = present_wrap
    win.command_project_open = cmd_wrap
    def _unwrap_open():
        project_io._normalize_project_data = orig_norm
        text_layer.migrate_open_project = orig_mig
        win.apply_project_data = orig_apply
        TimelineWidget.set_data = orig_set
        if orig_present is not None:
            win._present_preview_frame = orig_present
        win.command_project_open = orig_cmd

    extra["_unwrap_open"] = _unwrap_open


def _ffprobe_by_file(calls: list[dict]) -> dict:
    by = {}
    for rec in calls:
        key = os.path.normcase(os.path.abspath(rec.get("path") or "")) or "(none)"
        slot = by.setdefault(key, {"n": 0, "ms": 0.0, "main": 0})
        slot["n"] += 1
        slot["ms"] += float(rec.get("ms") or 0)
        if rec.get("main_thread"):
            slot["main"] += 1
    return {
        "unique_files": len(by),
        "total_calls": len(calls),
        "max_calls_on_one_file": max((v["n"] for v in by.values()), default=0),
        "files_probed_more_than_once": sum(1 for v in by.values() if v["n"] > 1),
        "total_ms": round(sum(v["ms"] for v in by.values()), 3),
        "top": sorted(
            (
                {
                    "path": os.path.basename(k),
                    "n": v["n"],
                    "ms": round(v["ms"], 3),
                    "main": v["main"],
                }
                for k, v in by.items()
            ),
            key=lambda r: (-r["n"], -r["ms"]),
        )[:8],
    }


def _cprofile_top(pr: cProfile.Profile, n: int = 20) -> list[dict]:
    buf = io.StringIO()
    stats = pstats.Stats(pr, stream=buf)
    stats.sort_stats("cumulative")
    rows = []
    for func, (cc, nc, tt, ct, callers) in list(stats.stats.items()):
        filename, line, name = func
        rows.append({
            "file": os.path.basename(str(filename)),
            "line": int(line),
            "func": str(name),
            "ncalls": int(nc),
            "tottime_ms": round(tt * 1000.0, 3),
            "cumtime_ms": round(ct * 1000.0, 3),
        })
    rows.sort(key=lambda r: -r["cumtime_ms"])
    return rows[:n]


def pump(app, ms: int) -> None:
    from PySide6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    QTimer.singleShot(int(ms), loop.quit)
    loop.exec()
    app.processEvents()


def run_one(app, win, path: str, clock: BucketClock, extra: dict,
            stall: StallWatch, pump_ms: int = 400) -> dict:
    wrap_open_path(win, clock, extra)
    pr = cProfile.Profile()
    t0 = time.perf_counter()
    pr.enable()
    win.command_project_open(path=path)
    pr.disable()
    open_ms = (time.perf_counter() - t0) * 1000.0
    stall.note_sync_block("command_project_open", open_ms)
    t1 = time.perf_counter()
    pump(app, pump_ms)
    extra["pump_ms"] = (time.perf_counter() - t1) * 1000.0
    n_tasks = len(getattr(win, "tasks", None) or [])
    n_clips = 0
    n_sources = 0
    n_segs = 0
    for task in getattr(win, "tasks", None) or []:
        n_segs += len(task.get("segments") or [])
        tl = task.get("timeline")
        if tl is None:
            continue
        srcs = getattr(tl, "sources", None) or {}
        n_sources += len(srcs)
        for tr in getattr(tl, "tracks", None) or []:
            n_clips += len(getattr(tr, "clips", None) or [])
    snap = clock.snapshot()
    accounted = sum(snap["ms"].values())
    belong = set()
    try:
        from cutora.media.probe_cache import collect_project_media_paths
        pdata = json.loads(open(path, encoding="utf-8").read() or "{}")
        belong = {
            os.path.normcase(os.path.abspath(p))
            for p in collect_project_media_paths(pdata)
        }
    except Exception:
        belong = set()
    n_strangers = 0
    if belong:
        seen_s = set()
        for call in extra["ffprobe_calls"]:
            p = os.path.normcase(os.path.abspath(call.get("path") or ""))
            if p and p not in belong and p not in seen_s:
                seen_s.add(p)
                n_strangers += 1
    rec = {
        "path": path,
        "open_ms": round(open_ms, 3),
        "pump_ms": round(extra["pump_ms"], 3),
        "n_tasks": n_tasks,
        "n_sources": n_sources,
        "n_clips": n_clips,
        "n_segments": n_segs,
        "buckets_ms": snap["ms"],
        "bucket_calls": snap["calls"],
        "accounted_ms": round(accounted, 3),
        "unaccounted_in_open_ms": round(max(0.0, open_ms - accounted), 3),
        "ffprobe": _ffprobe_by_file(extra["ffprobe_calls"]),
        "counters": {
            "probe_duration_calls": extra["probe_duration_calls"],
            "inspect_path_calls": extra["inspect_path_calls"],
            "classify_animated_calls": extra["classify_animated_calls"],
            "nb_frames_calls": extra["nb_frames_calls"],
            "ensure_pack_thumb_calls": extra["ensure_pack_thumb_calls"],
            "ensure_pack_thumb_ms": round(extra["ensure_pack_thumb_ms"], 3),
            "extract_frame_calls": extra["extract_frame_calls"],
            "request_clip_thumb_calls": extra["request_clip_thumb_calls"],
            "library_additem_calls": extra["library_additem_calls"],
            "library_additem_ms": round(extra["library_additem_ms"], 3),
            "cv_capture_calls": extra["cv_capture_calls"],
            "cv_capture_ms": round(extra["cv_capture_ms"], 3),
            "ffmpeg_calls": extra["ffmpeg_calls"],
            "set_data_ms": round(extra["set_data_ms"], 3),
            "present_frame_ms": round(extra["present_frame_ms"], 3),
            "migrate_ms": round(extra["migrate_ms"], 3),
            "normalize_ms": round(extra["normalize_ms"], 3),
            "apply_ms": round(extra["apply_ms"], 3),
        },
        "cprofile_top": _cprofile_top(pr, 18),
        "ffprobe_not_in_project": n_strangers,
        "stall": stall.report(),
        "paint": {
            "timeline_n": len(extra.get("paint_timeline_ms") or []),
            "timeline_max_ms": round(max(extra["paint_timeline_ms"]), 3) if extra.get("paint_timeline_ms") else 0.0,
            "header_n": len(extra.get("paint_header_ms") or []),
            "header_max_ms": round(max(extra["paint_header_ms"]), 3) if extra.get("paint_header_ms") else 0.0,
        },
        "cache": extra.get("cache_kind") or "cold",
    }
    unwrap = extra.get("_unwrap_open")
    if callable(unwrap):
        unwrap()
    return rec


def _md_table(buckets: dict) -> str:
    order = list(BUCKETS)
    total = sum(buckets.get(k, 0) for k in order) or 1.0
    lines = [
        "| 分段 | ms | 占分段合计 |",
        "|---|---:|---:|",
    ]
    for k in order:
        v = float(buckets.get(k) or 0)
        lines.append("| `%s` | %.1f | %.1f%% |" % (k, v, 100.0 * v / total))
    lines.append("| **合计（已归因）** | **%.1f** | 100%% |" % sum(
        float(buckets.get(k) or 0) for k in order))
    return "\n".join(lines)


def render_markdown(label: str, runs: list[dict], meta: dict) -> str:
    lines = [
        "# Q71 · 打开工程分段画像",
        "",
        "量的是 **`command_project_open` 墙钟**（窗口已建好之后），不是启动壳、不是 venv。",
        "「卡」= 主线程单次最长阻塞（**QApplication eventFilter**，与 "
        "`test_q71_main_thread_block_under_200ms` 同一量法）；",
        "「慢」= 打开总时长。两件事分开记。整段打开墙钟**不再**记进 max_gap。",
        "",
        "- 机器：Windows，offscreen Qt（`QT_QPA_PLATFORM=offscreen`）",
        "- 标签：`%s`" % label,
        "- 窗口构建（不含打开）：**%.1f ms**（单独记，不计入打开时间）" % meta.get(
            "window_construct_ms", 0),
        "- 卡：eventFilter 相邻事件间隔；门禁 200 ms",
        "",
    ]
    for run in runs:
        lines.append("## %s" % run.get("title", run.get("path")))
        lines.append("")
        lines.append("- 工程：`%s`" % run.get("path"))
        lines.append("- 任务数 %s · 源 %s · 片段 %s · 字幕句 %s" % (
            run.get("n_tasks"), run.get("n_sources"),
            run.get("n_clips"), run.get("n_segments")))
        lines.append("- 缓存：`%s`" % run.get("cache", "cold"))
        lines.append("- **打开总时长 `command_project_open`：%.1f ms**" % run["open_ms"])
        lines.append("- 打开后泵事件 %.1f ms（首帧 / 异步缩略图有机会跑）" % run["pump_ms"])
        lines.append("- **主线程单次最长阻塞（eventFilter）：%.1f ms**" % run["stall"]["max_gap_ms"])
        lines.append("- 未归因（打开墙钟 − 分段合计）：%.1f ms" % run["unaccounted_in_open_ms"])
        lines.append("")
        lines.append("### 分段")
        lines.append("")
        lines.append(_md_table(run["buckets_ms"]))
        lines.append("")
        lines.append("### 线索计数")
        lines.append("")
        c = run["counters"]
        fp = run["ffprobe"]
        lines.append("- ffprobe：%s 次、%s 个文件、同一文件最多 %s 次、主线程合计 %.1f ms" % (
            fp["total_calls"], fp["unique_files"],
            fp["max_calls_on_one_file"], fp["total_ms"]))
        if run.get("ffprobe_not_in_project"):
            lines.append("- **工程里没有的文件被探测 %s 个**（不应发生）" % run["ffprobe_not_in_project"])
        if fp.get("files_probed_more_than_once"):
            lines.append("- **有 %s 个文件被探测超过一次**" % fp["files_probed_more_than_once"])
        for row in fp.get("top") or []:
            lines.append("  - `%s` ×%s  %.1f ms" % (row["path"], row["n"], row["ms"]))
        lines.append("- `probe_duration_sec` %s 次 · `inspect_path` %s 次" % (
            c["probe_duration_calls"], c["inspect_path_calls"]))
        lines.append("- `classify_animated` %s · `_nb_frames` %s（打开工程不应走动画导入）" % (
            c["classify_animated_calls"], c["nb_frames_calls"]))
        lines.append("- `ensure_pack_thumb` %s 次 / %.1f ms（应在窗口构建，不在打开）" % (
            c["ensure_pack_thumb_calls"], c["ensure_pack_thumb_ms"]))
        lines.append("- 媒体库 `addItem` %s 次 / %.1f ms（同步 OpenCV 读首帧）" % (
            c["library_additem_calls"], c["library_additem_ms"]))
        lines.append("- 时间线 `request_clip_thumb` %s · `extract_frame` %s" % (
            c["request_clip_thumb_calls"], c["extract_frame_calls"]))
        lines.append("- `_open_video_capture` %s 次 / %.1f ms" % (
            c["cv_capture_calls"], c["cv_capture_ms"]))
        lines.append("- `timeline.set_data` %.1f ms · `_present_preview_frame` %.1f ms" % (
            c["set_data_ms"], c["present_frame_ms"]))
        lines.append("- `migrate_open_project` %.1f ms · `_normalize_project_data` %.1f ms · `apply_project_data` %.1f ms" % (
            c["migrate_ms"], c["normalize_ms"], c["apply_ms"]))
        paint = run.get("paint") or {}
        lines.append("- paint TimelineWidget n=%s max=%.1f ms · TrackHeaderPanel n=%s max=%.1f ms" % (
            paint.get("timeline_n", 0), paint.get("timeline_max_ms", 0),
            paint.get("header_n", 0), paint.get("header_max_ms", 0)))
        lines.append("")
        lines.append("### 主线程阻塞")
        lines.append("")
        lines.append("- 量法：`%s`" % (run["stall"].get("method") or "eventFilter"))
        lines.append("- 最长 %.1f ms；>8ms 的事件间隔 %s 个" % (
            run["stall"]["max_gap_ms"], run["stall"]["n_gaps_over_8ms"]))
        for b in run["stall"].get("sync_blocks") or []:
            lines.append("- 打开墙钟 `%s`：%.1f ms（不计入卡）" % (b["label"], b["ms"]))
        if run["stall"].get("top5_gaps_ms"):
            lines.append("- 事件间隔 top5：%s" % ", ".join(
                "%.1f" % g for g in run["stall"]["top5_gaps_ms"]))
        lines.append("")
        lines.append("### cProfile cumulative top")
        lines.append("")
        lines.append("| 文件 | 行 | 函数 | ncalls | tottime ms | cumtime ms |")
        lines.append("|---|---:|---|---:|---:|---:|")
        for r in run.get("cprofile_top") or []:
            lines.append("| `%s` | %s | `%s` | %s | %.1f | %.1f |" % (
                r["file"], r["line"], r["func"], r["ncalls"],
                r["tottime_ms"], r["cumtime_ms"]))
        lines.append("")
    lines.append("## 慢 vs 卡")
    lines.append("")
    lines.append("- **慢**看上面的打开总时长。")
    lines.append("- **卡**看 eventFilter 单次最长间隔，门禁 200 ms。")
    lines.append("- 打开进度：「探测媒体 N/M」。")
    lines.append("")
    return "\n".join(lines) + "\n"


def _merge_json(label: str, payload: dict) -> dict:
    data = {}
    if os.path.isfile(REPORT_JSON):
        try:
            data = json.load(open(REPORT_JSON, encoding="utf-8"))
        except Exception:
            data = {}
    data[label] = payload
    return data


def _reset_run_counters(clock: BucketClock, extra: dict) -> None:
    clock.ms = {k: 0.0 for k in BUCKETS}
    clock.calls = {k: 0 for k in BUCKETS}
    extra["ffprobe_calls"] = []
    extra["paint_timeline_ms"] = []
    extra["paint_header_ms"] = []
    for key in (
        "probe_duration_calls", "inspect_path_calls",
        "classify_animated_calls", "nb_frames_calls",
        "ensure_pack_thumb_calls", "extract_frame_calls",
        "request_clip_thumb_calls", "library_additem_calls",
        "cv_capture_calls", "ffmpeg_calls",
    ):
        extra[key] = 0
    for key in (
        "ensure_pack_thumb_ms", "library_additem_ms", "cv_capture_ms",
        "set_data_ms", "present_frame_ms", "migrate_ms",
        "normalize_ms", "apply_ms", "open_ms",
    ):
        extra[key] = 0.0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="before",
                        help="before / after / after_<change>")
    parser.add_argument("--clips", type=int, default=32)
    parser.add_argument("--skip-luca", action="store_true")
    parser.add_argument("--project", default="",
                        help="optional extra .asproj.json")
    parser.add_argument("--pump-ms", type=int, default=400)
    parser.add_argument("--skip-hot", action="store_true")
    args = parser.parse_args()

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")
    ud = tempfile.mkdtemp(prefix="cutora_q71_ud_")
    os.environ["CUTORA_USER_DATA"] = ud

    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    from cutora.core.paths import ensure_user_layout, refresh_cached_paths
    refresh_cached_paths()
    ensure_user_layout()
    from cutora.app import apply_ui_locale
    apply_ui_locale()

    from PySide6.QtWidgets import QApplication, QStyleFactory
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.theme.apply import apply_app_qss
    from cutora.ui.theme.fonts_ui import register_ui_fonts, ui_qfont
    from cutora.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication(["q71-profile-open"])
    load_embedded_fonts()
    register_ui_fonts()
    app.setFont(ui_qfont())
    app.setStyle(QStyleFactory.create("Fusion"))
    apply_app_qss(app)

    def _new_window():
        t_win = time.perf_counter()
        win = MainWindow()
        ms = (time.perf_counter() - t_win) * 1000.0
        _silence_win(win)
        win.show()
        pump(app, 80)
        win.show_editor()
        pump(app, 40)
        return win, ms

    def _run_cold_hot(win, path, title, clock, extra, pump_ms):
        out = []
        _reset_run_counters(clock, extra)
        extra["cache_kind"] = "cold"
        stall = StallWatch()
        stall.start(app)
        rec = run_one(app, win, path, clock, extra, stall, pump_ms=pump_ms)
        stall.stop()
        rec["title"] = title + " · 冷缓存"
        rec["cache"] = "cold"
        out.append(rec)
        if args.skip_hot:
            return out
        _reset_run_counters(clock, extra)
        extra["cache_kind"] = "hot"
        stall_h = StallWatch()
        stall_h.start(app)
        rec_h = run_one(app, win, path, clock, extra, stall_h, pump_ms=pump_ms)
        stall_h.stop()
        rec_h["title"] = title + " · 热缓存"
        rec_h["cache"] = "hot"
        out.append(rec_h)
        return out

    clock = BucketClock()
    extra = install_hooks(clock)
    runs = []
    window_ms = 0.0

    synth = build_n_clip_project(args.clips)
    win, window_ms = _new_window()
    extra["window_construct_ms"] = window_ms
    runs.extend(_run_cold_hot(
        win, synth, "合成 %d 片段工程（g19 媒体循环）" % args.clips,
        clock, extra, args.pump_ms))
    win.close()

    if not args.skip_luca and os.path.isfile(LUCA_PROJECT):
        # Fresh window so leftover 32-clip thumbs/waveforms cannot
        # ffprobe t0035 files that are not in Luca's project.
        win2, w2 = _new_window()
        window_ms += w2
        runs.extend(_run_cold_hot(
            win2, LUCA_PROJECT,
            "Luca 最近工程（3 条拼接成片，各 ~140 句字幕）",
            clock, extra, args.pump_ms))
        win2.close()

    if args.project and os.path.isfile(args.project):
        win3, w3 = _new_window()
        window_ms += w3
        runs.extend(_run_cold_hot(
            win3, args.project, "额外工程", clock, extra, args.pump_ms))
        win3.close()

    restore_hooks(extra)

    payload = {
        "label": args.label,
        "window_construct_ms": round(window_ms, 3),
        "user_data": ud,
        "runs": runs,
    }
    merged = _merge_json(args.label, payload)
    os.makedirs(os.path.dirname(REPORT_JSON), exist_ok=True)
    with open(REPORT_JSON, "w", encoding="utf-8") as fh:
        json.dump(merged, fh, ensure_ascii=False, indent=2)

    md = ""
    if "before" in merged:
        md += render_markdown("before", merged["before"]["runs"], merged["before"])
        md += "---\n\n"
    if args.label != "before" and args.label in merged:
        md += render_markdown(args.label, merged[args.label]["runs"], merged[args.label])
    if not md:
        md = render_markdown(args.label, runs, payload)
    # Keep a single file with every label that has been measured.
    parts = []
    for lab in ("before",) + tuple(k for k in merged if k != "before"):
        if lab in merged:
            parts.append(render_markdown(lab, merged[lab]["runs"], merged[lab]))
    md = "\n---\n\n".join(parts)
    with open(REPORT_MD, "w", encoding="utf-8") as fh:
        fh.write(md)

    print(json.dumps({
        "label": args.label,
        "window_construct_ms": round(window_ms, 3),
        "runs": [
            {
                "title": r["title"],
                "open_ms": r["open_ms"],
                "max_block_ms": r["stall"]["max_gap_ms"],
                "buckets_ms": r["buckets_ms"],
                "cache": r.get("cache"),
                "ffprobe_calls": r["ffprobe"]["total_calls"],
                "ffprobe_max_per_file": r["ffprobe"]["max_calls_on_one_file"],
                "ffprobe_not_in_project": r.get("ffprobe_not_in_project", 0),
            }
            for r in runs
        ],
        "report": REPORT_MD,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

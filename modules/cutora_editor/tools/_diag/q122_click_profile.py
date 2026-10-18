#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q122 step 0: profile a click on one video, and count full UI refreshes.

Measures the longest continuous main-thread stall while selecting a video,
and groups time spent inside that click by call site:

  canvas_first_frame / style_gallery / property_panel / other

Also counts how many times one user action calls refresh_ui,
timeline.set_data and video_canvas.update (library import of 5 files,
and a click).

Usage:
    runtime\\venv\\Scripts\\python.exe tools/_diag/q122_click_profile.py
    runtime\\venv\\Scripts\\python.exe tools/_diag/q122_click_profile.py --label after
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
import traceback

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

G01 = os.path.join(REPO, "tests", "golden", "_media", "g01", "video.mp4")
REPORT_DIR = os.path.join(REPO, "docs", "plan", "reports")
STALL_MS = 50.0


def _classify(stack: str) -> str:
    s = stack.replace("\\", "/")
    if any(tok in s for tok in (
        "canvas.py", "_present_preview_frame", "_get_cv_frame_for_preview",
        "_draw_preview_local", "frame_at", "AsyncVideoDecoder",
        "_open_video_capture", "grab_video_frame",
    )):
        # grab_video_frame is the gallery's frame grab; keep it on gallery
        # when the stack is inside the gallery, not the canvas.
        if "style_gallery.py" in s or "style_thumbs.py" in s:
            return "style_gallery"
        return "canvas_first_frame"
    if "style_gallery.py" in s or "style_thumbs.py" in s or "stylepack.py" in s:
        return "style_gallery"
    if "property.py" in s or "subtitle_style.py" in s or "role_mapping.py" in s:
        return "property_panel"
    return "other"


class StallWatch:
    def __init__(self):
        self.max_gap_ms = 0.0
        self.block_total_ms = 0.0
        self.gaps = []
        self.by_group = {
            "canvas_first_frame": 0.0,
            "style_gallery": 0.0,
            "property_panel": 0.0,
            "other": 0.0,
        }
        self._last = 0.0
        self._filter = None
        self._app = None
        self._active = False

    def start(self, app):
        from PySide6.QtCore import QObject
        self._app = app
        self._last = time.perf_counter()
        self._active = True
        watch = self

        class _Filt(QObject):
            def eventFilter(self, obj, event):
                if not watch._active:
                    return False
                now = time.perf_counter()
                gap = (now - watch._last) * 1000.0
                watch._last = now
                if gap > watch.max_gap_ms:
                    watch.max_gap_ms = gap
                if gap > STALL_MS:
                    stack = "".join(traceback.format_stack(limit=40))
                    group = _classify(stack)
                    watch.by_group[group] = watch.by_group.get(group, 0.0) + gap
                    watch.block_total_ms += gap
                    watch.gaps.append({
                        "ms": round(gap, 1),
                        "group": group,
                        "obj": type(obj).__name__,
                        "ev": int(event.type()),
                        "stack": stack[-2500:],
                    })
                return False

        self._filter = _Filt(app)
        app.installEventFilter(self._filter)
        app.processEvents()
        self._last = time.perf_counter()

    def stop(self):
        now = time.perf_counter()
        self._active = False
        gap = (now - self._last) * 1000.0
        if gap > self.max_gap_ms:
            self.max_gap_ms = gap
        if gap > STALL_MS:
            stack = "".join(traceback.format_stack(limit=40))
            group = _classify(stack)
            self.by_group[group] = self.by_group.get(group, 0.0) + gap
            self.block_total_ms += gap
            self.gaps.append({
                "ms": round(gap, 1),
                "group": group,
                "obj": "stop",
                "ev": -1,
                "stack": stack[-2500:],
            })
        if self._app is not None and self._filter is not None:
            self._app.removeEventFilter(self._filter)

    def snapshot(self) -> dict:
        return {
            "max_gap_ms": round(self.max_gap_ms, 1),
            "block_total_ms": round(self.block_total_ms, 1),
            "by_group_ms": {k: round(v, 1) for k, v in self.by_group.items()},
            "n_stalls_over_50ms": len(self.gaps),
            "top_gaps": [
                {"ms": g["ms"], "group": g["group"], "obj": g["obj"]}
                for g in sorted(self.gaps, key=lambda g: -g["ms"])[:8]
            ],
        }


class SectionTimer:
    """Wall time spent inside named call sites on the main thread."""

    def __init__(self):
        self.ms = {
            "canvas_first_frame": 0.0,
            "style_gallery": 0.0,
            "property_panel": 0.0,
            "other": 0.0,
        }
        self.calls = {}
        self._depth = {}

    def wrap(self, obj, name, group):
        orig = getattr(obj, name)
        timer = self

        def wrapped(*a, **k):
            timer.calls[name] = timer.calls.get(name, 0) + 1
            # Nested wraps in the same group must not double-count.
            depth = timer._depth.get(group, 0)
            timer._depth[group] = depth + 1
            t0 = time.perf_counter()
            try:
                return orig(*a, **k)
            finally:
                dt = (time.perf_counter() - t0) * 1000.0
                timer._depth[group] = depth
                if depth == 0:
                    timer.ms[group] = timer.ms.get(group, 0.0) + dt

        setattr(obj, name, wrapped)
        return orig

    def snapshot(self) -> dict:
        return {
            "ms": {k: round(v, 1) for k, v in self.ms.items()},
            "calls": dict(self.calls),
        }


class RefreshCount:
    def __init__(self):
        self.refresh_ui = 0
        self.set_data = 0
        self.canvas_update = 0
        self._orig = {}

    def install(self, win):
        orig_refresh = win.refresh_ui

        def refresh_ui(*a, **k):
            self.refresh_ui += 1
            return orig_refresh(*a, **k)

        win.refresh_ui = refresh_ui
        self._orig["refresh_ui"] = orig_refresh

        tl = win.timeline
        orig_set = tl.set_data

        def set_data(*a, **k):
            self.set_data += 1
            return orig_set(*a, **k)

        tl.set_data = set_data
        self._orig["set_data"] = orig_set

        canvas = win.video_canvas
        orig_up = canvas.update

        def update(*a, **k):
            self.canvas_update += 1
            return orig_up(*a, **k)

        canvas.update = update
        self._orig["canvas_update"] = orig_up

    def reset(self):
        self.refresh_ui = 0
        self.set_data = 0
        self.canvas_update = 0

    def snapshot(self) -> dict:
        return {
            "refresh_ui": self.refresh_ui,
            "timeline.set_data": self.set_data,
            "video_canvas.update": self.canvas_update,
        }


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


def _pump(app, ms=200):
    t_end = time.perf_counter() + ms / 1000.0
    while time.perf_counter() < t_end:
        app.processEvents()
        time.sleep(0.005)


def _wait_import(win, app, n, timeout=30.0):
    t_end = time.perf_counter() + timeout
    while time.perf_counter() < t_end:
        app.processEvents()
        tasks = list(win.tasks or [])
        if len(tasks) >= n and not any(t.get("probe_pending") for t in tasks):
            _pump(app, 60)
            return
        time.sleep(0.01)
    _pump(app, 60)


def _copies(tmp, n):
    files = []
    for i in range(n):
        dest = os.path.join(tmp, "q122_%02d.mp4" % i)
        shutil.copy2(G01, dest)
        files.append(dest)
    return files


def run_profile(label: str) -> dict:
    if not os.path.isfile(G01):
        raise SystemExit("missing %s" % G01)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")
    ud = tempfile.mkdtemp(prefix="q122_prof_")
    os.environ["CUTORA_USER_DATA"] = ud
    from cutora.core.paths import refresh_cached_paths
    refresh_cached_paths()
    from cutora.media.probe_cache import reset as reset_probe_cache
    from cutora.media.import_probe import reset_import_cache
    reset_probe_cache()
    reset_import_cache()

    from PySide6.QtWidgets import QApplication
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication(["q122-click-profile"])
    load_embedded_fonts()
    t_win0 = time.perf_counter()
    win = MainWindow()
    _silence(win)
    win.show()
    win.resize(1280, 800)
    app.processEvents()
    window_ms = (time.perf_counter() - t_win0) * 1000.0

    work = tempfile.mkdtemp(prefix="q122_media_")
    one = _copies(work, 1)[0]

    # --- import one, then click it ---
    win.on_drop([one])
    _wait_import(win, app, 1)

    sections = SectionTimer()
    canvas = win.video_canvas
    sections.wrap(canvas, "paintEvent", "canvas_first_frame")
    sections.wrap(canvas, "_draw_preview_local", "canvas_first_frame")
    sections.wrap(canvas, "_get_cv_frame_for_preview", "canvas_first_frame")
    sections.wrap(win, "_present_preview_frame", "canvas_first_frame")
    gal = getattr(win, "style_gallery", None)
    if gal is not None:
        sections.wrap(gal, "reload", "style_gallery")
        sections.wrap(gal, "_refill", "style_gallery")
        sections.wrap(gal, "_base_frame", "style_gallery")
        sections.wrap(gal, "_pack_item", "style_gallery")
    prop = getattr(win, "prop_panel", None)
    if prop is not None:
        sections.wrap(prop, "load_data", "property_panel")
    sub = getattr(win, "sub_panel", None)
    if sub is not None and hasattr(sub, "load_data"):
        sections.wrap(sub, "load_data", "property_panel")

    counts = RefreshCount()
    counts.install(win)

    item = win.file_list.item(0)
    watch = StallWatch()
    watch.start(app)
    counts.reset()
    t0 = time.perf_counter()
    if item is not None:
        win.on_click(item)
    app.processEvents()
    click_return_ms = (time.perf_counter() - t0) * 1000.0
    _pump(app, 500)
    click_wall_ms = (time.perf_counter() - t0) * 1000.0
    watch.stop()
    click_counts = counts.snapshot()
    click_sections = sections.snapshot()

    # --- import 5 more: refresh counts over that one action ---
    five = _copies(work, 5)
    counts.reset()
    t1 = time.perf_counter()
    win.on_drop(list(five))
    app.processEvents()
    import_return_ms = (time.perf_counter() - t1) * 1000.0
    import_sync_counts = counts.snapshot()
    _wait_import(win, app, 1 + 1 + 5)
    import_wall_counts = counts.snapshot()

    try:
        win.close()
    except Exception:
        pass

    out = {
        "label": label,
        "window_construct_ms": round(window_ms, 1),
        "click": {
            "on_click_return_ms": round(click_return_ms, 1),
            "wall_ms": round(click_wall_ms, 1),
            "stall": watch.snapshot(),
            "sections": click_sections,
            "refreshes_during_click_and_pump": click_counts,
        },
        "import_5": {
            "on_drop_return_ms": round(import_return_ms, 1),
            "refreshes_until_on_drop_returns": import_sync_counts,
            "refreshes_until_probes_idle": import_wall_counts,
        },
    }
    out["click"]["stall_stacks"] = [
        {"ms": g["ms"], "group": g["group"], "stack": g["stack"]}
        for g in sorted(watch.gaps, key=lambda g: -g["ms"])[:4]
    ]
    return out


def _print(data: dict) -> None:
    clk = data["click"]
    st = clk["stall"]
    print("=== Q122 click profile label=%s ===" % data["label"])
    print("window construct: %.1f ms" % data["window_construct_ms"])
    print("CLICK return %.1f ms  wall %.1f ms" % (
        clk["on_click_return_ms"], clk["wall_ms"]))
    print("  longest continuous block: %.1f ms" % st["max_gap_ms"])
    print("  block total (>50ms): %.1f ms   n=%s" % (
        st["block_total_ms"], st["n_stalls_over_50ms"]))
    print("  stall groups (sum of gaps>50ms): %s" % st["by_group_ms"])
    print("  top gaps: %s" % st["top_gaps"])
    print("  section time inside wrapped calls: %s" % clk["sections"]["ms"])
    print("  section calls: %s" % clk["sections"]["calls"])
    print("  refreshes: %s" % clk["refreshes_during_click_and_pump"])
    imp = data["import_5"]
    print("IMPORT 5 return %.1f ms" % imp["on_drop_return_ms"])
    print("  refreshes until return: %s" % imp["refreshes_until_on_drop_returns"])
    print("  refreshes until probes idle: %s" % imp["refreshes_until_probes_idle"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="before")
    args = ap.parse_args()
    data = run_profile(args.label)
    os.makedirs(REPORT_DIR, exist_ok=True)
    dest = os.path.join(REPORT_DIR, "Q122_profile_%s.json" % args.label)
    with open(dest, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    _print(data)
    print("wrote %s" % dest)


if __name__ == "__main__":
    main()

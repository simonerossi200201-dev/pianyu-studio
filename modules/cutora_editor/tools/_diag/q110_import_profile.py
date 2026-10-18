#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q110: profile importing 10 videos and clicking one.

Counts external processes (by call site), main-thread stall, and
repeat ffprobe of the same file. Writes JSON next to this script's
report path so before/after can be compared.

Usage:
    runtime\\venv\\Scripts\\python.exe tools/_diag/q110_import_profile.py
    runtime\\venv\\Scripts\\python.exe tools/_diag/q110_import_profile.py --label after
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import threading
import time
import traceback

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

G19 = os.path.join(REPO, "tests", "golden", "_media", "g19_stitch_timeline")
G01 = os.path.join(REPO, "tests", "golden", "_media", "g01", "video.mp4")
REPORT_DIR = os.path.join(REPO, "docs", "plan", "reports")
STALL_MS = 50.0


def _videos(n=10):
    import glob
    files = sorted(glob.glob(os.path.join(G19, "*.mp4")))
    if len(files) >= n:
        return [os.path.abspath(p) for p in files[:n]]
    if os.path.isfile(G01):
        return [os.path.abspath(G01)] * n
    return []


def _caller_site(skip_names=()):
    skip = {
        "run_wrap", "popen_wrap", "run", "popen", "_no_window_run",
        "_no_window_popen", "run_filter_script", "record",
        "_ffprobe_duration", "_ffprobe_off_main", "_ffprobe_full_uncached",
        "_ffprobe_alpha_uncached",
    }
    skip.update(skip_names)
    for fr in traceback.extract_stack()[:-1]:
        path = fr.filename.replace("\\", "/")
        if "/cutora/" not in path:
            continue
        name = fr.name
        if name in skip or name.startswith("run_wrap") or name.startswith("PopenWrap"):
            continue
        rel = path.split("/cutora/")[-1]
        return "cutora/%s:%s:%s" % (rel, fr.lineno, name)
    return "?"


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


def _cmd_path(cmd) -> str:
    if isinstance(cmd, (list, tuple)) and len(cmd) >= 2:
        return str(cmd[-1])
    return ""


class StallWatch:
    """Gap between QApplication events = main-thread stall."""

    def __init__(self, stall_ms=STALL_MS):
        self.stall_ms = float(stall_ms)
        self.gaps_ms = []
        self.max_gap_ms = 0.0
        self.block_total_ms = 0.0
        self.slow = []
        self._last = 0.0
        self._filter = None
        self._app = None
        self._active = False

    def start(self, app) -> None:
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
                if gap > watch.stall_ms:
                    watch.block_total_ms += gap
                    watch.gaps_ms.append(round(gap, 1))
                    stack = "".join(traceback.format_stack(limit=18))
                    watch.slow.append({
                        "ms": round(gap, 1),
                        "obj": type(obj).__name__,
                        "ev": int(event.type()),
                        "stack": stack[-2000:],
                    })
                return False

        self._filter = _Filt(app)
        app.installEventFilter(self._filter)
        app.processEvents()
        self._last = time.perf_counter()

    def snapshot(self) -> dict:
        return {
            "max_gap_ms": round(self.max_gap_ms, 1),
            "block_total_ms": round(self.block_total_ms, 1),
            "n_stalls_over_%sms" % int(self.stall_ms): len(self.gaps_ms),
            "top_gaps_ms": sorted(self.gaps_ms, reverse=True)[:8],
        }

    def stop(self):
        now = time.perf_counter()
        self._active = False
        gap = (now - self._last) * 1000.0
        if gap > self.max_gap_ms:
            self.max_gap_ms = gap
        if gap > self.stall_ms:
            self.block_total_ms += gap
            self.gaps_ms.append(round(gap, 1))
        if self._app is not None and self._filter is not None:
            self._app.removeEventFilter(self._filter)


class ProcLog:
    def __init__(self):
        self.calls = []
        self.main_ident = threading.get_ident()

    def record(self, kind, cmd, ms):
        path = _cmd_path(cmd)
        site = _caller_site()
        self.calls.append({
            "kind": kind,
            "ms": round(ms, 3),
            "path": path,
            "site": site,
            "main_thread": threading.get_ident() == self.main_ident,
            "exe": os.path.basename(str(cmd[0])).lower() if cmd else "",
        })

    def summary(self) -> dict:
        by_site = {}
        by_file_probe = {}
        n_ffprobe = n_ffmpeg = n_other = 0
        main_ms = 0.0
        for c in self.calls:
            if c["kind"] == "ffprobe":
                n_ffprobe += 1
                key = os.path.normcase(os.path.abspath(c["path"])) if c["path"] else "?"
                by_file_probe[key] = by_file_probe.get(key, 0) + 1
            elif c["kind"] == "ffmpeg":
                n_ffmpeg += 1
            else:
                n_other += 1
            if c["main_thread"]:
                main_ms += c["ms"]
            slot = by_site.setdefault(c["site"], {
                "n": 0, "ms": 0.0, "kind": c["kind"], "main_n": 0,
            })
            slot["n"] += 1
            slot["ms"] += c["ms"]
            if c["main_thread"]:
                slot["main_n"] += 1
        repeats = sorted(
            ((k, n) for k, n in by_file_probe.items() if n > 1),
            key=lambda kv: -kv[1],
        )
        sites = [
            {
                "site": s,
                "n": v["n"],
                "ms": round(v["ms"], 1),
                "kind": v["kind"],
                "main_n": v["main_n"],
            }
            for s, v in sorted(by_site.items(), key=lambda kv: -kv[1]["ms"])
        ]
        return {
            "n_processes": len(self.calls),
            "n_ffprobe": n_ffprobe,
            "n_ffmpeg": n_ffmpeg,
            "n_other": n_other,
            "main_thread_proc_ms": round(main_ms, 1),
            "sites": sites,
            "ffprobe_files": len(by_file_probe),
            "ffprobe_repeat_files": len(repeats),
            "ffprobe_max_per_file": max(by_file_probe.values()) if by_file_probe else 0,
            "ffprobe_repeats": [
                {"path": os.path.basename(p), "n": n} for p, n in repeats[:12]
            ],
        }


def install_proc_hooks(log: ProcLog):
    import subprocess as sp
    orig_popen = sp.Popen

    class PopenWrap(orig_popen):
        def __init__(self, cmd, *a, **k):
            self._q110_kind = (
                "ffprobe" if _is_ffprobe(cmd) else ("ffmpeg" if _is_ffmpeg(cmd) else "other")
            )
            self._q110_cmd = cmd
            self._q110_t0 = time.perf_counter()
            super().__init__(cmd, *a, **k)

        def wait(self, *a, **k):
            try:
                return super().wait(*a, **k)
            finally:
                if getattr(self, "_q110_t0", None) is not None:
                    log.record(
                        self._q110_kind, self._q110_cmd,
                        (time.perf_counter() - self._q110_t0) * 1000.0)
                    self._q110_t0 = None

        def communicate(self, *a, **k):
            try:
                return super().communicate(*a, **k)
            finally:
                if getattr(self, "_q110_t0", None) is not None:
                    log.record(
                        self._q110_kind, self._q110_cmd,
                        (time.perf_counter() - self._q110_t0) * 1000.0)
                    self._q110_t0 = None

    # subprocess.run constructs Popen. Hook Popen only so each process
    # is counted once.
    sp.Popen = PopenWrap
    return {
        "_orig_sp_popen": orig_popen,
    }


def restore_proc_hooks(saved):
    import subprocess as sp
    if saved.get("_orig_sp_run"):
        sp.run = saved["_orig_sp_run"]
    if saved.get("_orig_sp_popen"):
        sp.Popen = saved["_orig_sp_popen"]


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
    """Wait until n tasks exist and none are still probe_pending (if that flag exists)."""
    t_end = time.perf_counter() + timeout
    while time.perf_counter() < t_end:
        app.processEvents()
        tasks = list(win.tasks or [])
        if len(tasks) >= n:
            pending = [t for t in tasks if t.get("probe_pending")]
            if not pending:
                _pump(app, 80)
                return
        time.sleep(0.01)
    _pump(app, 80)


def run_profile(label: str) -> dict:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")
    ud = tempfile.mkdtemp(prefix="q110_prof_")
    os.environ["CUTORA_USER_DATA"] = ud
    from cutora.core.paths import refresh_cached_paths
    refresh_cached_paths()
    from cutora.media.probe_cache import reset as reset_probe_cache
    reset_probe_cache()

    files = _videos(10)
    if len(files) < 10:
        raise SystemExit("need 10 golden mp4s, got %s" % len(files))

    from PySide6.QtWidgets import QApplication
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication(["q110-import-profile"])
    load_embedded_fonts()
    t_win0 = time.perf_counter()
    win = MainWindow()
    _silence(win)
    win.show()
    app.processEvents()
    window_ms = (time.perf_counter() - t_win0) * 1000.0

    log = ProcLog()
    saved = install_proc_hooks(log)

    # --- import 10 ---
    watch_imp = StallWatch()
    watch_imp.start(app)
    t0 = time.perf_counter()
    win.on_drop(list(files))
    app.processEvents()
    import_return_ms = (time.perf_counter() - t0) * 1000.0
    _wait_import(win, app, 10)
    import_wall_ms = (time.perf_counter() - t0) * 1000.0
    watch_imp.stop()
    import_procs = log.summary()
    import_calls = list(log.calls)
    log.calls = []

    n_tasks = len(win.tasks or [])
    n_items = win.file_list.count() if hasattr(win, "file_list") else 0

    # --- click first ---
    watch_clk = StallWatch()
    watch_clk.start(app)
    t1 = time.perf_counter()
    item = win.file_list.item(0) if n_items else None
    if item is not None:
        win.on_click(item)
    app.processEvents()
    click_return_ms = (time.perf_counter() - t1) * 1000.0
    _pump(app, 400)
    click_wall_ms = (time.perf_counter() - t1) * 1000.0
    watch_clk.stop()
    click_procs = log.summary()

    restore_proc_hooks(saved)
    try:
        win.close()
    except Exception:
        pass
    reset_probe_cache()

    out = {
        "label": label,
        "n_files": 10,
        "n_tasks_after_import": n_tasks,
        "n_list_items": n_items,
        "window_construct_ms": round(window_ms, 1),
        "import": {
            "return_ms": round(import_return_ms, 1),
            "wall_ms": round(import_wall_ms, 1),
            "stall": watch_imp.snapshot(),
            "procs": import_procs,
        },
        "click": {
            "return_ms": round(click_return_ms, 1),
            "wall_ms": round(click_wall_ms, 1),
            "stall": watch_clk.snapshot(),
            "procs": click_procs,
        },
    }
    # keep a few stall stacks for the report
    out["import"]["stall_stacks"] = [
        {"ms": s["ms"], "stack": s["stack"]} for s in watch_imp.slow[:5]
    ]
    out["click"]["stall_stacks"] = [
        {"ms": s["ms"], "stack": s["stack"]} for s in watch_clk.slow[:5]
    ]
    out["import_first_8_calls"] = import_calls[:8]
    return out


def _print_report(data: dict) -> None:
    imp = data["import"]
    clk = data["click"]
    print("=== Q110 profile label=%s ===" % data["label"])
    print("files=10  tasks=%s  list=%s" % (
        data["n_tasks_after_import"], data["n_list_items"]))
    print("window construct: %.1f ms" % data["window_construct_ms"])
    print()
    print("IMPORT")
    print("  on_drop return: %.1f ms   wall (until probes idle): %.1f ms" % (
        imp["return_ms"], imp["wall_ms"]))
    st = imp["stall"]
    print("  stall max: %.1f ms   block total: %.1f ms   n>50ms: %s" % (
        st["max_gap_ms"], st["block_total_ms"], st["n_stalls_over_50ms"]))
    print("  top gaps: %s" % st["top_gaps_ms"])
    pr = imp["procs"]
    print("  processes: %s  (ffprobe %s, ffmpeg %s, other %s)" % (
        pr["n_processes"], pr["n_ffprobe"], pr["n_ffmpeg"], pr["n_other"]))
    print("  main-thread proc ms: %.1f" % pr["main_thread_proc_ms"])
    print("  ffprobe files: %s  repeats: %s  max/file: %s" % (
        pr["ffprobe_files"], pr["ffprobe_repeat_files"], pr["ffprobe_max_per_file"]))
    if pr["ffprobe_repeats"]:
        print("  repeat files: %s" % pr["ffprobe_repeats"][:6])
    print("  sites:")
    for s in pr["sites"][:12]:
        print("    %4d  %7.1f ms  main=%s  %s  %s" % (
            s["n"], s["ms"], s["main_n"], s["kind"], s["site"]))
    print()
    print("CLICK")
    print("  on_click return: %.1f ms   wall: %.1f ms" % (
        clk["return_ms"], clk["wall_ms"]))
    st = clk["stall"]
    print("  stall max: %.1f ms   block total: %.1f ms   n>50ms: %s" % (
        st["max_gap_ms"], st["block_total_ms"], st["n_stalls_over_50ms"]))
    print("  top gaps: %s" % st["top_gaps_ms"])
    pr = clk["procs"]
    print("  processes: %s  (ffprobe %s, ffmpeg %s, other %s)" % (
        pr["n_processes"], pr["n_ffprobe"], pr["n_ffmpeg"], pr["n_other"]))
    print("  main-thread proc ms: %.1f" % pr["main_thread_proc_ms"])
    print("  ffprobe files: %s  repeats: %s  max/file: %s" % (
        pr["ffprobe_files"], pr["ffprobe_repeat_files"], pr["ffprobe_max_per_file"]))
    print("  sites:")
    for s in pr["sites"][:12]:
        print("    %4d  %7.1f ms  main=%s  %s  %s" % (
            s["n"], s["ms"], s["main_n"], s["kind"], s["site"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="before")
    args = ap.parse_args()
    data = run_profile(args.label)
    os.makedirs(REPORT_DIR, exist_ok=True)
    dest = os.path.join(REPORT_DIR, "Q110_profile_%s.json" % args.label)
    with open(dest, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    _print_report(data)
    print()
    print("wrote %s" % dest)


if __name__ == "__main__":
    main()

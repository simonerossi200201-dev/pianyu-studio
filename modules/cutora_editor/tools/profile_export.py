#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""T35: wall-clock + stage probe for one timeline export. CUTORA_PROFILE_EXPORT=1."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "tools"))
os.chdir(REPO)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")


def _ensure_ffmpeg_path():
    ffdir = os.path.join(REPO, "runtime", "ffmpeg")
    path = os.environ.get("PATH") or ""
    parts = [os.path.normcase(p) for p in path.split(os.pathsep)]
    if os.path.normcase(ffdir) not in parts:
        os.environ["PATH"] = ffdir + os.pathsep + path


def _t023():
    cutora = os.path.join("D:\\", "1_PROJECT", "2026-09-15", "A认真.cutora")
    if os.path.isfile(cutora):
        return cutora
    root = os.path.join("D:\\", "1_PROJECT", "2026-09-15", "A")
    if not os.path.isdir(root):
        return None
    for dirpath, _d, names in os.walk(root):
        if "stitched.asproj.json" not in names:
            continue
        if os.sep + "T023" + os.sep not in (dirpath + os.sep):
            continue
        return os.path.join(dirpath, "stitched.asproj.json")
    return None


def _parse_args(argv):
    probe = "on"
    path = None
    canvas = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--probe" and i + 1 < len(argv):
            probe = argv[i + 1]
            i += 2
            continue
        if a.startswith("--probe="):
            probe = a.split("=", 1)[1]
            i += 1
            continue
        if a == "--canvas" and i + 1 < len(argv):
            canvas = argv[i + 1]
            i += 2
            continue
        if a.startswith("--canvas="):
            canvas = a.split("=", 1)[1]
            i += 1
            continue
        if not a.startswith("-"):
            path = a
        i += 1
    if probe not in ("on", "off"):
        raise SystemExit("probe must be on|off, got %r" % (probe,))
    return probe, path, canvas


def _apply_canvas_override(tasks, canvas_spec):
    from cutora.model.canvas import apply_canvas, parse_canvas_spec
    wh = parse_canvas_spec(canvas_spec)
    if not wh:
        raise SystemExit("canvas must be WxH, got %r" % (canvas_spec,))
    w, h = wh
    for task in tasks:
        apply_canvas(task, w, h)
    print("CANVAS_OVERRIDE", w, h, flush=True)
    return w, h


def main():
    probe, path, canvas = _parse_args(sys.argv[1:])
    os.environ["CUTORA_PROFILE_EXPORT"] = "1" if probe == "on" else "0"
    _ensure_ffmpeg_path()
    import golden_check as gc
    from cutora.render.export_probe import configure, dump, reset
    configure(probe == "on")
    path = path or _t023()
    if not path or not os.path.isfile(path):
        print("NO_T023", path)
        return 2
    host, _app = gc.boot_host()
    _data, tasks = gc.load_tasks(host, path)
    if not tasks:
        print("NO_TASKS")
        return 2
    if canvas:
        _apply_canvas_override(tasks, canvas)
    print("PROJECT", path, "n_tasks", len(tasks), "probe", probe)
    tmp = tempfile.mkdtemp(prefix="prof_export_")
    grand = time.perf_counter()
    rows = []
    for i, task in enumerate(tasks):
        reset()
        gc.reset_case_caches()
        t0 = time.perf_counter()
        outs = gc.render_serial(host, [task], os.path.join(tmp, "t%d" % i), "quality", False)
        wall = time.perf_counter() - t0
        stats = dump()
        stats["wall_s"] = round(wall, 3)
        stats["probe"] = probe
        stats["i"] = i
        stats["dur"] = float(task.get("duration") or 0)
        stats["nseg"] = len(task.get("segments") or [])
        stats["dims"] = task.get("dims")
        stats["out_ok"] = bool(outs and os.path.isfile(outs[0]))
        rows.append(stats)
        print("TASK", i, json.dumps(stats, ensure_ascii=False), flush=True)
    total = time.perf_counter() - grand
    print("WALL_S_ALL", round(total, 3), "probe", probe)
    print(json.dumps({
        "wall_s_all": round(total, 3),
        "probe": probe,
        "tasks": rows,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

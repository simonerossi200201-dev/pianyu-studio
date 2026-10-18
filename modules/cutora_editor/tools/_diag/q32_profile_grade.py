#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Q32: 720x1280 ms/frame for baseline / grade-off / grade-on+cube.

Uses the same boot_host + render_serial path as tools/profile_export.py.
T023 is 7+ minutes; this exports a 3 s 720x1280 lavfi clip so the table
is a per-frame cost, not a 40-minute wall clock.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time

MAIN = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.environ.get("CUTORA_ROOT") or MAIN
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
os.chdir(ROOT)
_ffdir = os.path.join(MAIN, "runtime", "ffmpeg")
os.environ["PATH"] = _ffdir + os.pathsep + (os.environ.get("PATH") or "")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUTORA_SKIP_ENCODER_PROBE", "1")
os.environ["CUTORA_PROFILE_EXPORT"] = "1"

W, H = 720, 1280
FPS = 30
SECONDS = 3.0
FRAMES = int(FPS * SECONDS)


def _ffmpeg():
    from cutora.core.config import FFMPEG_PATH
    return FFMPEG_PATH


def _color_mp4(path, color):
    # testsrc2: moving pattern so x264 output is >10 KB (solid color fails
    # RenderWorker's temp_video size check) and grade cost is not a no-op.
    subprocess.check_call(
        [
            _ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi",
            "-i", "testsrc2=size=%dx%d:rate=%d:duration=%.3f" % (W, H, FPS, SECONDS),
            "-frames:v", str(FRAMES),
            "-pix_fmt", "yuv420p",
            path,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _write_cube(path):
    """Non-identity 17³: lift the red channel. Identity would hide LUT cost."""
    n = 17
    den = float(n - 1)
    lines = ['TITLE "q32-warm"', "LUT_3D_SIZE %d" % n, ""]
    for bi in range(n):
        b = bi / den
        for gi in range(n):
            g = gi / den
            for ri in range(n):
                r = min(1.0, (ri / den) * 1.12 + 0.02)
                lines.append("%.6f %.6f %.6f" % (r, g, b))
    with open(path, "w", encoding="ascii") as fh:
        fh.write("\n".join(lines) + "\n")


def _task(media):
    from cutora.model.timeline import Timeline
    from cutora.model.timeline_model import Clip, Track
    from cutora.model.canvas import apply_canvas

    tl = Timeline(W, H, float(FPS))
    src_id = "src0"
    tl.sources[src_id] = {
        "id": src_id, "path": media,
        "duration": SECONDS, "has_video": True, "has_audio": False,
    }
    tr = Track("video")
    tr.id = "v0"
    clip = Clip(src_id, 0.0, SECONDS, 0.0)
    clip.fit_mode = "stretch"
    tr.clips.append(clip)
    tl.add_track(tr)
    task = {
        "video": media,
        "dims": (W, H),
        "canvas_width": W,
        "canvas_height": H,
        "duration": SECONDS,
        "timeline": tl,
        "style": {},
        "segments": [],
        "text_overlays": [],
        "image_overlays": [],
    }
    apply_canvas(task, W, H)
    return task, tr, clip


def _arm_grade(task, tr, clip, cube_path):
    from cutora.render.grade import identity_grade

    def _full():
        g = identity_grade()
        g["exposure"] = 0.35
        g["contrast"] = 0.22
        g["pivot"] = 0.18
        g["temperature"] = -400.0
        g["tint"] = 0.12
        g["saturation"] = 0.35
        g["vibrance"] = 0.20
        g["lift"] = [0.02, 0.0, -0.01, 0.01]
        g["gamma"] = [0.0, 0.03, 0.0, 0.0]
        g["gain"] = [0.0, 0.0, 0.04, 0.02]
        g["hsl"]["red"] = {"h": 0.08, "s": 0.10, "l": 0.0}
        g["cube"] = cube_path
        g["cube_mix"] = 0.75
        return g

    clip.grade = _full()
    tr.grade = identity_grade()
    tr.grade["saturation"] = 0.15
    task["grade"] = identity_grade()
    task["grade"]["contrast"] = 0.10


def _arm_channels(task, tr, clip):
    """Per-channel only. No temperature, saturation, HSL or cube — must stay off the 3D table."""
    from cutora.render.grade import identity_grade

    g = identity_grade()
    g["exposure"] = 0.35
    g["contrast"] = 0.22
    g["pivot"] = 0.18
    g["lift"] = [0.02, 0.0, -0.01, 0.01]
    g["gamma"] = [0.0, 0.03, 0.0, 0.0]
    g["gain"] = [0.0, 0.0, 0.04, 0.02]
    clip.grade = g


def _run(label, arm):
    from cutora.render.export_probe import configure, dump, reset
    import golden_check as gc

    configure(True)
    reset()
    tmp = tempfile.mkdtemp(prefix="q32_prof_")
    media = os.path.join(tmp, "src.mp4")
    cube = os.path.join(tmp, "warm.cube")
    _color_mp4(media, "0xC06020")
    _write_cube(cube)
    host, _app = gc.boot_host()
    task, tr, clip = _task(media)
    if arm == "on":
        _arm_grade(task, tr, clip, cube)
    elif arm == "channels":
        _arm_channels(task, tr, clip)
    out_dir = os.path.join(tmp, "out")
    os.makedirs(out_dir, exist_ok=True)
    outp = os.path.join(out_dir, "out_00.mp4")
    err = []
    t0 = time.perf_counter()
    worker = host.RenderWorker(
        task, outp, "quality", False, export_logs=True, compose_workers=1)
    worker.error.connect(lambda m: err.append(m))
    worker.run()
    wall = time.perf_counter() - t0
    if err or not os.path.isfile(outp):
        raise RuntimeError("render failed %s err=%s size=%s" % (
            label, err[:1], os.path.getsize(outp) if os.path.isfile(outp) else None))
    stats = dump()
    n = int(stats.get("frames") or 0) or FRAMES
    row = {
        "label": label,
        "wall_s": round(wall, 3),
        "frames": n,
        "ms_per_frame": round(wall * 1000.0 / max(1, n), 3),
        "probe_ms_per_frame": stats.get("ms_per_frame") or {},
        "out_ok": True,
        "out_bytes": os.path.getsize(outp),
        "canvas": "%dx%d" % (W, H),
        "seconds": SECONDS,
        "compose_workers": 1,
    }
    print("Q32_PROF", json.dumps(row, ensure_ascii=False), flush=True)
    return row


def _write(mode, row):
    # Q73 reuses this script. Q73_PROFILE=1 keeps Q32's old json files intact.
    prefix = "q73" if os.environ.get("Q73_PROFILE") == "1" else "q32"
    out = os.path.join(MAIN, "docs", "plan", "reports", "_%s_%s.json" % (prefix, mode))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(row, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print("WROTE", out, flush=True)
    return out


def main():
    mode = (sys.argv[1] if len(sys.argv) > 1 else "off").strip()
    if mode not in ("off", "on", "channels", "baseline"):
        raise SystemExit("usage: q32_profile_grade.py off|on|channels|baseline")
    label = {
        "off": "grade_off",
        "on": "grade_on_cube",
        "channels": "grade_channels_only",
        "baseline": "pre_grade_4279bbe",
    }[mode]
    row = _run(label, mode if mode in ("on", "channels") else "off")
    _write(mode, row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

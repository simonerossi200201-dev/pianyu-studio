# -*- coding: utf-8 -*-
"""T04f-6a: dump g09 expected vs got frames after removing max_lines==2 skip."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

import cv2
import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "tools"))

IMG = os.path.join(REPO, "docs", "plan", "reports", "img")
# Mid-segment times (seconds) covering hang / punct / overflow / Italian.
TIMES = (1.0, 4.5, 8.5, 11.5, 14.5, 17.5)


def _frame_at(path: str, t: float):
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(t * fps)))
    ok, frame = cap.read()
    cap.release()
    if not ok or frame is None:
        raise RuntimeError("no frame at t=%.1f in %s" % (t, path))
    return frame


def _side(a, b, t):
    h = min(a.shape[0], b.shape[0])
    w = min(a.shape[1], b.shape[1])
    a, b = a[:h, :w], b[:h, :w]
    diff = cv2.absdiff(a, b)
    bar = np.full((h, 8, 3), 40, dtype=np.uint8)
    row = np.concatenate([a, bar, b, bar, diff], axis=1)
    os.makedirs(IMG, exist_ok=True)
    path = os.path.join(IMG, "T04f-6a_g09_t%04.0fms.png" % (t * 1000))
    cv2.imwrite(path, row)
    gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
    changed = int((gray > 8).sum())
    ys, xs = np.where(gray > 8)
    bbox = None
    if len(xs):
        bbox = (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))
    psnr = cv2.PSNR(a, b)
    print("t=%.1f psnr=%.2f changed_px=%d bbox=%s -> %s" % (
        t, psnr, changed, bbox, path), flush=True)
    return path


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import golden_check as gc
    gc.prepare_env()
    host, _app = gc.boot_host()
    case = "g09_linebreak_latin"
    case_dir = os.path.join(gc.GOLDEN_ROOT, case)
    manifest = gc.load_manifest(case_dir)
    project = os.path.join(case_dir, manifest.get("project", "project.asproj.json"))
    data, tasks = gc.load_tasks(host, project)
    out_dir = os.path.join(REPO, "docs", "plan", "reports")
    os.makedirs(out_dir, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="g09_t04f6a_")
    try:
        produced = gc.render_serial(
            host, tasks, os.path.join(tmp, "ser"),
            manifest.get("mode") or "quality", False)
        got = produced[0]
        kept = os.path.join(out_dir, "T04f-6a_g09_got.mp4")
        shutil.copy2(got, kept)
        print("got", kept, "sha", gc.sha256_file(kept)[:12], flush=True)
        exp = os.path.join(case_dir, "expected.mp4")
        print("exp sha", gc.sha256_file(exp)[:12], flush=True)
        for t in TIMES:
            _side(_frame_at(exp, t), _frame_at(kept, t), t)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

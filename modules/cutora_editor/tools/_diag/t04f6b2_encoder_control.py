# -*- coding: utf-8 -*-
"""§4 control: one-character subtitle change vs unmodified g01 export.

Compares the upper 55% of frame 521. Does not rewrite golden fixtures.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

import cv2
import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

UNMOD = os.path.join(
    REPO, "docs", "plan", "reports", "nondeterminism",
    "g01_vertical_basic_r01_20260913_163840", "got_out_00.mp4",
)
PROJ = os.path.join(REPO, "tests", "golden", "g01_vertical_basic", "project.asproj.json")
FRAME_N = 521
Y_FRAC = 0.55
EDGE_PX = 4


def _mutate(src: str, dest: str) -> None:
    data = json.loads(open(src, encoding="utf-8").read())
    task = data["tasks"][0]
    task["text"] = str(task.get("text") or "").replace(
        "together.", "together,", 1)
    segs = task.get("segments") or []
    last = segs[-1]
    last["text"] = str(last.get("text") or "").replace("together.", "together,")
    for w in last.get("words") or []:
        if str(w.get("word") or "") == "together.":
            w["word"] = "together,"
    lines = last.get("lines")
    if isinstance(lines, list):
        last["lines"] = [str(x).replace("together.", "together,") for x in lines]
    with open(dest, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def _render(project: str, out_mp4: str) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from tools.golden_check import boot_host, load_tasks, render_serial
    host, app = boot_host()
    _ = app
    data, tasks = load_tasks(host, project)
    tmp = tempfile.mkdtemp(prefix="g01_ctrl_")
    try:
        produced = render_serial(host, tasks, tmp, "quality", False)
        shutil.copy2(produced[0], out_mp4)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _frame(path: str, n: int):
    cap = cv2.VideoCapture(path)
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(n))
    ok, fr = cap.read()
    cap.release()
    if not ok or fr is None:
        raise RuntimeError("frame %s #%d" % (path, n))
    return fr


def _strong_edge(gray: np.ndarray, radius: int) -> np.ndarray:
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    mag = cv2.magnitude(gx, gy)
    edge = mag > 40.0
    k = 2 * radius + 1
    kernel = np.ones((k, k), np.uint8)
    return cv2.dilate(edge.astype(np.uint8), kernel, iterations=1).astype(bool)


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("CUTORA_USER_DATA", tempfile.mkdtemp(prefix="cutora_ctrl_"))
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)

    work = tempfile.mkdtemp(prefix="g01_onechar_")
    mut_proj = os.path.join(work, "project.asproj.json")
    mut_mp4 = os.path.join(work, "mut.mp4")
    _mutate(PROJ, mut_proj)
    print("mutated", mut_proj, flush=True)
    _render(mut_proj, mut_mp4)
    print("rendered", mut_mp4, os.path.getsize(mut_mp4), flush=True)

    a = _frame(UNMOD, FRAME_N)
    b = _frame(mut_mp4, FRAME_N)
    h, w = a.shape[:2]
    ycut = int(h * Y_FRAC)
    au = a[:ycut].astype(np.int16)
    bu = b[:ycut].astype(np.int16)
    d = np.abs(au - bu).max(axis=2).astype(np.int16)
    gray = cv2.cvtColor(a[:ycut], cv2.COLOR_BGR2GRAY)
    near_edge = _strong_edge(gray, EDGE_PX)
    flat = ~near_edge
    mean_d = float(d.mean())
    max_d = int(d.max())
    flat_gt24 = int(((d > 24) & flat).sum())
    flat_gt8 = int(((d > 8) & flat).sum())
    n_flat = int(flat.sum())
    n_edge = int(near_edge.sum())
    gt8 = int((d > 8).sum())
    gt8_on_edge = int(((d > 8) & near_edge).sum())
    ok = mean_d <= 0.2 and max_d <= 60 and flat_gt24 < 100
    print(
        "CTRL frame", FRAME_N, "upper", "0-%.0f%%" % (Y_FRAC * 100),
        "mean", round(mean_d, 4), "max", max_d,
        "flat_gt24", flat_gt24, "flat_gt8", flat_gt8,
        "n_flat", n_flat, "n_edge", n_edge,
        "gt8", gt8, "gt8_on_edge_pct",
        round(100.0 * gt8_on_edge / max(1, gt8), 1),
        "PASS" if ok else "FAIL",
        flush=True,
    )
    out = {
        "ok": ok,
        "frame": FRAME_N,
        "mean": mean_d,
        "max": max_d,
        "flat_gt24": flat_gt24,
        "flat_gt8": flat_gt8,
        "n_flat": n_flat,
        "thresholds": {"mean": 0.2, "max": 60, "flat_gt24": 100},
    }
    path = os.path.join(REPO, "docs", "plan", "reports", "T04f-6b2_encoder_control.json")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(out, fh, indent=2)
        fh.write("\n")
    print("wrote", path, flush=True)
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())

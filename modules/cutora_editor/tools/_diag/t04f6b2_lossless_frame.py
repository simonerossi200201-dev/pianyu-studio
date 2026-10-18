# -*- coding: utf-8 -*-
"""g01 frame 521, pre-encode PNG: current fonts vs forced Arimo.

Compares the region outside the subtitle band (y < 0.55 h).
Does not rewrite golden fixtures.
"""
from __future__ import annotations

import copy
import json
import os
import sys

import cv2
import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

os.chdir(REPO)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from cutora.core.runtime import init_torch_runtime
init_torch_runtime()

from tools.golden_check import boot_host, load_tasks
from cutora.render.export_renderer import ExportRenderer
from cutora.render.frame_renderer import compute_render_order
from cutora.render.readers import _cv2_frame_to_qimage
from cutora.render.textfx.raster import as_bgra

IMG = os.path.join(REPO, "docs", "plan", "reports", "img")
REP = os.path.join(REPO, "docs", "plan", "reports")
PROJ = os.path.join(REPO, "tests", "golden", "g01_vertical_basic", "project.asproj.json")
VIDEO = os.path.join(REPO, "tests", "golden", "_media", "g01", "video.mp4")
FRAME_N = 521
Y_FRAC = 0.55
W, H = 1080, 1920


def _force_arimo(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in ("font_family", "dyn_box_font_family") and isinstance(v, str):
                out[k] = "Arimo"
            elif k == "body" and isinstance(v, str) and "font" in str(obj.keys()):
                out[k] = "Arimo"
            else:
                out[k] = _force_arimo(v)
        if "fonts" in out and isinstance(out["fonts"], dict):
            fonts = dict(out["fonts"])
            if "body" in fonts:
                fonts["body"] = "Arimo"
            out["fonts"] = fonts
        return out
    if isinstance(obj, list):
        return [_force_arimo(x) for x in obj]
    return obj


def _grab_video(n: int):
    cap = cv2.VideoCapture(VIDEO)
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(n))
    ok, fr = cap.read()
    cap.release()
    if not ok or fr is None:
        raise RuntimeError("video frame %d" % n)
    img = _cv2_frame_to_qimage(fr)
    if img is None:
        raise RuntimeError("qimage frame %d" % n)
    if img.width() != W or img.height() != H:
        from PySide6.QtCore import Qt
        img = img.scaled(W, H, Qt.AspectRatioMode.IgnoreAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    return img, fr


def _render(task, bg, t: float):
    order = compute_render_order(task)

    def _draw_bg(painter, image=bg):
        painter.drawImage(0, 0, image)

    task = dict(task)
    task["_fx_backdrop"] = bg
    return ExportRenderer.render_frame(
        task, W, H, t, draw_background=_draw_bg, render_order=order,
        backdrop=bg,
    )


def _bgra(qimg):
    _, arr = as_bgra(qimg)
    return arr


def _save_png(qimg, name):
    os.makedirs(IMG, exist_ok=True)
    path = os.path.join(IMG, name)
    if not qimg.save(path, "PNG"):
        raise RuntimeError("save %s" % path)
    print("wrote", path, flush=True)
    return path


def main():
    host, app = boot_host()
    _ = app
    data, tasks = load_tasks(host, PROJ)
    task = tasks[0]
    t = FRAME_N / 30.0
    bg, raw = _grab_video(FRAME_N)

    cur = _render(copy.deepcopy(task), bg, t)
    ari = _render(_force_arimo(copy.deepcopy(task)), bg, t)
    _save_png(cur, "T04f-6b2_lossless_g01_f521_current.png")
    _save_png(ari, "T04f-6b2_lossless_g01_f521_arimo.png")

    a = _bgra(cur)
    b = _bgra(ari)
    if a.shape != b.shape:
        raise RuntimeError("shape %s vs %s" % (a.shape, b.shape))

    # BGRA uint8. Byte identity on RGB (ignore premul alpha noise if any).
    rgb_a = a[..., :3]
    rgb_b = b[..., :3]
    diff = np.abs(rgb_a.astype(np.int16) - rgb_b.astype(np.int16)).max(axis=2)
    y_cut = int(round(H * Y_FRAC))
    upper = diff[:y_cut, :]
    lower = diff[y_cut:, :]

    def stats(region):
        n = int(region.size)
        return {
            "n": n,
            "mean": float(region.mean()) if n else 0.0,
            "max": int(region.max()) if n else 0,
            "nonzero": int((region > 0).sum()),
            "gt2": int((region > 2).sum()),
            "gt8": int((region > 8).sum()),
            "gt24": int((region > 24).sum()),
        }

    identical_upper = bool((upper == 0).all())
    identical_full = bool((diff == 0).all())
    result = {
        "ok": identical_upper,
        "identical_upper": identical_upper,
        "identical_full": identical_full,
        "frame": FRAME_N,
        "t": t,
        "y_frac": Y_FRAC,
        "y_cut": y_cut,
        "upper": stats(upper),
        "lower": stats(lower),
        "full": stats(diff),
    }
    vis = np.zeros((H, W, 3), dtype=np.uint8)
    vis[..., 1] = np.clip(diff * 8, 0, 255).astype(np.uint8)
    vis[..., 2] = np.clip(diff * 3, 0, 255).astype(np.uint8)
    vis[y_cut, :, :] = (255, 255, 255)
    os.makedirs(IMG, exist_ok=True)
    vis_path = os.path.join(IMG, "T04f-6b2_lossless_g01_f521_diff.png")
    cv2.imwrite(vis_path, vis)
    print("wrote", vis_path, flush=True)

    outp = os.path.join(REP, "T04f-6b2_lossless_frame.json")
    with open(outp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(result, fh, indent=2)
        fh.write("\n")
    print(json.dumps(result, indent=2), flush=True)
    if identical_upper:
        print("VERDICT: PASS — upper band byte-identical; residual is encoder", flush=True)
    else:
        print("VERDICT: FAIL — renderer differs outside subtitle band", flush=True)
    return 0 if identical_upper else 2


if __name__ == "__main__":
    sys.exit(main())

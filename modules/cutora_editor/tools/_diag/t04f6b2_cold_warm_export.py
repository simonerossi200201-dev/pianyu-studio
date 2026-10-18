# -*- coding: utf-8 -*-
"""Two quality exports of g08 in one process; frame-diff each cue.

Confirms the file-level cold/warm split after warmup was withdrawn.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

import cv2
import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

OUT_JSON = os.path.join(REPO, "docs", "plan", "reports", "T04f-6b2_cold_warm_export.json")
OUT_IMG = os.path.join(REPO, "docs", "plan", "reports", "img")

CUES = [
    ("fx_grad", 1.027),
    ("fx_metal", 3.517),
    ("fx_neon", 6.175),
    ("fx_glass", 8.370),
    ("fx_fonts", 10.680),
    ("fx_paper", 13.400),
    ("fx_bubble", 16.100),
    ("fx_box", 18.750),
    ("t0", 0.000),
]


def sha256(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            c = f.read(1 << 20)
            if not c:
                break
            h.update(c)
    return h.hexdigest()


def grab(path, t):
    cap = cv2.VideoCapture(path)
    cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, float(t) * 1000.0))
    ok, frame = cap.read()
    cap.release()
    if not ok or frame is None:
        raise RuntimeError("no frame t=%.3f %s" % (t, path))
    return frame


def stats(a, b):
    d = np.abs(a.astype("int16") - b.astype("int16"))
    nz = int((d.max(axis=2) > 0).sum())
    n = int(d.shape[0] * d.shape[1])
    ch = d.max(axis=(0, 1)).tolist()
    bbox = None
    if nz:
        ys, xs = np.nonzero(d.max(axis=2) > 0)
        bbox = [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())]
    return {
        "identical": nz == 0,
        "max_delta": int(d.max()),
        "mean_delta": float(d.mean()),
        "changed_px": nz,
        "changed_pct": 100.0 * nz / max(1, n),
        "max_delta_BGR": [int(x) for x in ch],
        "bbox": bbox,
    }


def save_bgr(frame, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cv2.imwrite(path, frame)


def save_diff(a, b, path):
    d = np.abs(a.astype("int16") - b.astype("int16")).clip(0, 255).astype("uint8")
    vis = np.zeros_like(a)
    vis[..., 2] = d.max(axis=2)  # R
    vis[..., 1] = np.minimum(d.max(axis=2), 40)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cv2.imwrite(path, vis)


def install_draw_probe(log):
    from cutora.render.textfx import pipeline as pipe
    orig = pipe.draw

    def wrapped(painter, event, style, t, video_w, video_h, backdrop=None, **kw):
        preview = pipe._is_preview_painter(painter)
        skip = preview and pipe._degraded
        before = pipe._text_rasters
        rec = orig(painter, event, style, t, video_w, video_h, backdrop, **kw)
        if len(log) < 40:
            log.append({
                "t": float(t),
                "role": (event or {}).get("role"),
                "preview": bool(preview),
                "skip_blur": bool(skip),
                "degraded": bool(pipe._degraded),
                "text_raster_this_call": pipe._text_rasters > before,
                "device": type(painter.device()).__name__,
            })
        return rec

    pipe.draw = wrapped
    return orig


def main():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("CUTORA_USER_DATA", tempfile.mkdtemp(prefix="cutora_cwe_"))
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    from tools.golden_check import (
        boot_host, load_tasks, GOLDEN_ROOT, render_serial,
    )
    from cutora.render.textfx import clear_roi_cache
    from cutora.render.textfx.pipeline import reset_degrade, reset_draw_counters, draw_counters
    from cutora.render.textfx.glass_thick import clear_sdf_cache
    from cutora.render.export_renderer import ExportRenderer
    from cutora.render.frame_renderer import compute_render_order
    from PySide6.QtGui import QImage
    from cutora.render.readers import _cv2_frame_to_qimage

    host, app = boot_host()
    _ = app
    proj = os.path.join(GOLDEN_ROOT, "g08_fx_static", "project.asproj.json")
    _data, tasks = load_tasks(host, proj)
    task = tasks[0]
    w, h = [int(x) for x in (task.get("dims") or (1080, 1920))]
    probe = []
    orig = install_draw_probe(probe)

    def reset_all():
        clear_roi_cache()
        clear_sdf_cache()
        reset_degrade()
        reset_draw_counters()

    tmp1 = tempfile.mkdtemp(prefix="cw_r1_")
    tmp2 = tempfile.mkdtemp(prefix="cw_r2_")
    reset_all()
    p1 = render_serial(host, tasks, tmp1, "quality", False)[0]
    h1 = sha256(p1)
    c1 = draw_counters()
    probe_r1 = list(probe)
    probe.clear()
    reset_all()
    p2 = render_serial(host, tasks, tmp2, "quality", False)[0]
    h2 = sha256(p2)
    c2 = draw_counters()
    probe_r2 = list(probe)
    from cutora.render.textfx import pipeline as pipe
    pipe.draw = orig

    frames = []
    for name, t in CUES:
        a = grab(p1, t)
        b = grab(p2, t)
        st = stats(a, b)
        st["name"] = name
        st["t"] = t
        frames.append(st)
        prefix = os.path.join(OUT_IMG, "T04f-6b2_cw_exp_%s" % name)
        save_bgr(a, prefix + "_r1.png")
        save_bgr(b, prefix + "_r2.png")
        if not st["identical"]:
            save_diff(a, b, prefix + "_diff.png")

    # Still-frame pair with live video backdrop (no encode).
    vid = task.get("video")
    stills = []
    order = compute_render_order(task)

    def grab_q(t):
        cap = cv2.VideoCapture(vid)
        cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, float(t) * 1000.0))
        ok, frame = cap.read()
        cap.release()
        img = _cv2_frame_to_qimage(frame)
        from PySide6.QtCore import Qt
        if img.width() != w or img.height() != h:
            img = img.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
        return img

    dummy = dict(task)
    dummy.pop("timeline", None)
    for name, t in CUES[:8]:
        reset_all()
        bg = grab_q(t)

        def draw_bg(p, image=bg):
            p.drawImage(0, 0, image)

        c_cold = ExportRenderer.render_frame(
            dummy, w, h, t, draw_background=draw_bg, render_order=order, backdrop=bg)
        c_warm = ExportRenderer.render_frame(
            dummy, w, h, t, draw_background=draw_bg, render_order=order, backdrop=bg)
        from cutora.render.textfx.raster import as_bgra
        _o, a = as_bgra(c_cold)
        a = a.copy()
        _o, b = as_bgra(c_warm)
        b = b.copy()
        st = stats(a[..., :3][:, :, ::-1], b[..., :3][:, :, ::-1])  # to BGR-ish
        st["name"] = name
        st["t"] = t
        stills.append(st)

    report = {
        "r1_sha256_12": h1[:12],
        "r2_sha256_12": h2[:12],
        "r1_sha256": h1,
        "r2_sha256": h2,
        "same_hash": h1 == h2,
        "counters_r1": c1,
        "counters_r2": c2,
        "probe_r1_preview_any": any(x["preview"] for x in probe_r1),
        "probe_r1_skip_blur_any": any(x["skip_blur"] for x in probe_r1),
        "probe_r1_degraded_any": any(x["degraded"] for x in probe_r1),
        "probe_r1_devices": sorted({x["device"] for x in probe_r1}),
        "probe_r1_head": probe_r1[:12],
        "probe_r2_preview_any": any(x["preview"] for x in probe_r2),
        "probe_r2_skip_blur_any": any(x["skip_blur"] for x in probe_r2),
        "export_frame_diffs": frames,
        "still_render_frame_cold_vs_warm": stills,
    }
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print("r1", h1[:12], "r2", h2[:12], "same", h1 == h2, flush=True)
    for st in frames:
        print("CUE", st["name"], "identical", st["identical"],
              "max", st["max_delta"], "pct", "%.3f" % st["changed_pct"],
              flush=True)
    print("WROTE", OUT_JSON, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

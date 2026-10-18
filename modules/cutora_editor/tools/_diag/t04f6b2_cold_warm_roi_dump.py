# -*- coding: utf-8 -*-
"""Dump the text ROI on cache-miss during two g08 quality exports.

Also dump the pre-ffmpeg canvas at t=0. Isolates raster vs composite vs encoder.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

OUT_JSON = os.path.join(REPO, "docs", "plan", "reports", "T04f-6b2_cold_warm_roi_dump.json")
OUT_IMG = os.path.join(REPO, "docs", "plan", "reports", "img")


def _px(img):
    from cutora.render.textfx.raster import as_bgra
    o, bgra = as_bgra(img)
    return o, bgra.copy()


def _stats(a, b):
    d = np.abs(a.astype("int16") - b.astype("int16"))
    nz = int((d.max(axis=2) > 0).sum())
    n = int(a.shape[0] * a.shape[1])
    return {
        "identical": nz == 0,
        "max_delta": int(d.max()) if d.size else 0,
        "changed_px": nz,
        "changed_pct": 100.0 * nz / max(1, n),
        "max_delta_BGRA": [int(x) for x in d.max(axis=(0, 1)).tolist()] if d.size else [],
        "shape": list(a.shape),
    }


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


def main():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("CUTORA_USER_DATA", tempfile.mkdtemp(prefix="cutora_cwd_"))
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    from tools.golden_check import boot_host, load_tasks, GOLDEN_ROOT, render_serial
    from cutora.render.textfx import pipeline as pipe
    from cutora.render.textfx import clear_roi_cache
    from cutora.render.textfx.glass_thick import clear_sdf_cache
    from cutora.render.render_worker import RenderWorker
    from PySide6.QtGui import QImage

    host, app = boot_host()
    _ = app
    proj = os.path.join(GOLDEN_ROOT, "g08_fx_static", "project.asproj.json")
    _data, tasks = load_tasks(host, proj)

    misses = []  # {pass, role, t, skip_blur, preview, px}
    canvases = []  # {pass, t, px} for t==0

    orig_raster = pipe._raster_layers

    def wrap_raster(layout, spec, style, t, event, backdrop, video_w, video_h,
                    skip_blur, xf=None):
        key = pipe._cache_key(layout, spec, t, event, video_w, video_h, skip_blur)
        hit = pipe._cached_layer("text", key)
        bg, text, origin = orig_raster(
            layout, spec, style, t, event, backdrop, video_w, video_h,
            skip_blur, xf=xf)
        if hit is None:
            _o, px = _px(text)
            misses.append({
                "role": (event or {}).get("role"),
                "t": float(t),
                "skip_blur": bool(skip_blur),
                "text_w": int(text.width()),
                "text_h": int(text.height()),
                "px": px,
            })
        return bg, text, origin

    orig_write = RenderWorker._write_qimage_to_ffmpeg

    def wrap_write(self, proc, canvas):
        t = getattr(self, "_cw_t", None)
        if t == 0.0 or (t is not None and abs(t - 1.027) < 1e-6):
            _o, px = _px(canvas)
            canvases.append({"t": float(t), "px": px,
                             "fmt": int(canvas.format())})
        return orig_write(self, proc, canvas)

    orig_run_loop_t = None

    # Patch render_frame call site by wrapping ExportRenderer.render_frame
    from cutora.render import export_renderer as er
    orig_rf = er.ExportRenderer.render_frame

    def wrap_rf(task_data, w, h, t, **kw):
        # stash t on the current worker if any
        wrap_rf.last_t = t
        return orig_rf(task_data, w, h, t, **kw)

    er.ExportRenderer.render_frame = wrap_rf

    orig_write2 = RenderWorker._write_qimage_to_ffmpeg

    def wrap_write2(self, proc, canvas):
        t = getattr(wrap_rf, "last_t", None)
        if t is not None and (abs(float(t) - 0.0) < 1e-9 or abs(float(t) - 1.027) < 0.02):
            _o, px = _px(canvas)
            canvases.append({"t": float(t), "pass_id": wrap_rf.pass_id,
                             "px": px})
        return orig_write2(self, proc, canvas)

    RenderWorker._write_qimage_to_ffmpeg = wrap_write2
    pipe._raster_layers = wrap_raster

    def reset():
        clear_roi_cache()
        clear_sdf_cache()
        pipe.reset_degrade()
        pipe.reset_draw_counters()

    results = []
    for i in range(2):
        wrap_rf.pass_id = i
        n0 = len(misses)
        c0 = len(canvases)
        reset()
        tmp = tempfile.mkdtemp(prefix="cwd_%d_" % i)
        out = render_serial(host, tasks, tmp, "quality", False)[0]
        h = sha256(out)
        results.append({
            "pass": i,
            "sha12": h[:12],
            "n_misses": len(misses) - n0,
            "n_canvases": len(canvases) - c0,
        })
        print("PASS", i, h[:12], "misses", len(misses) - n0, flush=True)

    pipe._raster_layers = orig_raster
    RenderWorker._write_qimage_to_ffmpeg = orig_write2
    er.ExportRenderer.render_frame = orig_rf

    # Pair misses by (role, round(t, 3)) first occurrence per pass
    by_pass = {0: [], 1: []}
    # misses were appended in order: pass0 all, then pass1 all
    n0 = results[0]["n_misses"]
    p0, p1 = misses[:n0], misses[n0:]
    print("MISS_ORDER_R1", [(m["role"], m["text_w"], m["text_h"], round(m["t"], 3)) for m in p0], flush=True)
    print("MISS_ORDER_R2", [(m["role"], m["text_w"], m["text_h"], round(m["t"], 3)) for m in p1], flush=True)
    roi_cmp = []
    os.makedirs(OUT_IMG, exist_ok=True)
    map1 = {m["role"]: m for m in p1}
    for a in p0:
        b = map1.get(a["role"])
        if b is None:
            roi_cmp.append({"role": a["role"], "missing_r2": True})
            continue
        same_shape = a["px"].shape == b["px"].shape
        if same_shape:
            st = _stats(a["px"], b["px"])
        else:
            st = {
                "identical": False, "max_delta": None, "changed_px": None,
                "changed_pct": None, "max_delta_BGRA": [],
                "shape_mismatch": [list(a["px"].shape), list(b["px"].shape)],
            }
        row = {
            "role": a["role"],
            "t_r1": a["t"], "t_r2": b["t"],
            "skip_blur_r1": a["skip_blur"],
            "skip_blur_r2": b["skip_blur"],
            "size_r1": [a["text_w"], a["text_h"]],
            "size_r2": [b["text_w"], b["text_h"]],
            **{k: v for k, v in st.items() if k != "shape"},
        }
        roi_cmp.append(row)
        print("ROI", a["role"], "size", row["size_r1"], row["size_r2"],
              "identical", st.get("identical"), "max", st.get("max_delta"), flush=True)
        if not st.get("identical"):
            from PySide6.QtGui import QImage
            def save_px(px, path):
                hh, ww = px.shape[:2]
                q = QImage(px.data, ww, hh, ww * 4, QImage.Format.Format_ARGB32)
                q.copy().save(path)
            save_px(a["px"], os.path.join(OUT_IMG, "T04f-6b2_cw_roi_%s_r1.png" % a["role"]))
            save_px(b["px"], os.path.join(OUT_IMG, "T04f-6b2_cw_roi_%s_r2.png" % a["role"]))

    canvas_cmp = []
    c0n = results[0]["n_canvases"]
    c0, c1 = canvases[:c0n], canvases[c0n:]
    # match by rounded t
    def nearest(lst, t):
        return min(lst, key=lambda x: abs(x["t"] - t)) if lst else None
    for tmark in (0.0, 1.027):
        a = nearest(c0, tmark)
        b = nearest(c1, tmark)
        if a is None or b is None:
            canvas_cmp.append({"t": tmark, "missing": True})
            continue
        st = _stats(a["px"], b["px"])
        canvas_cmp.append({"t_a": a["t"], "t_b": b["t"], **st})
        print("CANVAS t~%.3f identical" % tmark, st["identical"],
              "max", st["max_delta"], "pct", "%.3f" % st["changed_pct"], flush=True)

    report = {
        "passes": [{"pass": r["pass"], "sha12": r["sha12"]} for r in results],
        "roi_miss_r1_vs_r2": [{k: v for k, v in row.items()} for row in roi_cmp],
        "canvas_r1_vs_r2": canvas_cmp,
    }
    with open(OUT_JSON, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print("WROTE", OUT_JSON, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

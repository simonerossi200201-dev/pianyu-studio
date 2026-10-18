# -*- coding: utf-8 -*-
"""Cold vs warm v2 raster for g08 roles. Does not record golden hashes.

Usage:
    python tools/t04f6b2_cold_warm.py
    python tools/t04f6b2_cold_warm.py --only-role 0
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

OUT_JSON = os.path.join(REPO, "docs", "plan", "reports", "T04f-6b2_cold_warm.json")
OUT_IMG = os.path.join(REPO, "docs", "plan", "reports", "img")


def _px(img):
    from cutora.render.textfx.raster import as_bgra
    owned, bgra = as_bgra(img)
    return owned, bgra.copy()


def _stats(a, b):
    import numpy as np
    if a.shape != b.shape:
        return {
            "shape_a": list(a.shape), "shape_b": list(b.shape),
            "identical": False,
        }
    d = np.abs(a.astype("int16") - b.astype("int16"))
    ch = d.max(axis=(0, 1)).tolist()  # B,G,R,A
    nz = int((d.max(axis=2) > 0).sum())
    n = int(d.shape[0] * d.shape[1])
    return {
        "identical": nz == 0,
        "max_delta": int(d.max()),
        "mean_delta": float(d.mean()),
        "mean_delta_changed": float(d.max(axis=2)[d.max(axis=2) > 0].mean()) if nz else 0.0,
        "changed_px": nz,
        "changed_pct": 100.0 * nz / max(1, n),
        "max_delta_BGRA": [int(x) for x in ch],
        "bbox": _bbox(d.max(axis=2) > 0) if nz else None,
    }


def _bbox(mask):
    import numpy as np
    ys, xs = np.nonzero(mask)
    return [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())]


def _save(img, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path)


def _diff_img(a, b):
    from PySide6.QtGui import QImage
    import numpy as np
    d = np.abs(a.astype("int16") - b.astype("int16")).clip(0, 255).astype("uint8")
    vis = np.zeros_like(d)
    vis[..., 0] = 0
    vis[..., 1] = 0
    vis[..., 2] = np.maximum(d.max(axis=2), d[..., 2])  # R
    vis[..., 3] = np.where(d.max(axis=2) > 0, 255, 0)
    h, w = vis.shape[:2]
    q = QImage(vis.data, w, h, w * 4, QImage.Format.Format_ARGB32)
    return q.copy()


def _role_jobs(task):
    jobs = []
    seen = set()
    for seg in task.get("segments") or []:
        if not isinstance(seg, dict):
            continue
        role = seg.get("role", "default")
        if role in seen:
            continue
        seen.add(role)
        t0 = float(seg.get("start") or 0.0)
        t1 = float(seg.get("end") or t0)
        t = (t0 + t1) * 0.5
        style = task.get("style") or {}
        if role != "default":
            info = (task.get("subtitle_roles") or {}).get(role) or {}
            rs = info.get("style") if isinstance(info, dict) else None
            if isinstance(rs, dict) and rs:
                style = rs
        fx = (style.get("fx") or {}) if isinstance(style, dict) else {}
        fill = fx.get("fill") or {}
        glow = fx.get("glow") or {}
        bg = fx.get("bg") or {}
        jobs.append({
            "role": role,
            "t": t,
            "event": seg,
            "style": style,
            "fill_type": fill.get("type"),
            "glow_mode": glow.get("mode"),
            "glow_sigma": glow.get("sigma_em"),
            "bg_type": bg.get("type"),
            "extrude": (fx.get("extrude") or {}).get("depth_em"),
        })
    return jobs


def _raster_pair(event, style, t, w, h, backdrop=None):
    from cutora.render.textfx.spec import from_style
    from cutora.render.textfx.layout import layout_event
    from cutora.render.textfx.raster import raster_text, raster_bg
    from cutora.render.textfx.anim import transform_at
    spec = from_style(style)
    layout = layout_event(event, style, spec, w, h)
    xf = transform_at(t, event, spec, layout)
    text = raster_text(layout, spec, style, skip_blur=False, t=t, xf=xf)
    bg = None
    if spec.bg.type in ("glass", "box", "texture", "bubble"):
        bg = raster_bg(layout, spec, backdrop, w, h, skip_blur=False)
    return text, bg, spec, layout


def test_degrade(w, h):
    from PySide6.QtGui import QImage, QPainter, QColor
    from cutora.render.textfx import pipeline as pipe
    pipe.reset_degrade()
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor("#1A1520"))
    p = QPainter(img)
    try:
        preview = pipe._is_preview_painter(p)
        skip = preview and pipe._degraded
        pipe._note_slow(50.0, preview)  # would trip if export counted
        after = {
            "degraded": bool(pipe._degraded),
            "slow_frames": int(pipe._slow_frames),
        }
    finally:
        p.end()
    return {
        "export_painter_is_preview": preview,
        "skip_blur_on_export_painter": bool(skip),
        "after_50ms_note_slow": after,
        "note": "_note_slow returns immediately when not preview; skip_blur is preview AND _degraded",
    }


def test_mutation(event, style, t, w, h, backdrop):
    """Draw twice through pipeline.draw; see if ROI_CACHE pixels change."""
    from PySide6.QtGui import QImage, QPainter, QColor
    from cutora.render.textfx import clear_roi_cache, draw
    from cutora.render.textfx.cache import ROI_CACHE
    from cutora.render.textfx.pipeline import reset_degrade
    from cutora.render.textfx.glass_thick import clear_sdf_cache
    clear_roi_cache()
    clear_sdf_cache()
    reset_degrade()
    canvas = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(QColor("#1A1520"))

    def once():
        p = QPainter(canvas)
        try:
            draw(p, event, style, t, w, h, backdrop=backdrop)
        finally:
            p.end()

    once()
    snap = {}
    for key, (img, origin) in list(ROI_CACHE._data.items()):
        _o, px = _px(img)
        snap[str(key)] = px
    once()
    rows = []
    for key, (img, origin) in list(ROI_CACHE._data.items()):
        k = str(key)
        _o, px = _px(img)
        if k in snap:
            st = _stats(snap[k], px)
            st["key"] = k
            rows.append(st)
    return {
        "cache_entries": len(snap),
        "any_mutated": any(not r.get("identical") for r in rows),
        "rows": rows,
    }


def test_ramp_brush():
    from PySide6.QtGui import QImage, QPainter, QPainterPath, QColor
    from PySide6.QtCore import QRectF
    from cutora.render.textfx.paint_fill import ramp_brush
    from cutora.render.textfx.ramps import METAL_STOPS
    rect = QRectF(10, 10, 400, 80)
    path = QPainterPath()
    path.addRoundedRect(rect, 8, 8)
    glyph = QPainterPath()
    glyph.addText(20, 70, _qfont_sn(), "Hello friends.")

    def paint(which):
        img = QImage(420, 100, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(0)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.fillPath(which, ramp_brush(rect, 90.0, [
            (0.0, "#F4EBD8"), (1.0, "#E0BE68")]))
        p.end()
        return img

    ra, rb = paint(path), paint(path)
    ga, gb = paint(glyph), paint(glyph)
    _oa, pa = _px(ra)
    _ob, pb = _px(rb)
    _oc, pc = _px(ga)
    _od, pd = _px(gb)
    return {
        "gradient_rounded_rect": _stats(pa, pb),
        "gradient_glyphs": _stats(pc, pd),
    }


def _qfont_sn():
    from cutora.ui.panels.fonts import get_font_manager
    return get_font_manager().get_font("SN Pro", 70, True, False)


def test_gauss():
    import numpy as np
    import cv2
    rng = np.zeros((64, 64), dtype=np.uint8)
    rng[16:48, 16:48] = 255
    a = cv2.GaussianBlur(rng.astype(np.float32), (0, 0), sigmaX=4.0)
    b = cv2.GaussianBlur(rng.astype(np.float32), (0, 0), sigmaX=4.0)
    d = np.abs(a - b)
    return {
        "identical": bool(d.max() == 0),
        "max_delta": float(d.max()),
        "mean_delta": float(d.mean()),
    }


def run_role(job, w, h, backdrop, save_prefix):
    from cutora.render.textfx import clear_roi_cache
    from cutora.render.textfx.pipeline import reset_degrade
    from cutora.render.textfx.glass_thick import clear_sdf_cache
    clear_roi_cache()
    clear_sdf_cache()
    reset_degrade()
    text1, bg1, spec, layout = _raster_pair(
        job["event"], job["style"], job["t"], w, h, backdrop)
    _o1, p1 = _px(text1)
    text2, bg2, _s2, _l2 = _raster_pair(
        job["event"], job["style"], job["t"], w, h, backdrop)
    _o2, p2 = _px(text2)
    st = _stats(p1, p2)
    bg_st = None
    if bg1 is not None and bg2 is not None:
        _b1, q1 = _px(bg1)
        _b2, q2 = _px(bg2)
        bg_st = _stats(q1, q2)
    if save_prefix:
        _save(text1, save_prefix + "_text_cold.png")
        _save(text2, save_prefix + "_text_warm.png")
        if not st.get("identical"):
            _save(_diff_img(p1, p2), save_prefix + "_text_diff.png")
        if bg1 is not None:
            _save(bg1, save_prefix + "_bg_cold.png")
            _save(bg2, save_prefix + "_bg_warm.png")
    mut = test_mutation(job["event"], job["style"], job["t"], w, h, backdrop)
    abl = {}
    if not st.get("identical"):
        abl = ablation(job, w, h, backdrop)
    return {
        "role": job["role"],
        "t": job["t"],
        "fill_type": job["fill_type"],
        "glow_mode": job["glow_mode"],
        "glow_sigma": job["glow_sigma"],
        "bg_type": job["bg_type"],
        "extrude_depth_em": job["extrude"],
        "text": st,
        "bg": bg_st,
        "mutation": mut,
        "ablation": abl,
        "skip_blur_forced_false": True,
    }


def ablation(job, w, h, backdrop):
    """Force layers off one at a time; which one kills the cold/warm delta."""
    style0 = job["style"]
    fx0 = dict(style0.get("fx") or {})
    variants = {
        "as_is": None,
        "fill_solid": {"fill": {**(fx0.get("fill") or {}), "type": "solid",
                                "colors": ["#F4EBD8"]}},
        "glow_off": {"glow": {**(fx0.get("glow") or {}), "sigma_em": 0}},
        "shadow_off": {"shadow": {**(fx0.get("shadow") or {}), "sigma_em": 0}},
        "stroke_off": {"stroke": {**(fx0.get("stroke") or {}), "width_em": 0}},
        "extrude_off": {"extrude": {**(fx0.get("extrude") or {}), "depth_em": 0}},
    }
    out = {}
    for name, patch in variants.items():
        if patch is None:
            stl = style0
        else:
            stl = dict(style0)
            fx = dict(fx0)
            fx.update(patch)
            stl["fx"] = fx
        t1, _b1, _s, _l = _raster_pair(job["event"], stl, job["t"], w, h, backdrop)
        t2, _b2, _s2, _l2 = _raster_pair(job["event"], stl, job["t"], w, h, backdrop)
        _o1, p1 = _px(t1)
        _o2, p2 = _px(t2)
        st = _stats(p1, p2)
        out[name] = {"identical": st.get("identical"), "max_delta": st.get("max_delta"),
                     "changed_pct": st.get("changed_pct")}
    return out


def main():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("CUTORA_USER_DATA", tempfile.mkdtemp(prefix="cutora_cw_"))
    p = argparse.ArgumentParser()
    p.add_argument("--only-role", type=int, default=-1)
    p.add_argument("--no-spawn", action="store_true")
    args = p.parse_args()
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    from tools.golden_check import boot_host, load_tasks, GOLDEN_ROOT
    from PySide6.QtGui import QImage, QColor
    host, app = boot_host()
    _ = app
    proj = os.path.join(GOLDEN_ROOT, "g08_fx_static", "project.asproj.json")
    _data, tasks = load_tasks(host, proj)
    task = tasks[0]
    w, h = task.get("dims") or (1080, 1920)
    w, h = int(w), int(h)
    bg = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    bg.fill(QColor("#1A1520"))
    jobs = _role_jobs(task)
    report = {
        "degrade": test_degrade(w, h),
        "gauss_first_vs_second": test_gauss(),
        "ramp_brush": None,
        "roles_same_process": [],
        "roles_fresh_process": [],
    }
    try:
        report["ramp_brush"] = test_ramp_brush()
    except Exception as e:
        report["ramp_brush"] = {"error": str(e)}

    if args.only_role >= 0:
        job = jobs[args.only_role]
        prefix = os.path.join(OUT_IMG, "T04f-6b2_cw_r%d_%s" % (args.only_role, job["role"]))
        row = run_role(job, w, h, bg, prefix)
        row["fresh_process"] = True
        print(json.dumps(row, ensure_ascii=False), flush=True)
        return 0

    for i, job in enumerate(jobs):
        prefix = os.path.join(OUT_IMG, "T04f-6b2_cw_seq%d_%s" % (i, job["role"]))
        row = run_role(job, w, h, bg, prefix)
        row["fresh_process"] = False
        row["seq"] = i
        report["roles_same_process"].append(row)
        print("SEQ", i, job["role"],
              "text_identical", row["text"].get("identical"),
              "max", row["text"].get("max_delta"),
              "mutated", row["mutation"].get("any_mutated"),
              flush=True)

    if not args.no_spawn:
        py = sys.executable
        script = os.path.abspath(__file__)
        for i, job in enumerate(jobs):
            env = dict(os.environ)
            env["QT_QPA_PLATFORM"] = "offscreen"
            r = subprocess.run(
                [py, "-u", script, "--only-role", str(i)],
                cwd=REPO, env=env, capture_output=True, text=True,
                encoding="utf-8", errors="replace")
            line = ""
            for ln in reversed((r.stdout or "").splitlines()):
                if ln.startswith("{"):
                    line = ln
                    break
            if not line:
                report["roles_fresh_process"].append({
                    "role": job["role"], "error": (r.stderr or r.stdout or "")[-800:],
                    "code": r.returncode,
                })
                print("FRESH", i, job["role"], "FAIL", r.returncode, flush=True)
                continue
            row = json.loads(line)
            report["roles_fresh_process"].append(row)
            print("FRESH", i, job["role"],
                  "text_identical", row.get("text", {}).get("identical"),
                  "max", row.get("text", {}).get("max_delta"),
                  flush=True)

    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print("WROTE", OUT_JSON, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

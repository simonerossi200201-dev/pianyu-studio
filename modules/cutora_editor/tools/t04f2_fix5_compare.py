# -*- coding: utf-8 -*-
"""T04f-2-fix5: sample-space vs 1:1 stills, 4x crops, six metrics, lock hashes.

    runtime/venv/Scripts/python.exe tools/t04f2_fix5_compare.py --tag before
    runtime/venv/Scripts/python.exe tools/t04f2_fix5_compare.py --tag after
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from tools.fx_contact_sheet import (  # noqa: E402
    SAMPLE, _event, _stripes_bgr, _profile_scores, _metrics_stripes_cell,
)

IMG_DIR = os.path.join(REPO, "docs", "plan", "reports", "img")
CANVAS_W, CANVAS_H = 1920, 1080
SAMPLE_W, SAMPLE_H = 800, 450
LOCK_A = "1ecaddd881425d0ee76ce14eac60511e3f96640529ea933687a6e783a0a82ca0"


def _thick_fx():
    return {
        "type": "glass", "color": "#0B1A33", "alpha": 0.35,
        "radius_em": 0.2, "pad_em": [0.5, 0.35],
        "glass_sigma_em": 0.4, "glass_border": True,
        "glass_thickness_em": 0.14, "glass_refraction": 0.55,
        "glass_bevel": 0.7, "glass_sheen": 0.45, "glass_light_deg": 315,
    }


def _metrics_fx():
    return {
        "type": "glass", "color": "#0B1A33", "alpha": 0.35,
        "radius_em": 0.45, "pad_em": [0.55, 0.32],
        "glass_sigma_em": 0.7, "glass_border": True,
        "glass_thickness_em": 0.14, "glass_refraction": 0.55,
        "glass_bevel": 0.7, "glass_sheen": 0.45, "glass_light_deg": 315,
    }


def _style(w, h, fontsize, bg_fx, text_pos_y=0.55):
    from cutora.subtitle.stylepack import apply_pack_to_task, load_pack
    pack = load_pack(os.path.join(REPO, "resources", "stylepacks", "glass-white"))
    task = {"timeline": {"width": w, "height": h}, "style": {}}
    apply_pack_to_task(task, pack, apply_animation=True)
    st = task["style"]
    st["fontsize"] = fontsize
    st["fontsize_pct"] = fontsize / float(min(w, h))
    st["pos_x"] = w / 2.0
    st["pos_y"] = h * float(text_pos_y)
    fx = dict(st["fx"])
    fx["bg"] = dict(bg_fx)
    fx["glow"] = {"sigma_em": 0}
    fx["shadow"] = {"sigma_em": 0}
    st["fx"] = fx
    return st


def _qimg_from_bgr(bgr):
    from cutora.render.textfx.raster import bgr_numpy_to_qimage
    from PySide6.QtGui import QImage
    q = bgr_numpy_to_qimage(bgr)
    return q.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)


def _solid_qimg(w, h, hex_color):
    from PySide6.QtGui import QColor, QImage
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(hex_color))
    return img


def _scale_qimg(src, w, h):
    from PySide6.QtCore import Qt
    return src.scaled(
        w, h,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation)


def _paint_canvas(st, event, bd, w, h):
    from PySide6.QtGui import QImage, QPainter
    from cutora.render.textfx.layout import layout_event
    from cutora.render.textfx.raster import raster_roi
    from cutora.render.textfx.spec import from_style
    spec = from_style(st)
    lay = layout_event(event, st, spec, w, h)
    roi = raster_roi(lay, spec, st, bd, w, h, skip_blur=False)
    canvas = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(0)
    p = QPainter(canvas)
    shown = bd if bd.width() == w and bd.height() == h else _scale_qimg(bd, w, h)
    p.drawImage(0, 0, shown)
    p.drawImage(lay.origin[0], lay.origin[1], roi)
    p.end()
    return canvas, lay, spec, roi


def _save_png(qimg, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not qimg.save(path, "PNG"):
        raise SystemExit("failed to write %s" % path)
    print("wrote", path, flush=True)


def _corner_4x(canvas, lay, path, side=220, pad=12, roi=False):
    from PySide6.QtCore import Qt
    hx, hy = float(lay.hit_x), float(lay.hit_y)
    if roi:
        hx -= float(lay.origin[0])
        hy -= float(lay.origin[1])
    x0 = max(0, int(round(hx)) - pad)
    y0 = max(0, int(round(hy)) - pad)
    crop = canvas.copy(
        x0, y0,
        min(side, canvas.width() - x0),
        min(side, canvas.height() - y0))
    zoom = crop.scaled(
        crop.width() * 4, crop.height() * 4,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.FastTransformation)
    _save_png(zoom, path)
    return zoom


def _digest_roi(roi):
    from cutora.render.textfx.raster import as_bgra
    _o, bgra = as_bgra(roi)
    return hashlib.sha256(bgra.tobytes()).hexdigest(), bgra


def _sample_sizes(lay, spec, bd, w, h):
    from cutora.render.textfx.raster import _glass_rect, glass_blur_pair
    from cutora.render.textfx.glass_thick import rim_sigma_px
    rect = _glass_rect(lay, spec)
    tw = max(1, int(round(rect.width())))
    th = max(1, int(round(rect.height())))
    sigma = spec.bg.glass_sigma_em * lay.em
    pair = glass_blur_pair(
        bd, rect, w, h, sigma, False,
        sigma_rim=rim_sigma_px(lay.em),
        band_px=int(np.ceil(spec.bg.glass_thickness_em * lay.em * 1.5 + 2.0)))
    sh, sw = pair[0].shape[:2]
    return sw, sh, tw, th


def _crease_fwhm_px(native: np.ndarray, w_px: float) -> float:
    """FWHM of the inner-step dip around d≈width, in profile pixels."""
    if native.size < 8:
        return 0.0
    lo = min(len(native) - 3, max(1, int(round(0.5 * w_px))))
    hi = min(len(native) - 1, max(lo + 2, int(round(1.4 * w_px))))
    seg = native[lo:hi]
    min_i = lo + int(np.argmin(seg))
    n0 = max(0, min_i - 3)
    n1 = min(len(native) - 1, min_i + 3)
    neigh = float(np.mean([native[n0], native[n1]]))
    at = float(native[min_i])
    depth = neigh - at
    if depth < 2.0:
        return 0.0
    half = at + 0.5 * depth
    left = min_i
    while left > 0 and float(native[left]) < half:
        left -= 1
    right = min_i
    while right < len(native) - 1 and float(native[right]) < half:
        right += 1
    return float(right - left)


def _dark_metrics(canvas, lay, as_bgra):
    from cutora.render.textfx.raster import as_bgra as _as
    _ = as_bgra
    _o, bgra = _as(canvas)
    luma = bgra[..., :3].astype(np.float64).mean(axis=2)
    x0 = int(round(lay.hit_x))
    y0 = int(round(lay.hit_y))
    x1 = x0 + max(1, int(round(lay.hit_w)))
    y1 = y0 + max(1, int(round(lay.hit_h)))
    x0 = max(0, min(luma.shape[1] - 1, x0))
    y0 = max(0, min(luma.shape[0] - 1, y0))
    x1 = max(x0 + 1, min(luma.shape[1], x1))
    y1 = max(y0 + 1, min(luma.shape[0], y1))
    w_px = 0.14 * float(lay.em)
    mx = x0 + (x1 - x0) // 3
    depth = max(8, int(round(2.2 * w_px)))
    native = luma[y0:min(luma.shape[0], y0 + depth), mx]
    out = _profile_scores(native, w_px)
    rad = max(4, int(round(0.45 * float(lay.em))))
    band = max(2, int(round(0.30 * w_px)))
    lx0 = min(x1 - 1, x0 + rad)
    lx1 = min(x1, lx0 + max(8, (x1 - x0) // 4))
    bx1 = max(x0 + 1, x1 - rad)
    bx0 = max(x0, bx1 - max(8, (x1 - x0) // 4))
    lit = float(luma[y0:y0 + band, lx0:lx1].mean()) if y0 + band <= y1 else 0.0
    back = float(luma[y1 - band:y1, bx0:bx1].mean()) if y1 - band >= y0 else 0.0
    out["lit"] = round(lit, 2)
    out["back"] = round(back, 2)
    out["lit_vs_back_delta"] = round(lit - back, 2)
    out["pass_sides"] = bool((lit - back) >= 20.0)
    out["crease_fwhm_px"] = round(_crease_fwhm_px(native, w_px), 2)
    out["em"] = round(float(lay.em), 2)
    out["w_px"] = round(w_px, 2)
    sigma = max(1.0, min(1.5, 0.012 * float(lay.em)))
    out["step_sigma_design"] = round(sigma, 3)
    return out


def _k_band(lay, spec):
    from cutora.render.textfx.glass_thick import k_debug
    from cutora.render.textfx.raster import _rounded_mask_u8
    tw = max(1, int(round(lay.hit_w)))
    th = max(1, int(round(lay.hit_h)))
    radius = spec.bg.radius_em * lay.em
    mask = _rounded_mask_u8(tw, th, radius)
    k, width, ring, band_w = k_debug(mask, lay.em, spec.bg.glass_thickness_em, radius)
    _ = k, ring
    return {
        "w_px": round(width, 2),
        "k_gt_0.1_median_width_px": round(band_w, 2),
        "pass_width": bool(band_w >= 0.6 * width),
        "em": round(float(lay.em), 2),
    }


def _lock_a():
    from PySide6.QtGui import QColor, QImage
    from cutora.render.textfx.layout import layout_event
    from cutora.render.textfx.raster import raster_bg
    from cutora.render.textfx.spec import from_style
    w, h = 640, 360
    bd = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    bd.fill(QColor("#808080"))
    st = _style(w, h, 72, _thick_fx())
    spec = from_style(st)
    lay = layout_event(_event(SAMPLE), st, spec, w, h)
    img = raster_bg(lay, spec, bd, w, h, skip_blur=False)
    digest, _bgra = _digest_roi(img)
    return digest, img


def _lock_b():
    from cutora.render.textfx.layout import layout_event
    from cutora.render.textfx.raster import raster_bg
    from cutora.render.textfx.spec import from_style
    w, h = CANVAS_W, CANVAS_H
    full = _solid_qimg(w, h, "#808080")
    bd = _scale_qimg(full, SAMPLE_W, SAMPLE_H)
    st = _style(w, h, 72, _thick_fx())
    spec = from_style(st)
    lay = layout_event(_event(SAMPLE), st, spec, w, h)
    img = raster_bg(lay, spec, bd, w, h, skip_blur=False)
    sw, sh, tw, th = _sample_sizes(lay, spec, bd, w, h)
    digest, _bgra = _digest_roi(img)
    return digest, img, lay, sw, sh, tw, th


def _lock_c():
    from cutora.render.textfx.layout import layout_event
    from cutora.render.textfx.raster import _glass_rect, raster_bg
    from cutora.render.textfx.spec import from_style
    w, h = CANVAS_W, CANVAS_H
    bd = _solid_qimg(w, h, "#808080")
    fx = _thick_fx()
    fx["pad_em"] = [0.9, 1.15]
    st = _style(w, h, 140, fx, text_pos_y=0.50)
    st["wrap_left"] = 40
    st["wrap_right"] = w - 40
    spec = from_style(st)
    text = "Find its place in the world today now"
    lay = layout_event(_event(text), st, spec, w, h)
    rect = _glass_rect(lay, spec)
    tw = max(1, int(round(rect.width())))
    th = max(1, int(round(rect.height())))
    img = raster_bg(lay, spec, bd, w, h, skip_blur=False)
    digest, _bgra = _digest_roi(img)
    return digest, img, lay, tw, th, tw * th


def main(argv=None) -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if "CUTORA_USER_DATA" not in os.environ:
        os.environ["CUTORA_USER_DATA"] = tempfile.mkdtemp(prefix="cutora_fix5_")
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)

    p = argparse.ArgumentParser()
    p.add_argument("--tag", default="after", help="before | after")
    args = p.parse_args(argv)

    from cutora.app import apply_ui_locale
    apply_ui_locale()
    from PySide6.QtWidgets import QApplication, QStyleFactory
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.theme.apply import apply_app_qss
    from cutora.ui.theme.fonts_ui import register_ui_fonts, ui_qfont
    from cutora.render.textfx.raster import as_bgra
    from cutora.render.textfx.glass_thick import k_debug
    from cutora.render.textfx.raster import _rounded_mask_u8

    app = QApplication.instance() or QApplication(["t04f2-fix5"])
    load_embedded_fonts()
    register_ui_fonts()
    app.setFont(ui_qfont())
    app.setStyle(QStyleFactory.create("Fusion"))
    apply_app_qss(app)

    tag = args.tag
    os.makedirs(IMG_DIR, exist_ok=True)
    event = _event(SAMPLE)

    # --- visual: same frame, same style, sample vs 1:1 ---
    stripes_full = _qimg_from_bgr(_stripes_bgr(CANVAS_W, CANVAS_H))
    stripes_s = _scale_qimg(stripes_full, SAMPLE_W, SAMPLE_H)
    st_vis = _style(CANVAS_W, CANVAS_H, 96, _metrics_fx())
    canvas_s, lay_s, spec_s, roi_s = _paint_canvas(
        st_vis, event, stripes_s, CANVAS_W, CANVAS_H)
    canvas_1, lay_1, spec_1, roi_1 = _paint_canvas(
        st_vis, event, stripes_full, CANVAS_W, CANVAS_H)
    sw, sh, tw, th = _sample_sizes(lay_s, spec_s, stripes_s, CANVAS_W, CANVAS_H)
    print("sample_path sw,sh=%d,%d target=%d,%d sw*2<tw=%s" % (
        sw, sh, tw, th, sw * 2 < tw), flush=True)
    _save_png(canvas_s, os.path.join(IMG_DIR, "T04f-2_fix5_sample_%s.png" % tag))
    _save_png(canvas_1, os.path.join(IMG_DIR, "T04f-2_fix5_1to1_%s.png" % tag))
    _corner_4x(canvas_s, lay_s, os.path.join(
        IMG_DIR, "T04f-2_fix5_sample_%s_4x.png" % tag))
    _corner_4x(canvas_1, lay_1, os.path.join(
        IMG_DIR, "T04f-2_fix5_1to1_%s_4x.png" % tag))

    # dark plate for bevel / crease metrics
    dark_full = _solid_qimg(CANVAS_W, CANVAS_H, "#141A26")
    dark_s = _scale_qimg(dark_full, SAMPLE_W, SAMPLE_H)
    canvas_ds, lay_ds, spec_ds, _r = _paint_canvas(
        st_vis, event, dark_s, CANVAS_W, CANVAS_H)
    canvas_d1, lay_d1, spec_d1, _r = _paint_canvas(
        st_vis, event, dark_full, CANVAS_W, CANVAS_H)
    _save_png(canvas_ds, os.path.join(IMG_DIR, "T04f-2_fix5_dark_sample_%s.png" % tag))
    _save_png(canvas_d1, os.path.join(IMG_DIR, "T04f-2_fix5_dark_1to1_%s.png" % tag))
    _corner_4x(canvas_ds, lay_ds, os.path.join(
        IMG_DIR, "T04f-2_fix5_dark_sample_%s_4x.png" % tag))
    _corner_4x(canvas_d1, lay_d1, os.path.join(
        IMG_DIR, "T04f-2_fix5_dark_1to1_%s_4x.png" % tag))

    report = {
        "tag": tag,
        "sample_sw_sh": [sw, sh],
        "sample_target_wh": [tw, th],
        "sample_space_would_be": bool(sw * 2 < tw or sh * 2 < th),
        "k_band_sample": _k_band(lay_ds, spec_ds),
        "k_band_1to1": _k_band(lay_d1, spec_d1),
        "dark_sample": _dark_metrics(canvas_ds, lay_ds, as_bgra),
        "dark_1to1": _dark_metrics(canvas_d1, lay_d1, as_bgra),
        "stripes_sample": _metrics_stripes_cell(canvas_s, lay_s, 0.14 * lay_s.em, as_bgra),
        "stripes_1to1": _metrics_stripes_cell(canvas_1, lay_1, 0.14 * lay_1.em, as_bgra),
    }

    da, img_a = _lock_a()
    db, img_b, lay_b, bsw, bsh, btw, bth = _lock_b()
    dc, img_c, lay_c, ctw, cth, cpx = _lock_c()
    _save_png(img_b, os.path.join(IMG_DIR, "T04f-2_fix5_lock_b_%s.png" % tag))
    _save_png(img_c, os.path.join(IMG_DIR, "T04f-2_fix5_lock_c_%s.png" % tag))
    _corner_4x(img_b, lay_b, os.path.join(
        IMG_DIR, "T04f-2_fix5_lock_b_%s_4x.png" % tag), side=160, roi=True)
    _corner_4x(img_c, lay_c, os.path.join(
        IMG_DIR, "T04f-2_fix5_lock_c_%s_4x.png" % tag), side=280, roi=True)
    report["lock_a"] = {"sha256": da, "match_baseline": da == LOCK_A}
    report["lock_b"] = {
        "sha256": db, "sw": bsw, "sh": bsh, "tw": btw, "th": bth,
        "sample_space": bool(bsw * 2 < btw),
    }
    report["lock_c"] = {
        "sha256": dc, "tw": ctw, "th": cth, "pixels": cpx,
        "gt_400k": bool(cpx > 400000),
    }
    out_json = os.path.join(
        REPO, "docs", "plan", "reports", "T04f-2_fix5_compare_%s.json" % tag)
    with open(out_json, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    print("wrote", out_json, flush=True)
    _ = k_debug, _rounded_mask_u8
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

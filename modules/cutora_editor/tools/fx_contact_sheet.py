# -*- coding: utf-8 -*-
"""T04f-2 contact sheet. Geometry is hardcoded — do not shrink to "fit"."""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import tempfile

import cv2
import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from cutora.render.textfx.static_fx import (
    SHEET_CAP_RATIO,
    SHEET_CELL_H,
    SHEET_CELL_W,
    SHEET_COLS,
    SHEET_DARK,
    SHEET_LABEL_H,
    anim_cells,
    effect_cells,
    emphasis_cells,
)

DEFAULT_VIDEO = r"D:\1_PROJECT\2026-09-07\B\T0012-李良v03\clips\_stitched\T012.mp4"
DEFAULT_PROJECT = os.path.join(
    REPO, "tests", "golden", "g08_stylepack_amber", "project.asproj.json")
DEFAULT_T_MS = 178000
ZOOM_DIR = os.path.join(REPO, "build", "fx_zoom")
SAMPLE = "Place"


def _deep_merge(base: dict, patch: dict) -> dict:
    out = copy.deepcopy(base)
    for key, val in patch.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = copy.deepcopy(val)
    return out


def _load_project(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _first_task(data: dict) -> dict:
    if isinstance(data.get("tasks"), list) and data["tasks"]:
        return data["tasks"][0]
    return data


def _video_path(task: dict, override: str | None) -> str:
    if override and os.path.isfile(override):
        return override
    if os.path.isfile(DEFAULT_VIDEO):
        return DEFAULT_VIDEO
    for key in ("video", "path", "src"):
        raw = task.get(key)
        if isinstance(raw, str) and os.path.isfile(raw):
            return raw
    return os.path.join(REPO, "tests", "golden", "_media", "g01", "video.mp4")


def _grab_frame(video: str, t_ms: int) -> np.ndarray:
    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        raise SystemExit("cannot open video: %s" % video)
    cap.set(cv2.CAP_PROP_POS_MSEC, float(t_ms))
    ok, frame = cap.read()
    if not ok or frame is None:
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        ok, frame = cap.read()
    cap.release()
    if not ok or frame is None:
        raise SystemExit("cannot read frame from %s" % video)
    return frame


def _event(text: str, end: float | None = None) -> dict:
    lines = text.split("\n")
    words, t = [], 0.0
    for line in lines:
        for w in line.split():
            words.append({"word": w, "start": t, "end": t + 0.2})
            t += 0.2
    dur = float(end) if end is not None else max(2.0, t + 0.5)
    return {
        "text": text.replace("\n", " "), "lines": lines,
        "start": 0.0, "end": dur, "words": words, "id": "sheet0",
    }


def _stripes_bgr(cw: int, ch: int) -> np.ndarray:
    """High-contrast diagonal bars + dots so refraction is readable."""
    yy, xx = np.mgrid[0:ch, 0:cw]
    stripe = (((xx * 3 + yy * 4) // 36) % 2).astype(np.uint8)
    img = np.empty((ch, cw, 3), dtype=np.uint8)
    img[stripe == 0] = (40, 48, 220)
    img[stripe == 1] = (30, 220, 230)
    for y0 in range(48, ch, 96):
        for x0 in range(48, cw, 96):
            r2 = (yy - y0) ** 2 + (xx - x0) ** 2
            img[r2 < 16 ** 2] = (18, 18, 18)
            ring = (r2 < 22 ** 2) & (r2 >= 16 ** 2)
            img[ring] = (245, 245, 245)
    return img


def _cover_bgr(frame_bgr: np.ndarray, cw: int, ch: int) -> np.ndarray:
    h, w = frame_bgr.shape[:2]
    scale = max(cw / float(w), ch / float(h))
    nw, nh = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    resized = cv2.resize(frame_bgr, (nw, nh), interpolation=cv2.INTER_AREA)
    x0 = max(0, (nw - cw) // 2)
    y0 = max(0, (nh - ch) // 2)
    return resized[y0:y0 + ch, x0:x0 + cw]


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="T04f-2 fx contact sheet")
    p.add_argument("--project", default=DEFAULT_PROJECT)
    p.add_argument("--out-dir", default=os.path.join(REPO, "build"))
    p.add_argument("--video", default="")
    p.add_argument("--t-ms", type=int, default=DEFAULT_T_MS)
    p.add_argument("--metrics", action="store_true",
                   help="compute thick-glass §2.1 scores; implies --glass-only")
    p.add_argument("--glass-only", action="store_true",
                   help="only render bg-glass-* cells / 4x crops")
    p.add_argument("--emphasis", action="store_true",
                   help="T04f-3 emphasis/scatter sheet (does not rewrite T04f-2)")
    p.add_argument("--anim", action="store_true",
                   help="T04f-4a animation sheet (does not rewrite T04f-2)")
    return p.parse_args(argv)


def main(argv=None) -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if "CUTORA_USER_DATA" not in os.environ:
        os.environ["CUTORA_USER_DATA"] = tempfile.mkdtemp(prefix="cutora_fx_sheet_")
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    args = parse_args(argv)
    if args.metrics:
        args.glass_only = True
    return _render(args)


def _render(args) -> int:
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtWidgets import QApplication, QStyleFactory

    from cutora.app import apply_ui_locale
    from cutora.render.textfx.layout import layout_event
    from cutora.render.textfx.raster import bgr_numpy_to_qimage, raster_roi
    from cutora.render.textfx.spec import from_style
    from cutora.subtitle.stylepack import builtin_dir, load_pack
    from cutora.ui.panels.fonts import get_font_manager, load_embedded_fonts
    from cutora.ui.theme.apply import apply_app_qss
    from cutora.ui.theme.fonts_ui import register_ui_fonts, ui_qfont

    apply_ui_locale()
    app = QApplication.instance() or QApplication(["fx-contact-sheet"])
    load_embedded_fonts()
    register_ui_fonts()
    app.setFont(ui_qfont())
    app.setStyle(QStyleFactory.create("Fusion"))
    apply_app_qss(app)

    data = _load_project(args.project)
    task = _first_task(data)
    vid = _video_path(task, args.video or None)
    frame = _grab_frame(vid, args.t_ms)
    if args.anim:
        cell_fn = anim_cells
        sheet_tag = "T04f-4c"
    elif args.emphasis:
        cell_fn = emphasis_cells
        sheet_tag = "T04f-3"
    else:
        cell_fn = effect_cells
        sheet_tag = "T04f-2"
    cells = cell_fn("frame")
    rows = int(np.ceil(len(cells) / float(SHEET_COLS)))
    pitch_x, pitch_y = SHEET_CELL_W, SHEET_CELL_H + SHEET_LABEL_H
    sheet_w, sheet_h = SHEET_COLS * pitch_x, rows * pitch_y
    assert pitch_y > SHEET_CELL_H
    os.makedirs(args.out_dir, exist_ok=True)
    os.makedirs(ZOOM_DIR, exist_ok=True)

    pack = load_pack(os.path.join(builtin_dir(), "glass-white"))
    dark_hex = SHEET_DARK
    grounds = (
        ("frame", None),
        ("dark", dark_hex),
    )
    cap_min = SHEET_CAP_RATIO * SHEET_CELL_H
    fontsize = _fontsize_for_cap(get_font_manager(), cap_min)
    if args.emphasis or args.anim:
        fontsize = min(int(fontsize), 96)
    print("fontsize=%d cap_min=%.1f" % (fontsize, cap_min), flush=True)

    metric_cells = {}
    for ground, dark in grounds:
        cells = cell_fn(ground)
        sheet = None
        sp = None
        if not args.glass_only:
            sheet = QImage(sheet_w, sheet_h, QImage.Format.Format_ARGB32_Premultiplied)
            sheet.fill(QColor("#0B1A33"))
            sp = QPainter(sheet)
            sp.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        for i, cell in enumerate(cells):
            if args.glass_only and not str(cell["id"]).startswith("bg-glass"):
                continue
            col, row = i % SHEET_COLS, i // SHEET_COLS
            x, y = col * pitch_x, row * pitch_y
            cell_img, lay = _paint_cell(
                cell, frame, dark, fontsize, pack,
                bgr_numpy_to_qimage, from_style, layout_event, raster_roi)
            if sp is not None:
                sp.drawImage(x, y, cell_img)
                _paint_label(sp, QRectF(x, y + SHEET_CELL_H, SHEET_CELL_W, SHEET_LABEL_H),
                             "%s  %s" % (cell["id"], cell["title"]))
            _write_zoom(cell_img, cell["id"], ground, lay)
            if args.metrics:
                metric_cells[(cell["id"], ground)] = (cell_img, lay, fontsize)
        if sp is not None:
            sp.end()
            out_path = os.path.join(args.out_dir, "fx_contact_sheet_%s.png" % ground)
            if not sheet.save(out_path, "PNG"):
                raise SystemExit("failed to write %s" % out_path)
            print("wrote", out_path, "cells=%d %dx%d" % (
                len(cells), sheet_w, sheet_h), flush=True)
            report_img = os.path.join(
                REPO, "docs", "plan", "reports", "img",
                "%s_fx_contact_sheet_%s.png" % (sheet_tag, ground))
            os.makedirs(os.path.dirname(report_img), exist_ok=True)
            sheet.save(report_img, "PNG")
            print("wrote", report_img, flush=True)
    print("zoom dir", ZOOM_DIR, flush=True)
    if args.metrics:
        _write_glass_metrics(metric_cells, args.out_dir)
    return 0


def _painted_cap(mgr, size: int) -> float:
    from PySide6.QtGui import QColor, QImage, QPainter
    from cutora.render.textfx.raster import as_bgra
    img = QImage(size * 3, size * 3, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    p = QPainter(img)
    p.setPen(QColor("#FFFFFF"))
    p.setFont(mgr.get_font("Arimo", size, bold=True))
    p.drawText(size, int(size * 1.6), "H")
    p.end()
    _o, bgra = as_bgra(img)
    ys = np.where(bgra[..., 3] > 40)[0]
    if len(ys) == 0:
        return 0.0
    return float(ys.max() - ys.min() + 1)


def _fontsize_for_two_line_bubble(cap_fontsize: int) -> int:
    """Two lines + pad + gap must sit inside the cell (review §2.3)."""
    pad_y = 0.22
    gap = 0.28
    line = 1.30
    extra = 12.0
    coeff = 2.0 * line + gap + 2.0 * pad_y
    budget = SHEET_CELL_H * 0.72
    fs = int((budget - extra) / coeff)
    return max(24, min(int(cap_fontsize), fs))


def _fontsize_for_cap(mgr, cap_min: float) -> int:
    lo, hi = 24, int(SHEET_CELL_H)
    best = int(cap_min)
    while lo <= hi:
        mid = (lo + hi) // 2
        cap = _painted_cap(mgr, mid)
        if cap >= cap_min:
            best = mid
            hi = mid - 1
        else:
            lo = mid + 1
    return max(8, best)


def _base_style(pack, fontsize: int) -> dict:
    from cutora.subtitle.stylepack import apply_pack_to_task
    task = {
        "timeline": {"width": SHEET_CELL_W, "height": SHEET_CELL_H},
        "style": {},
    }
    apply_pack_to_task(task, pack, apply_animation=True)
    style = task["style"]
    fx = dict(style.get("fx") or {})
    fx["bg"] = {"type": "none"}
    fx["glow"] = {"sigma_em": 0, "mode": "soft"}
    fx["shadow"] = {"sigma_em": 0, "color": "#141A26", "alpha": 0.6}
    fx["stroke"] = {"width_em": 0, "color": "#141A26"}
    fx["extrude"] = {"depth_em": 0, "angle": 135, "color": ""}
    style["fx"] = fx
    style["fontsize"] = fontsize
    style["fontsize_pct"] = fontsize / float(min(SHEET_CELL_W, SHEET_CELL_H))
    style["pos_x"] = SHEET_CELL_W / 2.0
    style["pos_y"] = SHEET_CELL_H * 0.55
    style["wrap_lines"] = 8
    style["wrap_width_percent"] = max(
        1, int(round((SHEET_CELL_W - 32) / float(SHEET_CELL_W) * 100.0)))
    style.pop("anchor", None)
    style.pop("offset_x_pct", None)
    style.pop("offset_y_pct", None)
    rules = dict(style.get("layout_rules") or {})
    rules["max_lines"] = 8
    rules["max_chars_per_line"] = 80
    style["layout_rules"] = rules
    return style


def _cell_backdrop(frame_bgr, dark_hex, bgr_numpy_to_qimage):
    from PySide6.QtGui import QColor, QImage
    if dark_hex:
        img = QImage(SHEET_CELL_W, SHEET_CELL_H, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(QColor(dark_hex))
        return img
    crop = _cover_bgr(frame_bgr, SHEET_CELL_W, SHEET_CELL_H)
    qimg = bgr_numpy_to_qimage(crop)
    if qimg is None:
        raise SystemExit("frame convert failed")
    return qimg.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)


def _paint_cell(cell, frame_bgr, dark_hex, fontsize, pack,
                bgr_numpy_to_qimage, from_style, layout_event, raster_roi):
    from PySide6.QtGui import QImage, QPainter
    if cell.get("ground") == "stripes":
        from PySide6.QtGui import QImage
        crop = _stripes_bgr(SHEET_CELL_W, SHEET_CELL_H)
        qimg = bgr_numpy_to_qimage(crop)
        bg = qimg.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    else:
        bg = _cell_backdrop(frame_bgr, dark_hex, bgr_numpy_to_qimage)
    cell_img = QImage(SHEET_CELL_W, SHEET_CELL_H, QImage.Format.Format_ARGB32_Premultiplied)
    cell_img.fill(0)
    cp = QPainter(cell_img)
    cp.drawImage(0, 0, bg)
    fs = fontsize
    if cell.get("id") == "bubble-alt":
        fs = _fontsize_for_two_line_bubble(fontsize)
    style = _base_style(pack, fs)
    if cell.get("id") == "bubble-alt":
        style["pos_y"] = SHEET_CELL_H * 0.50
    style["fx"] = _deep_merge(style.get("fx") or {}, cell["fx"])
    sample = cell.get("sample") or SAMPLE
    event = _event(sample, end=cell.get("end"))
    spec = from_style(style)
    lay = layout_event(event, style, spec, SHEET_CELL_W, SHEET_CELL_H)
    t_now = cell.get("t")
    if str(cell.get("id") or "").startswith("anim-"):
        from cutora.render.textfx.anim import transform_at
        xf = transform_at(float(t_now or 0.0), event, spec, lay)
        print(
            "SAMPLE %s t=%.3f a=%.3f sx=%.3f sy=%.3f dx=%.3f dy=%.3f "
            "rot=%.2f clip=%.3f blur=%.3f track=%.3f fp=%.3f extra=%s lines=%d" % (
                cell["id"], float(t_now or 0.0),
                xf.alpha, xf.scale_x, xf.scale_y, xf.dx_em, xf.dy_em,
                xf.rot_deg, xf.clip, xf.blur_sigma_em, xf.tracking_em,
                xf.fill_progress, xf.extra_color, len(lay.lines)),
            flush=True)
    roi = raster_roi(
        lay, spec, style, bg, SHEET_CELL_W, SHEET_CELL_H,
        skip_blur=False, t=t_now, event=event)
    cp.drawImage(lay.origin[0], lay.origin[1], roi)
    cp.end()
    return cell_img, lay


def _paint_label(painter, rect, title: str) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QFont, QPen
    painter.fillRect(rect, QColor(11, 26, 51, 255))
    painter.setPen(QPen(QColor("#F4EBD8")))
    font = QFont("Arimo", 14)
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(
        rect.adjusted(10, 0, -10, 0),
        int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
        title)


_GLASS_CORNER_IDS = frozenset({
    "bg-glass-thick", "bg-glass-flat", "bg-glass-thick_stripes",
})


def _write_zoom(cell_img, cell_id: str, ground: str, lay=None) -> None:
    from PySide6.QtCore import Qt
    from cutora.render.textfx.raster import as_bgra
    owned, bgra = as_bgra(cell_img)
    alpha = bgra[..., 3]
    ys, xs = np.where(alpha > 24)
    if cell_id in _GLASS_CORNER_IDS and lay is not None:
        side = 220
        x0 = max(0, int(round(lay.hit_x)) - 12)
        y0 = max(0, int(round(lay.hit_y)) - 12)
        crop = cell_img.copy(
            x0, y0,
            min(side, cell_img.width() - x0),
            min(side, cell_img.height() - y0))
    elif len(xs) < 8:
        crop = cell_img
    else:
        pad = 12
        x0 = max(0, int(xs.min()) - pad)
        y0 = max(0, int(ys.min()) - pad)
        x1 = min(cell_img.width(), int(xs.max()) + pad + 1)
        y1 = min(cell_img.height(), int(ys.max()) + pad + 1)
        crop = cell_img.copy(x0, y0, max(1, x1 - x0), max(1, y1 - y0))
    zoom = crop.scaled(
        crop.width() * 4, crop.height() * 4,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.FastTransformation)
    path = os.path.join(ZOOM_DIR, "%s_%s.png" % (cell_id, ground))
    zoom.save(path, "PNG")
    if cell_id == "emph-pop-1" and len(xs) >= 8:
        side = 80
        x0 = max(0, int(xs.max()) - side + 8)
        y0 = max(0, int(ys.min()) - 8)
        corner = cell_img.copy(
            x0, y0,
            min(side, cell_img.width() - x0),
            min(side, int(ys.max()) - y0 + 16))
        cz = corner.scaled(
            corner.width() * 4, corner.height() * 4,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.FastTransformation)
        cz.save(os.path.join(ZOOM_DIR, "%s_%s_right4x.png" % (cell_id, ground)), "PNG")


def _luma(bgr: np.ndarray) -> np.ndarray:
    return bgr.astype(np.float64).mean(axis=-1)


def _write_glass_metrics(metric_cells: dict, out_dir: str) -> None:
    from cutora.render.textfx.glass_thick import k_debug
    from cutora.render.textfx.raster import _rounded_mask_u8, as_bgra
    report = {}
    thick_dark = metric_cells.get(("bg-glass-thick", "dark"))
    stripes = metric_cells.get(("bg-glass-thick_stripes", "frame"))
    if stripes is None:
        stripes = metric_cells.get(("bg-glass-thick_stripes", "dark"))
    if thick_dark is not None:
        cell_img, lay, fs = thick_dark
        em = float(lay.em)
        w_px = 0.14 * em
        tw = max(1, int(round(lay.hit_w)))
        th = max(1, int(round(lay.hit_h)))
        radius = 0.45 * em
        mask = _rounded_mask_u8(tw, th, radius)
        k, width, ring, band_w = k_debug(mask, em, 0.14, radius)
        kpath = os.path.join(ZOOM_DIR, "bg-glass-kmask.png")
        cv2.imwrite(kpath, ring)
        print("wrote", kpath, "k>0.1 median_width=%.1f w_px=%.1f" % (
            band_w, width), flush=True)
        report["k_band"] = {
            "w_px": round(width, 2),
            "k_gt_0.1_median_width_px": round(band_w, 2),
            "pass_width": bool(band_w >= 0.6 * width),
            "kmask": kpath,
            "em": round(em, 2),
        }
        report["dark"] = _metrics_dark_cell(cell_img, lay, w_px, as_bgra)
        zpath = os.path.join(ZOOM_DIR, "bg-glass-thick_dark.png")
        if os.path.isfile(zpath):
            report["dark_4x"] = _metrics_dark_zoom(zpath, w_px)
    if stripes is not None:
        cell_img, lay, fs = stripes
        w_px = 0.14 * float(lay.em)
        zpath = os.path.join(ZOOM_DIR, "bg-glass-thick_stripes_frame.png")
        if not os.path.isfile(zpath):
            zpath = os.path.join(ZOOM_DIR, "bg-glass-thick_stripes_dark.png")
        report["stripes"] = _metrics_stripes_zoom(zpath, w_px)
        report["stripes_1x"] = _metrics_stripes_cell(cell_img, lay, w_px, as_bgra)
    report["apparatus"] = "contact-sheet"
    report["pass"] = _metrics_pass(report)
    out_json = os.path.join(
        REPO, "docs", "plan", "reports", "T04f-2_fix3_glass_metrics.json")
    os.makedirs(os.path.dirname(out_json), exist_ok=True)
    with open(out_json, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    _print_metrics_table(report)
    print("wrote", out_json, flush=True)


def _cell_luma(cell_img, as_bgra) -> np.ndarray:
    _o, bgra = as_bgra(cell_img)
    return bgra[..., :3].astype(np.float64).mean(axis=2)


def _hit_box(lay) -> tuple[int, int, int, int]:
    x0 = int(round(lay.hit_x))
    y0 = int(round(lay.hit_y))
    x1 = x0 + max(1, int(round(lay.hit_w)))
    y1 = y0 + max(1, int(round(lay.hit_h)))
    return x0, y0, x1, y1


def _profile_scores(native: np.ndarray, w_px: float) -> dict:
    if native.size < 8:
        return {"error": "short profile"}
    floor = float(np.median(native[int(native.size * 0.55):]))
    peak = float(native.max())
    peak_i = int(np.argmax(native))
    decay_i = peak_i
    while decay_i < len(native) - 1 and native[decay_i] > floor + 5.0:
        decay_i += 1
    decay_w = float(decay_i - peak_i)
    lo_i = min(len(native) - 3, max(1, int(round(0.5 * w_px))))
    hi_i = min(len(native) - 1, max(lo_i + 2, int(round(1.4 * w_px))))
    seg = native[lo_i:hi_i]
    min_i = lo_i + int(np.argmin(seg))
    at = float(native[min_i])
    n0 = max(0, min_i - 3)
    n1 = min(len(native) - 1, min_i + 3)
    neigh = float(np.mean([native[n0], native[n1]]))
    step_delta = neigh - at
    peak_delta = peak - floor
    return {
        "floor": round(floor, 2),
        "top_peak": round(peak, 2),
        "top_peak_delta": round(peak_delta, 2),
        "decay_width_px": round(decay_w, 2),
        "step_delta": round(step_delta, 2),
        "step_at_px": int(min_i),
        "pass_peak": bool(60.0 <= peak_delta <= 110.0),
        "pass_decay": bool(decay_w >= 0.6 * w_px),
        "pass_step": bool(8.0 <= step_delta <= 22.0),
    }


def _metrics_dark_cell(cell_img, lay, w_px: float, as_bgra) -> dict:
    """§2.1 dark scores on the 1× cell. Sides cannot be read from the TL 4× crop."""
    luma = _cell_luma(cell_img, as_bgra)
    x0, y0, x1, y1 = _hit_box(lay)
    x0 = max(0, min(luma.shape[1] - 1, x0))
    y0 = max(0, min(luma.shape[0] - 1, y0))
    x1 = max(x0 + 1, min(luma.shape[1], x1))
    y1 = max(y0 + 1, min(luma.shape[0], y1))
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
    out["space"] = "1x_cell"
    return out


def _metrics_stripes_cell(cell_img, lay, w_px: float, as_bgra) -> dict:
    """Left mid-side 0–0.6 w vs the 12 px of stripes just outside the panel."""
    luma = _cell_luma(cell_img, as_bgra)
    x0, y0, x1, y1 = _hit_box(lay)
    rad = max(4, int(round(0.45 * float(lay.em))))
    y_a = min(y1 - 1, y0 + rad)
    y_b = max(y_a + 8, y1 - rad)
    edge_w = max(3, int(round(0.6 * w_px)))
    pad = 12
    x_out0 = max(0, x0 - pad)
    outside = luma[y_a:y_b, x_out0:x0]
    left = luma[y_a:y_b, x0:x0 + edge_w]
    std_out = float(outside.std()) if outside.size else 0.0
    std_edge = float(left.std()) if left.size else 0.0
    ratio = (std_edge / std_out) if std_out > 1e-6 else 0.0
    return {
        "std_outside": round(std_out, 2),
        "std_edge_0_0.6w": round(std_edge, 2),
        "ratio": round(ratio, 3),
        "pass_ratio": bool(ratio >= 0.6),
        "space": "1x_left_midside",
    }


def _metrics_stripes_zoom(path: str, w_px: float) -> dict:
    """Scores on the 4× corner PNG (same space Claude measures). Native-px via /4."""
    im = cv2.imread(path)
    luma = _luma(im)
    pad = 12 * 4
    w4 = w_px * 4.0
    edge = max(4, int(round(0.6 * w4)))
    skip = 16
    outside = luma[pad + skip:, :pad]
    left = luma[pad + skip:, pad:pad + edge]
    std_out = float(outside.std()) if outside.size else 0.0
    std_edge = float(left.std()) if left.size else 0.0
    ratio = (std_edge / std_out) if std_out > 1e-6 else 0.0
    shift = 0.0
    if luma.shape[0] > pad + skip + 8 and pad > 4:
        a = luma[pad + skip:, pad // 2]
        b = luma[pad + skip:, pad + max(1, edge // 2)]
        n = min(len(a), len(b))
        aa = a[:n] - float(a[:n].mean())
        bb = b[:n] - float(b[:n].mean())
        corr = np.correlate(aa, bb, mode="full")
        shift = float(np.argmax(corr) - (n - 1)) / 4.0
    return {
        "std_outside": round(std_out, 2),
        "std_edge_0_0.6w": round(std_edge, 2),
        "ratio": round(ratio, 3),
        "pass_ratio": bool(ratio >= 0.6),
        "shift_px": round(shift, 2),
        "space": "4x_zoom_native_shift",
    }


def _metrics_dark_zoom(path: str, w_px: float) -> dict:
    im = cv2.imread(path)
    luma = _luma(im)
    pad = 12 * 4
    w4 = w_px * 4.0
    mx = min(luma.shape[1] - 1, pad + luma.shape[1] // 3)
    profile = luma[pad:min(luma.shape[0], pad + int(round(2.2 * w4))), mx]
    native = profile[::4]
    out = _profile_scores(native, w_px)
    out["space"] = "4x_zoom_native_px"
    out["pass_sides"] = None
    return out


def _metrics_pass(report: dict) -> dict:
    """Gate only contact-sheet measurements (T04f-6a).

    ``stripes`` is the 4x crop taken from this same contact-sheet cell.
    ``stripes_1x`` is printed for same-apparatus contrast and is not gated
    — a 1:1 script that is not this sheet must not reuse these thresholds.
    """
    dark = report.get("dark") or {}
    stripes = report.get("stripes") or {}
    kband = report.get("k_band") or {}
    return {
        "k_width": bool(kband.get("pass_width")),
        "stripes_ratio": bool(stripes.get("pass_ratio")),
        "dark_peak": bool(dark.get("pass_peak")),
        "dark_decay": bool(dark.get("pass_decay")),
        "dark_step": bool(dark.get("pass_step")),
        "dark_sides": bool(dark.get("pass_sides")),
    }


def _print_metrics_table(report: dict) -> None:
    stripes = report.get("stripes") or {}
    stripes1 = report.get("stripes_1x") or {}
    dark = report.get("dark") or {}
    kband = report.get("k_band") or {}
    passed = report.get("pass") or {}
    print("", flush=True)
    print("GLASS METRICS (native px)", flush=True)
    print("  k>0.1 width %.1f / w_px %.1f  pass=%s" % (
        kband.get("k_gt_0.1_median_width_px", 0),
        kband.get("w_px", 0), passed.get("k_width")), flush=True)
    print("  stripes 4x std_edge/out %.2f/%.2f ratio=%.3f  pass=%s  shift=%.1f" % (
        stripes.get("std_edge_0_0.6w", 0), stripes.get("std_outside", 0),
        stripes.get("ratio", 0), passed.get("stripes_ratio"),
        stripes.get("shift_px", 0)), flush=True)
    print("  stripes 1x std_edge/out %.2f/%.2f ratio=%.3f  pass=%s" % (
        stripes1.get("std_edge_0_0.6w", 0), stripes1.get("std_outside", 0),
        stripes1.get("ratio", 0), stripes1.get("pass_ratio")), flush=True)
    print("  dark 1x peak_delta=%.1f decay_w=%.1f step=%.1f lit-back=%.1f" % (
        dark.get("top_peak_delta", 0), dark.get("decay_width_px", 0),
        dark.get("step_delta", 0), dark.get("lit_vs_back_delta", 0)), flush=True)
    print("  dark pass peak=%s decay=%s step=%s sides=%s" % (
        passed.get("dark_peak"), passed.get("dark_decay"),
        passed.get("dark_step"), passed.get("dark_sides")), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())

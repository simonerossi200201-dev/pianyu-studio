#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Q58 evidence: before | after | heatmap triples for red UI golden cases.

before = tests/ui_golden/<case>/got.png (hash == expected.sha256)
after  = tests/ui_golden/<case>/mismatch.png (current HEAD render)
heat   = threshold-8 pixel mask (same as tools/ui_golden.py _diff_mask)
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.join(REPO, "tests", "ui_golden")
OUT = os.path.join(REPO, "docs", "plan", "reports", "Q58_ui_diff")
THR = 8
CASES = ("u03d", "u04d", "u05", "u07", "u08")


def _rgb(path):
    im = Image.open(path).convert("RGB")
    return np.asarray(im, dtype=np.uint8), im.size


def _luma(arr):
    a = arr.astype(np.float64)
    return 0.2126 * a[:, :, 0] + 0.7152 * a[:, :, 1] + 0.0722 * a[:, :, 2]


def _sat(arr):
    a = arr.astype(np.float64)
    mx = a.max(axis=2)
    mn = a.min(axis=2)
    return np.where(mx > 0, (mx - mn) / mx, 0.0)


def best_vshift(a, b, max_shift=160):
    """Row-mean NCC; positive dy means after is shifted down vs before."""
    la, lb = _luma(a), _luma(b)
    ra, rb = la.mean(axis=1), lb.mean(axis=1)
    ra = ra - ra.mean()
    rb = rb - rb.mean()
    best_dy, best_c = 0, -1.0
    h = min(len(ra), len(rb))
    for dy in range(-max_shift, max_shift + 1):
        if dy >= 0:
            x, y = ra[: h - dy], rb[dy:h]
        else:
            x, y = ra[-dy:h], rb[: h + dy]
        if len(x) < 32:
            continue
        denom = float(np.sqrt((x * x).sum() * (y * y).sum()))
        if denom < 1e-9:
            continue
        c = float((x * y).sum() / denom)
        if c > best_c:
            best_c, best_dy = c, dy
    return int(best_dy), float(best_c)


def components(mask, min_px=80):
    import cv2
    nlab, _lab, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 4)
    boxes = []
    for i in range(1, nlab):
        x, y, w, h, n = (int(v) for v in stats[i])
        if n < min_px:
            continue
        boxes.append({"x0": x, "y0": y, "x1": x + w, "y1": y + h, "px": n,
                      "bbox": int(w * h)})
    boxes.sort(key=lambda b: -b["px"])
    return boxes


def heat_image(after, mask):
    heat = after.copy()
    tint = heat.astype(np.int16)
    tint[mask, 0] = np.clip(tint[mask, 0] + 90, 0, 255)
    tint[mask, 1] = np.clip(tint[mask, 1] - 40, 0, 255)
    tint[mask, 2] = np.clip(tint[mask, 2] - 40, 0, 255)
    heat = tint.astype(np.uint8)
    heat[mask] = (0.35 * heat[mask] + 0.65 * np.array([220, 24, 24])).astype(np.uint8)
    return heat


def _font(size):
    for p in (
        os.path.join(REPO, "resources", "fonts", "Noto_Sans_SC", "NotoSansSC-Regular.otf"),
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\arial.ttf",
    ):
        if os.path.isfile(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def make_triple(before, after, heat, boxes, dest, title):
    gap = 8
    header = 36
    h, w = before.shape[:2]
    canvas = Image.new("RGB", (w * 3 + gap * 4, h + header + 12), (24, 24, 24))
    draw = ImageDraw.Draw(canvas)
    font = _font(16)
    small = _font(12)
    draw.text((gap, 8), title, fill=(230, 230, 230), font=small)
    xs = [gap, gap + w + gap, gap + (w + gap) * 2]
    labels = ("before (expected)", "after (HEAD)", "diff heat (thr=8)")
    cols = ((255, 180, 160), (160, 220, 170), (200, 180, 255))
    for x, lab, col in zip(xs, labels, cols):
        draw.text((x, header - 22), lab, fill=col, font=font)
    canvas.paste(Image.fromarray(before), (xs[0], header))
    canvas.paste(Image.fromarray(after), (xs[1], header))
    him = Image.fromarray(heat)
    hd = ImageDraw.Draw(him)
    for i, b in enumerate(boxes[:15]):
        hd.rectangle([b["x0"], b["y0"], b["x1"] - 1, b["y1"] - 1], outline=(255, 220, 0), width=1)
        hd.text((b["x0"] + 2, b["y0"] + 1), str(b["px"]), fill=(255, 255, 0), font=small)
    canvas.paste(him, (xs[2], header))
    canvas.save(dest)


def process(name):
    before_p = os.path.join(ROOT, name, "got.png")
    after_p = os.path.join(ROOT, name, "mismatch.png")
    a, _ = _rgb(before_p)
    b, _ = _rgb(after_p)
    if a.shape != b.shape:
        raise SystemExit("%s size mismatch %s vs %s" % (name, a.shape, b.shape))
    d = np.abs(a.astype(np.int16) - b.astype(np.int16)).max(axis=2)
    mask = d > THR
    changed = int(mask.sum())
    total = int(mask.size)
    vshift, vcorr = best_vshift(a, b)
    sat_a, sat_b = _sat(a), _sat(b)
    lum_a, lum_b = _luma(a), _luma(b)
    sat_drop = int(((sat_b < sat_a - 0.08) & mask).sum())
    luma_drop = int(((lum_b < lum_a - 12) & mask).sum())
    boxes = components(mask)
    heat = heat_image(b, mask)
    os.makedirs(OUT, exist_ok=True)
    Image.fromarray(a).save(os.path.join(OUT, "%s_before.png" % name))
    Image.fromarray(b).save(os.path.join(OUT, "%s_after.png" % name))
    Image.fromarray(heat).save(os.path.join(OUT, "%s_heat.png" % name))
    make_triple(a, b, heat, boxes, os.path.join(OUT, "%s_triple.png" % name),
                "%s  changed=%d (%.1f%%)  vshift=%d  corr=%.3f" % (
                    name, changed, 100.0 * changed / total, vshift, vcorr))
    return {
        "size_before": [int(a.shape[1]), int(a.shape[0])],
        "size_after": [int(b.shape[1]), int(b.shape[0])],
        "changed_px": changed,
        "changed_pct": round(100.0 * changed / total, 3),
        "max_delta": int(d.max()),
        "vshift": vshift,
        "vshift_corr": round(vcorr, 4),
        "sat_drop_px": sat_drop,
        "luma_drop_px": luma_drop,
        "n_components": len(boxes),
        "top_boxes": boxes[:15],
    }


def main():
    stats = {}
    for name in CASES:
        print("===", name)
        stats[name] = process(name)
        s = stats[name]
        print("  changed=%d (%.1f%%) vshift=%d corr=%.3f boxes=%d" % (
            s["changed_px"], s["changed_pct"], s["vshift"], s["vshift_corr"],
            s["n_components"]))
    with open(os.path.join(OUT, "diff_stats.json"), "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2)
        f.write("\n")
    print("wrote", OUT)


if __name__ == "__main__":
    sys.exit(main())

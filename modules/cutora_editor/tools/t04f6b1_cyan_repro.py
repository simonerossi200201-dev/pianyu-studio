# -*- coding: utf-8 -*-
"""T04f-6b-1 C: dump cyan pixels before/after _feather_alpha. Confirm then stop."""
from __future__ import annotations

import os
import sys
import tempfile

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

IMG = os.path.join(REPO, "docs", "plan", "reports", "img")
OUT = os.path.join(REPO, "docs", "plan", "reports", "T04f-6b1_cyan_dump.txt")


def cyan_mask(bgra: np.ndarray) -> np.ndarray:
    """BGRA uint8. High-sat cyan: G,B high, R low, alpha visible."""
    b = bgra[..., 0].astype(np.int16)
    g = bgra[..., 1].astype(np.int16)
    r = bgra[..., 2].astype(np.int16)
    a = bgra[..., 3]
    return (a > 8) & (r < 130) & (g > 170) & (b > 170) & ((g + b) / 2 - r > 80)


def count_cyan(bgra: np.ndarray) -> int:
    return int(cyan_mask(bgra).sum())


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("CUTORA_USER_DATA", tempfile.mkdtemp(prefix="cutora_t04f6b1_c_"))
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    from cutora.app import apply_ui_locale
    apply_ui_locale()
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtWidgets import QApplication, QStyleFactory
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.theme.apply import apply_app_qss
    from cutora.ui.theme.fonts_ui import register_ui_fonts, ui_qfont
    from cutora.render.textfx.layout import layout_event
    from cutora.render.textfx.raster import as_bgra, raster_roi
    from cutora.render.textfx.spec import from_style
    from cutora.render.textfx import raster as raster_mod
    from cutora.subtitle.stylepack import apply_pack_to_task, load_pack
    from cutora.render.stickers import raster_sticker, catalog_items

    app = QApplication.instance() or QApplication(["t04f6b1-c"])
    load_embedded_fonts()
    register_ui_fonts()
    app.setFont(ui_qfont())
    app.setStyle(QStyleFactory.create("Fusion"))
    apply_app_qss(app)

    dumps = {"before": None, "after": None, "scale_max": None}

    orig = raster_mod._feather_alpha

    def wrapped(img, sigma):
        owned, bgra = as_bgra(img)
        dumps["before"] = count_cyan(bgra)
        dumps["before_amax"] = float(bgra[..., 3].max())
        a = bgra[..., 3].astype(np.float32)
        rgb = bgra[..., :3].astype(np.float32)
        # same formula as current code, for scale_max diagnostic
        if float(a.max()) >= 1.0:
            import cv2
            blurred = cv2.GaussianBlur(a, (0, 0), sigmaX=float(sigma))
            scale = blurred / np.maximum(a, 1.0)
            dumps["scale_max"] = float(scale.max())
            dumps["scale_p99"] = float(np.percentile(scale, 99))
            dumps["tiny_a"] = int((a < 1.0).sum())
            dumps["nonzero_rgb_tiny_a"] = int(
                ((a < 1.0) & (rgb.sum(axis=2) > 0)).sum())
        out = orig(img, sigma)
        _, bgra2 = as_bgra(out)
        dumps["after"] = count_cyan(bgra2)
        dumps["after_shape"] = tuple(bgra2.shape)
        return out

    raster_mod._feather_alpha = wrapped

    pack = load_pack(os.path.join(REPO, "resources", "stylepacks", "paper-torn"))
    task = {
        "timeline": {"width": 1080, "height": 1920, "fps": 30},
        "style": {},
        "segments": [{
            "start": 0.0, "end": 4.0,
            "text": "Find its place on the page",
            "words": [],
        }],
        "text_overlays": [],
        "image_overlays": [],
    }
    apply_pack_to_task(task, pack, apply_animation=True)

    w, h = 1080, 1920
    style = task["style"]
    spec = from_style(style)
    event = {
        "text": "Find its place on the page",
        "start": 0.0, "end": 4.0, "id": "seg0",
        "words": [{"word": w, "start": i * 0.3, "end": i * 0.3 + 0.3}
                  for i, w in enumerate("Find its place on the page".split())],
    }
    lay = layout_event(event, style, spec, w, h)
    roi = raster_roi(lay, spec, style, None, w, h, skip_blur=False)
    canvas = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(QColor("#1A1020"))
    p = QPainter(canvas)
    try:
        p.drawImage(int(lay.origin[0]), int(lay.origin[1]), roi)
    finally:
        p.end()

    os.makedirs(IMG, exist_ok=True)
    roi_path = os.path.join(IMG, "T04f-6b1_cyan_roi_before.png")
    roi.save(roi_path, "PNG")
    _, roi_bgra = as_bgra(roi)
    dumps["roi_cyan"] = count_cyan(roi_bgra)
    frame_path = os.path.join(IMG, "T04f-6b1_cyan_before.png")
    canvas.save(frame_path, "PNG")
    _, frame_bgra = as_bgra(canvas)
    frame_cyan = count_cyan(frame_bgra)
    ys, xs = np.where(cyan_mask(frame_bgra))
    samples = []
    if len(xs):
        for i in range(min(8, len(xs))):
            y, x = int(ys[i]), int(xs[i])
            pix = frame_bgra[y, x]
            samples.append({"x": x, "y": y, "BGRA": [int(v) for v in pix]})

    sticker_cyan = {}
    for it in catalog_items()[:12]:
        sid = it.get("id")
        img = raster_sticker(sid, 256)
        _, sbgra = as_bgra(img)
        sticker_cyan[sid] = count_cyan(sbgra)

    lines = [
        "paper-torn roi cyan=%s path=%s" % (dumps.get("roi_cyan"), roi_path),
        "paper-torn full frame cyan=%d" % frame_cyan,
        "frame=%s" % frame_path,
        "_feather_alpha before_cyan=%s after_cyan=%s scale_max=%s scale_p99=%s" % (
            dumps.get("before"), dumps.get("after"),
            dumps.get("scale_max"), dumps.get("scale_p99")),
        "tiny_a=%s nonzero_rgb_at_tiny_a=%s" % (
            dumps.get("tiny_a"), dumps.get("nonzero_rgb_tiny_a")),
        "samples=%s" % samples,
        "sticker_cyan_nonzero=%s" % {k: v for k, v in sticker_cyan.items() if v},
        "sticker_checked=%d" % len(sticker_cyan),
    ]
    text = "\n".join(lines) + "\n"
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(text, flush=True)
    raster_mod._feather_alpha = orig
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

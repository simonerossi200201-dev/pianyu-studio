# -*- coding: utf-8 -*-
"""T04f-2b stills: four element types with two-direction fade + 4x edge crops."""
from __future__ import annotations

import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

IMG_DIR = os.path.join(REPO, "docs", "plan", "reports", "img")
MARK = os.path.join(REPO, "resources", "brand", "mark_1024.png")


def _save(qimg, name):
    os.makedirs(IMG_DIR, exist_ok=True)
    path = os.path.join(IMG_DIR, name)
    qimg.save(path, "PNG")
    print("wrote", path, flush=True)
    return path


def _corner_4x(qimg, path, x0=8, y0=8, side=80):
    from PySide6.QtCore import Qt
    crop = qimg.copy(x0, y0, min(side, qimg.width() - x0), min(side, qimg.height() - y0))
    zoom = crop.scaled(
        crop.width() * 4, crop.height() * 4,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.FastTransformation)
    _save(zoom, os.path.basename(path))


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if "CUTORA_USER_DATA" not in os.environ:
        os.environ["CUTORA_USER_DATA"] = tempfile.mkdtemp(prefix="cutora_t04f2b_")
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    from cutora.app import apply_ui_locale
    apply_ui_locale()
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtWidgets import QApplication, QStyleFactory
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.theme.apply import apply_app_qss
    from cutora.ui.theme.fonts_ui import register_ui_fonts, ui_qfont
    from cutora.render.frame_renderer import FrameRenderer
    from cutora.render.textfx.layout import layout_event
    from cutora.render.textfx.raster import raster_bg, _glass_rect
    from cutora.render.textfx.spec import from_style
    from cutora.subtitle.stylepack import apply_pack_to_task, builtin_dir, load_pack

    app = QApplication.instance() or QApplication(["t04f2b-stills"])
    load_embedded_fonts()
    register_ui_fonts()
    app.setFont(ui_qfont())
    app.setStyle(QStyleFactory.create("Fusion"))
    apply_app_qss(app)

    w, h = 640, 360
    bg = QColor("#F4EBD8")

    def canvas():
        img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(bg)
        return img

    # 1) image element, top + left
    img = canvas()
    p = QPainter(img)
    ov = {"x": 80, "y": 40, "w": 480, "h": 280, "opacity": 255, "type": "image",
          "rotation": 0, "scale": 1.0}
    el_img = {
        "kind": "image", "path": MARK,
        "x": 0, "y": 0, "w": 480, "h": 280, "opacity": 255,
        "fade_top": 0.35, "fade_left": 0.25, "fade_bottom": 0.0, "fade_right": 0.0,
    }
    FrameRenderer.draw_sticker(p, ov, el_img)
    p.end()
    _save(img, "T04f-2b_fade_image.png")
    _corner_4x(img, os.path.join(IMG_DIR, "T04f-2b_fade_image_4x.png"), x0=80, y0=40)

    # 2) shape, top + bottom
    img = canvas()
    p = QPainter(img)
    el_sh = {
        "kind": "shape", "shape": "round_rect",
        "x": 80, "y": 60, "w": 480, "h": 240, "opacity": 255,
        "fill_color": "#0B1A33", "radius": 28,
        "fade_top": 0.4, "fade_bottom": 0.4, "fade_left": 0.0, "fade_right": 0.0,
    }
    FrameRenderer.draw_sticker(p, {"x": 0, "y": 0, "w": w, "h": h, "opacity": 255, "type": "image"}, el_sh)
    p.end()
    _save(img, "T04f-2b_fade_shape.png")
    _corner_4x(img, os.path.join(IMG_DIR, "T04f-2b_fade_shape_4x.png"), x0=80, y0=60)

    # 3) image overlay clip (legacy content), left + right
    img = canvas()
    p = QPainter(img)
    ov_clip = {
        "type": "image", "content": MARK,
        "x": 80, "y": 40, "w": 480, "h": 280,
        "opacity": 255, "scale": 1.0, "keep_aspect": False,
        "fade_left": 0.3, "fade_right": 0.3, "fade_top": 0.0, "fade_bottom": 0.0,
        "start": 0.0, "end": 2.0,
    }
    FrameRenderer.draw_single_overlay(p, ov_clip, 0.5)
    p.end()
    _save(img, "T04f-2b_fade_overlay.png")
    _corner_4x(img, os.path.join(IMG_DIR, "T04f-2b_fade_overlay_4x.png"), x0=80, y0=40)

    # 4) subtitle background fade (already shipped; still for the four-type sheet)
    pack = load_pack(os.path.join(builtin_dir(), "glass-white"))
    task = {"timeline": {"width": w, "height": h}, "style": {}}
    apply_pack_to_task(task, pack, apply_animation=True)
    st = task["style"]
    st["fontsize"] = 72
    st["fontsize_pct"] = 72.0 / float(min(w, h))
    st["pos_x"] = w / 2.0
    st["pos_y"] = h * 0.55
    fx = dict(st.get("fx") or {})
    bgfx = dict(fx.get("bg") or {})
    bgfx["fade"] = {"top": 0.35, "bottom": 0.0, "left": 0.25, "right": 0.0}
    fx["bg"] = bgfx
    fx["glow"] = {"sigma_em": 0}
    fx["shadow"] = {"sigma_em": 0}
    st["fx"] = fx
    spec = from_style(st)
    event = {
        "text": "Place", "start": 0.0, "end": 2.0,
        "words": [{"word": "Place", "start": 0.0, "end": 2.0}], "id": "seg0",
    }
    lay = layout_event(event, st, spec, w, h)
    bd = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    bd.fill(bg)
    roi = raster_bg(lay, spec, bd, w, h, skip_blur=False)
    img = canvas()
    p = QPainter(img)
    p.drawImage(0, 0, bd)
    if roi is not None:
        p.drawImage(lay.origin[0], lay.origin[1], roi)
    p.end()
    _save(img, "T04f-2b_fade_subtitle_bg.png")
    rect = _glass_rect(lay, spec)
    _corner_4x(img, os.path.join(IMG_DIR, "T04f-2b_fade_subtitle_bg_4x.png"),
               x0=max(0, int(round(rect.x()))), y0=max(0, int(round(rect.y()))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

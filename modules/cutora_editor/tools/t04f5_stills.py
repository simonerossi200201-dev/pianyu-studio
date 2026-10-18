# -*- coding: utf-8 -*-
"""T04f-5 stills: 5 platform presets, sticker panel, caption hint, fade crops."""
from __future__ import annotations

import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

IMG_DIR = os.path.join(REPO, "docs", "plan", "reports", "img")


def _save(qimg, name):
    os.makedirs(IMG_DIR, exist_ok=True)
    path = os.path.join(IMG_DIR, name)
    qimg.save(path, "PNG")
    print("wrote", path, flush=True)
    return path


def _corner_4x(qimg, name, x0, y0, side=80):
    from PySide6.QtCore import Qt
    crop = qimg.copy(x0, y0, min(side, qimg.width() - x0), min(side, qimg.height() - y0))
    zoom = crop.scaled(
        crop.width() * 4, crop.height() * 4,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.FastTransformation)
    return _save(zoom, name)


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if "CUTORA_USER_DATA" not in os.environ:
        os.environ["CUTORA_USER_DATA"] = tempfile.mkdtemp(prefix="cutora_t04f5_")
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    from cutora.app import apply_ui_locale
    apply_ui_locale()
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtWidgets import QApplication, QStyleFactory
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.theme.apply import apply_app_qss
    from cutora.ui.theme.fonts_ui import register_ui_fonts, ui_qfont
    from cutora.ui.canvas import VideoCanvas
    from cutora.render.safe_zones import SETTINGS_MASTER, SETTINGS_PRESETS, presets
    from cutora.core.history import _save_app_settings
    from cutora.render.frame_renderer import FrameRenderer
    from cutora.ui.panels.sticker_panel import StickerPanel
    import cutora.ui.panels.property  # bind ColorOptionPanel
    from cutora.ui.panels.subtitle_style import SubtitleStylePanel

    app = QApplication.instance() or QApplication(["t04f5-stills"])
    load_embedded_fonts()
    register_ui_fonts()
    app.setFont(ui_qfont())
    app.setStyle(QStyleFactory.create("Fusion"))
    apply_app_qss(app)

    aspect = {
        "youtube_landscape": (1280, 720),
        "youtube_shorts": (720, 1280),
        "tiktok": (720, 1280),
        "instagram_reels": (720, 1280),
        "instagram_square": (1080, 1080),
    }
    for p in presets():
        pid = str(p.get("id"))
        w, h = aspect.get(pid, (720, 1280))
        _save_app_settings({
            SETTINGS_MASTER: True,
            SETTINGS_PRESETS: {pid: True},
        })
        img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(QColor("#0B1A33"))
        painter = QPainter(img)
        painter.fillRect(QRectF(0, 0, w, h), QColor("#141A26"))
        VideoCanvas._draw_safe_zones(None, painter, w, h)
        painter.end()
        _save(img, "T04f-5_safe_%s.png" % pid)

    # sticker with four-edge fade + 4x crops
    img = QImage(640, 640, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor("#0B1A33"))
    p = QPainter(img)
    ov = {"x": 0, "y": 0, "w": 640, "h": 640, "opacity": 255, "type": "image"}
    el = {
        "kind": "image", "sticker_id": "1f525",
        "x": 80, "y": 80, "w": 480, "h": 480, "opacity": 255,
        "fade_top": 0.35, "fade_bottom": 0.25, "fade_left": 0.25, "fade_right": 0.25,
    }
    FrameRenderer.draw_sticker(p, ov, el)
    p.end()
    _save(img, "T04f-5_sticker_fade.png")
    _corner_4x(img, "T04f-5_sticker_fade_top4x.png", 80, 80)
    _corner_4x(img, "T04f-5_sticker_fade_bottom4x.png", 80, 480)
    _corner_4x(img, "T04f-5_sticker_fade_left4x.png", 80, 280)
    _corner_4x(img, "T04f-5_sticker_fade_right4x.png", 480, 280)

    sp = StickerPanel(populate=True)
    sp.resize(360, 640)
    sp.show()
    for _ in range(12):
        app.processEvents()
        from PySide6.QtCore import QEventLoop, QTimer
        loop = QEventLoop()
        QTimer.singleShot(50, loop.quit)
        loop.exec()
    pix = sp.grab()
    pix.save(os.path.join(IMG_DIR, "T04f-5_sticker_panel.png"), "PNG")
    print("wrote", os.path.join(IMG_DIR, "T04f-5_sticker_panel.png"), flush=True)

    panel = SubtitleStylePanel()
    panel.resize(320, 700)
    panel.current_data = {
        "fontsize": 72, "pos_x": 360, "pos_y": 1100,
        "wrap_lines": 2, "box_padding_y": 20, "font_family": "Arimo",
    }
    _save_app_settings({
        SETTINGS_MASTER: True,
        SETTINGS_PRESETS: {"tiktok": True},
    })
    panel._refresh_safe_zone_hint()
    panel.show()
    app.processEvents()
    pix2 = panel.grab()
    pix2.save(os.path.join(IMG_DIR, "T04f-5_caption_hint.png"), "PNG")
    print("wrote", os.path.join(IMG_DIR, "T04f-5_caption_hint.png"), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

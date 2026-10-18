# -*- coding: utf-8 -*-
"""T04f-6b2 stills: sticker panel + multi-emoji canvas with fade."""
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


def _pump(app, ms=40):
    from PySide6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()
    app.processEvents()


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if "CUTORA_USER_DATA" not in os.environ:
        os.environ["CUTORA_USER_DATA"] = tempfile.mkdtemp(prefix="cutora_t04f6b2_")
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
    from cutora.ui.panels.sticker_panel import StickerPanel
    import cutora.ui.panels.property  # bind ColorOptionPanel

    app = QApplication.instance() or QApplication(["t04f6b2-stills"])
    load_embedded_fonts()
    register_ui_fonts()
    app.setFont(ui_qfont())
    app.setStyle(QStyleFactory.create("Fusion"))
    apply_app_qss(app)

    sp = StickerPanel(populate=True)
    sp.resize(360, 720)
    sp.show()
    for _ in range(40):
        _pump(app, 50)
        if sp._buttons and not sp._pending_icons:
            break
    pix = sp.grab()
    pix.save(os.path.join(IMG_DIR, "T04f-6b2_sticker_panel.png"), "PNG")
    print("wrote", os.path.join(IMG_DIR, "T04f-6b2_sticker_panel.png"),
          pix.width(), "x", pix.height(), "btns", len(sp._buttons), flush=True)

    ids = [
        ("1f525", 80, 80, 200, 200, 0.0, 0.0, 0.0, 0.0),
        ("1f389", 320, 80, 200, 200, 0.0, 0.0, 0.0, 0.0),
        ("2764", 560, 80, 200, 200, 0.0, 0.0, 0.0, 0.0),
        ("1f44d", 800, 80, 200, 200, 0.0, 0.0, 0.0, 0.0),
        ("1f308", 80, 320, 200, 200, 0.0, 0.0, 0.0, 0.0),
        ("1f468-200d-1f469-200d-1f467-200d-1f466", 320, 320, 200, 200, 0.0, 0.0, 0.0, 0.0),
        ("1f1e8-1f1f3", 560, 320, 200, 200, 0.0, 0.0, 0.0, 0.0),
        ("1f525", 800, 320, 200, 200, 0.40, 0.0, 0.30, 0.0),
    ]
    img = QImage(1080, 640, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor("#0B1A33"))
    p = QPainter(img)
    ov = {"x": 0, "y": 0, "w": 1080, "h": 640, "opacity": 255, "type": "image"}
    for sid, x, y, w, h, ft, fb, fl, fr in ids:
        el = {
            "kind": "image", "sticker_id": sid,
            "x": x, "y": y, "w": w, "h": h, "opacity": 255,
            "fade_top": ft, "fade_bottom": fb, "fade_left": fl, "fade_right": fr,
        }
        FrameRenderer.draw_sticker(p, ov, el)
    p.end()
    _save(img, "T04f-6b2_sticker_canvas.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

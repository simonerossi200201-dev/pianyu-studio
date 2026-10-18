# -*- coding: utf-8 -*-
"""T04f-6a stills: 14-pack gallery overview + feather before/after."""
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
    print("wrote", path, qimg.width(), "x", qimg.height(), flush=True)
    return path


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if "CUTORA_USER_DATA" not in os.environ:
        os.environ["CUTORA_USER_DATA"] = tempfile.mkdtemp(prefix="cutora_t04f6a_")
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    from cutora.app import apply_ui_locale
    apply_ui_locale()
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QImage, QPainter, QFont
    from PySide6.QtWidgets import QApplication, QStyleFactory
    from cutora.core.migrate_user_data import migrate_feather_to_fade
    from cutora.render.frame_renderer import FrameRenderer
    from cutora.subtitle.stylepack import (
        gallery_card_size, list_pack_dirs, load_pack,
    )
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.panels.style_thumbs import pack_sample, render_pack_card
    from cutora.ui.theme.apply import apply_app_qss
    from cutora.ui.theme.fonts_ui import register_ui_fonts, ui_qfont

    app = QApplication.instance() or QApplication(["t04f6a-stills"])
    load_embedded_fonts()
    register_ui_fonts()
    app.setFont(ui_qfont())
    app.setStyle(QStyleFactory.create("Fusion"))
    apply_app_qss(app)

    packs = []
    for path in list_pack_dirs():
        if "resources" not in path.replace("\\", "/"):
            continue
        packs.append(load_pack(path))
    assert len(packs) == 14, [p.get("id") for p in packs]

    base = QImage(1080, 1920, QImage.Format.Format_RGB888)
    base.fill(QColor("#5A6570"))
    tw, th = gallery_card_size(1080, 1920, col_w=180)
    cols, gap, label_h, pad = 2, 12, 22, 16
    rows = (len(packs) + cols - 1) // cols
    sheet_w = pad * 2 + cols * tw + (cols - 1) * gap
    sheet_h = pad * 2 + rows * (th + label_h + 6) + (rows - 1) * gap
    sheet = QImage(sheet_w, sheet_h, QImage.Format.Format_RGB888)
    sheet.fill(QColor("#141210"))
    sp = QPainter(sheet)
    sp.setPen(QColor("#EFE7DA"))
    sp.setFont(QFont("Arimo", 11))
    for i, pack in enumerate(packs):
        r, c = divmod(i, cols)
        x = pad + c * (tw + gap)
        y = pad + r * (th + label_h + 6 + gap)
        pix = render_pack_card(base, pack, pack_sample(pack), tw, th, t=0.4)
        sp.drawPixmap(x, y, pix)
        preview = os.path.join(pack["path"], "preview.png")
        pix.save(preview, "PNG")
        name = str((pack.get("meta") or {}).get("name") or pack.get("id"))
        sp.drawText(x, y + th + 2, tw, label_h, int(Qt.AlignmentFlag.AlignLeft), name)
    sp.end()
    _save(sheet, "T04f-6a_gallery_14.png")

    # feather before/after on the same image element
    w, h = 640, 360
    bg = QColor("#1C1815")

    def paint(el):
        img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(bg)
        p = QPainter(img)
        ov = {"x": 80, "y": 40, "w": 480, "h": 280, "opacity": 255,
              "type": "image", "rotation": 0, "scale": 1.0}
        FrameRenderer.draw_sticker(p, ov, el)
        p.end()
        return img

    el_before = {
        "kind": "image", "path": MARK,
        "x": 0, "y": 0, "w": 480, "h": 280, "opacity": 255,
        "feather": 40,
        "fade_top": 0.0, "fade_bottom": 0.0, "fade_left": 0.0, "fade_right": 0.0,
    }
    before = paint(el_before)
    proj = {"tasks": [{"timeline": {"width": 640, "height": 360, "tracks": []},
                       "image_overlays": [{"elements": [dict(el_before)]}]}]}
    migrate_feather_to_fade(proj)
    el_after = proj["tasks"][0]["image_overlays"][0]["elements"][0]
    after = paint(el_after)
    combo = QImage(w * 2 + 8, h + 28, QImage.Format.Format_RGB888)
    combo.fill(QColor("#141210"))
    cp = QPainter(combo)
    cp.drawImage(0, 24, before)
    cp.drawImage(w + 8, 24, after)
    cp.setPen(QColor("#EFE7DA"))
    cp.setFont(QFont("Arimo", 12))
    cp.drawText(8, 18, "feather=40 (linear px)")
    cp.drawText(w + 16, 18, "fade_* after migrate (smoothstep ratio)")
    cp.end()
    _save(before, "T04f-6a_feather_before.png")
    _save(after, "T04f-6a_feather_after.png")
    _save(combo, "T04f-6a_feather_compare.png")
    print("migrated fade_top", el_after.get("fade_top"),
          "legacy", el_after.get("feather_legacy"), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

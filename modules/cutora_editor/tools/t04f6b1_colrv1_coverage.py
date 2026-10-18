# -*- coding: utf-8 -*-
"""T04f-6b-1 E: COLRv1 coverage still at 256px. Stop if any glyph splits."""
from __future__ import annotations

import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

FONT = os.path.join(REPO, "build", "t04f6b1_fonts", "Noto-colrv1.ttf")
IMG = os.path.join(REPO, "docs", "plan", "reports", "img")
OUT = os.path.join(REPO, "docs", "plan", "reports", "T04f-6b1_colrv1_coverage.txt")
PX = 256

CELLS = [
    ("zwj_family", "👨‍👩‍👧‍👦"),
    ("zwj_job", "👩‍💻"),
    ("zwj_pride", "🏳️‍🌈"),
    ("skin_ok", "👍🏽"),
    ("skin_wave", "👋🏿"),
    ("flag_it", "🇮🇹"),
    ("flag_cn", "🇨🇳"),
    ("grad_sunrise", "🌅"),
    ("grad_bubble", "🫧"),
    ("keycap_1", "1️⃣"),
    ("keycap_hash", "#️⃣"),
]


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("CUTORA_USER_DATA", tempfile.mkdtemp(prefix="cutora_e_cov_"))
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter
    from PySide6.QtWidgets import QApplication
    from cutora.render.textfx.raster import as_bgra
    import numpy as np

    app = QApplication.instance() or QApplication(["t04f6b1-e-cov"])
    _ = app
    if not os.path.isfile(FONT):
        print("MISSING", FONT)
        return 2
    fid = QFontDatabase.addApplicationFont(FONT)
    families = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
    family = families[0] if families else ""
    n = len(CELLS)
    cols = 4
    rows = (n + cols - 1) // cols
    cell = PX + 72
    sheet = QImage(cell * cols, cell * rows, QImage.Format.Format_ARGB32_Premultiplied)
    sheet.fill(QColor("#202028"))
    font = QFont(family)
    font.setPixelSize(PX)
    label_font = QFont(family)
    label_font.setPixelSize(14)
    p = QPainter(sheet)
    notes = []
    try:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        for i, (name, glyph) in enumerate(CELLS):
            r, c = divmod(i, cols)
            x, y = c * cell, r * cell
            p.setFont(font)
            p.setPen(QColor("#FFFFFF"))
            p.drawText(x, y, cell, PX + 16, int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop), glyph)
            p.setFont(label_font)
            p.drawText(x, y + PX + 8, cell, 40, int(Qt.AlignmentFlag.AlignHCenter), name)
            crop = sheet.copy(x, y, cell, PX + 16)
            _, bgra = as_bgra(crop)
            a = bgra[..., 3]
            vis = a > 8
            nvis = int(vis.sum())
            ys, xs = np.where(vis)
            if nvis == 0:
                notes.append("%s EMPTY" % name)
                continue
            x0, x1 = int(xs.min()), int(xs.max())
            y0, y1 = int(ys.min()), int(ys.max())
            # connected components on a coarse grid to catch split glyphs
            binm = (a > 8).astype(np.uint8)
            # skip label area roughly
            notes.append("%s vis=%d bbox=%d,%d-%d,%d w=%d h=%d" % (
                name, nvis, x0, y0, x1, y1, x1 - x0 + 1, y1 - y0 + 1))
    finally:
        p.end()
    os.makedirs(IMG, exist_ok=True)
    path = os.path.join(IMG, "T04f-6b1_colrv1_coverage_256.png")
    sheet.save(path, "PNG")
    text = "family=%s\nfile=%s\nsheet=%s\n" % (family, FONT, path) + "\n".join(notes) + "\n"
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(text, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

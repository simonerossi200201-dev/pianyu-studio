# -*- coding: utf-8 -*-
"""T04f-6b-1 E§0: Qt color-font feasibility. Do not touch sticker product code."""
from __future__ import annotations

import os
import struct
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

FONT_DIR = os.path.join(REPO, "build", "t04f6b1_fonts")
IMG_DIR = os.path.join(REPO, "docs", "plan", "reports", "img")
OUT = os.path.join(REPO, "docs", "plan", "reports", "T04f-6b1_colorfont.txt")

# Fonts already fetched into build/t04f6b1_fonts/ (googlefonts/noto-emoji).
LOCAL = {
    "colrv1": os.path.join(FONT_DIR, "Noto-colrv1.ttf"),
    "cbdt": os.path.join(FONT_DIR, "Noto-cbdt.ttf"),
}
SAMPLE = "❤️ 🎉 🔥 👍 🌈"


def _tables(path):
    with open(path, "rb") as fh:
        header = fh.read(12)
        if len(header) < 12:
            return []
        n = struct.unpack(">H", header[4:6])[0]
        names = []
        for _ in range(n):
            rec = fh.read(16)
            if len(rec) < 16:
                break
            names.append(rec[0:4].decode("latin1", "replace").replace("\x00", "").strip())
        return names


def _render(font_path, px, out_path, label):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import (
        QColor, QFont, QFontDatabase, QImage, QPainter, QRawFont, QGlyphRun,
    )
    fid = QFontDatabase.addApplicationFont(font_path)
    families = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
    family = families[0] if families else ""
    glyphs = ["❤️", "🎉", "🔥", "👍", "🌈"]
    cell = px + 24
    img = QImage(cell * len(glyphs), cell, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor("#202028"))
    p = QPainter(img)
    notes = []
    try:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        font = QFont(family)
        font.setPixelSize(px)
        p.setFont(font)
        p.setPen(QColor("#FFFFFF"))
        for i, g in enumerate(glyphs):
            p.drawText(i * cell, 0, cell, cell, int(Qt.AlignmentFlag.AlignCenter), g)
    finally:
        p.end()
    img.save(out_path, "PNG")
    from cutora.render.textfx.raster import as_bgra
    import numpy as np
    _, bgra = as_bgra(img)
    rgb = bgra[..., :3]
    a = bgra[..., 3]
    visible = a > 8
    nvis = int(visible.sum())
    if nvis == 0:
        kind = "empty"
    else:
        std = float(rgb[visible].std())
        uniq = len(np.unique(rgb[visible].reshape(-1, 3), axis=0))
        # color vs mono: chroma spread
        b, g, r = rgb[..., 0], rgb[..., 1], rgb[..., 2]
        chroma = np.abs(r.astype(np.int16) - g.astype(np.int16)) + np.abs(
            g.astype(np.int16) - b.astype(np.int16))
        chroma_vis = float(chroma[visible].mean())
        if chroma_vis < 4 and uniq < 8:
            kind = "mono-or-tofu"
        else:
            kind = "color"
        notes.append("std=%.1f uniq=%d chroma=%.1f vis=%d" % (
            std, uniq, chroma_vis, nvis))
    return {
        "label": label,
        "px": px,
        "path": out_path,
        "family": family,
        "kind": kind,
        "notes": "; ".join(notes),
        "fid": fid,
    }


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("CUTORA_USER_DATA", tempfile.mkdtemp(prefix="cutora_t04f6b1_e_"))
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    from cutora.app import apply_ui_locale
    apply_ui_locale()
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(["t04f6b1-e"])
    _ = app
    os.makedirs(FONT_DIR, exist_ok=True)
    os.makedirs(IMG_DIR, exist_ok=True)
    lines = []
    results = []
    for kind, path in LOCAL.items():
        if not os.path.isfile(path):
            lines.append("%s MISSING %s" % (kind, path))
            continue
        tables = _tables(path)
        lines.append("%s file=%s size=%d tables=%s" % (
            kind, path, os.path.getsize(path), ",".join(tables)))
        has_colr = "COLR" in tables
        has_cbdt = "CBDT" in tables
        lines.append("  COLR=%s CBDT=%s CPAL=%s CBLC=%s SVG=%s" % (
            has_colr, has_cbdt, "CPAL" in tables, "CBLC" in tables, "SVG " in tables or "SVG" in tables))
        for px in (128, 256, 512):
            out = os.path.join(IMG_DIR, "T04f-6b1_emoji_%s_%d.png" % (kind, px))
            rec = _render(path, px, out, "%s-%d" % (kind, px))
            rec["tables_colr"] = has_colr
            rec["tables_cbdt"] = has_cbdt
            results.append(rec)
            lines.append("  %s kind=%s %s -> %s" % (
                rec["label"], rec["kind"], rec["notes"], rec["path"]))
    text = "\n".join(lines) + "\n"
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(text, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# -*- coding: utf-8 -*-
"""Full-canvas side-by-side stills: v1 case vs fx mirror, same t, no crop."""
from __future__ import annotations

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "tools"))
os.chdir(REPO)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PAIRS = [
    ("g01_vertical_basic", "g11_vertical_basic_fx"),
    ("g02_vertical_fx", "g12_vertical_fx_path"),
    ("g03_horizontal_it", "g13_horizontal_it_fx"),
]
# First-cue mid-word (highlight) + early entrance.
TIMES = (0.08, 0.50, 1.50)
IMG = os.path.join(REPO, "docs", "plan", "reports", "img")


def _ink(img):
    from PySide6.QtGui import QImage
    bits = img.constBits()
    buf = bytes(bits)
    # ARGB32 premul: count non-black-ish pixels as ink vs a dark video is noisy.
    # Use alpha of a difference image instead — caller compares two RGB frames.
    return buf


def _diff_ink(a, b) -> tuple[int, int, float]:
    wa, ha = a.width(), a.height()
    if (wa, ha) != (b.width(), b.height()):
        return 0, 0, 1.0
    n = wa * ha
    ba = bytes(a.constBits())
    bb = bytes(b.constBits())
    changed = 0
    # BGRA 4 bytes
    for i in range(0, len(ba), 4):
        db = abs(ba[i] - bb[i]) + abs(ba[i + 1] - bb[i + 1]) + abs(ba[i + 2] - bb[i + 2])
        if db > 24:
            changed += 1
    return changed, n, (changed / float(n) if n else 0.0)


def _side_by_side(left, right):
    from PySide6.QtGui import QImage, QPainter, QColor, QFont
    from PySide6.QtCore import Qt
    gap = 8
    w = left.width() + gap + right.width()
    h = max(left.height(), right.height()) + 28
    out = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(QColor("#111111"))
    p = QPainter(out)
    p.drawImage(0, 28, left)
    p.drawImage(left.width() + gap, 28, right)
    p.setPen(QColor("#EEEEEE"))
    p.setFont(QFont("Arimo", 12))
    p.drawText(8, 20, "v1")
    p.drawText(left.width() + gap + 8, 20, "fx")
    p.end()
    return out


def main() -> int:
    import golden_check as gc
    from cutora.render.export_renderer import ExportRenderer
    from cutora.subtitle.io import canvas_size

    host, _app = gc.boot_host()
    os.makedirs(IMG, exist_ok=True)
    lines = ["# T11g visual pair stills", "", "| pair | t_s | changed_px | total | frac |", "|---|---|---|---|---|"]
    for src_name, dst_name in PAIRS:
        src_dir = os.path.join(gc.GOLDEN_ROOT, src_name)
        dst_dir = os.path.join(gc.GOLDEN_ROOT, dst_name)
        _sdata, stasks = gc.load_tasks(host, os.path.join(src_dir, "project.asproj.json"))
        _ddata, dtasks = gc.load_tasks(host, os.path.join(dst_dir, "project.asproj.json"))
        stask = stasks[0]
        dtask = dtasks[0]
        cw, ch = canvas_size(stask)
        for t in TIMES:
            a = ExportRenderer.render_frame(stask, cw, ch, float(t), fast=False)
            b = ExportRenderer.render_frame(dtask, cw, ch, float(t), fast=False)
            changed, total, frac = _diff_ink(a, b)
            pair = _side_by_side(a, b)
            name = "T11g_pair_%s_%s_t%04dms.png" % (
                src_name.split("_")[0], dst_name.split("_")[0], int(t * 1000))
            path = os.path.join(IMG, name)
            pair.save(path, "PNG")
            a.save(os.path.join(IMG, name.replace("pair_", "v1_")), "PNG")
            b.save(os.path.join(IMG, name.replace("pair_", "fx_")), "PNG")
            lines.append(
                "| %s → %s | %.2f | %d | %d | %.4f |"
                % (src_name, dst_name, t, changed, total, frac)
            )
            print("STILL", name, "frac", round(frac, 4), "changed", changed, flush=True)
    report = os.path.join(REPO, "docs", "plan", "reports", "T11g_pair_stills.md")
    with open(report, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
        fh.write("\n全画布、同一 t、不裁剪。frac = 通道差>24 的像素占比。\n")
    print("wrote", report, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# -*- coding: utf-8 -*-
"""T04f-1b §3: measure whether 极简白 highlights the current word on canvas."""
from __future__ import annotations

import json
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if "CUTORA_USER_DATA" not in os.environ:
    os.environ["CUTORA_USER_DATA"] = tempfile.mkdtemp(prefix="cutora_t04f1b_hl_")

from cutora.core.runtime import init_torch_runtime
init_torch_runtime(render_child=False)

from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication
from cutora.app import apply_ui_locale
from cutora.render.frame_renderer import FrameRenderer
from cutora.render.textfx.spec import rgb_tuple
from cutora.subtitle.stylepack import apply_pack_to_task, load_pack
from cutora.ui.panels.fonts import load_embedded_fonts
from cutora.ui.theme.fonts_ui import register_ui_fonts, ui_qfont


G08 = os.path.join(
    REPO, "tests", "golden", "g08_stylepack_amber", "project.asproj.json")
PACK = os.path.join(REPO, "resources", "stylepacks", "minimal-white")
OUT = os.path.join(REPO, "docs", "plan", "reports", "img", "t04f1b_measure_minimal.png")


def _pick_event(task):
    for seg in task.get("segments") or []:
        words = [w for w in (seg.get("words") or []) if str(w.get("word") or "").strip()]
        if len(words) >= 3:
            return seg, words
    raise SystemExit("no 3-word segment")


def _mean_rgb(img: QImage, x0, y0, x1, y1):
    import numpy as np
    rgb = img.convertToFormat(QImage.Format.Format_RGB888)
    w, h = rgb.width(), rgb.height()
    bpl = rgb.bytesPerLine()
    ptr = rgb.bits()
    arr = np.frombuffer(ptr, dtype=np.uint8, count=bpl * h)
    arr = arr.reshape(h, bpl)[:, : w * 3].reshape(h, w, 3)
    x0, x1 = max(0, x0), min(w, x1)
    y0, y1 = max(0, y0), min(h, y1)
    chip = arr[y0:y1, x0:x1]
    if chip.size == 0:
        return (0, 0, 0)
    return tuple(int(round(v)) for v in chip.reshape(-1, 3).mean(axis=0))


def main() -> int:
    apply_ui_locale()
    app = QApplication.instance() or QApplication(["t04f1b-hl"])
    load_embedded_fonts()
    register_ui_fonts()
    app.setFont(ui_qfont())
    data = json.loads(open(G08, encoding="utf-8").read())
    task = data["tasks"][0]
    pack = load_pack(PACK)
    apply_pack_to_task(task, pack, apply_animation=True)
    style = task["style"]
    seg, words = _pick_event(task)
    mid = words[1]
    t = (float(mid["start"]) + float(mid["end"])) / 2.0
    w, h = 1080, 1920
    img = QImage(w, h, QImage.Format.Format_RGB888)
    img.fill(QColor("#3A4654"))
    p = QPainter(img)
    try:
        rect = FrameRenderer.draw_subtitle(
            p, seg, style, t, w, h, backdrop=img)
    finally:
        p.end()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    img.save(OUT, "PNG")
    # three horizontal thirds of the returned text rect ≈ three words
    x, y, bw, bh = int(rect.x()), int(rect.y()), int(rect.width()), int(rect.height())
    third = max(1, bw // 3)
    chips = []
    for i in range(3):
        cx0 = x + third * i + third // 4
        cx1 = x + third * i + third * 3 // 4
        cy0 = y + bh // 4
        cy1 = y + bh * 3 // 4
        chips.append(_mean_rgb(img, cx0, cy0, cx1, cy1))
    high = rgb_tuple(str(style.get("color_high") or "#FDE68A"))
    text = rgb_tuple(str(style.get("color_text") or "#F4F7FB"))
    print("words", [w.get("word") for w in words[:3]], "t", t)
    print("color_text", text, "color_high", high)
    print("chips L/M/R", chips)
    print("rect", x, y, bw, bh)
    print("wrote", OUT)

    def dist(a, b):
        return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5

    mid_to_high = dist(chips[1], high)
    mid_to_text = dist(chips[1], text)
    side_to_text = min(dist(chips[0], text), dist(chips[2], text))
    turns_gold = mid_to_high + 15 < mid_to_text and mid_to_high < 80
    print("mid_to_high", round(mid_to_high, 1), "mid_to_text", round(mid_to_text, 1),
          "side_to_text", round(side_to_text, 1))
    print("TURNS_GOLD" if turns_gold else "NO_GOLD")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

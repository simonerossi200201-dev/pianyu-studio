# -*- coding: utf-8 -*-
"""T04f-6b-1 B stills: four switch combos + 10-cycle byte identity."""
from __future__ import annotations

import hashlib
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

IMG = os.path.join(REPO, "docs", "plan", "reports", "img")
PACK_DIR = os.path.join(REPO, "resources", "stylepacks")


def _render(task, text="Find its place"):
    from cutora.render.textfx.layout import layout_event
    from cutora.render.textfx.raster import as_bgra, raster_roi
    from cutora.render.textfx.spec import from_style
    style = task["style"]
    spec = from_style(style)
    words = text.split()
    event = {
        "text": text, "start": 0.0, "end": 2.0, "id": "s0",
        "words": [{"word": w, "start": i * 0.2, "end": i * 0.2 + 0.2}
                  for i, w in enumerate(words)],
    }
    lay = layout_event(event, style, spec, 1080, 1920)
    img = raster_roi(lay, spec, style, None, 1080, 1920, skip_blur=True)
    _, bgra = as_bgra(img)
    return img, hashlib.sha256(bgra.tobytes()).hexdigest()


def _v1():
    return {
        "id": "legacy-v1",
        "version": 1,
        "style": {"font_family": "Arimo", "color_text": "#F4F7FB", "fontsize": 48},
        "layout_rules": {"max_lines": 2},
        "animation": {},
        "keyword_rules": [],
    }


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("CUTORA_USER_DATA", tempfile.mkdtemp(prefix="cutora_b_stills_"))
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtWidgets import QApplication
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.subtitle.stylepack import apply_pack_to_task, load_pack

    app = QApplication.instance() or QApplication(["t04f6b1-b"])
    load_embedded_fonts()
    _ = app
    os.makedirs(IMG, exist_ok=True)
    v1 = _v1()
    v2a = load_pack(os.path.join(PACK_DIR, "pop-word"))
    v2b = load_pack(os.path.join(PACK_DIR, "paper-torn"))
    combos = [
        ("v1_to_v1", v1, v1),
        ("v1_to_v2", v1, v2a),
        ("v2_to_v1", v2a, v1),
        ("v2_to_v2", v2a, v2b),
    ]
    task0 = {"timeline": {"width": 1080, "height": 1920}, "style": {}}
    for name, a, b in combos:
        task = {"timeline": {"width": 1080, "height": 1920}, "style": dict(task0["style"])}
        apply_pack_to_task(task, a, apply_animation=True)
        before, _ = _render(task)
        apply_pack_to_task(task, b, apply_animation=True)
        after, _ = _render(task)
        pair = QImage(before.width() + after.width() + 16, max(before.height(), after.height()) + 8,
                      QImage.Format.Format_ARGB32_Premultiplied)
        pair.fill(QColor("#101018"))
        p = QPainter(pair)
        p.drawImage(4, 4, before)
        p.drawImage(before.width() + 12, 4, after)
        p.end()
        path = os.path.join(IMG, "T04f-6b1_switch_%s.png" % name)
        pair.save(path, "PNG")
        print("wrote", path, flush=True)

    ids = sorted(n for n in os.listdir(PACK_DIR)
                 if os.path.isfile(os.path.join(PACK_DIR, n, "style.json")))
    first = load_pack(os.path.join(PACK_DIR, ids[0]))
    task = {"timeline": {"width": 1080, "height": 1920}, "style": {"holy_glow_radius": 99}}
    apply_pack_to_task(task, first, apply_animation=True)
    _, h0 = _render(task)
    for i in range(10):
        apply_pack_to_task(
            task, load_pack(os.path.join(PACK_DIR, ids[i % len(ids)])),
            apply_animation=True)
    apply_pack_to_task(task, first, apply_animation=True)
    _, h1 = _render(task)
    print("cycle10 first=%s again=%s match=%s" % (h0, h1, h0 == h1), flush=True)
    task_off = {"timeline": {"width": 1080, "height": 1920}, "style": {}}
    apply_pack_to_task(task_off, v2a, apply_animation=True)
    _, k0 = _render(task_off)
    for i in range(10):
        apply_pack_to_task(
            task_off, load_pack(os.path.join(PACK_DIR, ids[i % len(ids)])),
            apply_animation=False)
    apply_pack_to_task(task_off, v2a, apply_animation=False)
    _, k1 = _render(task_off)
    print("cycle10_noanim first=%s again=%s match=%s" % (k0, k1, k0 == k1), flush=True)
    return 0 if (h0 == h1) else 1


if __name__ == "__main__":
    raise SystemExit(main())

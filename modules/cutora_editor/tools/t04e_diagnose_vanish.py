# -*- coding: utf-8 -*-
"""T04e-B §1: does 极简白 also vanish? log canvas size vs pos_y."""
from __future__ import annotations

import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from cutora.subtitle.io import canvas_size
from cutora.subtitle.stylepack import apply_pack_to_task, load_pack


CANVASES = (
    (1080, 1920, "portrait"),
    (1920, 1080, "landscape"),
)
PACKS = ("minimal-white", "chant")


def main() -> int:
    rows = []
    for pack_id in PACKS:
        path = os.path.join(REPO, "resources", "stylepacks", pack_id)
        pack = load_pack(path)
        anim = (pack.get("animation") or {}).get("anim_in_type")
        for w, h, label in CANVASES:
            task = {
                "timeline": {"width": w, "height": h},
                "style": {"font_family": "Arimo", "fontsize": 40, "pos_x": 960, "pos_y": 540},
            }
            apply_pack_to_task(task, pack, apply_animation=True)
            cw, ch = canvas_size(task)
            style = task["style"]
            px, py = style.get("pos_x"), style.get("pos_y")
            outside = py is not None and (float(py) < 0 or float(py) > ch)
            row = {
                "pack": pack_id,
                "anim_in_type": anim,
                "canvas": label,
                "canvas_w": cw,
                "canvas_h": ch,
                "pos_x": px,
                "pos_y": py,
                "pos_y_outside": outside,
            }
            rows.append(row)
            print("T04e-B-diag " + json.dumps(row, ensure_ascii=False), flush=True)
    whites = [r for r in rows if r["pack"] == "minimal-white" and r["pos_y_outside"]]
    chants = [r for r in rows if r["pack"] == "chant" and r["pos_y_outside"]]
    if whites and chants:
        verdict = "coord_overflow"
    elif chants and not whites:
        verdict = "holy_glow_only"
    else:
        verdict = "neither_outside"
    print("T04e-B-diag VERDICT " + verdict, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

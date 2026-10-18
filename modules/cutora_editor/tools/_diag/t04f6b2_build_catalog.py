# -*- coding: utf-8 -*-
"""Build resources/stickers/catalog.json from emoji-test + COLRv1 raster probe."""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

EMOJI_TEST = os.path.join(REPO, "build", "emoji-test.txt")
CLDR_XML = os.path.join(REPO, "build", "cldr-annotations-en.xml")
OUT = os.path.join(REPO, "resources", "stickers", "catalog.json")

GROUP_CAT = {
    "Smileys & Emotion": "smileys",
    "People & Body": "people",
    "Animals & Nature": "nature",
    "Food & Drink": "food",
    "Travel & Places": "travel",
    "Activities": "activities",
    "Objects": "objects",
    "Symbols": "symbols",
    "Flags": "flags",
}


def _parse_test(path: str) -> list[dict]:
    group = ""
    out = []
    for raw in open(path, encoding="utf-8"):
        line = raw.strip()
        if line.startswith("# group:"):
            group = line.split(":", 1)[1].strip()
            continue
        if not line or line.startswith("#"):
            continue
        if "; fully-qualified" not in line:
            continue
        left, _, rest = line.partition("#")
        cps = left.split(";")[0].strip()
        m = re.search(r"E\d+\.\d+\s+(.+)$", rest.strip())
        name = (m.group(1).strip() if m else rest.strip()).strip()
        sid = "-".join(p.lower() for p in cps.split())
        cat = GROUP_CAT.get(group, "symbols")
        if group == "Component":
            continue
        out.append({"id": sid, "category": cat, "name": name, "group": group})
    return out


def _cldr_keywords(path: str) -> dict[str, list[str]]:
    if not os.path.isfile(path):
        return {}
    tree = ET.parse(path)
    kw = {}
    for el in tree.getroot().iter("annotation"):
        if el.get("type") == "tts":
            continue
        cp = el.get("cp") or ""
        if not cp:
            continue
        words = [w.strip() for w in (el.text or "").split("|") if w.strip()]
        if words:
            kw[cp] = words
    return kw


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("CUTORA_USER_DATA", tempfile.mkdtemp(prefix="cutora_cat_"))
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=True)
    from PySide6.QtWidgets import QApplication
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.render.stickers import raster_sticker, sticker_text, clear_raster_cache
    from cutora.render.textfx.raster import as_bgra
    import numpy as np

    app = QApplication.instance() or QApplication(["t04f6b2-cat"])
    load_embedded_fonts()
    _ = app
    rows = _parse_test(EMOJI_TEST)
    kws = _cldr_keywords(CLDR_XML)
    kept = []
    skipped = 0
    for i, row in enumerate(rows):
        sid = row["id"]
        img = raster_sticker(sid, 48)
        _, bgra = as_bgra(img)
        vis = int((bgra[..., 3] > 16).sum())
        clear_raster_cache()
        if vis < 30:
            skipped += 1
            continue
        glyph = sticker_text(sid)
        extra = kws.get(glyph) or []
        name = row["name"]
        keys = []
        for w in extra + name.split():
            w = w.strip().lower()
            if w and w not in keys:
                keys.append(w)
        kept.append({
            "id": sid,
            "category": row["category"],
            "name": name,
            "keywords": keys[:12],
        })
        if (i + 1) % 400 == 0:
            print("probe", i + 1, "/", len(rows), "kept", len(kept), flush=True)
    data = {
        "source": {
            "font": "Noto Color Emoji COLRv1",
            "font_repo": "https://github.com/googlefonts/noto-emoji",
            "font_file": "fonts/Noto-COLRv1.ttf",
            "commit": "8998f5dd683424a73e2314a8c1f1e359c19e8742",
            "license": "OFL-1.1",
            "names": "Unicode CLDR annotations (en) + emoji-test.txt",
            "names_license": "Unicode License",
            "emoji_test": "unicode-org/cldr .../emoji/emoji-test.txt (Emoji 18.0, Date: 2026-02-05)",
            "cldr": "unicode-org/cldr common/annotations/en.xml",
        },
        "count": len(kept),
        "items": kept,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print("wrote", OUT, "kept", len(kept), "skipped", skipped, "from", len(rows), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

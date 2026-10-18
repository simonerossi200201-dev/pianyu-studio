# -*- coding: utf-8 -*-
"""Build g08_fx_static / g10_fx_anim fixtures and preview stills.

Does NOT write manifest.json or expected.sha256. Do not record until
Claude has seen the stills.

Usage:
    runtime\\venv\\Scripts\\python.exe tools\\t04f6b2_g08_g10_build.py
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

GOLDEN = os.path.join(REPO, "tests", "golden")
G01_PROJ = os.path.join(GOLDEN, "g01_vertical_basic", "project.asproj.json")
G01_VID = os.path.join(GOLDEN, "_media", "g01", "video.mp4")
G08_DIR = os.path.join(GOLDEN, "g08_fx_static")
G10_DIR = os.path.join(GOLDEN, "g10_fx_anim")
IMG = os.path.join(REPO, "docs", "plan", "reports", "img")
REP = os.path.join(REPO, "docs", "plan", "reports")
W, H = 1080, 1920
SENTENCE = "The hill is lit."
PT_REF = 35  # same size as the 2026-09-13 gate table (stem 5/6/7)
PT_FX = 70   # g08/g10 body size

# Known 35 pt scan-line table from T04f-6b2_rerecord_gate.md
REF_STEM = {"Regular": 5.0, "SemiBold": 6.0, "Bold": 7.0}
REF_INK = {"Regular": 2528, "SemiBold": 3143, "Bold": 3583}

G08_CUES = [
    {
        "role": "fx_grad", "name": "渐变描边",
        "start": 0.0, "end": 2.054,
        "text": "Hello friends.",
        "keep_g01_words": True,
        "mid": 1.02,
    },
    {
        "role": "fx_metal", "name": "金属挤出",
        "start": 2.104, "end": 4.93,
        "text": "Welcome to the studio. Today we will",
        "keep_g01_words": True,
        "mid": 3.52,
    },
    {
        "role": "fx_neon", "name": "霓虹柔影",
        "start": 4.97, "end": 7.385,
        "text": "learn how to edit by meaning.",
        "keep_g01_words": True,
        "mid": 6.18,
    },
    {
        "role": "fx_glass", "name": "厚玻璃",
        "start": 7.435, "end": 9.309,
        "text": "Peace be with you.",
        "keep_g01_words": True,
        "mid": 8.37,
    },
    {
        "role": "fx_fonts", "name": "双字体强调",
        "start": 9.359, "end": 12.0,
        "text": "Let us begin this journey together.",
        "keep_g01_words": True,
        "mid": 10.68,
    },
    {
        "role": "fx_paper", "name": "纸纹",
        "start": 12.10, "end": 14.70,
        "text": "Paper texture holds this line.",
        "keep_g01_words": False,
        "mid": 13.40,
    },
    {
        "role": "fx_bubble", "name": "气泡",
        "start": 14.80, "end": 17.40,
        "text": "Each line gets its own bubble here.",
        "keep_g01_words": False,
        "mid": 16.10,
    },
    {
        "role": "fx_box", "name": "色块",
        "start": 17.50, "end": 20.0,
        "text": "A solid box is the cheapest.",
        "keep_g01_words": False,
        "mid": 18.75,
    },
]

G10_CUES = [
    {
        "role": "an_pop", "name": "入场 pop_in / numbers",
        "start": 0.0, "end": 3.0,
        "text": "Open the API in 2026.",
        "anim": {"in": "pop_in", "loop": "none", "out": "none",
                 "unit": "word", "in_s": 0.35, "out_s": 0.40,
                 "stagger_s": 0.05, "seed": 0},
        "emphasis": {"auto": ["numbers"], "words": [], "color": "#C45C26",
                     "scale": 1.20, "font": "body", "anim": "none",
                     "max_per_cue": 2},
    },
    {
        "role": "an_stomp", "name": "入场 stomp / caps",
        "start": 3.0, "end": 6.0,
        # Short + smaller type so 1.70× peak stays inside SAFE_MARGIN.
        # Do not pin canvas clip. Caps still hit SDK / READY.
        "text": "SDK READY.",
        "fontsize": 56,
        "anim": {"in": "stomp", "loop": "none", "out": "none",
                 "unit": "word", "in_s": 0.35, "out_s": 0.40,
                 "stagger_s": 0.05, "seed": 0},
        "emphasis": {"auto": ["caps"], "words": [], "color": "#C45C26",
                     "scale": 1.20, "font": "body", "anim": "none",
                     "max_per_cue": 2},
    },
    {
        "role": "an_zoom", "name": "循环 zoom_slow / longest",
        "start": 6.0, "end": 9.0,
        "text": "Measure the longest token here.",
        "anim": {"in": "none", "loop": "zoom_slow", "out": "none",
                 "unit": "segment", "in_s": 0.35, "out_s": 0.40,
                 "stagger_s": 0.05, "seed": 0},
        "emphasis": {"auto": ["longest"], "words": [], "color": "#C45C26",
                     "scale": 1.20, "font": "body", "anim": "none",
                     "max_per_cue": 2},
    },
    {
        "role": "an_kara", "name": "循环 karaoke / list",
        "start": 9.0, "end": 12.0,
        "text": "First we align every word.",
        "anim": {"in": "none", "loop": "karaoke", "out": "none",
                 "unit": "word", "in_s": 0.35, "out_s": 0.40,
                 "stagger_s": 0.05, "seed": 0},
        "emphasis": {"auto": ["list"], "words": [], "color": "#C45C26",
                     "scale": 1.20, "font": "body", "anim": "none",
                     "max_per_cue": 2},
    },
    {
        "role": "an_fade", "name": "出场 fade / 双色跳字",
        "start": 12.0, "end": 15.0,
        "text": "Highlight journey and studio.",
        "anim": {"in": "none", "loop": "none", "out": "fade",
                 "unit": "word", "in_s": 0.35, "out_s": 0.40,
                 "stagger_s": 0.05, "seed": 0},
        "emphasis": {"auto": [], "words": ["journey", "studio"],
                     "color": "#C45C26",
                     "palette": ["#7C3AED", "#22D3EE"],
                     "scale": 1.20, "font": "body", "anim": "none",
                     "max_per_cue": 2},
    },
    {
        "role": "an_type", "name": "入场 typewriter / glossary",
        "start": 15.0, "end": 18.0,
        "text": "Check the glossary term CUTORA.",
        "anim": {"in": "typewriter", "loop": "none", "out": "none",
                 "unit": "word", "in_s": 0.80, "out_s": 0.40,
                 "stagger_s": 0.05, "seed": 0},
        "emphasis": {"auto": ["glossary"], "words": [], "color": "#C45C26",
                     "scale": 1.20, "font": "body", "anim": "none",
                     "max_per_cue": 2},
        "glossary": ["CUTORA"],
    },
]


def _save(img, name):
    os.makedirs(IMG, exist_ok=True)
    path = os.path.join(IMG, name)
    img.save(path, "PNG")
    print("wrote", path, img.width(), "x", img.height(), flush=True)
    return path


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print("wrote", path, flush=True)


def _gray(qimg):
    import numpy as np
    from cutora.render.textfx.raster import as_bgra
    _, bgra = as_bgra(qimg)
    b = bgra[..., 0].astype(np.float32)
    g = bgra[..., 1].astype(np.float32)
    r = bgra[..., 2].astype(np.float32)
    a = bgra[..., 3].astype(np.float32)
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return lum, a


def _stem_and_ink(lum, a=None, xh_frac=0.52):
    import numpy as np
    ink = lum > 40
    if a is not None:
        ink = ink & (a > 40)
    total = int(ink.sum())
    ys = np.where(ink.any(axis=1))[0]
    xs = np.where(ink.any(axis=0))[0]
    if len(ys) == 0 or len(xs) == 0:
        return 0.0, total, (0, 0, 0, 0)
    y0, y1 = int(ys[0]), int(ys[-1])
    x0, x1 = int(xs[0]), int(xs[-1])
    y = y0 + int(round((y1 - y0) * xh_frac))
    y = min(max(y, y0), y1)
    row = ink[y]
    runs = []
    n = 0
    for v in row.tolist():
        if v:
            n += 1
        elif n:
            runs.append(n)
            n = 0
    if n:
        runs.append(n)
    stems = [w for w in runs if 2 <= w <= 40]
    stem = float(sorted(stems)[len(stems) // 2]) if stems else 0.0
    return stem, total, (x0, y0, x1 - x0 + 1, y1 - y0 + 1)


def _edge_smear(a, ink):
    """Mean alpha on the 1 px ring around ink. Synthetic bold smears this."""
    import numpy as np
    mask = ink.astype(np.uint8)
    # 4-neighbour dilation minus original = ring
    pad = np.pad(mask, 1, mode="constant")
    ring = (
        pad[0:-2, 1:-1] | pad[2:, 1:-1] | pad[1:-1, 0:-2] | pad[1:-1, 2:]
    ) & (~mask.astype(bool))
    if not ring.any():
        return 0.0
    return float(a[ring].mean())


def _render_weight(name, mode, pt):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QFont, QFontInfo, QImage, QPainter
    from cutora.ui.panels.fonts import get_font_manager, resolve_font_request

    img = QImage(1080, 240, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor("#0B1A33"))
    p = QPainter(img)
    rec = {"kind": mode, "family": "SN Pro", "style": None}
    if mode in ("Regular", "SemiBold", "Bold"):
        font = QFont("SN Pro")
        font.setPointSize(int(pt))
        font.setStyleName(mode)
        rec = {"kind": "explicit", "family": "SN Pro", "style": mode}
    elif mode == "bold_true":
        rec = resolve_font_request("SN Pro")
        font = get_font_manager().get_font("SN Pro", int(pt), bold=True, italic=False)
    elif mode == "bold_false":
        rec = resolve_font_request("SN Pro")
        font = get_font_manager().get_font("SN Pro", int(pt), bold=False, italic=False)
    else:
        raise ValueError(mode)
    info = QFontInfo(font)
    p.setFont(font)
    p.setPen(QColor("#FFFFFF"))
    p.drawText(
        img.rect(),
        int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
        "    " + SENTENCE,
    )
    p.end()
    lum, a = _gray(img)
    stem, ink, bbox = _stem_and_ink(lum, a)
    ink_mask = (lum > 40) & (a > 40)
    smear = _edge_smear(a, ink_mask)
    row = {
        "name": name, "pt": int(pt),
        "family": info.family(), "styleName": info.styleName(),
        "weight": int(info.weight()), "exactMatch": bool(info.exactMatch()),
        "stem": stem, "ink": ink, "bbox": list(bbox), "smear": smear,
        "resolve": rec,
    }
    print("WEIGHT", row, flush=True)
    _save(img, "T04f-6b2_g08_weight_%s_pt%d.png" % (name, int(pt)))
    return row, img


def _absdiff_ink(a_img, b_img):
    import numpy as np
    from cutora.render.textfx.raster import as_bgra
    _, a = as_bgra(a_img)
    _, b = as_bgra(b_img)
    d = np.abs(a.astype(np.int16) - b.astype(np.int16)).max(axis=2)
    return int(d.max()), int((d > 0).sum()), float(d.mean())


def measure_weight():
    """Scan-line SN Pro + bold:true against Regular / SemiBold / Bold."""
    rows_35 = []
    imgs_35 = {}
    for mode, name in (
            ("Regular", "regular"),
            ("SemiBold", "semibold"),
            ("Bold", "bold"),
            ("bold_true", "bold_true"),
            ("bold_false", "bold_false")):
        row, img = _render_weight(name, mode, PT_REF)
        rows_35.append(row)
        imgs_35[name] = img

    rows_70 = []
    for mode, name in (
            ("Regular", "regular"),
            ("SemiBold", "semibold"),
            ("Bold", "bold"),
            ("bold_true", "bold_true")):
        row, _img = _render_weight(name, mode, PT_FX)
        rows_70.append(row)

    bt = next(r for r in rows_35 if r["name"] == "bold_true")
    diffs = {}
    for name in ("regular", "semibold", "bold"):
        mx, nz, mean = _absdiff_ink(imgs_35["bold_true"], imgs_35[name])
        diffs[name] = {"max": mx, "nonzero": nz, "mean": mean}
        print("DIFF bold_true vs %s max=%d nz=%d mean=%.4f" % (
            name, mx, nz, mean), flush=True)

    # Closest named tier at 35 pt by stem, then ink.
    named = [r for r in rows_35 if r["name"] in ("regular", "semibold", "bold")]
    closest = min(
        named,
        key=lambda r: (abs(r["stem"] - bt["stem"]), abs(r["ink"] - bt["ink"])),
    )
    stem_match = abs(closest["stem"] - bt["stem"]) < 0.51
    ink_close = abs(closest["ink"] - bt["ink"]) <= max(8, int(0.01 * closest["ink"]))
    pixel_ident = diffs[closest["name"]]["max"] == 0
    # Synthetic bold typically fattens the 1 px ring without landing on a stem bin.
    intermediate = (not stem_match) or (not ink_close and not pixel_ident)
    smear_flag = bt["smear"] > (closest["smear"] + 8.0)

    if pixel_ident or (stem_match and ink_close and not smear_flag):
        verdict = "freeze"
        tier = closest["name"]
        reason = "SN Pro + bold:true matches explicit %s (stem %.0f / ink %d)" % (
            closest["styleName"], bt["stem"], bt["ink"])
    else:
        verdict = "stop"
        tier = "none"
        reason = (
            "SN Pro + bold:true did not land on Regular/SemiBold/Bold "
            "(stem=%.1f ink=%d smear=%.1f; closest %s stem=%.1f ink=%d; "
            "diff max=%d). Synthetic bold cannot be a baseline." % (
                bt["stem"], bt["ink"], bt["smear"], closest["name"],
                closest["stem"], closest["ink"], diffs[closest["name"]]["max"])
        )

    out = {
        "sentence": SENTENCE, "pt_ref": PT_REF, "pt_fx": PT_FX,
        "rows_35": rows_35, "rows_70": rows_70, "diffs_35": diffs,
        "closest": closest["name"], "verdict": verdict, "tier": tier,
        "reason": reason, "intermediate": intermediate, "smear_flag": smear_flag,
    }
    _write_json(os.path.join(REP, "T04f-6b2_g08_weight.json"), out)

    # Contact: Regular / SemiBold / Bold / bold:true
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QFont, QImage, QPainter
    sheet = QImage(1080, 240 * 4 + 8, QImage.Format.Format_RGB888)
    sheet.fill(QColor("#141210"))
    sp = QPainter(sheet)
    sp.setPen(QColor("#EFE7DA"))
    sp.setFont(QFont("Arimo", 12))
    for i, name in enumerate(("regular", "semibold", "bold", "bold_true")):
        y = i * 240
        sp.drawImage(0, y, imgs_35[name])
        sp.drawText(8, y + 18, name)
    sp.end()
    _save(sheet, "T04f-6b2_g08_weight_contact.png")
    print("WEIGHT_VERDICT", verdict, tier, reason, flush=True)
    return out


def _words_even(text, start, end, wid0=0):
    toks = [t for t in str(text).split() if t]
    n = max(1, len(toks))
    step = (float(end) - float(start)) / float(n)
    out = []
    for i, w in enumerate(toks):
        out.append({
            "word": w,
            "start": float(start) + i * step,
            "end": float(start) + (i + 1) * step,
            "wid": int(wid0 + i),
        })
    return out


def _clamp_g01_words(words, start, end):
    out = []
    for w in words:
        ws = float(w.get("start") or start)
        we = float(w.get("end") or end)
        if we <= start or ws >= end:
            continue
        item = dict(w)
        item["start"] = max(start, ws)
        item["end"] = min(end, we)
        out.append(item)
    return out or _words_even(" ".join(w.get("word", "") for w in words), start, end)


def _v2_base(fx, extra=None):
    """v2 style: exact family SN Pro + bold:true. v1 anims off."""
    style = {
        "font_family": "SN Pro",
        "fontsize": PT_FX,
        "bold": True,
        "italic": False,
        "pos_x": 540,
        "pos_y": 1114,
        "anchor": "bottom-center",
        "offset_x_pct": 0.0,
        "offset_y_pct": -0.42,
        "fontsize_pct": 0.064815,
        "rotation": 0,
        "color_text": "#F4EBD8",
        "color_high": "#F4EBD8",
        "color_bg": "#0B1A33",
        "opacity_bg": 180,
        "opacity_shadow": 255,
        "use_box": False,
        "box_padding_x": 20,
        "box_padding_y": 8,
        "box_radius": 8,
        "box_blur": 0,
        "use_stroke": False,
        "border_width": 0,
        "opacity_outline": 255,
        "color_outline": "#141A26",
        "wrap_lines": 2,
        "line_spacing": 4,
        "dynamic_box": False,
        "anim_in_type": "none",
        "anim_out_type": "none",
        "fade_in_out_enabled": False,
        "high_grad": {"enabled": False, "mode": 1, "dir": 0,
                      "c1": "#F4EBD8", "c2": "#F4EBD8", "c3": "#F4EBD8"},
        "fx": fx,
    }
    if extra:
        style.update(extra)
    return style


def _anim_none():
    return {
        "in": "none", "loop": "none", "out": "none",
        "unit": "segment", "in_s": 0.35, "out_s": 0.25,
        "stagger_s": 0.05, "seed": 0,
    }


def g08_styles():
    fade18 = {"top": 0.18, "bottom": 0.18, "left": 0.18, "right": 0.18}
    return {
        "fx_grad": _v2_base({
            "fill": {"type": "gradient", "colors": ["#F4EBD8", "#E0BE68"],
                     "angle": 90},
            "stroke": {"width_em": 0.04, "color": "#3A2A10", "join": "round"},
            "glow": {"sigma_em": 0, "color": "#22D3EE"},
            "bg": {"type": "none"},
            "fonts": {"body": "SN Pro"},
            "anim": _anim_none(),
            "scatter": {"enabled": False},
        }),
        "fx_metal": _v2_base({
            "fill": {"type": "metal", "metal": "gold"},
            "stroke": {"width_em": 0.03, "color": "#3A2A10", "join": "round"},
            "glow": {"sigma_em": 0, "color": "#22D3EE"},
            "extrude": {"depth_em": 0.14, "angle": 135, "color": "#3A0A14"},
            "bg": {"type": "none"},
            "fonts": {"body": "SN Pro"},
            "anim": _anim_none(),
            "scatter": {"enabled": False},
        }),
        "fx_neon": _v2_base({
            "fill": {"type": "solid", "colors": ["#E8FFFC"]},
            "stroke": {"width_em": 0.02, "color": "#062E2E"},
            "glow": {"sigma_em": 0.18, "spread_em": 0.02, "color": "#22D3EE",
                     "strength": 0.85, "mode": "neon"},
            "shadow": {"sigma_em": 0.10, "dx_em": 0.04, "dy_em": 0.06,
                       "color": "#141A26", "alpha": 0.55},
            "bg": {"type": "none"},
            "fonts": {"body": "SN Pro"},
            "anim": _anim_none(),
            "scatter": {"enabled": False},
        }, extra={"color_text": "#E8FFFC", "color_high": "#E8FFFC"}),
        "fx_glass": _v2_base({
            "fill": {"type": "solid", "colors": ["#F4F7FB"]},
            "stroke": {"width_em": 0, "color": "#141A26"},
            "glow": {"sigma_em": 0, "color": "#22D3EE"},
            "shadow": {"sigma_em": 0.10, "dx_em": 0.04, "dy_em": 0.06,
                       "color": "#141A26", "alpha": 0.55},
            "bg": {
                "type": "glass", "color": "#0B1A33", "alpha": 0.35,
                "radius_em": 0.45, "pad_em": [0.55, 0.32],
                "glass_sigma_em": 0.7, "glass_border": True,
                "glass_thickness_em": 0.14, "glass_refraction": 0.55,
                "glass_bevel": 0.7, "glass_sheen": 0.45,
                "glass_light_deg": 315,
                "fade": fade18,
            },
            "fonts": {"body": "SN Pro"},
            "anim": _anim_none(),
            "scatter": {"enabled": False},
        }, extra={"color_text": "#F4F7FB", "color_high": "#F4F7FB"}),
        "fx_fonts": _v2_base({
            "fill": {"type": "solid", "colors": ["#F4EBD8"]},
            "stroke": {"width_em": 0.03, "color": "#141A26"},
            "glow": {"sigma_em": 0, "color": "#22D3EE"},
            "bg": {"type": "none"},
            "fonts": {"body": "SN Pro", "emphasis": "Caveat"},
            "emphasis": {
                "auto": [], "words": ["journey"], "color": "#C45C26",
                "scale": 1.20, "font": "emphasis", "anim": "none",
                "max_per_cue": 2,
            },
            "anim": _anim_none(),
            "scatter": {"enabled": False},
            "tracking_em": 0.08,
            "letter_case": "upper",
        }),
        "fx_paper": _v2_base({
            "fill": {"type": "solid", "colors": ["#3A0A14"]},
            "stroke": {"width_em": 0, "color": "#141A26"},
            "glow": {"sigma_em": 0, "color": "#22D3EE"},
            "bg": {
                "type": "texture", "texture": "paper",
                "color": "#F1E9D8", "alpha": 0.92,
                "radius_em": 0.12, "pad_em": [0.5, 0.32],
                "texture_tint": "#F1E9D8",
            },
            "fonts": {"body": "SN Pro"},
            "anim": _anim_none(),
            "scatter": {"enabled": False},
        }, extra={"color_text": "#3A0A14", "color_high": "#3A0A14"}),
        "fx_bubble": _v2_base({
            "fill": {"type": "solid", "colors": ["#F4F7FB"]},
            "stroke": {"width_em": 0, "color": "#141A26"},
            "glow": {"sigma_em": 0, "color": "#22D3EE"},
            "bg": {
                "type": "bubble", "color": "#0B1A33", "alpha": 0.82,
                "radius_em": 0.55, "pad_em": [0.45, 0.28],
                "align": "alternate", "line_gap_em": 0.18,
                "fade": fade18,
            },
            "fonts": {"body": "SN Pro"},
            "anim": _anim_none(),
            "scatter": {"enabled": False},
        }, extra={"color_text": "#F4F7FB", "color_high": "#F4F7FB"}),
        "fx_box": _v2_base({
            "fill": {"type": "solid", "colors": ["#F4F7FB"]},
            "stroke": {"width_em": 0, "color": "#141A26"},
            "glow": {"sigma_em": 0, "color": "#22D3EE"},
            "bg": {
                "type": "box", "color": "#0B1A33", "alpha": 0.86,
                "radius_em": 0.0, "pad_em": [0.55, 0.28],
            },
            "fonts": {"body": "SN Pro"},
            "anim": _anim_none(),
            "scatter": {"enabled": False},
        }, extra={"color_text": "#F4F7FB", "color_high": "#F4F7FB"}),
    }


def g10_styles():
    out = {}
    for cue in G10_CUES:
        extra = {}
        if cue.get("glossary"):
            extra["_glossary"] = list(cue["glossary"])
        if cue.get("fontsize"):
            extra["fontsize"] = int(cue["fontsize"])
            extra["fontsize_pct"] = float(cue["fontsize"]) / float(W)
        out[cue["role"]] = _v2_base({
            "fill": {"type": "solid", "colors": ["#F4EBD8"]},
            "stroke": {"width_em": 0.03, "color": "#141A26", "join": "round"},
            "glow": {"sigma_em": 0, "color": "#22D3EE"},
            "bg": {"type": "none"},
            "fonts": {"body": "SN Pro"},
            "emphasis": cue["emphasis"],
            "anim": cue["anim"],
            "scatter": {"enabled": False},
        }, extra=extra)
    return out


def _g01_words_by_text():
    with open(G01_PROJ, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    segs = data["tasks"][0]["segments"]
    return {s["text"]: s.get("words") or [] for s in segs}


def _make_segments(cues, g01_words, even=False):
    segs = []
    wid = 0
    for cue in cues:
        start, end, text = cue["start"], cue["end"], cue["text"]
        if (not even) and cue.get("keep_g01_words") and text in g01_words:
            words = _clamp_g01_words(g01_words[text], start, end)
            for w in words:
                w["wid"] = wid
                wid += 1
        else:
            words = _words_even(text, start, end, wid)
            wid += len(words)
        segs.append({
            "start": float(start),
            "end": float(end),
            "words": words,
            "text": text,
            "role": cue["role"],
            "lang": "en",
        })
    return segs


def _roles_dict(styles, cues):
    roles = {}
    for cue in cues:
        rid = cue["role"]
        roles[rid] = {"name": cue["name"], "style": copy.deepcopy(styles[rid])}
    # Keep a default so loaders that inject one do not invent a v1 style.
    first = cues[0]["role"]
    roles["default"] = {
        "name": "默认",
        "style": copy.deepcopy(styles[first]),
    }
    return roles


def _relayout_per_role(task, styles):
    """Freeze ``lines`` per cue using that role's style. Do not split cues."""
    from cutora.subtitle.layout import LayoutRules, _pixel_cap_lines, break_lines
    segs = list(task.get("segments") or [])
    first = segs[0].get("role")
    for i, seg in enumerate(segs):
        rid = seg.get("role") or "default"
        style = styles.get(rid) or task.get("style")
        task["style"] = copy.deepcopy(style)
        local = LayoutRules(
            max_chars_per_line=32,
            max_lines=2,
            balance_lines=True,
            lang="en",
            line_break="space",
        )
        lines = break_lines(seg, local)
        seg["lines"] = _pixel_cap_lines(seg, lines, task, local)
        print("LINES", i, rid, seg.get("lines"), flush=True)
    task["style"] = copy.deepcopy(styles[first])
    n = len(task.get("segments") or [])
    if n != len(segs):
        raise RuntimeError("relayout changed cue count %d -> %d" % (len(segs), n))
    return task


def build_from_g01(cues, styles, duration, case_name, extra_task=None):
    with open(G01_PROJ, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    g01_words = _g01_words_by_text()
    even = case_name.startswith("g10")
    segs = _make_segments(cues, g01_words, even=even)
    task = data["tasks"][0]
    task["video"] = G01_VID
    task["path"] = G01_VID
    task["text"] = " ".join(c["text"] for c in cues)
    task["segments"] = segs
    task["dims"] = [W, H]
    task["duration"] = float(duration)
    task["trim_end_sec"] = float(duration)
    task["subtitle_roles"] = _roles_dict(styles, cues)
    first = cues[0]["role"]
    task["style"] = copy.deepcopy(styles[first])
    task["layout_rules"] = {
        "max_chars_per_line": 32,
        "max_lines": 2,
        "balance_lines": True,
        "line_break": "space",
    }
    if extra_task:
        task.update(extra_task)
    # Keep g01 media paths; duration stays 20 s so the clip out_t matches.
    data["tasks"] = [task]
    data["app"] = "CUTORA"
    data["created_at"] = "2026-09-14 00:00:00"
    _relayout_per_role(task, styles)
    return data


def _grab_frame(path, t):
    import cv2
    from cutora.render.readers import _cv2_frame_to_qimage
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise RuntimeError("cannot open %s" % path)
    cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, float(t) * 1000.0))
    ok, frame = cap.read()
    cap.release()
    if not ok or frame is None:
        raise RuntimeError("no frame t=%.3f %s" % (t, path))
    img = _cv2_frame_to_qimage(frame)
    if img is None:
        raise RuntimeError("qimage failed")
    from PySide6.QtCore import Qt
    if img.width() != W or img.height() != H:
        img = img.scaled(W, H, Qt.AspectRatioMode.IgnoreAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    return img


def _render_at(task, t):
    from cutora.render.export_renderer import ExportRenderer
    from cutora.render.frame_renderer import compute_render_order
    from cutora.render.textfx import clear_roi_cache
    clear_roi_cache()
    # Stills draw the video ourselves. Drop timeline so FrameRenderer.draw_layers
    # does not hit main_window._tl_of's broken config import on a dict timeline.
    task = dict(task)
    task.pop("timeline", None)
    bg = _grab_frame(task.get("video") or G01_VID, t)
    order = compute_render_order(task)

    def draw_bg(p, image=bg):
        p.drawImage(0, 0, image)

    canvas = ExportRenderer.render_frame(
        task, W, H, float(t),
        draw_background=draw_bg,
        render_order=order,
        backdrop=bg,
    )
    return canvas, bg


def _crop_band(img, y0_frac=0.42, y1_frac=0.78):
    y0 = int(img.height() * y0_frac)
    y1 = int(img.height() * y1_frac)
    return img.copy(0, y0, img.width(), max(1, y1 - y0))


def _sheet(rows, path, title, cell_w=270, cell_h=480):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QFont, QImage, QPainter
    cols = min(4, max(1, len(rows)))
    n = len(rows)
    grid_rows = (n + cols - 1) // cols
    pad, gap, label_h = 16, 10, 36
    sw = pad * 2 + cols * cell_w + (cols - 1) * gap
    sh = pad * 2 + 28 + grid_rows * (cell_h + label_h + gap)
    sheet = QImage(sw, sh, QImage.Format.Format_RGB888)
    sheet.fill(QColor("#141210"))
    p = QPainter(sheet)
    p.setPen(QColor("#EFE7DA"))
    p.setFont(QFont("Arimo", 13))
    p.drawText(pad, 22, title)
    p.setFont(QFont("Arimo", 10))
    for i, (label, img) in enumerate(rows):
        r, c = divmod(i, cols)
        x = pad + c * (cell_w + gap)
        y = pad + 28 + r * (cell_h + label_h + gap)
        scaled = img.scaled(cell_w, cell_h,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation)
        p.drawImage(x, y, scaled)
        p.drawText(x, y + scaled.height() + 2, cell_w, label_h,
                   int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop),
                   label)
    p.end()
    _save(sheet, os.path.basename(path))


def render_g08_stills(task):
    rows = []
    crops = []
    for i, cue in enumerate(G08_CUES):
        t = float(cue["mid"])
        canvas, _bg = _render_at(task, t)
        _save(canvas, "T04f-6b2_g08_c%d.png" % i)
        band = _crop_band(canvas)
        _save(band, "T04f-6b2_g08_c%d_band.png" % i)
        label = "c%d  %s  t=%.2fs\n%s" % (i, cue["name"], t, cue["text"])
        rows.append((label, canvas))
        crops.append((label, band))
    _sheet(rows, os.path.join(IMG, "T04f-6b2_g08_contact.png"),
           "g08_fx_static  8 cues  mid-hold", cell_w=216, cell_h=384)
    _sheet(crops, os.path.join(IMG, "T04f-6b2_g08_bands.png"),
           "g08_fx_static  subtitle band", cell_w=320, cell_h=140)


def _peak_t(event, spec, word_k=0):
    """t that maximises visible scale (scale × alpha) on word *word_k* during in."""
    from cutora.render.textfx.anim import transform_for, unit_progress
    start = float(event.get("start") or 0.0)
    words = event.get("words") or []
    word = words[word_k] if word_k < len(words) else None
    n = max(1, len(words))
    in_s = float(spec.anim.in_s)
    best_t, best = start, -1.0
    t = start
    while t <= start + in_s + 0.02:
        phase, u = unit_progress(t, word_k, n, spec.anim, event, word)
        xf = transform_for(phase, u, spec.anim, {
            "k": word_k, "seed": spec.anim.seed, "seg_id": "",
        })
        score = float(xf.scale_x) * float(xf.alpha)
        if score > best:
            best, best_t = score, t
        t += 0.005
    return best_t, best


def _fade_mid_t(event, spec):
    """Mid-out for word 0: alpha ≈ 0.5 after OUT_MIRROR, not already gone."""
    end = float(event.get("end") or 0.0)
    out_s = float(spec.anim.out_s)
    n = max(1, len(event.get("words") or []))
    stagger = float(spec.anim.stagger_s)
    out0 = end - out_s - (n - 1) * stagger
    return out0 + 0.5 * out_s


def _g10_sample_times(cue, event, spec):
    s, e = float(cue["start"]), float(cue["end"])
    start_t, mid_t, end_t = s + 0.15, (s + e) / 2.0, e - 0.15
    if cue["role"] == "an_pop":
        start_t, score = _peak_t(event, spec, 0)
        print("POP_PEAK t=%.3f score=%.3f" % (start_t, score), flush=True)
    elif cue["role"] == "an_stomp":
        start_t, score = _peak_t(event, spec, 0)
        print("STOMP_PEAK t=%.3f score=%.3f" % (start_t, score), flush=True)
    elif cue["role"] == "an_fade":
        end_t = _fade_mid_t(event, spec)
        print("FADE_MID t=%.3f" % end_t, flush=True)
    return start_t, mid_t, end_t


def _ink_bbox(qimg, luma_min=40, alpha_min=40):
    import numpy as np
    lum, a = _gray(qimg)
    ink = (lum > luma_min) & (a > alpha_min)
    ys, xs = np.where(ink)
    if len(xs) == 0:
        return None, 0
    return (int(xs.min()), int(ys.min()),
            int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)), int(ink.sum())


def _render_on_solid(task, t, color="#1A1520"):
    from PySide6.QtGui import QColor, QImage
    from cutora.render.export_renderer import ExportRenderer
    from cutora.render.frame_renderer import compute_render_order
    from cutora.render.textfx import clear_roi_cache
    clear_roi_cache()
    task = dict(task)
    task.pop("timeline", None)
    solid = QImage(W, H, QImage.Format.Format_ARGB32_Premultiplied)
    solid.fill(QColor(color))

    def draw_bg(p, image=solid):
        p.drawImage(0, 0, image)

    order = compute_render_order(task)
    return ExportRenderer.render_frame(
        task, W, H, float(t),
        draw_background=draw_bg,
        render_order=order,
        backdrop=solid,
    ), solid


def check_stomp_margin(task, t):
    """1.70× peak must stay inside SAFE_MARGIN. Do not freeze canvas clip."""
    import numpy as np
    from cutora.render.textfx.raster import as_bgra
    from cutora.subtitle.stylepack import SAFE_MARGIN
    solid, _bg = _render_on_solid(task, t, "#1A1520")
    canvas, video_bg = _render_at(task, t)
    _, bgra = as_bgra(solid)
    a = bgra[..., 3]
    r = bgra[..., 2].astype(np.float32)
    g = bgra[..., 1].astype(np.float32)
    bch = bgra[..., 0].astype(np.float32)
    luma = 0.2126 * r + 0.7152 * g + 0.0722 * bch
    # Solid fill is #1A1520; subtitle ink is brighter or different.
    ink = (a > 40) & (luma > 40)
    ys, xs = np.where(ink)
    if len(xs) == 0:
        raise RuntimeError("stomp peak has no ink on solid")
    x, y = int(xs.min()), int(ys.min())
    w, h = int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)
    n = int(ink.sum())
    margin = int(round(W * float(SAFE_MARGIN)))
    left_ok = x >= margin
    right_ok = (x + w) <= (W - margin)
    top_ok = y >= margin
    bot_ok = (y + h) <= (H - margin)
    rec = {
        "t": t, "bbox": [x, y, w, h], "ink": n,
        "margin_px": margin, "safe_margin": SAFE_MARGIN,
        "left_ok": left_ok, "right_ok": right_ok,
        "top_ok": top_ok, "bottom_ok": bot_ok,
        "ok": bool(left_ok and right_ok and top_ok and bot_ok),
    }
    print("STOMP_MARGIN", rec, flush=True)
    _save(solid, "T04f-6b2_g10_c1_start_solid.png")
    return rec, canvas


def render_g10_stills(task):
    from cutora.render.textfx.spec import from_style
    rows = []
    crops = []
    times = []
    margin = None
    for i, cue in enumerate(G10_CUES):
        event = task["segments"][i]
        style = task["subtitle_roles"][cue["role"]]["style"]
        spec = from_style(style)
        start_t, mid_t, end_t = _g10_sample_times(cue, event, spec)
        samples = (("start", start_t), ("mid", mid_t), ("end", end_t))
        for tag, t in samples:
            if cue["role"] == "an_stomp" and tag == "start":
                rec, canvas = check_stomp_margin(task, t)
                margin = rec
                if not rec["ok"]:
                    raise RuntimeError(
                        "stomp 1.70x peak clips SAFE_MARGIN: %s" % rec)
            else:
                canvas, _bg = _render_at(task, t)
            name = "T04f-6b2_g10_c%d_%s.png" % (i, tag)
            _save(canvas, name)
            band = _crop_band(canvas)
            _save(band, "T04f-6b2_g10_c%d_%s_band.png" % (i, tag))
            label = "c%d %s t=%.2f\n%s" % (i, tag, t, cue["name"])
            rows.append((label, canvas))
            crops.append((label, band))
            times.append({"cue": i, "tag": tag, "t": round(t, 4),
                          "role": cue["role"]})
    _sheet(rows, os.path.join(IMG, "T04f-6b2_g10_contact.png"),
           "g10_fx_anim  6 cues x start/mid/end", cell_w=180, cell_h=320)
    _sheet(crops, os.path.join(IMG, "T04f-6b2_g10_bands.png"),
           "g10_fx_anim  subtitle band", cell_w=240, cell_h=110)
    return times, margin


def g08_c1_metal_proof(task):
    """4× crop + face/side RGB. Side must be darker than face and not pure black."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QImage, QPainter, QPen
    from cutora.render.textfx.layout import layout_event
    from cutora.render.textfx.raster import as_bgra, raster_text
    from cutora.render.textfx.ramps import darken_for_extrude, mix_hex
    from cutora.render.textfx.spec import from_style, is_pure_black, rgb_tuple
    import numpy as np

    cue = G08_CUES[1]
    t = float(cue["mid"])
    canvas, bg = _render_at(task, t)
    import numpy as np
    from cutora.render.textfx.raster import as_bgra as _as_bgra
    _, ca = _as_bgra(canvas)
    _, ba = _as_bgra(bg)
    d = np.abs(ca.astype(np.int16) - ba.astype(np.int16)).max(axis=2)
    ys, xs = np.where(d > 18)
    if len(xs) == 0:
        raise RuntimeError("g08 c1 has no ink vs backdrop")
    x, y = int(xs.min()), int(ys.min())
    w, h = int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)
    pad = 12
    x0 = max(0, x - pad)
    y0 = max(0, y - pad)
    x1 = min(canvas.width(), x + w + pad)
    y1 = min(canvas.height(), y + h + pad)
    tight = canvas.copy(x0, y0, x1 - x0, y1 - y0)
    x4 = tight.scaled(tight.width() * 4, tight.height() * 4,
                      Qt.AspectRatioMode.IgnoreAspectRatio,
                      Qt.TransformationMode.FastTransformation)
    _save(x4, "T04f-6b2_g08_c1_4x.png")

    # Isolated raster (no video) so side #3A0A14 is not mixed with the gray card.
    seg = task["segments"][1]
    style = task["subtitle_roles"]["fx_metal"]["style"]
    spec = from_style(style)
    layout = layout_event(seg, style, spec, W, H)
    roi = raster_text(layout, spec, style, skip_blur=True, t=t)
    iso = QImage(roi.width(), roi.height(), QImage.Format.Format_RGB888)
    iso.fill(QColor("#1A1520"))
    ip = QPainter(iso)
    ip.drawImage(0, 0, roi)
    ip.end()
    iso4 = iso.scaled(iso.width() * 4, iso.height() * 4,
                      Qt.AspectRatioMode.IgnoreAspectRatio,
                      Qt.TransformationMode.FastTransformation)
    _save(iso4, "T04f-6b2_g08_c1_4x_isolated.png")

    owned, bgra = as_bgra(roi)
    b = bgra[..., 0].astype(np.float32)
    g = bgra[..., 1].astype(np.float32)
    r = bgra[..., 2].astype(np.float32)
    a = bgra[..., 3].astype(np.float32)
    luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
    opaque = a > 180
    face_m = opaque & (luma > 140)
    # Explicit extrude.color #3A0A14; far = mix 15% toward #141A26.
    side_hex = spec.extrude.color or darken_for_extrude("#F4EBD8")
    far_hex = mix_hex(side_hex, "#141A26", 0.15)
    sr, sg, sb = rgb_tuple(side_hex)
    dist = np.abs(r - sr) + np.abs(g - sg) + np.abs(b - sb)
    side_m = opaque & (luma < 90) & (dist < 80)

    def _mean(mask):
        if not mask.any():
            return None
        rr = float(r[mask].mean())
        gg = float(g[mask].mean())
        bb = float(b[mask].mean())
        return {
            "rgb": [round(rr, 1), round(gg, 1), round(bb, 1)],
            "hex": "#%02X%02X%02X" % (int(round(rr)), int(round(gg)), int(round(bb))),
            "n": int(mask.sum()),
        }

    face = _mean(face_m)
    side = _mean(side_m)
    # Mark a few sample pixels on the 4× isolated image.
    marked = QImage(iso4)
    mp = QPainter(marked)
    def _dots(mask, color, n=8):
        ys, xs = np.where(mask)
        if len(xs) == 0:
            return
        step = max(1, len(xs) // n)
        mp.setPen(QPen(QColor(color), 3))
        for i in range(0, len(xs), step):
            mp.drawRect(int(xs[i]) * 4 - 2, int(ys[i]) * 4 - 2, 8, 8)
    _dots(face_m, "#FFE08A", 10)
    _dots(side_m, "#7C3AED", 10)
    mp.end()
    _save(marked, "T04f-6b2_g08_c1_4x_samples.png")

    face_l = None if face is None else (
        0.2126 * face["rgb"][0] + 0.7152 * face["rgb"][1] + 0.0722 * face["rgb"][2])
    side_l = None if side is None else (
        0.2126 * side["rgb"][0] + 0.7152 * side["rgb"][1] + 0.0722 * side["rgb"][2])
    rec = {
        "text": cue["text"],
        "text_note": (
            "g01 ASR segment 1, kept for frozen word times. Not a truncated "
            "sentence: c2 continues 'learn how to edit by meaning.'"
        ),
        "extrude_color_spec": spec.extrude.color,
        "extrude_color_rgb": list(rgb_tuple(side_hex)),
        "far_hex": far_hex,
        "far_rgb": list(rgb_tuple(far_hex)),
        "face": face,
        "side": side,
        "side_darker_than_face": (
            face_l is not None and side_l is not None and side_l < face_l - 20
        ),
        "side_not_pure_black": (
            (not is_pure_black(side["hex"])) if side else False
        ),
        "spec_not_pure_black": not is_pure_black(side_hex),
        "far_not_pure_black": not is_pure_black(far_hex),
        "bbox_video": [x, y, w, h],
    }
    print("EXTRUDE", rec, flush=True)
    _write_json(os.path.join(REP, "T04f-6b2_g08_c1_extrude.json"), rec)
    return rec


def probe_g10_timing(task):
    """Confirm loop uses t-start, and report is_static_hold per cue."""
    from cutora.render.textfx.anim import (
        LOOP_PERIOD_S, is_static_hold, unit_progress,
    )
    from cutora.render.textfx.spec import from_style
    report = {"LOOP_PERIOD_S": LOOP_PERIOD_S, "cues": []}
    for i, cue in enumerate(G10_CUES):
        segs = task["segments"]
        event = segs[i]
        style = task["subtitle_roles"][cue["role"]]["style"]
        spec = from_style(style)
        start = float(event["start"])
        # Same wall t, two calls: identical frac (no wall clock, no frame count).
        t = start + 0.73
        p1 = unit_progress(t, 0, 1, spec.anim, event)
        p2 = unit_progress(t, 0, 1, spec.anim, event)
        t2 = t + LOOP_PERIOD_S
        p3 = unit_progress(t2, 0, 1, spec.anim, event)
        holds = []
        n = 0
        false_n = 0
        t0, t1 = float(event["start"]), float(event["end"])
        tt = t0
        while tt <= t1 + 1e-9:
            h = bool(is_static_hold(tt, event, spec))
            holds.append(h)
            n += 1
            if not h:
                false_n += 1
            tt += 0.1
        row = {
            "cue": i, "role": cue["role"], "loop": spec.anim.loop,
            "in": spec.anim.in_name, "out": spec.anim.out_name,
            "unit_progress_repeat_same": p1 == p2,
            "unit_progress_plus_period": p1 == p3,
            "phase_at_0.73": p1[0], "u_at_0.73": p1[1],
            "hold_samples": n, "static_hold_false": false_n,
            "static_hold_true": n - false_n,
            "all_false": false_n == n,
        }
        report["cues"].append(row)
        print("G10_TIMING", row, flush=True)
    return report


def _fonts_digest():
    sys.path.insert(0, os.path.join(REPO, "tools"))
    import golden_check
    count, digest = golden_check._fonts_digest()
    recorded = golden_check._read_recorded_fonts_digest()
    return {
        "count": count, "digest": digest, "recorded": recorded,
        "match": digest.lower() == (recorded or "").lower(),
    }


def _write_readmes(weight):
    g08 = os.path.join(G08_DIR, "README.md")
    g10 = os.path.join(G10_DIR, "README.md")
    amber = os.path.join(GOLDEN, "g08_stylepack_amber", "README.md")
    with open(g08, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("# g08_fx_static\n\n")
        fh.write("v2 `style.fx` static hold. One role per cue, 8 cues, 20 s, ")
        fh.write("1080×1920, media = g01 `video.mp4`.\n\n")
        fh.write("Font: `font_family: \"SN Pro\"` + `bold: true` (exact family ")
        fh.write("path, same as the 14 stylepacks). Scan-line at 35 pt: **%s**.\n\n"
                 % weight["reason"])
        fh.write("Not recorded until Claude signs off the stills.\n\n")
        fh.write("| # | t | role | pins |\n|---|---|---|---|\n")
        pins = [
            "fill.gradient + stroke",
            "fill.metal=gold + extrude (not pure black)",
            "glow.neon + shadow",
            "bg.glass + four-side fade=0.18 (live video backdrop)",
            "fonts.body/emphasis + tracking + letter_case=upper + words=journey",
            "bg.texture=paper (_feather_alpha / un-premultiply)",
            "bg.bubble + per-line fade",
            "bg.box",
        ]
        for i, cue in enumerate(G08_CUES):
            fh.write("| %d | %.2f–%.2f | `%s` | %s |\n" % (
                i, cue["start"], cue["end"], cue["role"], pins[i]))
        fh.write("\nStills: `docs/plan/reports/img/T04f-6b2_g08_c*.png`.\n")
        fh.write("No `manifest.json` until record.\n")
    with open(g10, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("# g10_fx_anim\n\n")
        fh.write("v2 `fx.anim` phases + emphasis rules. 6 cues × 3 s, media g01 ")
        fh.write("(20 s, last 2 s empty). Glossary is `_glossary: [\"CUTORA\"]` ")
        fh.write("on the cue style — does not read USER_DATA.\n\n")
        fh.write("Font: same exact-family path as g08 (`SN Pro` + `bold: true`, ")
        fh.write("tier **%s**).\n\n" % weight["tier"])
        fh.write("Not recorded until Claude signs off the stills.\n\n")
        fh.write("| # | t | anim | emphasis |\n|---|---|---|---|\n")
        for i, cue in enumerate(G10_CUES):
            fh.write("| %d | %.0f–%.0f | in=%s loop=%s out=%s unit=%s | %s |\n" % (
                i, cue["start"], cue["end"],
                cue["anim"]["in"], cue["anim"]["loop"], cue["anim"]["out"],
                cue["anim"]["unit"], cue["name"]))
        fh.write("\nStills: `docs/plan/reports/img/T04f-6b2_g10_c*_*.png` ")
        fh.write("(start / mid / end per cue).\n")
        fh.write("No `manifest.json` until record.\n")
    with open(amber, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("# g08_stylepack_amber\n\n")
        fh.write("Draft amber-karaoke on B-roll stills. **Not a golden.**\n\n")
        fh.write("Replaced by `tests/golden/g08_fx_static/` (v2 static fx, ")
        fh.write("no karaoke loop). Kept on disk because ")
        fh.write("`tests/test_t04e_stylepack.py` still opens this project.\n")
    print("wrote readmes", flush=True)


def main() -> int:
    review2 = "--review2" in sys.argv
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if "CUTORA_USER_DATA" not in os.environ:
        os.environ["CUTORA_USER_DATA"] = tempfile.mkdtemp(prefix="cutora_g08g10_")
    from cutora.core.runtime import init_torch_runtime
    init_torch_runtime(render_child=False)
    from cutora.app import apply_ui_locale
    apply_ui_locale()
    from PySide6.QtWidgets import QApplication, QStyleFactory
    from cutora.ui.panels.fonts import load_embedded_fonts
    from cutora.ui.theme.apply import apply_app_qss
    from cutora.ui.theme.fonts_ui import register_ui_fonts, ui_qfont

    app = QApplication.instance() or QApplication(["t04f6b2-g08-g10"])
    if not load_embedded_fonts():
        raise SystemExit("font load failed")
    register_ui_fonts()
    app.setFont(ui_qfont())
    app.setStyle(QStyleFactory.create("Fusion"))
    apply_app_qss(app)

    fd = _fonts_digest()
    print("FONTS_DIGEST", fd, flush=True)
    if not fd["match"]:
        print("FONT SET CHANGED — refuse to freeze", flush=True)
        return 2

    if review2:
        weight = {
            "verdict": "freeze", "tier": "bold",
            "reason": "SN Pro + bold:true matches explicit Bold (stem 7 / ink 3583)",
        }
    else:
        weight = measure_weight()
        if weight["verdict"] != "freeze":
            print("STOP: " + weight["reason"], flush=True)
            return 3

    g08_st = g08_styles()
    g10_st = g10_styles()
    g08 = build_from_g01(G08_CUES, g08_st, 20.0, "g08")
    g10 = build_from_g01(G10_CUES, g10_st, 20.0, "g10")
    _write_json(os.path.join(G08_DIR, "project.asproj.json"), g08)
    _write_json(os.path.join(G10_DIR, "project.asproj.json"), g10)
    _write_readmes(weight)

    if not review2:
        render_g08_stills(g08["tasks"][0])
    extrude = g08_c1_metal_proof(g08["tasks"][0])
    g10_times, margin = render_g10_stills(g10["tasks"][0])
    timing = probe_g10_timing(g10["tasks"][0])
    _write_json(os.path.join(REP, "T04f-6b2_g10_timing.json"), {
        "fonts_digest": fd, "weight_tier": weight["tier"],
        "stills": g10_times, "timing": timing, "stomp_margin": margin,
        "extrude": extrude,
    })
    print("DONE stills; no manifest; no record", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

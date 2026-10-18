# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Deterministic CUTORA core pack: 12 element + 12 frame + 12 lower_third SVGs.

Fixed seed. Colors from theme tokens (never pure black/white). Geometry uses
only path / rect / circle / linearGradient. No Grok-generated images.
"""
from __future__ import annotations

import json
import os
import random
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from cutora.ui.theme.tokens import (  # noqa: E402
    ACCENT, ACCENT_DIM, ACCENT_HI, BG2, ERR, FG0, FG1, INFO, OK, WARN,
)

SEED = 20260918
NS = "http://www.w3.org/2000/svg"
TOKENS = (ACCENT, ACCENT_HI, ACCENT_DIM, FG0, FG1, BG2, INFO, OK, WARN, ERR)


def _svg(w, h, body: str) -> str:
    return (
        '<svg xmlns="%s" width="%d" height="%d" viewBox="0 0 %d %d">\n'
        "%s"
        "</svg>\n" % (NS, w, h, w, h, body)
    )


def _grad(gid, c0, c1, x2="0", y2="1"):
    return (
        '  <defs>\n'
        '    <linearGradient id="%s" x1="0" y1="0" x2="%s" y2="%s">\n'
        '      <stop offset="0" stop-color="%s"/>\n'
        '      <stop offset="1" stop-color="%s"/>\n'
        '    </linearGradient>\n'
        '  </defs>\n' % (gid, x2, y2, c0, c1)
    )


def _item_json(item_id, typ, name, filename, color_default, extra_params=None):
    params = {
        "color.primary": {"type": "color", "default": color_default, "label": "主色"},
    }
    if extra_params:
        params.update(extra_params)
    return {
        "id": item_id,
        "type": typ,
        "name": name,
        "license": "CUTORA-original",
        "tags": [typ, "core"],
        "aspect": ["9:16", "16:9"],
        "file": filename,
        "params": params,
    }


def _elements(rng: random.Random) -> list[tuple[str, str, str, str]]:
    """id, name, filename, svg body (512)."""
    c = "{{color.primary}}"
    items = [
        ("el_disc", "圆片",
         '  <circle cx="256" cy="256" r="180" fill="%s"/>\n' % c),
        ("el_ring", "圆环",
         '  <circle cx="256" cy="256" r="170" fill="none" stroke="%s" stroke-width="28"/>\n' % c),
        ("el_roundrect", "圆角板",
         '  <rect x="64" y="96" width="384" height="320" rx="36" fill="%s"/>\n' % c),
        ("el_pill", "胶囊",
         '  <rect x="56" y="200" width="400" height="112" rx="56" fill="%s"/>\n' % c),
        ("el_diamond", "菱形",
         '  <path d="M256 48 L464 256 L256 464 L48 256 Z" fill="%s"/>\n' % c),
        ("el_hex", "六边",
         '  <path d="M256 40 L430 148 L430 364 L256 472 L82 364 L82 148 Z" fill="%s"/>\n' % c),
        ("el_chevron", "指示",
         '  <path d="M120 80 L392 256 L120 432 L120 352 L280 256 L120 160 Z" fill="%s"/>\n' % c),
        ("el_plus", "十字",
         '  <rect x="216" y="64" width="80" height="384" rx="12" fill="%s"/>\n'
         '  <rect x="64" y="216" width="384" height="80" rx="12" fill="%s"/>\n' % (c, c)),
        ("el_corners", "四角",
         '  <path d="M48 48 H168 V88 H88 V168 H48 Z" fill="%s"/>\n'
         '  <path d="M464 48 H344 V88 H424 V168 H464 Z" fill="%s"/>\n'
         '  <path d="M48 464 H168 V424 H88 V344 H48 Z" fill="%s"/>\n'
         '  <path d="M464 464 H344 V424 H424 V344 H464 Z" fill="%s"/>\n' % (c, c, c, c)),
        ("el_badge", "徽章",
         '  <circle cx="256" cy="256" r="200" fill="%s"/>\n'
         '  <circle cx="256" cy="256" r="118" fill="none" stroke="%s" stroke-width="18"/>\n' % (c, "{{color.secondary}}")),
        ("el_arc", "弧标",
         '  <path d="M96 360 A160 160 0 1 1 416 360" fill="none" stroke="%s" stroke-width="32"/>\n'
         '  <circle cx="256" cy="248" r="36" fill="%s"/>\n' % (c, c)),
        ("el_triangle", "三角",
         '  <path d="M256 64 L456 432 L56 432 Z" fill="%s"/>\n' % c),
    ]
    out = []
    for i, (iid, name, body) in enumerate(items):
        extra = ""
        if iid == "el_badge":
            g = _grad("g_el_badge", TOKENS[i % len(TOKENS)], TOKENS[(i + 3) % len(TOKENS)])
            extra = g
        svg = _svg(512, 512, extra + body)
        out.append((iid, name, "shape.svg", svg))
    return out


def _frames(rng: random.Random) -> list[tuple[str, str, str, str]]:
    c = "{{color.primary}}"
    items = [
        ("fr_thin", "细线框",
         '  <rect x="24" y="24" width="1032" height="1872" fill="none" stroke="%s" stroke-width="6"/>\n' % c),
        ("fr_double", "双线框",
         '  <rect x="20" y="20" width="1040" height="1880" fill="none" stroke="%s" stroke-width="4"/>\n'
         '  <rect x="40" y="40" width="1000" height="1840" fill="none" stroke="%s" stroke-width="4"/>\n' % (c, c)),
        ("fr_round", "圆角框",
         '  <rect x="28" y="28" width="1024" height="1864" rx="48" fill="none" stroke="%s" stroke-width="10"/>\n' % c),
        ("fr_inset", "内缩框",
         '  <rect x="64" y="64" width="952" height="1792" fill="none" stroke="%s" stroke-width="8"/>\n' % c),
        ("fr_corners", "角框",
         '  <path d="M40 40 H240 V70 H70 V240 H40 Z" fill="%s"/>\n'
         '  <path d="M1040 40 H840 V70 H1010 V240 H1040 Z" fill="%s"/>\n'
         '  <path d="M40 1880 H240 V1850 H70 V1680 H40 Z" fill="%s"/>\n'
         '  <path d="M1040 1880 H840 V1850 H1010 V1680 H1040 Z" fill="%s"/>\n' % (c, c, c, c)),
        ("fr_topbot", "上下条",
         '  <rect x="0" y="0" width="1080" height="48" fill="%s"/>\n'
         '  <rect x="0" y="1872" width="1080" height="48" fill="%s"/>\n' % (c, c)),
        ("fr_sides", "侧边条",
         '  <rect x="0" y="0" width="36" height="1920" fill="%s"/>\n'
         '  <rect x="1044" y="0" width="36" height="1920" fill="%s"/>\n' % (c, c)),
        ("fr_vignette", "圆窗",
         '  <circle cx="540" cy="960" r="420" fill="none" stroke="%s" stroke-width="16"/>\n' % c),
        ("fr_inner_round", "内圆角",
         '  <rect x="80" y="120" width="920" height="1680" rx="80" fill="none" stroke="%s" stroke-width="12"/>\n' % c),
        ("fr_oct", "切角框",
         '  <path d="M120 40 H960 L1040 120 V1800 L960 1880 H120 L40 1800 V120 Z" fill="none" stroke="%s" stroke-width="8"/>\n' % c),
        ("fr_film", "胶片孔",
         "".join(
             '  <rect x="16" y="%d" width="28" height="40" rx="4" fill="%s"/>\n'
             '  <rect x="1036" y="%d" width="28" height="40" rx="4" fill="%s"/>\n'
             % (y, c, y, c)
             for y in range(40, 1880, 80)
         )),
        ("fr_grad", "渐变边",
         _grad("g_fr_grad", "{{color.primary}}", "{{color.secondary}}", x2="1", y2="0")
         + '  <rect x="24" y="24" width="1032" height="1872" fill="none" stroke="url(#g_fr_grad)" stroke-width="14"/>\n'),
    ]
    out = []
    for iid, name, body in items:
        out.append((iid, name, "frame.svg", _svg(1080, 1920, body)))
    return out


def _lower_thirds(rng: random.Random) -> list[tuple[str, str, str, str]]:
    c = "{{color.primary}}"
    s = "{{color.secondary}}"
    items = [
        ("lt_full", "通栏条",
         '  <rect x="0" y="1580" width="1080" height="160" fill="%s"/>\n' % c),
        ("lt_accent", "左强调条",
         '  <rect x="0" y="1580" width="16" height="160" fill="%s"/>\n'
         '  <rect x="16" y="1580" width="1064" height="160" fill="%s"/>\n' % (s, c)),
        ("lt_pill", "底部胶囊",
         '  <rect x="80" y="1620" width="920" height="120" rx="60" fill="%s"/>\n' % c),
        ("lt_split", "双色条",
         '  <rect x="0" y="1600" width="540" height="140" fill="%s"/>\n'
         '  <rect x="540" y="1600" width="540" height="140" fill="%s"/>\n' % (c, s)),
        ("lt_grad", "渐变条",
         _grad("g_lt_grad", c, s, x2="1", y2="0")
         + '  <rect x="0" y="1588" width="1080" height="148" fill="url(#g_lt_grad)"/>\n'),
        ("lt_short", "短条",
         '  <rect x="48" y="1640" width="640" height="100" fill="%s"/>\n' % c),
        ("lt_bullet", "圆点条",
         '  <circle cx="96" cy="1680" r="36" fill="%s"/>\n'
         '  <rect x="160" y="1640" width="840" height="80" fill="%s"/>\n' % (s, c)),
        ("lt_angle", "斜切条",
         '  <path d="M0 1570 L1080 1620 L1080 1780 L0 1730 Z" fill="%s"/>\n' % c),
        ("lt_stack", "双层条",
         '  <rect x="0" y="1540" width="1080" height="72" fill="%s"/>\n'
         '  <rect x="0" y="1612" width="1080" height="96" fill="%s"/>\n' % (s, c)),
        ("lt_rule", "底线",
         '  <rect x="64" y="1748" width="952" height="10" fill="%s"/>\n' % c),
        ("lt_cap", "圆头条",
         '  <rect x="40" y="1600" width="1000" height="128" rx="24" fill="%s"/>\n' % c),
        ("lt_stripe", "侧条字幕",
         '  <rect x="0" y="1480" width="12" height="360" fill="%s"/>\n'
         '  <rect x="12" y="1600" width="900" height="120" fill="%s"/>\n' % (s, c)),
    ]
    out = []
    for iid, name, body in items:
        out.append((iid, name, "bar.svg", _svg(1080, 1920, body)))
    return out


def generate(dest_root: str, seed: int = SEED) -> str:
    rng = random.Random(int(seed))
    pack_dir = os.path.abspath(dest_root)
    items_root = os.path.join(pack_dir, "items")
    os.makedirs(items_root, exist_ok=True)
    pack = {
        "id": "cutora_core",
        "name": "CUTORA 核心",
        "version": "1.0.0",
        "author": "CUTORA",
        "license": "CUTORA-original",
        "categories": ["element", "frame", "lower_third"],
        "tags": ["core", "original"],
        "language": "zh",
    }
    groups = (
        ("element", _elements(rng)),
        ("frame", _frames(rng)),
        ("lower_third", _lower_thirds(rng)),
    )
    for typ, rows in groups:
        for i, (iid, name, filename, svg) in enumerate(rows):
            item_dir = os.path.join(items_root, iid)
            os.makedirs(item_dir, exist_ok=True)
            with open(os.path.join(item_dir, filename), "w", encoding="utf-8", newline="\n") as fh:
                fh.write(svg)
            primary = TOKENS[i % len(TOKENS)]
            secondary = TOKENS[(i + 4) % len(TOKENS)]
            extra = None
            if "{{color.secondary}}" in svg:
                extra = {
                    "color.secondary": {
                        "type": "color", "default": secondary, "label": "辅色",
                    }
                }
            meta = _item_json(iid, typ, name, filename, primary, extra)
            with open(os.path.join(item_dir, "item.json"), "w", encoding="utf-8", newline="\n") as fh:
                json.dump(meta, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
    with open(os.path.join(pack_dir, "pack.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(pack, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return pack_dir


def default_dest() -> str:
    return os.path.join(REPO, "cutora", "assets", "builtin", "cutora_core")


def main(argv=None) -> int:
    dest = default_dest()
    if argv and argv[0]:
        dest = argv[0]
    out = generate(dest)
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

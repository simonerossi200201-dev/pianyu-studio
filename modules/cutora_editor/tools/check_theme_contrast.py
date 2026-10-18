# -*- coding: utf-8 -*-
"""WCAG contrast gate for CUTORA palettes.

    runtime/venv/Scripts/python.exe tools/check_theme_contrast.py
"""
from __future__ import annotations

import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

PALETTE_DIR = os.path.join(REPO, "resources", "theme", "palettes")

from cutora.ui.theme.contrast import (  # noqa: E402
    accent_fg,
    check_palette,
    contrast_ratio,
)


def load_palette(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("palette is not an object: %s" % path)
    return data


def check_all(palette_dir: str | None = None) -> list[str]:
    folder = palette_dir or PALETTE_DIR
    fails: list[str] = []
    names = sorted(n for n in os.listdir(folder) if n.endswith(".json"))
    if not names:
        return ["no palettes in %s" % folder]
    for name in names:
        path = os.path.join(folder, name)
        theme_id = os.path.splitext(name)[0]
        try:
            data = load_palette(path)
        except Exception as exc:
            fails.append("[%s] load: %s" % (theme_id, exc))
            continue
        fails.extend(check_palette(data, theme_id))
    return fails


def main(argv: list[str] | None = None) -> int:
    fails = check_all()
    if fails:
        for line in fails:
            print(line)
        print("FAIL %d" % len(fails))
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

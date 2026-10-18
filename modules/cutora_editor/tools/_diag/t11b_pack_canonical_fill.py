# -*- coding: utf-8 -*-
"""T11b: reverse-fill CANONICAL_STYLE_KEYS from each pack's applied style.

Dry-run by default. --write is refused until T11g signs the canonical set
(do not mutate the 14 builtin packs from this tool yet).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
OUT = os.path.join(REPO, "docs", "plan", "reports", "T11b_pack_canonical_preview.json")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--write", action="store_true",
        help="mutate builtin packs (refused until T11g canonical set is signed)")
    args = parser.parse_args()
    from cutora.subtitle.style_schema import CANONICAL_STYLE_KEYS
    from cutora.subtitle.stylepack import (
        apply_pack_to_task, builtin_dir, default_subtitle_style, list_pack_dirs,
        load_pack,
    )

    if args.write:
        print("REFUSING --write: T11g has not signed the canonical set.")
        print("This tool only dumps a preview of what fill would do.")
        return 2

    defaults = default_subtitle_style()
    canon = list(CANONICAL_STYLE_KEYS)
    rows = []
    for path in list_pack_dirs():
        if not path.replace("\\", "/").startswith(builtin_dir().replace("\\", "/")):
            continue
        pack = load_pack(path)
        task = {"style": {}, "dims": [1080, 1920], "segments": []}
        apply_pack_to_task(task, pack)
        style = task.get("style") or {}
        have = set(style)
        missing = [k for k in canon if k not in have]
        extra = sorted(k for k in have if k not in set(canon) and k not in ("fx", "preset_name", "geom_user_set"))
        proposed = {}
        for k in missing:
            if k in defaults:
                proposed[k] = defaults[k]
            else:
                proposed[k] = None
        rows.append({
            "id": pack.get("id"),
            "path": os.path.relpath(path, REPO).replace("\\", "/"),
            "applied_keys": len(have),
            "canonical": len(canon),
            "missing": missing,
            "extra": extra,
            "proposed_from_default_subtitle_style": proposed,
        })
    report = {
        "note": "Preview only. Values are default_subtitle_style() for missing keys, not reverse-rendered pixels. Do not --write until T11g.",
        "canonical_count": len(canon),
        "packs": rows,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    missing_n = sum(len(r["missing"]) for r in rows)
    print("wrote", OUT, "packs", len(rows), "missing_total", missing_n)
    for r in rows:
        print(" ", r["id"], "missing", len(r["missing"]), "extra", len(r["extra"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

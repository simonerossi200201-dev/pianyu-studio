# -*- coding: utf-8 -*-
"""Build g19_stitch_timeline fixture from T0035 clips. Never references D:\\1_PROJECT in the golden asproj."""
from __future__ import annotations

import json
import os
import shutil
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

SRC_CLIPS = os.path.join(
    r"D:\1_PROJECT\2026-09-15\C", "T0035-实滢-0915-001", "clips")
MEDIA = os.path.join(REPO, "tests", "golden", "_media", "g19_stitch_timeline")
CASE = os.path.join(REPO, "tests", "golden", "g19_stitch_timeline")
STILLS_WORK = os.path.join(REPO, "docs", "plan", "reports", "g19_stills")

# Hash-gate subset of T0035. All 27 clips are copied into _media;
# --all encodes these five (~29 s) so the live golden set does not grow by 3 min.
HASH_CLIPS = [
    ("T0035-s001-p03.mp4", 0.0, 8.0),
    ("T0035-s004-p02.mp4", 0.0, 4.71),
    ("T0035-s014-p04.mp4", 0.0, 4.0),
    ("T0035-s021-p03.mp4", 1.45, 6.0),
    ("T0035-s027-p01.mp4", 0.0, 8.0),
]


def _copy_all_clips() -> list[str]:
    os.makedirs(MEDIA, exist_ok=True)
    names = []
    for fn in sorted(os.listdir(SRC_CLIPS)):
        if not fn.lower().endswith(".mp4"):
            continue
        if not fn.startswith("T0035-s"):
            continue
        src = os.path.join(SRC_CLIPS, fn)
        if not os.path.isfile(src):
            continue
        dst = os.path.join(MEDIA, fn)
        if (not os.path.isfile(dst)
                or os.path.getsize(dst) != os.path.getsize(src)):
            shutil.copy2(src, dst)
        names.append(fn)
    return names


def _write_cutlist(names: list[str]) -> dict:
    by_name = {n: os.path.join(MEDIA, n) for n in names}
    items = []
    hash_names = {n for n, _a, _b in HASH_CLIPS}
    segs_of = {n: [a, b] for n, a, b in HASH_CLIPS}
    for n, a, b in HASH_CLIPS:
        path = by_name[n]
        items.append({
            "path": path,
            "name": n,
            "keep": True,
            "in_t": a,
            "out_t": b,
            "segs": [[a, b]],
            "dur": b,
        })
    # Unused copies stay on disk but out of the hash-gate cutlist.
    cuts = {"T035": items}
    os.makedirs(CASE, exist_ok=True)
    dest = os.path.join(CASE, "cutlist.json")
    with open(dest, "w", encoding="utf-8") as fh:
        json.dump(cuts, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print("wrote", dest, "n=", len(items), "copied=", len(names),
          "unused=", len(names) - len(hash_names))
    return cuts


def _write_project(cuts: dict) -> str:
    from cutora.batch.timeline_stitch import (
        placements_from_cutlist, project_from_placements, write_placements,
    )
    places = placements_from_cutlist(cuts)
    write_placements(CASE, places)
    dest = os.path.join(CASE, "project.asproj.json")
    project_from_placements(places, dest, text="")
    data = json.loads(open(dest, encoding="utf-8").read())
    blob = json.dumps(data)
    if "1_PROJECT" in blob.replace("/", "\\"):
        raise SystemExit("asproj still references D:\\1_PROJECT")
    for p in places:
        if "1_PROJECT" in p.source_path:
            raise SystemExit("placement path escaped into 1_PROJECT")
    print("placements", len(places), "duration",
          places[-1].start_t + places[-1].out_t - places[-1].in_t)
    return dest


def _write_manifest() -> None:
    man = {
        "case": "g19_stitch_timeline",
        "project": "project.asproj.json",
        "gpu": False,
        "workers": 1,
        "mode": "quality",
        "expected": ["expected.mp4"],
        "asr_frozen": True,
        "notes": (
            "T20a timeline stitch. T0035 27 clips copied into "
            "tests/golden/_media/g19_stitch_timeline (gitignored, never "
            "D:\\1_PROJECT). Hash gate is 5 clips / ~29s (start, trimmed tail, "
            "short, non-zero in_t, end) so --all does not grow by 3 min. "
            "fill + hard cut + linked audio. No _stitched mp4."
        ),
    }
    path = os.path.join(CASE, "manifest.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(man, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    readme = os.path.join(CASE, "README.md")
    with open(readme, "w", encoding="utf-8") as fh:
        fh.write("# g19_stitch_timeline\n\n")
        fh.write("T20a: cutlist → timeline (fill, hard cut, linked audio).\n")
        fh.write("Does not write `_stitched/*.mp4`.\n\n")
        fh.write("T0035's 27 clips are copied into `_media/g19_stitch_timeline`.\n")
        fh.write("The protected hash uses 5 of them (~29 s) so `golden --all`\n")
        fh.write("does not grow by three minutes. Luca's product run is the\n")
        fh.write("full 27-clip stitch from `ddc2872`+.\n")
    print("wrote", path)


def main() -> int:
    if not os.path.isdir(SRC_CLIPS):
        print("missing T0035 clips:", SRC_CLIPS)
        return 2
    names = _copy_all_clips()
    if len(names) < 27:
        print("expected 27 T0035 clips, got", len(names), names)
        return 2
    cuts = _write_cutlist(names)
    _write_project(cuts)
    _write_manifest()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Build a 1-hour / 1,500-sentence synthetic project and time push_undo (T13c D8)."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import tracemalloc

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

N_SEGS = 1500
DURATION = 3600.0


def build_project() -> dict:
    segs = []
    words_all = []
    step = DURATION / N_SEGS
    for i in range(N_SEGS):
        start = i * step
        end = start + step * 0.9
        text = "Sentence number %d of the synthetic hour." % (i + 1)
        words = [
            {"word": w, "start": start + j * 0.15, "end": start + (j + 1) * 0.15, "wid": i * 10 + j}
            for j, w in enumerate(text.split())
        ]
        segs.append({"start": start, "end": end, "text": text, "role": "default", "words": words})
        words_all.extend(words)
    return {
        "version": 2,
        "app": "CUTORA",
        "tasks": [{
            "video": os.path.join(REPO, "tests", "golden", "_media", "g01", "video.mp4"),
            "path": os.path.join(REPO, "tests", "golden", "_media", "g01", "video.mp4"),
            "text": " ".join(s["text"] for s in segs),
            "segments": segs,
            "duration": DURATION,
        }],
    }


def time_push(project: dict, repeats: int = 8) -> dict:
    from cutora.core.history import HistoryManager
    mgr = HistoryManager()
    times = []
    tracemalloc.start()
    before = tracemalloc.get_traced_memory()[0]
    for i in range(repeats):
        t0 = time.perf_counter()
        mgr.push("d8-%d" % i, project)
        times.append((time.perf_counter() - t0) * 1000.0)
    after = tracemalloc.get_traced_memory()[0]
    tracemalloc.stop()
    return {
        "repeats": repeats,
        "mean_ms": round(sum(times) / len(times), 3),
        "max_ms": round(max(times), 3),
        "min_ms": round(min(times), 3),
        "mem_delta_mb": round((after - before) / (1024 * 1024), 3),
        "segments": N_SEGS,
        "duration_sec": DURATION,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    project = build_project()
    stats = time_push(project)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(project, fh)
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

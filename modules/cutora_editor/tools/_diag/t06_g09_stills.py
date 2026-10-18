# -*- coding: utf-8 -*-
"""Extract g09 per-segment stills + line metrics. before|after."""
from __future__ import annotations

import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

IMG = os.path.join(REPO, "docs", "plan", "reports", "img")
CASE = os.path.join(REPO, "tests", "golden", "g09_linebreak_latin")
FFMPEG = os.path.join(REPO, "runtime", "ffmpeg", "ffmpeg.exe")


def _segs():
    data = json.loads(open(os.path.join(CASE, "project.asproj.json"), encoding="utf-8").read())
    task = data["tasks"][0]
    style = task.get("style") or {}
    out = []
    for i, seg in enumerate(task.get("segments") or []):
        start = float(seg.get("start") or 0)
        end = float(seg.get("end") or 0)
        mid = (start + end) / 2.0
        frozen = [str(x) for x in (seg.get("lines") or [])]
        live = frozen
        try:
            from cutora.subtitle.layout import LayoutRules, break_lines
            live = break_lines(
                {"text": seg.get("text") or "", "lang": seg.get("lang") or "en"},
                LayoutRules(
                    max_chars_per_line=int((task.get("layout_rules") or {}).get("max_chars_per_line") or 42),
                    max_lines=int((task.get("layout_rules") or {}).get("max_lines") or 2),
                    balance_lines=True,
                    lang=seg.get("lang") or "en",
                    line_break="space",
                ),
            )
        except Exception:
            live = frozen
        words = [ln.split() for ln in live]
        out.append({
            "i": i,
            "start": start,
            "end": end,
            "mid": mid,
            "text": seg.get("text"),
            "lines_frozen": frozen,
            "lines": live,
            "n_lines": len(live),
            "n_words": [len(w) for w in words],
            "fontsize": style.get("fontsize"),
        })
    return out, style, task


def extract(mp4: str, tag: str) -> list[dict]:
    os.makedirs(IMG, exist_ok=True)
    segs, style, _task = _segs()
    rows = []
    for rec in segs:
        png = os.path.join(IMG, "T06_g09_%s_seg%d.png" % (tag, rec["i"]))
        subprocess.check_call([
            FFMPEG, "-y", "-ss", "%.3f" % rec["mid"], "-i", mp4,
            "-frames:v", "1", "-q:v", "2", png,
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        rec = dict(rec)
        rec["png"] = png
        rec["tag"] = tag
        rows.append(rec)
        print("WROTE", png, "t=%.3f lines=%s words=%s fs=%s" % (
            rec["mid"], rec["n_lines"], rec["n_words"], rec["fontsize"]), flush=True)
    return rows


def _export_g09() -> str:
    sys.path.insert(0, os.path.join(REPO, "tools"))
    import golden_check as gc
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    host, _app = gc.boot_host()
    case_dir = CASE
    manifest = gc.load_manifest(case_dir)
    project = os.path.join(case_dir, manifest.get("project", "project.asproj.json"))
    _data, tasks = gc.load_tasks(host, project)
    gc.reset_case_caches()
    import tempfile
    tmp = tempfile.mkdtemp(prefix="g09_after_")
    outs = gc.render_serial(
        host, tasks, tmp, manifest.get("mode") or "quality",
        bool(manifest.get("gpu", False)))
    return outs[0]


def main() -> int:
    tag = (sys.argv[1] if len(sys.argv) > 1 else "before")
    if tag == "after" and len(sys.argv) < 3:
        mp4 = _export_g09()
        print("EXPORTED", mp4, flush=True)
    else:
        mp4 = sys.argv[2] if len(sys.argv) > 2 else os.path.join(CASE, "expected.mp4")
    rows = extract(mp4, tag)
    report = os.path.join(IMG, "T06_g09_%s_metrics.json" % tag)
    with open(report, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, indent=2)
    print("METRICS", report, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

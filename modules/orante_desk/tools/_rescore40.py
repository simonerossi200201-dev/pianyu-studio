# -*- coding: utf-8 -*-
from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.probe40 import score, OUT  # noqa: E402

START = Path(r"D:\1_PROJECT\2026-09-07\B\T0013-李良v04\clips\_tails\s001.jpg")
END = Path(r"D:\1_PROJECT\2026-09-07\B\T0013-李良v04\images\p17.jpg")


def main():
    p = OUT / "b-results.json"
    rows = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else []
    extra = []
    for work in sorted(OUT.glob("b-m*-t*")):
        name = work.name  # b-m5-t1
        parts = name.split("-")
        if len(parts) < 3:
            continue
        method, trial = parts[1][1:], parts[2][1:]
        try:
            method, trial = int(method) if method.isdigit() else method, int(trial)
        except Exception:
            continue
        video = work / "out.mp4"
        if not video.is_file():
            continue
        rec = next((r for r in rows
                    if r.get("method") == method and r.get("trial") == trial), None)
        if rec is None:
            rec = {"method": method, "trial": trial, "reverse": True}
            extra.append(rec)
            rows.append(rec)
        if rec.get("dist_first_start") is not None and not rec.get("error"):
            continue
        a = work / "01_首帧_start.jpg"
        b = work / "02_尾帧_end.jpg"
        rec.update(score(video, a if a.is_file() else START,
                         b if b.is_file() else END, work))
        rec["ok"] = bool(rec.get("start_match") and rec.get("end_match"))
        rec["error"] = ""
        rec["file"] = "out.mp4"
        rec["bytes"] = video.stat().st_size
        print("  %s%s  首↔起 %s  首↔终 %s  尾↔起 %s  尾↔终 %s  双对=%s" % (
            method, trial, rec.get("dist_first_start"), rec.get("dist_first_end"),
            rec.get("dist_last_start"), rec.get("dist_last_end"), rec.get("ok")),
              flush=True)
    dest = OUT / "b-rescore.json"
    dest.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", dest)


if __name__ == "__main__":
    main()

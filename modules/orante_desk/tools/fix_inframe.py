# -*- coding: utf-8 -*-
"""规格 60：把误判成出画的近景尾帧（in_frame=False 且 face_ok=True）改回 True。

默认 --dry-run 只列出；--yes 才写盘。已经带 in_frame_note 的跳过（幂等）。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orante import db  # noqa: E402
from orante.models import Manifest  # noqa: E402
from orante.services import pipeline  # noqa: E402

NOTE = "60号：近景误判出画，face_ok=True 已放行"


def iter_task_dirs() -> list[dict]:
    rows = db.rows("SELECT id, name, dir, status FROM tasks")
    out = []
    for r in rows:
        if (r.get("status") or "") == "trash":
            continue
        d = r.get("dir") or ""
        if not d:
            continue
        p = Path(d)
        if not p.is_dir() or not (p / "manifest.json").is_file():
            continue
        out.append({"id": r.get("id") or "", "name": r.get("name") or "", "dir": p})
    return out


def hits_of(m: Manifest) -> list[dict]:
    """in_frame=False 且 face_ok=True、还没有 note 的接片段。"""
    out = []
    for sg in m.segments or []:
        br = sg.get("bridge") if isinstance(sg.get("bridge"), dict) else {}
        checks = br.get("tail_checks") if isinstance(br.get("tail_checks"), dict) else {}
        if checks.get("in_frame") is not False:
            continue
        if checks.get("face_ok") is not True:
            continue
        if checks.get("in_frame_note"):
            continue
        out.append({
            "s": int(sg.get("s") or 0),
            "from_seg": br.get("from_seg"),
            "face_ok": checks.get("face_ok"),
            "in_frame": checks.get("in_frame"),
            "gap": br.get("gap_score"),
        })
    return out


def apply_hits(m: Manifest, hits: list[dict]) -> int:
    want = {int(h["s"]) for h in hits}
    n = 0
    for sg in m.segments or []:
        try:
            s = int(sg.get("s") or 0)
        except (TypeError, ValueError):
            continue
        if s not in want:
            continue
        br = sg.get("bridge") if isinstance(sg.get("bridge"), dict) else {}
        checks = br.get("tail_checks") if isinstance(br.get("tail_checks"), dict) else {}
        if checks.get("in_frame") is not False:
            continue
        if checks.get("in_frame_note"):
            continue
        checks["in_frame"] = True
        checks["in_frame_note"] = NOTE
        br["tail_checks"] = checks
        sg["bridge"] = br
        n += 1
        src = pipeline.prev_of(m, sg)
        if src:
            pipeline.refresh_bridge_ready(m, src, sg)
    if n:
        pipeline.stamp_waits(m)
    return n


def scan_all() -> list[dict]:
    rows = []
    for t in iter_task_dirs():
        try:
            m = Manifest.load(t["dir"])
        except Exception as e:  # noqa: BLE001
            rows.append({"tid": t["id"], "error": str(e)[:160], "hits": []})
            continue
        hits = hits_of(m)
        if hits:
            rows.append({"tid": t["id"], "name": t["name"], "dir": t["dir"], "hits": hits})
    return rows


def apply_all(rows: list[dict]) -> int:
    n = 0
    for row in rows:
        d = row.get("dir")
        if not d:
            continue
        m = Manifest.load(d)
        n += apply_hits(m, row.get("hits") or [])
        m.save(d)
    return n


def _fmt(row: dict) -> str:
    bits = []
    for h in row.get("hits") or []:
        bits.append("s%03d(from s%03d, gap=%s)" % (
            h["s"], int(h.get("from_seg") or 0),
            h.get("gap") if h.get("gap") is not None else "—"))
    return "  %s  %s  %s" % (row.get("tid") or "?", row.get("name") or "", "、".join(bits))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="把误判的 in_frame=False 改回 True（默认只列出）")
    p.add_argument("--yes", action="store_true", help="真写；默认只列出")
    p.add_argument("--dry-run", action="store_true", help="只列出（默认就是）")
    args = p.parse_args(argv)
    db.init()
    rows = scan_all()
    n = sum(len(r.get("hits") or []) for r in rows)
    if not n:
        print("没有需要改的段。")
        return 0
    print("该改 %d 段：" % n)
    for r in rows:
        if r.get("hits"):
            print(_fmt(r))
        elif r.get("error"):
            print("  %s 读失败：%s" % (r.get("tid"), r.get("error")))
    if not args.yes:
        print("（dry-run，没写盘。加 --yes 才改。）")
        return 0
    written = apply_all(rows)
    print("已改 %d 段，in_frame_note=%s" % (written, NOTE))
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    raise SystemExit(main())

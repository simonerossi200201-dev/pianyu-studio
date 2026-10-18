# -*- coding: utf-8 -*-
"""规格 60：用旧/新提示词重跑 clips/_tails/ 的 in_frame 判断。

默认 --tid T0020。--compare 旧词和新词各问一次；默认只跑新词。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orante import db, tasks as taskmod  # noqa: E402
from orante.models import Manifest  # noqa: E402
from orante.services import tails, ai  # noqa: E402

# 改前那句：把近景（上半身）判成出画。
OLD_ASK = (
    "这是一段讲道视频靠近结尾的一帧。请用 JSON 回答，不要 markdown、不要解释：\n"
    "{\n"
    '  "face_ok": true或false,\n'
    '  "in_frame": true或false,\n'
    '  "pose": {\n'
    '    "head": "头的朝向",\n'
    '    "eyes": "视线落点",\n'
    '    "hands": "双手位置与动作",\n'
    '    "body": "身姿",\n'
    '    "expr": "表情",\n'
    '    "text": "一句话概括"\n'
    "  }\n"
    "}\n\n"
    "face_ok：脸是否完整、没有变形、五官清晰可见。\n"
    "in_frame：人物是否完整在画面里（没被裁掉半个身子或半张脸）。\n"
    "pose 各字段用中文短句。"
)


def _task_dir(tid: str | None, raw_dir: str | None) -> Path:
    if raw_dir:
        p = Path(raw_dir)
        if not p.is_dir():
            raise SystemExit("目录不在：%s" % p)
        return p
    db.init()
    tid = (tid or "T0020").strip()
    t = taskmod.get(tid)
    d = Path(t["dir"]) if t.get("dir") else None
    if not d or not d.is_dir():
        raise SystemExit("任务 %s 的目录不在" % tid)
    return d


def _tails(task_dir: Path) -> list[Path]:
    folder = Path(task_dir) / "clips" / "_tails"
    if not folder.is_dir():
        return []
    files = [p for p in folder.glob("s*.jpg") if p.is_file()]
    files.sort(key=lambda p: p.name)
    return files


def _ask(gemini, prompt: str, img: Path) -> dict:
    raw = gemini.ask_image(prompt, img, json_mode=True)
    d = tails.parse_json_obj(raw) or {}
    return {
        "face_ok": tails._as_bool(d.get("face_ok", True)),
        "in_frame": tails._as_bool(d.get("in_frame", True)),
        "raw": (raw or "").replace("\n", " ")[:180],
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="重跑尾帧 in_frame 判断")
    p.add_argument("--tid", default="T0020", help="任务 id，默认 T0020")
    p.add_argument("--dir", default="", help="直接给任务目录，覆盖 --tid")
    p.add_argument("--limit", type=int, default=12, help="最多几张，默认 12")
    p.add_argument("--compare", action="store_true", help="旧词和新词各问一次")
    p.add_argument("--prefer", default="s007,s009,s023",
                   help="优先这几张，逗号分隔")
    args = p.parse_args(argv)

    d = _task_dir(args.tid, args.dir or None)
    files = _tails(d)
    if not files:
        print("没有尾帧：%s" % (d / "clips" / "_tails"))
        return 1
    prefer = []
    for tok in (args.prefer or "").split(","):
        tok = tok.strip().lower().replace(".jpg", "")
        if not tok:
            continue
        if not tok.startswith("s"):
            tok = "s" + tok
        prefer.append(tok if tok.startswith("s") else tok)
    ordered = []
    seen = set()
    by = {p.stem.lower(): p for p in files}
    for name in prefer:
        hit = by.get(name)
        if hit and hit not in seen:
            ordered.append(hit)
            seen.add(hit)
    for f in files:
        if f not in seen:
            ordered.append(f)
            seen.add(f)
    ordered = ordered[: max(1, int(args.limit))]

    stored = {}
    try:
        m = Manifest.load(d)
        for sg in m.segments or []:
            br = sg.get("bridge") if isinstance(sg.get("bridge"), dict) else {}
            checks = br.get("tail_checks") if isinstance(br.get("tail_checks"), dict) else {}
            rel = (br.get("tail_frame") or "").replace("\\", "/")
            if rel:
                stored[Path(rel).stem.lower()] = checks.get("in_frame")
            # 尾帧文件名是上一段 sNNN
            src = br.get("from_seg")
            if src is not None:
                stored.setdefault("s%03d" % int(src), checks.get("in_frame"))
    except Exception:
        pass

    gemini = ai.client(tag="inframe60")
    print("目录 %s" % d)
    print("共 %d 张，跑 %d 张%s" % (
        len(files), len(ordered), "（旧+新对比）" if args.compare else "（新词）"))
    print("")
    hdr = "%-8s %-8s %-8s %-8s" % ("file", "stored", "old", "new")
    if not args.compare:
        hdr = "%-8s %-8s %-8s" % ("file", "stored", "new")
    print(hdr)
    rows = []
    for img in ordered:
        key = img.stem.lower()
        st = stored.get(key)
        st_s = "—" if st is None else str(bool(st))
        try:
            new = _ask(gemini, tails.TAIL_ASK, img)
        except Exception as e:  # noqa: BLE001
            new = {"in_frame": None, "raw": str(e)[:120]}
        old = None
        if args.compare:
            try:
                old = _ask(gemini, OLD_ASK, img)
            except Exception as e:  # noqa: BLE001
                old = {"in_frame": None, "raw": str(e)[:120]}
        new_s = "—" if new.get("in_frame") is None else str(bool(new.get("in_frame")))
        if args.compare:
            old_s = "—" if old.get("in_frame") is None else str(bool(old.get("in_frame")))
            print("%-8s %-8s %-8s %-8s" % (img.name, st_s, old_s, new_s))
        else:
            print("%-8s %-8s %-8s" % (img.name, st_s, new_s))
        rows.append({
            "file": img.name,
            "stored": st,
            "old": None if old is None else old.get("in_frame"),
            "new": new.get("in_frame"),
        })
    print("")
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    raise SystemExit(main())

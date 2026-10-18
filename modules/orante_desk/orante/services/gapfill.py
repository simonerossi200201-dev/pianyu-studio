# -*- coding: utf-8 -*-
"""查漏补缺：读 Cutora 的「缺失文案清单.txt」，按词序映射回段号。

不引入 Whisper。文案完整性由 Cutora 出清单、Orante 读清单来闭环。
重生复用现有 regenerate 入口。

规格 24：每一条 gap 分 real / maybe / note 三档，和落盘校验交叉印证。
括号里的经文出处是假缺失，照单全收去重生会白烧额度。
"""
from __future__ import annotations

import re
from pathlib import Path

from ..errors import OranteError
from ..models import Manifest

GROUP_RE = re.compile(r"【组\s*(T\d+)】", re.I)
GAP_RE = re.compile(r"缺失\d+\s*\(\s*文案第\s*(\d+)\s*~\s*(\d+)\s*词\s*\)")
CLIPS_RE = re.compile(r"现有片段\s*[:：]\s*(.*)")
TOL = 3  # ASR 词序漂移容差（词）
# 成对括号；未配对的 ) 另外处理
_PAIR_RE = re.compile(r"\([^()]*\)")
# 实义词：剥完括号后，词里至少要有一个字母（含拉丁扩展、汉字）
_LETTER_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ\u4e00-\u9fff]")
EMPTY_COUNTS = {"real": 0, "maybe": 0, "note": 0, "mismatch": 0}


def task_num(tid: str) -> int:
    m = re.search(r"T0*(\d+)", tid or "", re.I)
    return int(m.group(1)) if m else -1


def group_num(label: str) -> int:
    return task_num(label)


def default_report_path(task_dir: Path) -> Path:
    return Path(task_dir) / "clips" / "_stitched" / "缺失文案清单.txt"


def parse_report(text: str) -> list[dict]:
    """按【组 T0xx】切开。每组 {group, clips, gaps:[{from,to,text}]}。"""
    text = text or ""
    hits = list(GROUP_RE.finditer(text))
    if not hits:
        return []
    groups = []
    for i, m in enumerate(hits):
        start = m.end()
        end = hits[i + 1].start() if i + 1 < len(hits) else len(text)
        body = text[start:end]
        clips = []
        cm = CLIPS_RE.search(body)
        if cm:
            clips = [x.strip().rstrip(",") for x in cm.group(1).split(",") if x.strip()]
        gaps = []
        ghits = list(GAP_RE.finditer(body))
        for j, gm in enumerate(ghits):
            gstart = gm.end()
            gend = ghits[j + 1].start() if j + 1 < len(ghits) else len(body)
            excerpt = body[gstart:gend].strip()
            # 下一组标题不该出现在这里（已经按组切过），去掉尾部空行
            excerpt = excerpt.strip()
            gaps.append({
                "from": int(gm.group(1)),
                "to": int(gm.group(2)),
                "text": excerpt,
            })
        groups.append({
            "group": m.group(1).upper(),
            "clips": clips,
            "gaps": gaps,
        })
    return groups


def pick_group(groups: list[dict], tid: str) -> dict | None:
    n = task_num(tid)
    if n < 0:
        return None
    for g in groups:
        if group_num(g.get("group") or "") == n:
            return g
    return None


def word_spans(m: Manifest) -> list[dict]:
    """每段词区间。用 copy.text.split() 建总词序，段区间 = 前面各段词数之和。

    词序号从 1 起、闭区间。
    """
    spans = []
    pos = 1
    for sg in m.segments:
        n = len((sg.get("text") or "").split())
        lo, hi = pos, pos + n - 1 if n else pos - 1
        spans.append({
            "s": int(sg.get("s") or 0),
            "from": lo,
            "to": hi,
            "n": n,
            "text": sg.get("text") or "",
            "image": sg.get("image") or "",
        })
        pos += n
    return spans


def _overlap_n(a1: int, a2: int, b1: int, b2: int) -> int:
    if a2 < a1 or b2 < b1:
        return 0
    lo, hi = max(a1, b1), min(a2, b2)
    return max(0, hi - lo + 1)


def core_hit(a1: int, a2: int, b1: int, b2: int) -> bool:
    """不容差的相交。邻段只在边界叠 1 词的不算（Cutora 词序常有 ±1）。

    短缺口（≤3 词，比如一句经文出处）整段可能只有 3 词，1 词也算。
    """
    n = _overlap_n(a1, a2, b1, b2)
    if n >= 2:
        return True
    gap_len = b2 - b1 + 1
    return n >= 1 and gap_len <= 3


def map_gaps(m: Manifest, gaps: list[dict], tol: int = TOL) -> list[dict]:
    """先按原文词区间相交；这一轮谁都没中，再把缺口两端放宽 tol 词重试。

    容差是给 ASR 漂移用的，不是把边界上的邻段拖进来。
    """
    spans = word_spans(m)
    out = []
    for g in gaps:
        gf, gt = int(g["from"]), int(g["to"])
        segs = [sp["s"] for sp in spans
                if sp["n"] > 0 and core_hit(sp["from"], sp["to"], gf, gt)]
        if not segs:
            segs = [sp["s"] for sp in spans
                    if sp["n"] > 0 and core_hit(sp["from"], sp["to"],
                                                gf - tol, gt + tol)]
        item = dict(g)
        item["from"] = gf
        item["to"] = gt
        item["segs"] = segs
        out.append(item)
    return out


def flatten_rows(m: Manifest, mapped: list[dict]) -> list[dict]:
    """抽屉一行一段：显示区间取「段区间 ∩ 缺口」，文案取段自己的。"""
    spans = {sp["s"]: sp for sp in word_spans(m)}
    rows, seen = [], set()
    for g in mapped:
        gf, gt = int(g["from"]), int(g["to"])
        excerpt = (g.get("text") or "").strip()
        for s in g.get("segs") or []:
            if s in seen:
                continue
            seen.add(s)
            sp = spans.get(int(s))
            if not sp:
                continue
            lo = max(sp["from"], gf)
            hi = min(sp["to"], gt)
            if hi < lo:
                lo, hi = sp["from"], sp["to"]
            # 文案：优先段自己的（s009 那种要从缺口中段起），没有就用缺口摘录
            text = (sp.get("text") or "").strip() or excerpt
            rows.append({
                "s": int(s),
                "from": lo,
                "to": hi,
                "text": text,
                "image": sp.get("image") or "",
            })
    return rows


def strip_comments(text: str) -> str:
    """去掉成对括号里的内容；未配对的 ) 及其左边到行首一并当注释剥掉。"""
    s = text or ""
    while True:
        n = _PAIR_RE.sub("", s)
        if n == s:
            break
        s = n
    lines = s.splitlines()
    if not lines:
        lines = [s]
    out = []
    for line in lines:
        if ")" in line:
            # 规格：未配对的 ) 从行首到该括号都是注释
            line = line[line.rfind(")") + 1:]
        out.append(line)
    return "\n".join(out)


def content_words(text: str) -> list[str]:
    """剥括号后按空白切分，至少含一个字母的才算实义词。

    纯标点 / 数字 / : / - 不算。`(John 8:34-35)` 剥完是 0 个。
    """
    s = strip_comments(text)
    return [w for w in s.split() if _LETTER_RE.search(w)]


def paren_mismatch(text: str) -> bool:
    """括号不配对（像 `(John 8:34-35) 16:13)`）。不改文案，只提示。"""
    s = text or ""
    return s.count("(") != s.count(")")


def base_tier(n_words: int) -> str:
    """只按实义词个数：<3 note，3~5 maybe，≥6 real。"""
    if n_words < 3:
        return "note"
    if n_words >= 6:
        return "real"
    return "maybe"


def _is_bad(verdict: str) -> bool:
    from . import verify
    return (verdict or "").strip().lower() in verify.BAD_VERDICTS


def assemble(m: Manifest, mapped: list[dict],
             verdicts: dict[int, str] | None = None) -> dict:
    """给每条 gap 打 tier，补「文件对不上」行，算出默认勾选。

    交叉印证：
      缺 + wrong_clip/dup → real（行尾注明落盘也判拿错）
      缺 + ok、实义词 3~5 → maybe
      实义词 ≥ 6 → real（片子对也可能漏念）
      实义词 < 3（括号出处）→ note，不因校验升级
      不缺 + wrong_clip/dup → mismatch，默认勾选
    """
    verdicts = {int(k): (v or "").strip().lower()
                for k, v in (verdicts or {}).items()}
    spans = {sp["s"]: sp for sp in word_spans(m)}
    rows: list[dict] = []
    gaps_out: list[dict] = []
    listed: set[int] = set()  # 出现在缺失清单里的段，不进「文件对不上」

    for g in mapped:
        excerpt = (g.get("text") or "").strip()
        excerpt = re.sub(r"^:\s*", "", excerpt).strip()
        words = content_words(excerpt)
        n = len(words)
        base = base_tier(n)
        unbal = paren_mismatch(excerpt)
        segs = [int(x) for x in (g.get("segs") or [])]
        # 短缺口（实义词 < 6）被邻段边界拖进来时，只留重叠最大的一段
        if len(segs) > 1 and n < 6:
            scored = []
            gf, gt = int(g.get("from") or 0), int(g.get("to") or 0)
            for s in segs:
                sp = spans.get(s)
                if not sp:
                    continue
                ov = _overlap_n(int(sp["from"]), int(sp["to"]), gf, gt)
                scored.append((ov, s))
            scored.sort(key=lambda x: -x[0])
            segs = [scored[0][1]] if scored else segs[:1]
        gap_tier = base
        if base != "note":
            for s in segs:
                if _is_bad(verdicts.get(s, "")):
                    gap_tier = "real"
                    break
        item = dict(g)
        item["tier"] = gap_tier
        item["n_words"] = n
        item["paren_mismatch"] = unbal
        gaps_out.append(item)

        # 映不到段也留一行，注释档尤其不能丢
        targets = segs if segs else [0]
        for s in targets:
            if s:
                listed.add(s)
            sp = spans.get(s) or {}
            gf, gt = int(g.get("from") or 0), int(g.get("to") or 0)
            if sp:
                lo = max(int(sp.get("from") or gf), gf)
                hi = min(int(sp.get("to") or gt), gt)
                if hi < lo:
                    lo, hi = int(sp.get("from") or gf), int(sp.get("to") or gt)
            else:
                lo, hi = gf, gt
            v = verdicts.get(s, "") if s else ""
            tier = gap_tier
            if base != "note" and _is_bad(v):
                tier = "real"
            rows.append({
                "s": s,
                "from": lo,
                "to": hi,
                "text": excerpt or (sp.get("text") or "").strip(),
                "image": sp.get("image") or "",
                "tier": tier,
                "n_words": n,
                "verdict": v,
                "verify_wrong": bool(s) and _is_bad(v) and tier == "real",
                "paren_mismatch": unbal and tier == "note",
                "checked": tier == "real",
            })

    for sg in m.segments:
        sn = int(sg.get("s") or 0)
        v = verdicts.get(sn, "")
        if not _is_bad(v) or sn in listed:
            continue
        sp = spans.get(sn) or {}
        rows.append({
            "s": sn,
            "from": int(sp.get("from") or 0),
            "to": int(sp.get("to") or 0),
            "text": (sp.get("text") or sg.get("text") or "").strip(),
            "image": sp.get("image") or sg.get("image") or "",
            "tier": "mismatch",
            "n_words": 0,
            "verdict": v,
            "verify_wrong": True,
            "paren_mismatch": False,
            "checked": True,
        })

    counts = {
        "real": sum(1 for r in rows if r["tier"] == "real"),
        "maybe": sum(1 for r in rows if r["tier"] == "maybe"),
        "note": sum(1 for r in rows if r["tier"] == "note"),
        "mismatch": sum(1 for r in rows if r["tier"] == "mismatch"),
    }
    return {"gaps": gaps_out, "rows": rows, "counts": counts}


def resolve_path(task_dir: Path, m: Manifest, path: str | None) -> Path | None:
    if path:
        p = Path(path)
        return p if p.is_file() else None
    saved = ((m.gapfill or {}).get("report_path") if isinstance(m.gapfill, dict) else None) or ""
    if saved:
        p = Path(saved)
        if p.is_file():
            return p
    p = default_report_path(task_dir)
    return p if p.is_file() else None


def get_gapfill(tid: str, path: str | None = None) -> dict:
    from .. import tasks as taskmod
    t = taskmod.get(tid)
    if not t.get("dir"):
        raise OranteError("这个任务还没准备", "先在任务面板点「准备」。")
    d = Path(t["dir"])
    m = Manifest.load(d)
    report = resolve_path(d, m, path)
    empty = {
        "report_path": str(report) if report else "",
        "group": "",
        "clips_seen": 0,
        "gaps": [],
        "rows": [],
        "counts": dict(EMPTY_COUNTS),
        "found": False,
    }
    if not report:
        return empty
    try:
        text = report.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = report.read_text(encoding="utf-8-sig")
    groups = parse_report(text)
    g = pick_group(groups, m.task_id or tid)
    if not g:
        # 清单里没有本任务的分组：不报错，也不把别组的缺失算过来
        empty["report_path"] = str(report)
        empty["found"] = True
        return empty
    from . import verify
    mapped = map_gaps(m, g.get("gaps") or [])
    payload = assemble(m, mapped, verify.verdicts_of(m))
    return {
        "report_path": str(report),
        "group": g.get("group") or "",
        "clips_seen": len(g.get("clips") or []),
        "found": True,
        **payload,
    }


def set_path(tid: str, path: str) -> dict:
    from .. import tasks as taskmod
    t = taskmod.get(tid)
    if not t.get("dir"):
        raise OranteError("这个任务还没准备", "先在任务面板点「准备」。")
    p = Path(path or "")
    if not p.is_file():
        raise OranteError("找不到这份清单", str(p))
    d = Path(t["dir"])
    m = Manifest.load(d)
    gf = dict(m.gapfill or {})
    gf["report_path"] = str(p)
    m.gapfill = gf
    m.save(d)
    return {"ok": True, "report_path": str(p)}

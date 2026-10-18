# -*- coding: utf-8 -*-
"""配图轨 —— 从 Orante1 bridge/track.py 搬来，算法不动，输入输出改成 manifest v2。

三段式：钉区手选不动；中段洗牌袋抽图（一张连撑 hold ± jitter）；空镜不碰。
同一个 seed 出同一条轨，所以预演和应用一定一致。
"""
from __future__ import annotations

import random

from ..errors import OranteError
from ..models import Manifest

# submitted / collected 已经进生成或收片，配图不许改
LOCKED_STATES = ("submitted", "collected")


def fmt_p(v) -> str | None:
    """把 1 / '1' / 'p1' / 'p01' 收成 'p01'；b1 / b01 收成 'b01'。"""
    if v is None or v == "" or v is False:
        return None
    if isinstance(v, int):
        if v <= 0:
            return None
        return f"p{v:02d}"
    s = str(v).strip()
    if not s:
        return None
    prefix = "p"
    rest = s
    low = s.lower()
    if low[:1] in ("p", "b") and len(s) > 1:
        prefix = low[0]
        rest = s[1:]
    try:
        n = int(rest)
    except (TypeError, ValueError):
        return s
    if n <= 0:
        return None
    return f"{prefix}{n:02d}"


def _dur(sg):
    v = sg.get("duration")
    if v is None:
        v = sg.get("dur_expect")
    try:
        return float(v)
    except (TypeError, ValueError):
        return 8.0


def _seg_id(sg) -> int:
    v = sg.get("s")
    if v is None:
        v = sg.get("idx")
    return int(v)


def _seg_image(sg) -> str | None:
    if sg.get("image"):
        return fmt_p(sg.get("image"))
    if sg.get("p") not in (None, ""):
        return fmt_p(sg.get("p"))
    return None


def _seg_status(sg) -> str:
    return sg.get("status") or sg.get("state") or "planned"


def subject_pool(images):
    """能用来配主体段的图：主体、status=ok、没被拒、文件在（如果带了 exists）。

    pending_review / blocked / 空镜 都不进配图轨。
    """
    out = []
    for im in images:
        p = fmt_p(im.get("p"))
        if not p:
            continue
        if im.get("role") == "空镜":
            continue
        st = im.get("status") or "ok"
        if st != "ok":
            continue
        if im.get("blocked"):
            continue
        if "exists" in im and not im.get("exists"):
            continue
        out.append(p)
    return out


class _Bag:
    """洗牌袋：洗乱后依次取，取空再洗。尽量不与上一张重复。"""

    def __init__(self, pool, rng):
        self.pool = list(pool)
        self.rng = rng
        self.bag = []

    def draw(self, avoid=None):
        if not self.pool:
            return None
        if len(self.pool) == 1:
            return self.pool[0]
        for _ in range(len(self.pool) + 1):
            if not self.bag:
                self.bag = list(self.pool)
                self.rng.shuffle(self.bag)
            cand = self.bag.pop()
            if cand != avoid:
                return cand
            self.bag.insert(0, cand)      # 压回袋底，换一张
        return cand


def plan(segments, images, pins=None, hold=3, jitter=1, unit="seg",
         pools=None, seed=1, pool=None, touch_broll=False, keep=False):
    """算出一条新的配图轨。纯函数，不碰盘。

    segments / images 用 manifest v2 结构（s / image / status / duration；p 为 pNN）。
    pins:   {段号: 图号}，'1':'p01' 或 1:'p01' 都行，优先于一切。
    pools:  [{"from":10,"to":20,"images":["p02","p03"]}]，框选范围只在这几张里抽。
    pool:   全局图池（缩略图条进/出）；空则用全部可用主体图。
    unit:   "seg" 按段数算块长，"sec" 按秒。
    keep:   True = 稳定模式：现在的配图只要还在它该用的池子里就原样保留，
            只有钉住的、池子里没有的、还没配图的段才重算。改钉子 / 限定图池
            只动选中的那几段，别的段不跟着换。False = 整轨重算（「换一批」）。
    """
    rng = random.Random(seed)
    pin_map = {}
    for k, v in (pins or {}).items():
        fp = fmt_p(v)
        if not fp:
            continue
        try:
            pin_map[int(k)] = fp
        except (TypeError, ValueError):
            continue

    by_p = {}
    for im in images:
        p = fmt_p(im.get("p"))
        if p:
            by_p[p] = im

    usable = subject_pool(images)
    raw_pool = pool if pool is not None else usable
    pool = [p for p in (fmt_p(x) for x in (raw_pool or [])) if p and p in usable] or list(usable)

    def _clean(ps):
        got = [x for x in (fmt_p(x) for x in (ps or [])) if x and x in usable]
        return got or None

    zones = []
    for i, r in enumerate(pools or []):
        if not isinstance(r, dict):
            continue
        ps = _clean(r.get("images") or r.get("pool") or [])
        if not ps:
            continue
        zones.append((int(r.get("from") or 1), int(r.get("to") or 1),
                      ps, "z%d" % i))

    def pool_for(s):
        for a, b, ps, key in zones:
            if a <= s <= b:
                return ps, key
        return pool, "*"

    bags = {}

    def bag_for(key, ps):
        if key not in bags:
            bags[key] = _Bag(ps, rng)
        return bags[key]

    rows = []
    prev = None
    cur, left, acc, zone = None, 0, 0.0, None
    for sg in sorted(segments, key=_seg_id):
        s = _seg_id(sg)
        old = _seg_image(sg)
        st = _seg_status(sg)
        locked = None
        if st in LOCKED_STATES:
            locked = "已是 %s，改不了（要改先打回 planned）" % st

        warn = None
        # 段上 role 在锚点模式下是 anchor/bridge；空镜看配图
        img_role = (by_p.get(old) or {}).get("role") or sg.get("img_role") or ""
        role = sg.get("role") or img_role or "主体"
        if locked:
            # 已提交 / 已收片的段改不了，就别给它算新图，
            # 否则图池一变它们就"要改"，把整次应用卡住
            new = old
            why = "已出片，不动"
            cur, left, acc, zone = None, 0, 0.0, None
            prev = old or prev
        elif s in pin_map:
            new = pin_map[s]
            why = "钉住"
            im = by_p.get(new) or {}
            if not im:
                warn = "%s 不在这一组的图里" % new
            elif im.get("role") == "空镜":
                warn = "%s 已转空镜，需重排" % new
            elif im.get("blocked") or im.get("status") == "blocked":
                warn = "%s 被拒过（blocked），钉了多半还是出不来" % new
            elif "exists" in im and not im.get("exists"):
                warn = "%s 的图不在盘上" % new
            cur, left, acc, zone = None, 0, 0.0, None
            prev = new
        elif img_role == "空镜" and not touch_broll:
            new = old
            why = "空镜，不动"
        elif not pool:
            new = old
            why = "没有可用的主体图"
        elif keep and old and old in pool_for(s)[0]:
            # 稳定模式：这一段现在的图还在它该用的池子里 → 不动
            ps, key = pool_for(s)
            new = old
            why = "保持"
            cur, left, acc, zone = None, 0, 0.0, key
            prev = new
        else:
            ps, key = pool_for(s)
            need_new = (left <= 0) if unit == "seg" else (acc <= 0)
            if key != zone:            # 进出框选范围一定换图
                need_new = True
                zone = key
            if need_new:
                cur = bag_for(key, ps).draw(avoid=prev)
                if unit == "seg":
                    left = max(1, hold + rng.randint(-jitter, jitter))
                else:
                    acc = max(2.0, hold + rng.uniform(-jitter, jitter))
            new = cur
            if unit == "seg":
                left -= 1
            else:
                acc -= _dur(sg)
            why = "自动" if key == "*" else "框选池"
            prev = new

        rows.append({
            "s": s, "label": sg.get("label") or ("s%03d" % s),
            "role": role,
            "state": st, "status": st, "dur": _dur(sg),
            "text": (sg.get("text") or "")[:70],
            "p_old": old, "p_new": new, "why": why,
            "locked": locked, "warn": warn,
            "changed": bool(new and old != new and not locked),
        })
    stats = summarize(rows)
    return {"rows": rows, "summary": stats, "stats": stats, "pool": pool,
            "seed": seed,
            "warnings": [r["warn"] for r in rows if r.get("warn")]}


def _switches(vals):
    vals = [v for v in vals if v]
    return sum(1 for a, b in zip(vals, vals[1:]) if a != b)


def summarize(rows):
    before = _switches([r["p_old"] for r in rows])
    after = _switches([r["p_new"] for r in rows])
    dur = sum(r["dur"] for r in rows) or 1.0
    return {
        "n": len(rows),
        "n_changed": sum(1 for r in rows if r["changed"]),
        "n_locked": sum(1 for r in rows if r["locked"]),
        "switch_before": before,
        "switch_after": after,
        "sec_per_image_before": round(dur / (before + 1), 1),
        "sec_per_image_after": round(dur / (after + 1), 1),
        "total_sec": round(dur, 1),
    }


def apply(m: Manifest, rows) -> Manifest:
    """把配图写进 manifest：每段填 image，status 仍 planned，prompt_final 清空。

    submitted / collected 的段不许改；若 rows 里要改它们，抛 OranteError 点名。
    手写锁定（prompt_locked）的段仍换图，但成品提示词不清空。
    返回的 manifest 上挂 _n_prompt_locked，给 arrange 打统计用。
    """
    bad = []
    by_s = {int(r["s"]): r for r in (rows or [])}
    for sg in m.segments:
        r = by_s.get(int(sg["s"]))
        if not r:
            continue
        if sg.get("status") not in LOCKED_STATES:
            continue
        new = r.get("p_new")
        if new and new != (sg.get("image") or ""):
            bad.append("s%03d（%s）" % (sg["s"], sg.get("status")))
        elif r.get("changed"):
            bad.append("s%03d（%s）" % (sg["s"], sg.get("status")))
    if bad:
        raise OranteError(
            "这些段已经提交或已回收，不能改配图：" + "、".join(bad),
            "要改先把它们打回 planned。")

    n_prompt_locked = 0
    for sg in m.segments:
        r = by_s.get(int(sg["s"]))
        if not r:
            continue
        if sg.get("status") in LOCKED_STATES:
            continue
        new = r.get("p_new")
        if new:
            sg["image"] = new
            sg.pop("image_note", None)
            im = m.image(new)
            if im and im.get("role"):
                # 空镜记在 img_role；段上的 anchor/bridge 不动
                sg["img_role"] = im["role"]
        sg["status"] = "planned"
        if sg.get("prompt_locked"):
            n_prompt_locked += 1
        else:
            sg["prompt_final"] = ""
        sg["error"] = None
    m._n_prompt_locked = n_prompt_locked  # type: ignore[attr-defined]
    return m

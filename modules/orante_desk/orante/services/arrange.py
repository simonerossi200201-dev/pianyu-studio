# -*- coding: utf-8 -*-
"""配图轨 job：把 track 写入 manifest、跑 plan、apply。不占账号。

preview 是纯算，给 HTTP 用。
"""
from __future__ import annotations

from pathlib import Path

from .. import config, events, tasks as taskmod
from ..errors import OranteError
from ..jobs import register
from ..models import Manifest
from . import track as trackmod


def _cfg(key, default=None):
    try:
        return config.get(key, default)
    except Exception:
        from ..config import DEFAULTS
        return DEFAULTS.get(key, default)


def _task_dir(tid: str) -> Path:
    t = taskmod.get(tid)
    if not t.get("dir"):
        raise OranteError("这个任务还没准备", "先在任务面板点「准备」。")
    d = Path(t["dir"])
    if not d.is_dir():
        raise OranteError("任务文件夹不在了", str(d))
    return d


def _annotate_images(m: Manifest, task_dir: Path) -> list[dict]:
    """给 plan 用：补 exists / blocked / mtime（毫秒），不改原 dict。"""
    from . import images as imgmod
    out = imgmod.annotate_images(task_dir, m.images)
    for x in out:
        x["blocked"] = x.get("status") == "blocked"
    return out


def _norm_track(track, m: Manifest | None = None) -> dict:
    src = dict((m.track if m else None) or {})
    if track:
        src.update(track)
    pins = {}
    for k, v in (src.get("pins") or {}).items():
        fp = trackmod.fmt_p(v)
        if not fp:
            continue
        try:
            pins[str(int(k))] = fp
        except (TypeError, ValueError):
            continue
    pools = []
    for r in src.get("pools") or src.get("ranges") or []:
        if not isinstance(r, dict):
            continue
        imgs = [trackmod.fmt_p(x) for x in (r.get("images") or r.get("pool") or [])]
        imgs = [x for x in imgs if x]
        if not imgs:
            continue
        try:
            a = int(r.get("from") or 1)
            b = int(r.get("to") or 1)
        except (TypeError, ValueError):
            continue
        pools.append({"from": a, "to": b, "images": imgs})
    try:
        hold = int(src.get("hold"))
    except (TypeError, ValueError):
        hold = int(_cfg("track.hold", 3) or 3)
    try:
        jitter = int(src.get("jitter"))
    except (TypeError, ValueError):
        jitter = int(_cfg("track.jitter", 1) or 1)
    unit = src.get("unit") or "seg"
    if unit not in ("seg", "sec"):
        unit = "seg"
    seed = src.get("seed")
    try:
        seed = int(seed) if seed is not None and seed != "" else None
    except (TypeError, ValueError):
        seed = None
    if "no_adjacent_repeat" in src and src.get("no_adjacent_repeat") is not None:
        nar = bool(src.get("no_adjacent_repeat"))
    else:
        nar = bool(_cfg("track.no_adjacent_repeat", True))
    out = {
        "pins": pins,
        "pools": pools,
        "hold": max(1, hold),
        "jitter": max(0, jitter),
        "unit": unit,
        "seed": seed,
        "no_adjacent_repeat": nar,
    }
    if src.get("pool"):
        out["pool"] = [p for p in (trackmod.fmt_p(x) for x in src["pool"]) if p]
    if src.get("keep"):
        out["keep"] = True
    return out


def save_track(task_id: str, track: dict | None = None) -> dict:
    """只把钉住 / 限定图池 / 参数存进 manifest，不算轨也不改配图。

    面板上一改就存，这样切走再回来、重启软件，钉子和限定都还在。
    """
    taskmod.ensure_writable(task_id)
    d = _task_dir(task_id)
    m = Manifest.load(d)
    tr = _norm_track(track, m)
    tr.pop("keep", None)          # keep 是这一次的算法模式，不存
    m.track = tr
    m.save(d)
    return tr


def _lab(s) -> str:
    try:
        return "s%03d" % int(s)
    except (TypeError, ValueError):
        return str(s)


def _pin_set(tr: dict) -> set[int]:
    out = set()
    for k in (tr.get("pins") or {}):
        try:
            out.add(int(k))
        except (TypeError, ValueError):
            continue
    return out


def _row_fixed(row: dict, pins: set[int]) -> bool:
    """钉住 / 已出片 / 空镜：间隔排列绝对不动。"""
    if not row:
        return True
    if row.get("locked"):
        return True
    why = row.get("why") or ""
    if why in ("钉住", "空镜，不动", "已出片，不动"):
        return True
    try:
        s = int(row.get("s"))
    except (TypeError, ValueError):
        return True
    if s in pins:
        return True
    st = row.get("status") or row.get("state") or ""
    if st in trackmod.LOCKED_STATES:
        return True
    if (row.get("role") or "") == "空镜" and why != "自动":
        return True
    return False


def _pool_for(s: int, tr: dict, usable: list[str]) -> list[str]:
    """这段能用的图：限定图池优先，否则全局图池。"""
    usable_set = set(usable)
    for r in tr.get("pools") or []:
        if not isinstance(r, dict):
            continue
        try:
            a = int(r.get("from") or 1)
            b = int(r.get("to") or 1)
        except (TypeError, ValueError):
            continue
        if a <= s <= b:
            imgs = [trackmod.fmt_p(x) for x in (r.get("images") or r.get("pool") or [])]
            got = [p for p in imgs if p and p in usable_set]
            return got or list(usable)
    raw = tr.get("pool")
    if raw:
        got = [trackmod.fmt_p(x) for x in raw]
        got = [p for p in got if p and p in usable_set]
        return got or list(usable)
    return list(usable)


def _group_pool(rows: list[dict], a: int, b: int, tr: dict, usable: list[str]) -> list[str]:
    acc = None
    for i in range(a, b):
        try:
            s = int(rows[i]["s"])
        except (TypeError, ValueError, KeyError):
            continue
        ps = set(_pool_for(s, tr, usable))
        acc = ps if acc is None else (acc & ps)
    if acc:
        # 保持图池原顺序
        order = _pool_for(int(rows[a]["s"]), tr, usable)
        return [p for p in order if p in acc]
    return _pool_for(int(rows[a]["s"]), tr, usable)


def _last_used(rows: list[dict], p: str, before: int) -> int:
    """p 在 before 之前最后一次出现的下标；没出现过返回 -1（优先挑）。"""
    last = -1
    for i in range(before):
        if (rows[i].get("p_new") or "") == p:
            last = i
    return last


def _pick_farthest(cands: list[str], rows: list[dict], before: int) -> str | None:
    """候选里挑距离上一次被用最远的那张（没出现过的最优先）。"""
    if not cands:
        return None
    best, best_lu = None, 10 ** 9
    for p in cands:
        lu = _last_used(rows, p, before)
        if lu < best_lu:
            best, best_lu = p, lu
    return best


def _img_class(p: str, by_p: dict) -> str:
    im = by_p.get(p) or {}
    cov = im.get("cov") if isinstance(im, dict) else None
    if not isinstance(cov, dict):
        return ""
    return str(cov.get("class") or "").strip()


def _iter_groups(rows: list[dict], tr: dict, pins: set[int]):
    """把 rows 切成组。hold≥2 时连续同一张是一组；过长的跑（合并组）按 hold 切开。

    hold=1 或按秒：每段单独一组，这样「间隔」就是字面的相邻两段。
    """
    n = len(rows)
    hold = int(tr.get("hold") or 1)
    jitter = int(tr.get("jitter") or 0)
    unit = tr.get("unit") or "seg"
    i = 0
    while i < n:
        if hold < 2 or unit != "seg" or _row_fixed(rows[i], pins):
            yield (i, i + 1)
            i += 1
            continue
        p = rows[i].get("p_new") or ""
        j = i + 1
        while j < n and (rows[j].get("p_new") or "") == p and not _row_fixed(rows[j], pins):
            j += 1
        run = j - i
        max_ok = hold + max(0, jitter)
        if run <= max_ok:
            yield (i, j)
            i = j
            continue
        # 同一张连太长 = 两组撞车合成一段，按 hold 切开，只动组界
        k = i
        while k < j:
            yield (k, min(k + hold, j))
            k += hold
        i = j


def _recolor_group(rows, a, b, chosen, pins) -> int:
    """把组里没钉住的段换成 chosen。返回改了几段。"""
    n = 0
    for i in range(a, b):
        row = rows[i]
        if _row_fixed(row, pins):
            continue
        if (row.get("p_new") or "") == chosen:
            continue
        row["p_new"] = chosen
        old = row.get("p_old")
        row["changed"] = bool(chosen and old != chosen and not row.get("locked"))
        if row.get("why") in ("自动", "框选池", "保持", ""):
            row["why"] = "间隔"
        n += 1
    return n


def enforce_spacing(res: dict, tr: dict, images: list[dict]) -> dict:
    """plan 之后、apply 之前：同一张图不要用在相邻两段（hold 组内除外）。

    钉住 / 已出片 / 空镜不动。hold≥2 时只处理组界（第 N 段和第 N+1 段），
    不许把组内拆开。
    """
    rows = list(res.get("rows") or [])
    if len(rows) < 2:
        sm = dict(res.get("summary") or {})
        sm["spacing_changed"] = 0
        sm["spacing_blocked"] = 0
        sm["spacing_note"] = "间隔排列：改了 0 段，0 处改不动"
        res["summary"] = sm
        res["stats"] = sm
        return res

    pins = _pin_set(tr)
    usable = trackmod.subject_pool(images)
    groups = list(_iter_groups(rows, tr, pins))
    extra_warn = []
    n_chg = 0
    n_block = 0
    by_p = {str(im.get("p") or ""): im for im in (images or []) if isinstance(im, dict)}

    def _img(gi):
        a, _b = groups[gi]
        return rows[a].get("p_new") or ""

    def _try_recolor(gi, avoid_prev, avoid_next, avoid_class: str | None = None) -> bool:
        nonlocal n_chg
        a, b = groups[gi]
        if all(_row_fixed(rows[i], pins) for i in range(a, b)):
            return False
        pool = _group_pool(rows, a, b, tr, usable)
        cands = [p for p in pool if p and p != avoid_prev and p != avoid_next]
        if avoid_class:
            pref = [p for p in cands if _img_class(p, by_p) != avoid_class]
            if pref:
                cands = pref
            else:
                return False
        if not cands:
            return False
        chosen = _pick_farthest(cands, rows, a)
        if not chosen:
            return False
        n_chg += _recolor_group(rows, a, b, chosen, pins)
        return True

    for gi in range(1, len(groups)):
        a0, b0 = groups[gi - 1]
        a1, b1 = groups[gi]
        p0 = rows[b0 - 1].get("p_new") or ""
        p1 = rows[a1].get("p_new") or ""
        if not p0 or not p1 or p0 != p1:
            continue
        # 和前一组撞了
        nxt = _img(gi + 1) if gi + 1 < len(groups) else ""
        prv = (rows[a0 - 1].get("p_new") or "") if a0 > 0 else ""
        g1_fixed = all(_row_fixed(rows[i], pins) for i in range(a1, b1))
        g0_fixed = all(_row_fixed(rows[i], pins) for i in range(a0, b0))
        ok = False
        if not g1_fixed:
            # 优先换后面那一组（规格：撞了就换 rows[i]）
            ok = _try_recolor(gi, p0, nxt)
        if not ok and not g0_fixed:
            # 后面钉了 / 换不成 → 改前面没钉的
            ok = _try_recolor(gi - 1, prv, rows[a1].get("p_new") or p1)
        if not ok:
            n_block += 1
            s0 = rows[b0 - 1].get("s")
            s1 = rows[a1].get("s")
            if g0_fixed and g1_fixed:
                extra_warn.append("%s 和 %s 都钉了 %s，间隔排列改不动" % (_lab(s0), _lab(s1), p0))
            else:
                extra_warn.append("%s 和 %s 图池不够换，间隔排列改不动" % (_lab(s0), _lab(s1)))

    # 尽量不要相邻两组同一个 class（改不动就跳过，不报错）
    for gi in range(1, len(groups)):
        a0, b0 = groups[gi - 1]
        a1, b1 = groups[gi]
        p0 = rows[b0 - 1].get("p_new") or ""
        p1 = rows[a1].get("p_new") or ""
        if not p0 or not p1 or p0 == p1:
            continue
        c0, c1 = _img_class(p0, by_p), _img_class(p1, by_p)
        if not c0 or not c1 or c0 != c1:
            continue
        nxt = _img(gi + 1) if gi + 1 < len(groups) else ""
        prv = (rows[a0 - 1].get("p_new") or "") if a0 > 0 else ""
        g1_fixed = all(_row_fixed(rows[i], pins) for i in range(a1, b1))
        g0_fixed = all(_row_fixed(rows[i], pins) for i in range(a0, b0))
        if not g1_fixed:
            if _try_recolor(gi, p0, nxt, avoid_class=c0):
                continue
        if not g0_fixed:
            _try_recolor(gi - 1, prv, rows[a1].get("p_new") or p1, avoid_class=c1)

    sm = trackmod.summarize(rows)
    sm["spacing_changed"] = n_chg
    sm["spacing_blocked"] = n_block
    sm["spacing_note"] = "间隔排列：改了 %d 段，%d 处改不动" % (n_chg, n_block)
    res["rows"] = rows
    res["summary"] = sm
    res["stats"] = sm
    old_w = [w for w in (res.get("warnings") or []) if w]
    res["warnings"] = old_w + extra_warn
    return res


def _run_plan(m: Manifest, task_dir: Path, tr: dict) -> dict:
    images = _annotate_images(m, task_dir)
    seed = tr.get("seed")
    if seed is None:
        import random
        seed = random.randint(1, 99999)
        tr["seed"] = seed
    res = trackmod.plan(
        m.segments, images,
        pins=tr.get("pins"),
        hold=tr.get("hold", 3),
        jitter=tr.get("jitter", 1),
        unit=tr.get("unit") or "seg",
        pools=tr.get("pools"),
        seed=seed,
        pool=tr.get("pool"),
        keep=bool(tr.get("keep")),
    )
    # 间隔排列：plan 之后、apply 之前扫一遍相邻重复
    if tr.get("no_adjacent_repeat", True):
        enforce_spacing(res, tr, images)
    return res


def _seg_clip(task_dir: Path, m, sg: dict) -> dict:
    """已收片的段带上成片路径，镜头序列用首帧；文件不在就空。不改原 dict。"""
    from .review import clip_path
    p = clip_path(task_dir, m, sg)
    if not p or not p.is_file():
        return {}
    try:
        rel = str(p.relative_to(task_dir)).replace("\\", "/")
        mtime = int(p.stat().st_mtime * 1000)
    except (ValueError, OSError):
        return {}
    video = p.suffix.lower() in {".mp4", ".webm", ".mov", ".mkv"}
    return {"clip_file": rel, "clip_mtime": mtime, "clip_video": video}


def get_track(task_id: str) -> dict:
    """GET /api/tasks/{tid}/track → {images, segments, track}。"""
    from . import broll, expand as expandmod
    d, m, sync_log = broll.load_synced(task_id)
    images = _annotate_images(m, d)
    tr = _norm_track(m.track, m)
    usable = trackmod.subject_pool(images)
    pool = [p for p in (tr.get("pool") or usable) if p in usable] or list(usable)
    n_editable = sum(
        1 for sg in m.segments
        if sg.get("status") not in trackmod.LOCKED_STATES)
    hits = broll.segments_using_broll(m)
    from . import duration_edit as _duredit
    segs = []
    for sg in m.segments:
        item = _duredit.enrich_seg(d, m, sg)
        item.update(_seg_clip(d, m, sg))
        segs.append(item)
    from . import pipeline
    return {
        "images": images,
        "segments": segs,
        "track": tr,
        "pool": pool,
        "n_editable": n_editable,
        "sync_log": sync_log,
        "mode": pipeline.mode_of(m),
        "broll_hits": [{"s": sg.get("s"), "image": sg.get("image")} for sg in hits],
        # 扩图会用哪个账号（Flow 任务用 Flow 号、Grok 任务用 Grok 号），给按钮显示用
        "expand_account": expandmod.account_hint(task_id),
    }


def preview(task_id: str, track: dict | None = None) -> dict:
    """纯算一条轨，不写盘。"""
    d = _task_dir(task_id)
    m = Manifest.load(d)
    if not m.segments:
        raise OranteError("这个任务还没有段", "先拆段、写描述语。")
    tr = _norm_track(track, m)
    res = _run_plan(m, d, tr)
    return {
        "rows": res["rows"],
        "summary": res["summary"],
        "warnings": res["warnings"],
        "seed": res["seed"],
        "pool": res["pool"],
    }


@register("arrange")
def arrange(job, track=None):
    """把 track 存进 manifest，plan + apply，任务标 arranged。不占账号。"""
    if not job.task_id:
        raise OranteError("arrange 需要 task_id")
    from . import broll
    d, m, sync_log = broll.load_synced(job.task_id)
    for ln in sync_log:
        job.log(ln)
    if not m.segments:
        raise OranteError("这个任务还没有段", "先拆段、写描述语。")
    tr = _norm_track(track, m)
    # 钉住已转空镜的图：去掉，让 plan 另抽
    for k, v in list((tr.get("pins") or {}).items()):
        im = m.image(v)
        if im and im.get("role") == "空镜":
            job.log(f"钉 s{k}={v} 已转空镜，去掉这个钉")
            del tr["pins"][k]
    if tr.get("seed") is None:
        import random
        tr["seed"] = random.randint(1, 99999)
    tr_saved = {k: v for k, v in tr.items() if k != "keep"}
    m.track = tr_saved
    m.save(d)
    job.log("hold=%s jitter=%s unit=%s seed=%s 钉 %d 限定 %d" % (
        tr["hold"], tr["jitter"], tr["unit"], tr["seed"],
        len(tr.get("pins") or {}), len(tr.get("pools") or {})))
    res = _run_plan(m, d, tr)
    for w in res.get("warnings") or []:
        job.log("⚠ " + w)
    if tr.get("no_adjacent_repeat", True):
        sm0 = res.get("summary") or {}
        job.log("间隔排列：改了 %d 段，%d 处改不动" % (
            int(sm0.get("spacing_changed") or 0),
            int(sm0.get("spacing_blocked") or 0)))
    trackmod.apply(m, res["rows"])
    n_kept = int(getattr(m, "_n_prompt_locked", 0) or 0)
    from . import pipeline
    if pipeline.is_anchor_mode(m):
        changed = [int(r["s"]) for r in (res.get("rows") or [])
                   if r.get("changed") and r.get("s") is not None]
        for s in changed:
            try:
                pipeline.on_anchor_image_changed(d, m, s, log=job.log)
            except Exception as e:  # noqa: BLE001
                job.log("  · s%03d 换图后重算跨度失败：%s" % (s, str(e)[:120]))
    m.save(d)
    taskmod.set_status(job.task_id, "arranged")
    events.emit("tasks.changed")
    sm = dict(res["summary"] or {})
    sm["n_prompt_locked"] = n_kept
    job.log("✓ 配图 %d 段，换图 %s → %s 次，改 %d 段" % (
        sm["n"], sm["switch_before"], sm["switch_after"], sm["n_changed"]))
    if n_kept:
        job.log(f"  · {n_kept} 段因为锁定没动")
    return {"n": sm["n"], "summary": sm, "seed": res["seed"], "n_prompt_locked": n_kept}

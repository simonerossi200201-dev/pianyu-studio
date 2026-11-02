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
    from . import broll, expand as expandmod, prompt_presets
    d, m, sync_log = broll.load_synced(task_id)
    t = taskmod.get(task_id) or {}
    presets_info = prompt_presets.list_presets()
    presets_by_id = {p["id"]: p for p in presets_info.get("presets", [])}
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
        # 二选一规范：若该段绑定了 preset_id，则 custom_prompt 视为未单独覆盖，动态取最新预设预览
        if item.get("preset_id"):
            item["custom_prompt"] = ""
        sg_for_prev = dict(sg)
        if sg_for_prev.get("preset_id"):
            sg_for_prev["custom_prompt"] = ""
        pname, preview = _resolve_seg_prompt_preview(m, sg_for_prev, t, presets_by_id)
        item["preset_name"] = pname
        item["prompt_preview"] = preview
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


def auto_arrange_manifest(m: Manifest, task_dir: Path, track: dict | None = None) -> dict:
    """对一个任务的 Manifest 直接执行自动配图编排（不占后台 Job 队列）。
    对短视频（单张图 p01）直接全段填入 p01；对多图任务按标准间隔排布规则配图。
    """
    if not m.segments:
        return {"rows": [], "summary": {"n": 0, "n_changed": 0}}
    tr = _norm_track(track, m)
    for k, v in list((tr.get("pins") or {}).items()):
        im = m.image(v)
        if im and im.get("role") == "空镜":
            del tr["pins"][k]
    if tr.get("seed") is None:
        import random
        tr["seed"] = random.randint(1, 99999)
    tr_saved = {k: v for k, v in tr.items() if k != "keep"}
    m.track = tr_saved
    res = _run_plan(m, task_dir, tr)
    trackmod.apply(m, res["rows"])
    # 兜底：若存在单张主体图（比如 p01 曾被标 blocked 或图池仅 1 张），确保所有未配图段都挂上第一张主体图
    subj = [im.get("p") for im in (m.images or []) if isinstance(im, dict) and im.get("role") != "空镜" and im.get("p")]
    fallback_p = subj[0] if subj else ((m.images[0].get("p") if m.images else "") or "p01")
    if fallback_p:
        for sg in m.segments:
            if not (sg.get("image") or "").strip():
                sg["image"] = fallback_p
    return res


def batch_arrange_all(
    task_ids: list[str] | None = None,
    *,
    only_unarranged: bool = False,
    start_generate: bool = False,
) -> dict:
    """一键批量对所有任务（或指定任务）应用/跳过编排，并支持可选直接排队开始生成。"""
    from . import broll, split as splitmod
    from .. import jobs as jobmod

    if task_ids:
        target_tasks = []
        for tid in task_ids:
            try:
                target_tasks.append(taskmod.get(str(tid).strip()))
            except Exception:
                continue
    else:
        target_tasks = taskmod.list_all(include_finished=False)

    arranged_ids: list[str] = []
    skipped: list[dict] = []
    started_jobs: list[dict] = []

    for t in target_tasks:
        tid = t.get("id") or ""
        if not tid or t.get("archived"):
            continue
        st = t.get("status") or "draft"
        if st in ("draft", "trash", "finished"):
            skipped.append({"id": tid, "reason": st})
            continue
        d_str = t.get("dir") or ""
        if not d_str or not Path(d_str).is_dir():
            skipped.append({"id": tid, "reason": "no_dir"})
            continue
        d = Path(d_str)
        try:
            _, m, _ = broll.load_synced(tid)
        except Exception as e:
            skipped.append({"id": tid, "reason": str(e)})
            continue
        if not m.segments:
            skipped.append({"id": tid, "reason": "no_segments"})
            continue

        # 如果任务表里的引擎和 manifest 当前拆段引擎不一致且尚未收片，先按当前引擎规则自动重拆段
        t_eng = str(t.get("engine") or "").strip().lower()
        m_eng = str(((m.split or {}).get("rule") or {}).get("engine") or m.engine or "").strip().lower()
        has_collected = any(sg.get("status") == "collected" for sg in (m.segments or []))
        if t_eng in ("flow", "grok", "studio") and t_eng != m_eng and not has_collected:
            try:
                splitmod.resplit_task_for_engine(tid, t_eng, new_account_id=t.get("account_id"))
                m = Manifest.load(d)
            except Exception:
                pass

        missing_img = any(not (sg.get("image") or "").strip() for sg in (m.segments or []))
        if only_unarranged and st not in ("prepared", "described") and not missing_img:
            skipped.append({"id": tid, "reason": "already_arranged"})
            continue

        auto_arrange_manifest(m, d)
        m.save(d)
        if st in ("prepared", "described"):
            taskmod.set_status(tid, "arranged")
        c = taskmod.remember_counts(d, m)
        taskmod.emit_seg_changed(tid, c, force=True)
        arranged_ids.append(tid)

    events.emit("tasks.changed")

    if start_generate:
        # 对所有已编排且待出片的任务排队启动 generate
        queue_tids = list(arranged_ids)
        for t in target_tasks:
            tid = t.get("id") or ""
            if tid and tid not in queue_tids and (t.get("status") in ("arranged",)):
                queue_tids.append(tid)
        for tid in queue_tids:
            try:
                j = jobmod.start("generate", tid)
                started_jobs.append({"task_id": tid, "job_id": getattr(j, "id", "")})
            except Exception as e:
                skipped.append({"id": tid, "reason": f"start_generate: {e}"})

    return {
        "arranged": arranged_ids,
        "count": len(arranged_ids),
        "started_jobs": started_jobs,
        "started_count": len(started_jobs),
        "skipped": skipped,
    }


def _load(task_id: str) -> tuple[dict, Manifest, Path]:
    t = taskmod.get(task_id)
    d = _task_dir(task_id)
    m = Manifest.load(d)
    return t, m, d


def set_seg_flow_mode(
    task_id: str,
    s: int | None = None,
    flow_mode: str = "auto",
    items: list[dict] | None = None,
) -> dict:
    """设置单镜头或批量镜头的 Flow 生成模式（'auto' | 'frames' | 'ingredients'）。"""
    from ..engines.flow_api import normalize_flow_mode

    def _norm(m: str | None) -> str:
        raw = (m or "").strip().lower()
        if raw in ("", "auto", "跟随任务", "跟随账号"):
            return "auto"
        return normalize_flow_mode(raw, default="frames")

    t, m, d = _load(task_id)
    pairs: list[tuple[int, str]] = []
    if items and isinstance(items, list):
        for it in items:
            if isinstance(it, dict) and it.get("s") is not None:
                pairs.append((int(it["s"]), _norm(it.get("flow_mode"))))
    elif s is not None:
        pairs.append((int(s), _norm(flow_mode)))
    else:
        mode_val = _norm(flow_mode)
        pairs = [(int(sg["s"]), mode_val) for sg in (m.segments or [])]

    updated = 0
    for seg_s, mode_val in pairs:
        seg = m.seg(seg_s)
        if not seg:
            continue
        seg["flow_mode"] = mode_val
        updated += 1
    m.save(d)
    events.emit("arrange.changed", task_id=task_id)
    return {"ok": True, "task_id": task_id, "updated": updated, "segments": m.segments}


def _resolve_seg_prompt_preview(m: Manifest, seg: dict, task: dict, presets_by_id: dict[str, dict]) -> tuple[str, str]:
    """返回 `(preset_name, prompt_preview)`。"""
    from . import prompt_presets, prompts as pr_mod
    pid = str(seg.get("preset_id") or task.get("preset_id") or "").strip()
    pr = presets_by_id.get(pid) if pid else None
    pname = (pr or {}).get("name") or ""
    try:
        preview = pr_mod.build_final_prompt(m, seg, task=task)
    except Exception:
        custom = str(seg.get("custom_prompt") or "").strip()
        if custom:
            preview = prompt_presets.insert_speech_at_max_newlines(custom, seg.get("text") or "")
        elif pr and pr.get("template"):
            preview = prompt_presets.insert_speech_at_max_newlines(pr["template"], seg.get("text") or "")
        else:
            preview = str(seg.get("prompt_final") or seg.get("text") or "")
    return pname, preview


def get_all_tasks_segments_table(
    task_ids: list[str] | None = None,
    include_finished: bool = False,
    player_mode: bool = False,
) -> dict:
    """获取跨所有任务的镜头与提示词总表（支持任务折叠/展开与勾选、多列提示词、模式切换）。"""
    from . import broll, prompt_presets

    presets_info = prompt_presets.list_presets()
    presets_by_id = {p["id"]: p for p in presets_info.get("presets", [])}

    if task_ids:
        raw_tasks = []
        for tid in task_ids:
            try:
                raw_tasks.append(taskmod.get(str(tid).strip()))
            except Exception:
                pass
    else:
        raw_tasks = taskmod.list_all(include_finished=include_finished, player_mode=player_mode)

    tasks_out: list[dict] = []
    flat_rows: list[dict] = []

    for t in raw_tasks:
        tid = t.get("id") or ""
        if not tid or t.get("status") == "trash":
            continue
        d_str = t.get("dir") or ""
        m: Manifest | None = None
        if d_str and Path(d_str).is_dir():
            try:
                _, m, _ = broll.load_synced(tid)
            except Exception:
                try:
                    m = Manifest.load(Path(d_str))
                except Exception:
                    m = None

        images_out: list[dict] = []
        img_map: dict[str, dict] = {}
        if m and m.images:
            for im in m.images:
                p_code = im.get("p") or ""
                fname = im.get("file") or ""
                url = f"/api/tasks/{tid}/file/{fname}" if fname else ""
                item = {
                    "p": p_code,
                    "file": fname,
                    "url": url,
                    "desc": im.get("desc") or "",
                    "role": im.get("role") or "主体",
                }
                images_out.append(item)
                if p_code:
                    img_map[p_code] = item

        segs_out: list[dict] = []
        if m and m.segments:
            for sg in m.segments:
                s_num = int(sg.get("s") or 0)
                p_code = sg.get("image") or ""
                im_info = img_map.get(p_code, {})
                sg_for_prev = dict(sg)
                if sg_for_prev.get("preset_id"):
                    sg_for_prev["custom_prompt"] = ""
                pname, preview = _resolve_seg_prompt_preview(m, sg_for_prev, t, presets_by_id)
                extra_cols = sg.get("extra_prompts") if isinstance(sg.get("extra_prompts"), list) else []
                row = {
                    "task_id": tid,
                    "task_name": t.get("name") or tid,
                    "s": s_num,
                    "checked": bool(sg.get("checked", False)),
                    "image": p_code,
                    "image_file": im_info.get("file") or "",
                    "image_url": im_info.get("url") or "",
                    "image_desc": im_info.get("desc") or "",
                    "text": sg.get("text") or "",
                    "duration": int(sg.get("duration") or 6),
                    "role": sg.get("role") or "主体",
                    "shot": sg.get("shot") or "",
                    "flow_mode": sg.get("flow_mode") or "auto",
                    "task_flow_mode": t.get("flow_mode") or "auto",
                    "preset_id": sg.get("preset_id") or "",
                    "preset_name": pname,
                    "custom_prompt": "" if sg.get("preset_id") else (sg.get("custom_prompt") or ""),
                    "extra_prompts": extra_cols,
                    "prompt_preview": preview,
                    "status": sg.get("status") or "planned",
                }
                segs_out.append(row)
                flat_rows.append(row)

        tasks_out.append({
            "id": tid,
            "name": t.get("name") or tid,
            "engine": t.get("engine") or "flow",
            "account_id": t.get("account_id") or "",
            "account_name": t.get("account_name") or "",
            "flow_mode": t.get("flow_mode") or "auto",
            "preset_id": t.get("preset_id") or "",
            "status": t.get("status") or "draft",
            "copy": t.get("copy") or "",
            "images": images_out,
            "segments": segs_out,
            "seg_count": len(segs_out),
        })

    return {
        "tasks": tasks_out,
        "rows": flat_rows,
        "total_tasks": len(tasks_out),
        "total_segments": len(flat_rows),
        "presets": presets_info.get("presets", []),
        "preset_categories": presets_info.get("categories", []),
    }


def batch_update_segments(
    updates: list[dict] | None = None,
    items: list[dict] | None = None,
    task_updates: list[dict] | None = None,
) -> dict:
    """批量保存跨任务镜头表的修改（勾选状态、配图、文案、时长、生成模式、预设、自定义提示词、附加列）。"""
    from . import prompt_presets, prompts as pr_mod
    from ..engines.flow_api import normalize_flow_mode

    merged_updates = list(updates or []) + list(items or []) + list(task_updates or [])
    by_task: dict[str, list[dict]] = {}
    for u in merged_updates:
        if not isinstance(u, dict):
            continue
        tid = str(u.get("task_id") or "").strip()
        if tid:
            by_task.setdefault(tid, []).append(u)

    updated_segments = 0
    updated_tasks = 0

    for tid, items in by_task.items():
        try:
            t, m, d = _load(tid)
        except Exception:
            continue
        changed = False
        for it in items:
            if it.get("s") is None:
                # 任务级字段更新
                if "flow_mode" in it:
                    taskmod.set_flow_mode(tid, str(it["flow_mode"]))
                if "preset_id" in it:
                    taskmod.set_preset_id(tid, str(it["preset_id"]))
                continue
            seg = m.seg(int(it["s"]))
            if not seg:
                continue
            if "checked" in it:
                seg["checked"] = bool(it["checked"])
                changed = True
            if "image" in it and it["image"] is not None:
                seg["image"] = str(it["image"]).strip()
                changed = True
            if "text" in it and it["text"] is not None:
                seg["text"] = str(it["text"])
                changed = True
            if "duration" in it and it["duration"] is not None:
                seg["duration"] = int(it["duration"])
                changed = True
            if "flow_mode" in it and it["flow_mode"] is not None:
                raw_m = str(it["flow_mode"]).strip().lower()
                seg["flow_mode"] = "auto" if raw_m in ("", "auto") else normalize_flow_mode(raw_m, default="frames")
                changed = True
            if "preset_id" in it and it["preset_id"] is not None:
                pid = str(it["preset_id"]).strip()
                seg["preset_id"] = pid
                if "custom_prompt" not in it:
                    seg["custom_prompt"] = ""
                changed = True
            if "custom_prompt" in it and it["custom_prompt"] is not None:
                cp = str(it["custom_prompt"])
                seg["custom_prompt"] = cp
                if cp.strip() and not str(it.get("preset_id") or "").strip():
                    seg["preset_id"] = ""
                changed = True
            if "extra_prompts" in it and isinstance(it["extra_prompts"], list):
                seg["extra_prompts"] = [str(x) for x in it["extra_prompts"]]
                changed = True
            try:
                seg["prompt_final"] = pr_mod.build_final_prompt(m, seg, task=t)
            except Exception:
                pass
            updated_segments += 1

        if changed:
            m.save(d)
            updated_tasks += 1
            events.emit("arrange.changed", task_id=tid)

    return {
        "ok": True,
        "updated_tasks": updated_tasks,
        "updated_segments": updated_segments,
    }


def batch_apply_preset(
    targets: list[dict],
    mode: str = "single",
    preset_id: str = "",
    category: str = "",
    extra_preset_ids: list[str] | None = None,
    ai_describer=None,
    gemini_template_id: str = "",
    flow_mode: str | None = None,
) -> dict:
    """对勾选的镜头或任务批量套用提示词预设：
    - mode == 'single'：所有勾选镜头统一套用 `preset_id`
    - mode == 'random_group'：从 `category` 分组中为每个勾选镜头随机抽取一条预设套用
    - mode == 'gemini_template'：使用 Gemini 写词模板看图生成描述词，按 `(task_id, image_p)` 去重，
      同一任务内共用同一张图的多个镜头只调用一次 AI！
    """
    import random
    from . import prompt_presets, prompts as pr_mod
    from ..engines.flow_api import normalize_flow_mode

    if not preset_id and gemini_template_id:
        preset_id = gemini_template_id
    mode_clean = (mode or "single").strip().lower()
    by_task: dict[str, list[int | None]] = {}
    for tgt in (targets or []):
        if not isinstance(tgt, dict):
            continue
        tid = str(tgt.get("task_id") or "").strip()
        if not tid:
            continue
        s_val = int(tgt["s"]) if tgt.get("s") is not None else None
        by_task.setdefault(tid, []).append(s_val)

    # 准备预设池
    single_pr = prompt_presets.get_preset(preset_id) if preset_id else None
    group_pool: list[dict] = []
    if mode_clean == "random_group":
        listed = prompt_presets.list_presets(category=category if category else None, role="main")
        group_pool = listed.get("presets") or []
        if not group_pool:
            group_pool = prompt_presets.list_presets(role="main").get("presets") or []
        if not group_pool:
            raise OranteError(f"分组「{category or '默认'}」中没有可用的提示词预设")
    elif mode_clean == "single":
        if not single_pr:
            raise OranteError(f"找不到指定的提示词预设：{preset_id}")

    applied_segments = 0
    ai_calls = 0
    seen_task_images: dict[tuple[str, str], str] = {}

    for tid, s_list in by_task.items():
        try:
            t, m, d = _load(tid)
        except Exception:
            continue

        if None in s_list:
            target_segs = list(m.segments or [])
            if mode_clean == "single" and single_pr:
                taskmod.set_preset_id(tid, single_pr["id"])
        else:
            want_s = set(s_list)
            target_segs = [sg for sg in (m.segments or []) if int(sg.get("s") or 0) in want_s]

        for sg in target_segs:
            if extra_preset_ids is not None:
                sg["extra_prompts"] = [str(x) for x in extra_preset_ids if str(x).strip()]
            if flow_mode:
                raw_fm = str(flow_mode).strip().lower()
                sg["flow_mode"] = "auto" if raw_fm in ("", "auto") else normalize_flow_mode(raw_fm, default="frames")

            if mode_clean == "single" and single_pr:
                sg["preset_id"] = single_pr["id"]
                sg["custom_prompt"] = ""
                sg["prompt_final"] = pr_mod.build_final_prompt(m, sg, task=t)
                sg["final_prompt"] = sg["prompt_final"]
                applied_segments += 1

            elif mode_clean == "random_group" and group_pool:
                chosen = random.choice(group_pool)
                sg["preset_id"] = chosen["id"]
                sg["custom_prompt"] = ""
                sg["prompt_final"] = pr_mod.build_final_prompt(m, sg, task=t)
                sg["final_prompt"] = sg["prompt_final"]
                applied_segments += 1

            elif mode_clean == "gemini_template":
                img_p = (sg.get("image") or "").strip() or "p01"
                dedup_key = (tid, img_p)
                if dedup_key not in seen_task_images:
                    tpl_text = (single_pr or {}).get("template") or ""
                    if callable(ai_describer):
                        gen_desc = str(ai_describer(tid, img_p, tpl_text))
                    else:
                        im_obj = m.image(img_p) or {}
                        img_rel = im_obj.get("file") or ""
                        img_path = (d / img_rel) if img_rel else None
                        gen_desc = ""
                        if img_path and img_path.is_file():
                            try:
                                from . import gemini as gem_mod
                                g = gem_mod.Gemini()
                                raw_resp = g.describe_image(img_path, tpl_text or "Generate video prompt")
                                if isinstance(raw_resp, str) and raw_resp.strip().startswith("{"):
                                    import json as _json
                                    parsed = _json.loads(raw_resp)
                                    gen_desc = str(parsed.get("prompt") or parsed.get("desc") or raw_resp)
                                else:
                                    gen_desc = str(raw_resp or "")
                            except Exception:
                                gen_desc = ""
                        if not gen_desc:
                            existing_desc = (im_obj.get("desc") or "").strip()
                            if existing_desc:
                                gen_desc = existing_desc
                            elif tpl_text:
                                gen_desc = tpl_text
                            else:
                                gen_desc = f"[闭眼][常规] 人物特写与自然动作 ({img_p})"
                    seen_task_images[dedup_key] = gen_desc
                    ai_calls += 1
                    im_obj = m.image(img_p)
                    if im_obj is not None:
                        im_obj["desc"] = gen_desc

                gen_tpl = seen_task_images[dedup_key]
                if "\n" in gen_tpl:
                    sg["custom_prompt"] = prompt_presets.insert_speech_at_max_newlines(
                        gen_tpl, sg.get("text") or ""
                    )
                else:
                    sg["custom_prompt"] = ""
                if single_pr:
                    sg["preset_id"] = single_pr["id"]
                sg["prompt_final"] = pr_mod.build_final_prompt(m, sg, task=t)
                sg["final_prompt"] = sg["prompt_final"]
                applied_segments += 1

        m.save(d)
        events.emit("arrange.changed", task_id=tid)

    return {
        "ok": True,
        "mode": mode_clean,
        "applied_segments": applied_segments,
        "updated_segments": applied_segments,
        "unique_image_ai_calls": ai_calls,
        "gemini_calls": ai_calls,
    }

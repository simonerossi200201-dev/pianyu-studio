# -*- coding: utf-8 -*-
"""规格 84：编排/任务改时长 + 画幅灰字。"""
from __future__ import annotations

from pathlib import Path

from .. import db, events, tasks as taskmod
from ..engines import caps
from ..errors import OranteError
from ..models import Manifest
from . import broll, pipeline, split as splitmod
from .track import LOCKED_STATES

COLLECTED = "collected"


def duration_options(engine: str) -> list[int]:
    eng = (engine or "").strip().lower()
    if eng not in caps.DURATIONS:
        raise OranteError(f"未知引擎 {engine!r}", "引擎只能是 flow、grok 或 studio（本地模型）。")
    return list(caps.DURATIONS[eng])


def auto_duration(sg: dict, engine: str, task: dict | None = None) -> int:
    eng = (engine or "").strip().lower()
    text = (sg.get("text") or "").strip()
    if not text:
        return int(splitmod.no_copy_duration(task or {}, eng))
    # 有文案但用户在 ① 任务表手选了时长档 → 自动 = 那一档
    try:
        pref = splitmod.copy_duration_pref(task or {}, eng)
    except Exception:
        pref = None
    if pref:
        return int(pref)
    rule = splitmod._rule_for(eng)
    if eng == "flow":
        return int(splitmod._duration_flow(text, rule))
    if eng == "studio":
        return int(splitmod._duration_studio(text, rule))    # 6/10/15 里够念完的最短一档（学 Grok）
    return int(splitmod._duration_grok(text, rule))


def duration_warn(sg: dict, engine: str) -> str:
    """念不完 / 档位偏长 → 黄字提醒；不拦。"""
    eng = (engine or "").strip().lower()
    text = (sg.get("text") or "").strip()
    if not text:
        return ""
    try:
        dur = int(sg.get("duration") or 0)
    except (TypeError, ValueError):
        return ""
    if dur not in caps.DURATIONS.get(eng, ()):
        return ""
    rule = splitmod._rule_for(eng)
    if eng == "studio":
        # 本地模型：Flow 的语速（20 词 ≈ 8 秒）。手改成不在档位上的秒数（比如 12）也按语速折算
        words = int(splitmod.flow_split.word_count(text))
        cap8 = int(rule.get("dur_8s_max_words") or 20)

        def lim(d):
            k = f"dur_{d}s_max_words"
            return int(rule[k]) if k in rule else int(round(cap8 * d / 8))
        if words > lim(dur):
            return f"这段 {words} 词，{dur} 秒念不完"
        shorter = [d for d in (rule.get("auto_tiers") or caps.STUDIO_AUTO_TIERS) if d < dur]   # 跟「自动」同一套档
        if shorter and words <= lim(max(shorter)):
            return f"这段只有 {words} 词，{dur} 秒会有长停顿"
        return ""
    if eng == "flow":
        words = int(splitmod.flow_split.word_count(text))
        lim = {
            4: int(rule.get("dur_4s_max_words") or 8),
            6: int(rule.get("dur_6s_max_words") or 13),
            8: int(rule.get("dur_8s_max_words") or 20),
        }
        mx = lim.get(dur)
        if mx is not None and words > mx:
            return f"这段 {words} 词，{dur} 秒念不完"
        shorter = [d for d in caps.DURATIONS["flow"] if d < dur]
        if shorter:
            prev = max(shorter)
            if words <= lim[prev]:
                return f"这段只有 {words} 词，{dur} 秒会有长停顿"
        return ""
    n = len(text)
    lim = {
        6: int(rule.get("dur_6s_max_chars") or 112),
        10: int(rule.get("max_chars_10s") or 187),
        15: int(rule.get("max_chars_15s") or 250),
    }
    mx = lim.get(dur)
    if mx is not None and n > mx:
        return f"这段 {n} 字，{dur} 秒念不完"
    shorter = [d for d in caps.DURATIONS["grok"] if d < dur]
    if shorter:
        prev = max(shorter)
        if n <= lim[prev]:
            return f"这段只有 {n} 字，{dur} 秒会有长停顿"
    return ""


def aspect_label_for_path(path, engine: str = "flow") -> str:
    a = caps.aspect_for_image(path, engine)
    if a == "16:9":
        return "横 16:9"
    if a == "9:16":
        return "竖 9:16"
    return str(a or "")


def aspect_label_for_seg(task_dir: Path, m: Manifest, sg: dict) -> str:
    eng = (getattr(m, "engine", None) or "flow")
    p = (sg.get("image") or "").strip()
    if not p:
        return ""
    im = next((x for x in (m.images or []) if (x.get("p") or "") == p), None)
    if not im:
        return ""
    rel = (im.get("file") or "").replace("\\", "/").lstrip("/")
    if not rel or ".." in rel.split("/"):
        return ""
    path = Path(task_dir) / rel
    if not path.is_file():
        return ""
    try:
        return aspect_label_for_path(path, eng)
    except Exception:
        return ""


def enrich_seg(task_dir: Path, m: Manifest, sg: dict) -> dict:
    eng = (getattr(m, "engine", None) or "flow")
    item = dict(sg)
    try:
        opts = duration_options(eng)
    except OranteError:
        opts = []
    item["aspect_label"] = aspect_label_for_seg(task_dir, m, sg)
    item["duration_warn"] = duration_warn(sg, eng)
    item["duration_options"] = opts
    return item


def enrich_segments(task_dir: Path, m: Manifest) -> list[dict]:
    return [enrich_seg(task_dir, m, sg) for sg in (m.segments or [])]


def preserve_manual_durations(old_segs: list, new_segs: list) -> list:
    by_s = {}
    for sg in old_segs or []:
        if isinstance(sg, dict) and sg.get("duration_manual"):
            by_s[int(sg.get("s") or 0)] = sg
    if not by_s:
        return new_segs
    out = []
    for i, sg in enumerate(new_segs or []):
        item = dict(sg)
        s = int(item.get("s") or (i + 1))
        old = by_s.get(s)
        if old is None and i < len(old_segs or []):
            cand = old_segs[i]
            if isinstance(cand, dict) and cand.get("duration_manual"):
                old = cand
        if old:
            try:
                item["duration"] = int(old.get("duration"))
            except (TypeError, ValueError):
                pass
            item["duration_manual"] = True
            if old.get("duration_stale"):
                item["duration_stale"] = True
        out.append(item)
    return out


def _load(tid: str):
    taskmod.ensure_writable(tid)
    d, m, _log = broll.load_synced(tid)
    return d, m


def _save(tid: str, d: Path, m: Manifest) -> None:
    pipeline.stamp_waits(m)
    m.save(d)
    taskmod.remember_counts(d, m)
    events.emit("tasks.changed")


def _seg(m: Manifest, s) -> dict:
    sg = m.seg(int(s)) if hasattr(m, "seg") else None
    if not sg:
        sg = next((x for x in (m.segments or []) if int(x.get("s") or 0) == int(s)), None)
    if not sg:
        raise OranteError(f"没有 s{int(s):03d}")
    return sg


def set_seg_durations(tid: str, pairs: list, *, skip_collected: bool = False) -> dict:
    """pairs: [{s, duration}, ...]。写 duration_manual=True。已收片只打 duration_stale，不改 status。"""
    t = taskmod.get(tid)
    eng = (t.get("engine") or "flow")
    allowed = set(duration_options(eng))
    d, m = _load(tid)
    changed, skipped, warns = [], [], []
    for pair in pairs or []:
        s = int(pair.get("s"))
        try:
            dur = int(pair.get("duration"))
        except (TypeError, ValueError):
            raise OranteError("时长必须是数字", f"引擎 {eng} 只能是 {sorted(allowed)}。")
        if dur not in allowed:
            raise OranteError(
                f"{eng} 不能出 {dur} 秒",
                f"只能选 {', '.join(str(x) for x in sorted(allowed))}。")
        sg = _seg(m, s)
        st = sg.get("status") or "planned"
        if skip_collected and st == COLLECTED:
            skipped.append(s)
            continue
        sg["duration"] = dur
        sg["duration_manual"] = True
        if st == COLLECTED:
            sg["duration_stale"] = True
        else:
            sg.pop("duration_stale", None)
        changed.append(s)
        w = duration_warn(sg, eng)
        if w:
            warns.append({"s": s, "warn": w})
    if changed:
        _save(tid, d, m)
    return {
        "changed": changed,
        "skipped_collected": skipped,
        "warns": warns,
        "engine": eng,
        "options": list(sorted(allowed)),
        "segments": enrich_segments(d, m),
    }


def restore_seg_durations(tid: str, s_list: list) -> dict:
    t = taskmod.get(tid)
    eng = (t.get("engine") or "flow")
    d, m = _load(tid)
    changed = []
    for s in s_list or []:
        if s is None:
            continue
        sg = _seg(m, int(s))
        sg["duration"] = int(auto_duration(sg, eng, t))
        sg.pop("duration_manual", None)
        sg.pop("duration_stale", None)
        changed.append(int(s))
    if changed:
        _save(tid, d, m)
    return {"changed": changed, "segments": enrich_segments(d, m)}


def set_tasks_duration_pref(ids: list | None, duration_by_engine: dict) -> dict:
    """批量设任务 duration_pref。ids 空/None = 全部。

    0923：有文案的任务也能设。设了就按这一档的字/词上限拆段，每段都是这个秒数；
    清空（前端那一栏留空）＝回到按语速自动。
    """
    rows = taskmod.list_all()
    if ids:
        idset = set(str(x) for x in ids)
        rows = [r for r in rows if str(r.get("id")) in idset]
    with_copy = [r for r in rows if (r.get("copy") or "").strip()]
    no_copy = [r for r in rows if not (r.get("copy") or "").strip()]
    updated = []
    for r in rows:
        eng = (r.get("engine") or "").strip().lower()
        if eng not in duration_by_engine:
            continue
        dur = int(duration_by_engine[eng])
        if dur not in caps.DURATIONS.get(eng, ()):
            raise OranteError(
                f"{eng} 不能出 {dur} 秒",
                f"只能选 {', '.join(str(x) for x in caps.DURATIONS[eng])}。")
        tid = r["id"]
        taskmod.ensure_writable(tid)
        db.run(
            "UPDATE tasks SET duration_pref=?, updated=? WHERE id=?",
            (str(dur), db.now(), tid))
        updated.append(tid)
    events.emit("tasks.changed")
    return {
        "updated": updated,
        "skipped_with_copy": len(with_copy),
        "with_copy_ids": [r["id"] for r in with_copy],
        "no_copy_n": len(no_copy),
    }

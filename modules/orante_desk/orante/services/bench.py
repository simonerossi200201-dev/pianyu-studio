# -*- coding: utf-8 -*-
"""尾帧检查台：红/黄项给人看，不是关卡。

绿的自动放行，链条自己跑。人只在跨度偏大、脸没拍到时被叫来。
in_frame 是黄旗（近景常被误判成出画），不挡生成。
五个动作：自动换锚点、挑锚点、重生上一段、改提示词、手挑尾帧；
黄色可直接通过，红色必须先做其中一个才能通过。
"""
from __future__ import annotations

from pathlib import Path

from .. import events, tasks as taskmod
from ..errors import OranteError
from ..models import Manifest
from . import pipeline, tails, review as reviewmod, broll

# 手挑尾帧：倒数 1~15
TAIL_PICK_N = 15
LEVEL_RANK = {"red": 0, "yellow": 1, "green": 2}


def _lab(s) -> str:
    try:
        return "s%03d" % int(s)
    except (TypeError, ValueError):
        return str(s)


def _mtime(path: Path | None) -> int:
    from . import images as imgmod
    return imgmod.file_mtime_ms(path)


def _media(tid: str, rel: str, ver: int = 0) -> str:
    rel = (rel or "").replace("\\", "/").lstrip("/")
    url = "/media/%s/%s" % (tid, rel)
    if ver:
        url += "?v=%d" % ver
    return url


def _pose_text(pose) -> str:
    return pipeline._pose_text(pose)


def block_why_of(sg: dict) -> str:
    """检查台小字：这段为什么被挡。别再笼统一句「对不上」。"""
    if int(sg.get("mismatch_count") or 0) >= pipeline.MISMATCH_STOP:
        return "两次首帧都对不上"
    err = str(sg.get("error") or "")
    if int(sg.get("thumb_count") or 0) >= pipeline.THUMB_STOP \
            or "缩略图" in err or "下载一直不合格" in err:
        return "下载一直不合格"
    if int(sg.get("timeout_count") or 0) >= pipeline.TIMEOUT_STOP \
            or "等了太久没出片 ×3" in err:
        return "超时 3 次"
    if "下载" in err:
        return "下载一直不合格"
    return ""


def can_retry_of(sg: dict) -> bool:
    """failed 或被两次/超时/缩略图闸挡住 → 给「重试接片」。"""
    if sg.get("role") != "bridge":
        return False
    if sg.get("status") == "failed":
        return True
    if int(sg.get("mismatch_count") or 0) >= pipeline.MISMATCH_STOP:
        return True
    if int(sg.get("timeout_count") or 0) >= pipeline.TIMEOUT_STOP:
        return True
    if int(sg.get("thumb_count") or 0) >= pipeline.THUMB_STOP:
        return True
    return False


def _br(sg: dict) -> dict:
    b = sg.get("bridge") if isinstance(sg.get("bridge"), dict) else {}
    return b


def bench_level(bridge_sg: dict, m=None) -> str:
    """red / yellow / green。已通过的当绿。

    advise（Flow 接片）下跨度 ≥4 是黄不是红：已经放行，拍完复查。
    in_frame=False 也是黄（近景常被误判）。红只留给拍不成（没尾帧 / 没拍到脸）
    和 53 号三个计数器超限。
    """
    br = _br(bridge_sg)
    if br.get("passed"):
        return "green"
    if int(bridge_sg.get("mismatch_count") or 0) >= pipeline.MISMATCH_STOP:
        return "red"
    if int(bridge_sg.get("timeout_count") or 0) >= pipeline.TIMEOUT_STOP:
        return "red"
    if int(bridge_sg.get("thumb_count") or 0) >= pipeline.THUMB_STOP:
        return "red"
    checks = br.get("tail_checks") if isinstance(br.get("tail_checks"), dict) else {}
    if checks.get("face_ok") is False:
        return "red"
    if checks.get("sharp") is False:
        return "red"
    g = pipeline.gap_of(bridge_sg)
    advise = pipeline.gate_mode_of(m, bridge_sg) == "advise"
    if g is None:
        return "red" if not br.get("tail_frame") else "yellow"
    if g >= pipeline.GAP_SWAP and not advise:
        return "red"
    yellow = False
    if checks.get("in_frame") is False:
        yellow = True
    if g == pipeline.GAP_ENQUEUE or (advise and g >= pipeline.GAP_SWAP):
        yellow = True
    if br.get("tail_warn"):
        yellow = True
    first_d = br.get("mismatch_first_dist")
    if first_d is None:
        tks = bridge_sg.get("takes") or []
        if tks:
            first_d = tks[-1].get("frame_dist")
            if tks[-1].get("frame_rescued"):
                first_d = None          # 结构复核过了（均值哈希被色调骗了），不标红
    try:
        first_d = int(first_d) if first_d is not None else None
    except (TypeError, ValueError):
        first_d = None
    if first_d is not None and first_d > 16:
        be = str(bridge_sg.get("bridge_engine") or "").lower()
        # 距离格 >16 标红（规格 C）。Flow 双图 4b 红线是 30，卡片级别不因 17~30 变红。
        if be != "flow":
            return "red"
    if yellow:
        return "yellow"
    return "green"


def can_pass(bridge_sg: dict, m=None) -> bool:
    """黄色随时能过；红色必须先做过一个修正动作（含改提示词 / 手挑尾帧）。"""
    lv = bench_level(bridge_sg, m)
    if lv == "green":
        return True
    if lv == "yellow":
        return True
    br = _br(bridge_sg)
    return bool(br.get("bench_acted") or bridge_sg.get("prompt_locked"))


def mark_acted(sg: dict) -> None:
    br = _br(sg)
    br["bench_acted"] = True
    sg["bridge"] = br


def _clear_pass(sg: dict) -> None:
    """跨度重算后旧的放行作废，要重新看。"""
    br = _br(sg)
    br.pop("passed", None)
    sg["bridge"] = br


def counts_of(m: Manifest) -> dict:
    """给折叠行横幅用：红/黄条数。非锚点 / 非接龙模式全 0。"""
    out = {"bench_red": 0, "bench_yellow": 0, "bench_green": 0}
    if pipeline.is_chain_mode(m):
        for sg in m.segments or []:
            lv = _chain_level(sg)
            if lv == "red":
                out["bench_red"] += 1
            elif lv == "yellow":
                out["bench_yellow"] += 1
            else:
                out["bench_green"] += 1
        return out
    if not pipeline.is_anchor_mode(m):
        return out
    for sg in m.segments or []:
        if sg.get("role") != "bridge":
            continue
        lv = bench_level(sg, m)
        if lv == "red":
            out["bench_red"] += 1
        elif lv == "yellow":
            out["bench_yellow"] += 1
        else:
            out["bench_green"] += 1
    return out


def _item(tid: str, task_dir: Path, m: Manifest, sg: dict) -> dict:
    br = _br(sg)
    prev = pipeline.prev_of(m, sg)
    nxt = pipeline.next_of(m, sg)
    from_s = int(br.get("from_seg") or (prev or {}).get("s") or 0) or None
    to_p = (nxt or {}).get("image") or br.get("to_image") or ""
    dest_im = m.image(to_p) if to_p else None
    tail_rel = (br.get("tail_frame") or "").replace("\\", "/")
    tail_path = (task_dir / tail_rel) if tail_rel else None
    dest_rel = ""
    dest_path = None
    if dest_im:
        dest_rel = (dest_im.get("file") or "").replace("\\", "/")
        dest_path = (task_dir / dest_rel) if dest_rel else None
        if dest_path is not None and not dest_path.is_file():
            dest_path = None
    lv = bench_level(sg, m)
    g = pipeline.gap_of(sg)
    checks = br.get("tail_checks") if isinstance(br.get("tail_checks"), dict) else {}
    why = (br.get("gap_why") or "").strip()
    if checks.get("face_ok") is False:
        why = why or "尾帧没拍到脸"
    if checks.get("in_frame") is False:
        why = why or "尾帧可能构图偏紧，拍完复查"
    if checks.get("sharp") is False:
        why = why or "尾帧偏糊"
    if br.get("tail_warn"):
        why = why or str(br.get("tail_warn"))
    bw = block_why_of(sg)
    if bw:
        why = bw
    advise = pipeline.gate_mode_of(m, sg) == "advise"
    if (advise and g is not None and g >= pipeline.GAP_SWAP
            and lv == "yellow" and not bw
            and checks.get("in_frame") is not False):
        why = "跨度 %s（已放行，拍完复查）" % g
    if (checks.get("in_frame") is False and lv == "yellow" and not bw
            and checks.get("face_ok") is not False):
        why = "尾帧可能构图偏紧，拍完复查"
    if not tail_rel:
        why = why or "还没有尾帧"
    first_rel = (br.get("first_frame") or "").replace("\\", "/")
    first_path = (task_dir / first_rel) if first_rel else None
    if first_path is not None and not first_path.is_file():
        first_path = None
        first_rel = ""
    first_dist = br.get("mismatch_first_dist")
    if first_dist is None:
        tks = sg.get("takes") or []
        if tks:
            first_dist = tks[-1].get("frame_dist")
    try:
        first_dist = int(first_dist) if first_dist is not None else None
    except (TypeError, ValueError):
        first_dist = None
    be = str(sg.get("bridge_engine") or "").lower()
    if be == "grok-single":
        engine_label = "Grok 单图"
    elif be == "flow":
        engine_label = "Flow 双图"
    elif be == "grok":
        engine_label = "Grok 双图"
    else:
        eng = str(getattr(m, "engine", "") or "").lower()
        if eng == "grok":
            engine_label = "Grok 单图" if pipeline.GROK_BRIDGE_MODE == "single_tail" else "Grok 双图"
        elif eng == "flow":
            engine_label = "Flow 双图"
        else:
            engine_label = ""
    wr = pipeline.wait_reason(m, sg) or {}
    return {
        "s": int(sg.get("s") or 0),
        "level": lv,
        "gap": g,
        "gap_why": why,
        "from_seg": from_s,
        "to_image": to_p,
        "to_s": int(nxt["s"]) if nxt else None,
        "tail_url": _media(tid, tail_rel, _mtime(tail_path)) if tail_rel else "",
        "tail_k": int(br.get("tail_from_end") or 0) or None,
        "anchor_url": _media(tid, dest_rel, _mtime(dest_path)) if dest_rel else "",
        "first_url": _media(tid, first_rel, _mtime(first_path)) if first_rel else "",
        "first_dist": first_dist,
        "dist_red": bool(first_dist is not None and first_dist > 16),
        "engine": be,
        "engine_label": engine_label,
        "tail_warn": br.get("tail_warn") or "",
        "fail_twice": int(sg.get("mismatch_count") or 0) >= pipeline.MISMATCH_STOP,
        "block_why": bw,
        "can_retry": can_retry_of(sg),
        "status": sg.get("status") or "",
        "now_text": _pose_text(br.get("tail_pose")),
        "target_text": _pose_text((dest_im or {}).get("pose")),
        "text": (sg.get("text") or "").strip(),
        "passed": bool(br.get("passed")),
        "acted": bool(br.get("bench_acted") or sg.get("prompt_locked")),
        "can_pass": can_pass(sg, m),
        "gate_mode": pipeline.gate_mode_of(m, sg),
        "role": sg.get("role") or "bridge",
        "role_manual": bool(sg.get("role_manual")),
        "image": sg.get("image") or "",
        "seg_mode": "bridge" if sg.get("role") == "bridge" else "single",
        "swapped_from": br.get("auto_swapped_from") or None,
        "prompt_final": sg.get("prompt_final") or "",
        "prompt_locked": bool(sg.get("prompt_locked")),
        "src_status": (prev or {}).get("status") or "",
        "src_label": _lab(from_s) if from_s else "",
        "dest_label": _lab(nxt["s"]) if nxt else "",
        "checks": {
            "face_ok": checks.get("face_ok"),
            "in_frame": checks.get("in_frame"),
            "sharp": checks.get("sharp"),
        },
        "wait_why": wr.get("text") or sg.get("wait_why") or "",
        "wait_code": wr.get("code") or sg.get("wait_code") or "",
        "stale_from": int(sg.get("stale_from") or 0) or None,
        "stale_tail": bool(br.get("stale_tail") or sg.get("stale_from")),
    }


def _pool(tid: str, task_dir: Path, m: Manifest) -> list[dict]:
    out = []
    for im in m.images or []:
        if not pipeline._subject_ok(im):
            continue
        rel = (im.get("file") or "").replace("\\", "/")
        if not rel:
            continue
        fp = task_dir / rel
        if not fp.is_file():
            continue
        out.append({
            "p": im.get("p") or "",
            "url": _media(tid, rel, _mtime(fp)),
            "file": rel,
        })
    return out


def _chain_level(sg: dict) -> str:
    """接龙检查台：距离没有红，只有黄。严重穿帮 / 距>16 / 上游尾帧已变 → 黄。"""
    ch = sg.get("chain") if isinstance(sg.get("chain"), dict) else {}
    if ch.get("passed"):
        return "green"
    if ch.get("tail_severe") or ch.get("upstream_changed") or sg.get("stale_from"):
        return "yellow"
    d = ch.get("head_dist")
    if d is None:
        tks = sg.get("takes") or []
        if tks:
            d = tks[-1].get("frame_dist")
    try:
        d = int(d) if d is not None else None
    except (TypeError, ValueError):
        d = None
    if d is not None and d > 16:
        return "yellow"
    return "green"


def _chain_item(tid: str, task_dir: Path, m: Manifest, sg: dict) -> dict:
    """接龙检查台一行：上一段尾帧 | 本段首帧 | 距离 | 尾帧状态。距离没有红。"""
    from . import tails as tailsmod
    ch = sg.get("chain") if isinstance(sg.get("chain"), dict) else {}
    prev = pipeline.prev_of(m, sg)
    s = int(sg.get("s") or 0)
    from_s = int(prev["s"]) if prev else None
    lv = _chain_level(sg)
    head_dist = ch.get("head_dist")
    if head_dist is None:
        tks = sg.get("takes") or []
        if tks:
            head_dist = tks[-1].get("frame_dist")
    try:
        head_dist = int(head_dist) if head_dist is not None else None
    except (TypeError, ValueError):
        head_dist = None
    # 上一段尾帧
    tail_rel = ""
    if from_s:
        tp = tailsmod.tail_path(task_dir, from_s)
        if tp.is_file():
            try:
                tail_rel = str(tp.relative_to(task_dir)).replace("\\", "/")
            except ValueError:
                tail_rel = str(tp)
    if not tail_rel:
        tail_rel = (ch.get("ref_file") or "").replace("\\", "/") if ch.get("ref_kind") == "tail" else ""
    # 规格 95：第一段（或换图起头）没有"上一段尾帧"，左格放起头图，别显示 s000
    start_p = ""
    if not tail_rel and (prev is None or pipeline.is_chain_restart(sg)):
        start_p = (sg.get("image") if pipeline.is_chain_restart(sg) else "") \
            or pipeline.chain_cfg(m).get("start_image") or pipeline.default_start_image(m)
        sim = m.image(start_p) if start_p else None
        if sim and sim.get("file"):
            tail_rel = (sim.get("file") or "").replace("\\", "/")
    tail_path = (task_dir / tail_rel) if tail_rel else None
    prev_ch = (prev.get("chain") if prev and isinstance(prev.get("chain"), dict) else {}) or {}
    tail_error = str(prev_ch.get("tail_error") or "")
    tail_im = tailsmod.tail_image_of(m, from_s) if from_s else None
    # 本段首帧
    first_rel = (ch.get("head_file") or "").replace("\\", "/")
    first_path = (task_dir / first_rel) if first_rel else None
    if first_path is None or not first_path.is_file():
        hp = tailsmod.head_path(task_dir, s)
        if hp.is_file():
            first_path = hp
            try:
                first_rel = str(hp.relative_to(task_dir)).replace("\\", "/")
            except ValueError:
                first_rel = str(hp)
    severe = ch.get("tail_severe") or ""
    why = ""
    if severe:
        why = "尾帧%s" % severe
    elif ch.get("upstream_changed") or sg.get("stale_from"):
        src = ch.get("stale_from") or sg.get("stale_from")
        why = "上游 %s 尾帧已变，这里可能有跳变" % pipeline._lab(src)
    elif head_dist is not None and head_dist > 16:
        why = "首帧距参考 %s（黄旗，片子照收）" % head_dist
    return {
        "s": s,
        "kind": "chain",
        "level": lv,
        "gap": None,
        "gap_why": why,
        "from_seg": from_s,
        "tail_url": _media(tid, tail_rel, _mtime(tail_path)) if tail_rel else "",
        "first_url": _media(tid, first_rel, _mtime(first_path)) if first_rel else "",
        "first_dist": head_dist,
        "dist_red": False,
        "dist_yellow": bool(head_dist is not None and head_dist > 16),
        "tail_severe": severe,
        "tail_status": severe or ("提清过" if ch.get("tail_upscaled") else ""),
        "upstream_changed": bool(ch.get("upstream_changed") or sg.get("stale_from")),
        "stale_from": int(ch.get("stale_from") or sg.get("stale_from") or 0) or None,
        "stale_tail": bool(ch.get("upstream_changed") or sg.get("stale_from")),
        "can_cascade": bool(ch.get("upstream_changed") or sg.get("stale_from")),
        "engine_label": "接龙",
        "status": sg.get("status") or "",
        "text": (sg.get("text") or "").strip(),
        "role": "chain",
        "seg_mode": "first_frame" if pipeline.is_chain_restart(sg) else "chain",
        "image": sg.get("image") or "",
        "src_label": (("起头图 %s" % start_p) if start_p else
                      (pipeline._lab(from_s) if from_s else "")),
        "tail_image": (tail_im or {}).get("p") or "",
        "tail_error": tail_error,
        "block_why": (sg.get("wait_why") or "") if sg.get("status") in ("planned", "failed") else "",
        "ref_from": ch.get("ref_from") or "",
        "ref_note": ch.get("ref_note") or "",
        "passed": bool(ch.get("passed")),
        "can_pass": True,
        "wait_why": sg.get("wait_why") or "",
    }


def snapshot(tid: str) -> dict:
    """检查台整页：接片卡 + 图池（图池不算跨度，悬停再算）。"""
    t = taskmod.get(tid)
    d = Path(t["dir"]) if t.get("dir") else None
    if not d or not d.is_dir():
        raise OranteError("任务文件夹不在了")
    m = Manifest.load(d)
    pipeline.migrate_bridge_acct_53(m)
    items = []
    if pipeline.is_chain_mode(m):
        for sg in m.segments or []:
            items.append(_chain_item(tid, d, m, sg))
    elif pipeline.is_anchor_mode(m):
        for sg in m.segments or []:
            if sg.get("role") != "bridge":
                continue
            items.append(_item(tid, d, m, sg))
    # 复查清单：最可能有问题的排最前。首帧距 > 跨度 > 段号。
    items.sort(key=lambda it: (
        -(it["first_dist"] if it["first_dist"] is not None else -1),
        -(it["gap"] if it["gap"] is not None else -1),
        it["s"],
    ))
    c = counts_of(m)
    from .. import config as cfg
    dens = cfg.get("ui.bench.density", "comfy") or "comfy"
    if dens not in ("compact", "comfy", "large"):
        dens = "comfy"
    try:
        zoom = int(cfg.get("ui.bench.zoom", 100) or 100)
    except (TypeError, ValueError):
        zoom = 100
    zoom = max(40, min(200, zoom))
    return {
        "tid": tid,
        "mode": pipeline.mode_of(m),
        "items": items,
        "pool": _pool(tid, d, m) if (pipeline.is_anchor_mode(m) or pipeline.is_chain_mode(m)) else [],
        "density": dens,
        "zoom": zoom,
        "summary": {
            "red": c["bench_red"],
            "yellow": c["bench_yellow"],
            "green": c["bench_green"],
            "need": c["bench_red"] + c["bench_yellow"],
        },
    }


def _load(tid: str) -> tuple[Path, Manifest]:
    taskmod.ensure_writable(tid)
    d, m, _log = broll.load_synced(tid)
    return d, m


def _save(tid: str, d: Path, m: Manifest) -> None:
    pipeline.stamp_waits(m)
    m.save(d)
    taskmod.remember_counts(d, m)
    events.emit("tasks.changed")


def _bridge_of(m: Manifest, s) -> dict:
    sg = m.seg(int(s))
    if not sg:
        raise OranteError("没有 %s" % _lab(s))
    if pipeline.is_chain_mode(m):
        # 规格 95：接龙段也能在检查台手挑上一段尾帧
        if not isinstance(sg.get("chain"), dict):
            sg["chain"] = {}
        if not isinstance(sg.get("bridge"), dict):
            sg["bridge"] = {}
        return sg
    if sg.get("role") != "bridge":
        raise OranteError("%s 不是接片" % _lab(s), "检查台只处理接片段。")
    if not isinstance(sg.get("bridge"), dict):
        sg["bridge"] = {}
    return sg


def _kick_generate(tid: str, m: Manifest) -> str | None:
    """放行之后如果没人在跑，把刚 ready 的接片再丢进生成。"""
    ready = [sg for sg in m.segments or []
             if sg.get("status") in ("planned", "failed") and pipeline.can_enqueue(m, sg)]
    if not ready:
        return None
    try:
        from ..scheduler import scheduler
        from .. import jobs as jobmod
        for j in scheduler.list(True):
            if j.get("task_id") == tid and j.get("kind") in ("generate", "regenerate"):
                return None
        job = jobmod.start("generate", tid)
        return job.id if job else None
    except Exception:
        return None


def auto_swap(tid: str, s, log=None) -> dict:
    """当场走 37 号自动换锚点，重算三格。"""
    d, m = _load(tid)
    with reviewmod._io_lock(tid):
        m = Manifest.load(d)
        sg = _bridge_of(m, s)
        _clear_pass(sg)
        mark_acted(sg)
        r = pipeline.auto_swap_anchor(d, m, sg, log=log)
        src = pipeline.prev_of(m, sg)
        if src:
            pipeline.refresh_bridge_ready(m, src, sg)
        _save(tid, d, m)
    job_id = _kick_generate(tid, m)
    item = _item(tid, d, m, sg)
    return {"item": item, "swap": r, "job_id": job_id}


def swap_all_red(tid: str, log=None) -> dict:
    """红色项自动换锚点。换不动的不盖章（放行按钮仍锁着）。"""
    d, m = _load(tid)
    done = []
    n_ok = 0
    n_stuck = 0
    with reviewmod._io_lock(tid):
        m = Manifest.load(d)
        for sg in list(m.segments or []):
            if sg.get("role") != "bridge":
                continue
            if bench_level(sg, m) != "red":
                continue
            _clear_pass(sg)
            r = pipeline.auto_swap_anchor(d, m, sg, log=log)
            # 只有真换过（或试过候选）才算做过修正；换不动不盖章。
            if r is not None:
                mark_acted(sg)
                if r.get("changed"):
                    n_ok += 1
            else:
                n_stuck += 1
            src = pipeline.prev_of(m, sg)
            if src:
                pipeline.refresh_bridge_ready(m, src, sg)
            done.append({"s": int(sg["s"]), "swap": r, "level": bench_level(sg, m)})
        _save(tid, d, m)
    job_id = _kick_generate(tid, m)
    snap = snapshot(tid)
    snap["done"] = done
    snap["job_id"] = job_id
    snap["swapped"] = n_ok
    snap["stuck"] = n_stuck
    snap["summary_text"] = "换成 %d 个，换不动 %d 个（下一段主片已收片）" % (n_ok, n_stuck)
    return snap


def pick_anchor(tid: str, s, p: str) -> dict:
    """点选一张图当下一锚点，钉进配图轨，重算跨度。"""
    from . import track as trackmod
    d, m = _load(tid)
    with reviewmod._io_lock(tid):
        m = Manifest.load(d)
        sg = _bridge_of(m, s)
        nxt = pipeline.next_of(m, sg)
        if not nxt:
            raise OranteError("%s 没有下一段锚点" % _lab(s))
        if nxt.get("status") in trackmod.LOCKED_STATES:
            raise OranteError(
                "%s 已经%s，不能换锚点" % (
                    _lab(nxt["s"]),
                    "收片" if nxt.get("status") == "collected" else "提交"),
                "重生那一段之后再换。")
        np = trackmod.fmt_p(p)
        if not np or not m.image(np):
            raise OranteError("没有图片 %s" % (p or "—"))
        _clear_pass(sg)
        mark_acted(sg)
        old = nxt.get("image") or ""
        pipeline.pin_image(m, int(nxt["s"]), np)
        br = _br(sg)
        br["to_image"] = np
        if old and old != np:
            br["auto_swapped_from"] = old
        sg["bridge"] = br
        pipeline._rescore_bridge(d, m, sg)
        src = pipeline.prev_of(m, sg)
        if src:
            pipeline.refresh_bridge_ready(m, src, sg)
        _save(tid, d, m)
    job_id = _kick_generate(tid, m)
    return {"item": _item(tid, d, m, sg), "from": old, "to": np, "job_id": job_id}


def score_one(tid: str, s, p: str) -> dict:
    """悬停才算：这张图对这个接片的跨度。"""
    t = taskmod.get(tid)
    d = Path(t["dir"])
    m = Manifest.load(d)
    sg = _bridge_of(m, s)
    from . import track as trackmod
    np = trackmod.fmt_p(p)
    im = m.image(np) if np else None
    if not im:
        raise OranteError("没有图片 %s" % (p or "—"))
    dest = pipeline._image_file(d, m, np)
    if not dest:
        raise OranteError("%s 的文件不在盘上" % np)
    br = _br(sg)
    rel = br.get("tail_frame")
    tail = (d / rel) if rel else None
    if not (tail and tail.is_file()):
        raise OranteError("%s 还没有尾帧" % _lab(s), "等上一段收片后再算。")
    sc, why = tails.score_gap(
        tail, dest, br.get("tail_pose"), im.get("pose"))
    return {"s": int(sg["s"]), "p": np, "gap": sc, "why": why}


def pass_one(tid: str, s, force: bool = False) -> dict:
    """放行一条接片。force=True：⑤ 收片「忽略这条检查，直接拍」，红也放。"""
    d, m = _load(tid)
    with reviewmod._io_lock(tid):
        m = Manifest.load(d)
        sg = _bridge_of(m, s)
        lv = bench_level(sg, m)
        if lv == "red" and not can_pass(sg, m) and not force:
            raise OranteError(
                "红色项不能直接通过",
                "先自动换锚点、挑锚点、重生上一段或改提示词。")
        br = _br(sg)
        br["passed"] = True
        sg["bridge"] = br
        src = pipeline.prev_of(m, sg)
        if src:
            src["bridge_ready"] = True
        _save(tid, d, m)
    job_id = _kick_generate(tid, m)
    return {"item": _item(tid, d, m, sg), "job_id": job_id}


def pass_all_yellow(tid: str) -> dict:
    d, m = _load(tid)
    n = 0
    with reviewmod._io_lock(tid):
        m = Manifest.load(d)
        for sg in m.segments or []:
            if sg.get("role") != "bridge":
                continue
            if bench_level(sg, m) != "yellow":
                continue
            br = _br(sg)
            br["passed"] = True
            sg["bridge"] = br
            src = pipeline.prev_of(m, sg)
            if src:
                src["bridge_ready"] = True
            n += 1
        _save(tid, d, m)
    job_id = _kick_generate(tid, m)
    snap = snapshot(tid)
    snap["passed"] = n
    snap["job_id"] = job_id
    return snap


def pass_all(tid: str) -> dict:
    """全部放行（含红）。跨度项跳过事前闸，拍完到 ⑤ 复查。"""
    d, m = _load(tid)
    n = 0
    n_gap = 0
    with reviewmod._io_lock(tid):
        m = Manifest.load(d)
        for sg in m.segments or []:
            if sg.get("role") != "bridge":
                continue
            br = _br(sg)
            if br.get("passed"):
                continue
            g = pipeline.gap_of(sg)
            if g is not None and g >= pipeline.GAP_SWAP:
                n_gap += 1
            br["passed"] = True
            sg["bridge"] = br
            src = pipeline.prev_of(m, sg)
            if src:
                src["bridge_ready"] = True
            n += 1
        _save(tid, d, m)
    job_id = _kick_generate(tid, m)
    snap = snapshot(tid)
    snap["passed"] = n
    snap["skipped_gap"] = n_gap
    snap["job_id"] = job_id
    return snap


def parse_seg_mode(raw) -> str:
    v = (raw or "").strip().lower()
    if v in ("single", "anchor", "单图首帧", "first_frame"):
        return "single"
    if v in ("bridge", "首尾帧接片"):
        return "bridge"
    if v in ("chain", "follow", "tail_chain", "接龙", "跟随任务"):
        return "chain"
    raise OranteError("生成方式只能是单图首帧或首尾帧接片")


def _set_chain_seg_mode(tid, d, m, s, mode) -> dict:
    """接龙段级：first_frame = 换图起头；chain/follow = 跟随任务继续从尾帧接。"""
    mode = parse_seg_mode(mode)
    sg = m.seg(int(s))
    if not sg:
        raise OranteError("没有 %s" % _lab(s))
    cur = "single" if pipeline.is_chain_restart(sg) else "chain"
    want = "single" if mode == "single" else "chain"
    if cur == want:
        return {
            "s": int(sg["s"]),
            "role": "chain",
            "mode": cur,
            "changed": False,
            "hint": "%s 已是%s" % (
                _lab(sg["s"]),
                "换图起头" if cur == "single" else "尾帧接龙"),
            "roles": [x.get("role") for x in m.segments],
            "job_id": None,
        }
    if want == "single":
        sg["gen_mode"] = pipeline.MODE_FIRST
        hint = "%s 改成换图起头（下一段仍从这段尾帧接）" % _lab(sg["s"])
    else:
        sg.pop("gen_mode", None)
        hint = "%s 改回尾帧接龙" % _lab(sg["s"])
    pipeline._reset_seg_planned(d, m, sg, why="改生成方式重拍")
    pipeline.clear_bridge_acct(sg)
    _save(tid, d, m)
    job_id = _kick_generate(tid, m)
    return {
        "s": int(sg["s"]),
        "role": "chain",
        "mode": want,
        "changed": True,
        "hint": hint,
        "roles": [x.get("role") for x in m.segments],
        "job_id": job_id,
    }


def cascade_chain_from(tid: str, s) -> dict:
    """检查台「从这里往后重跑」。"""
    d, m = _load(tid)
    with reviewmod._io_lock(tid):
        m = Manifest.load(d)
        if not pipeline.is_chain_mode(m):
            raise OranteError("只有尾帧接龙才能从这里往后重跑")
        r = pipeline.cascade_from(d, m, int(s))
        _save(tid, d, m)
    job_id = _kick_generate(tid, m)
    r["job_id"] = job_id
    return r


def _effective_role(m: Manifest, sg: dict, want_role: str) -> str:
    """toggle_role 的边界：末段强制单图，第一段改不成接片。"""
    if not m.segments:
        return "anchor"
    last = m.segments[-1]
    first = m.segments[0]
    if sg is last:
        return "anchor"
    if sg is first and want_role == "bridge":
        return "anchor"
    return want_role if want_role in ("anchor", "bridge") else "anchor"


def set_seg_mode(tid: str, s, mode) -> dict:
    """段级生成方式：single=单图首帧，bridge=首尾帧接片。幂等。

    和当前一致直接返回；不一致就 toggle_role + 旧片进 _弃用 + 重拍。
    """
    mode = parse_seg_mode(mode)
    d, m = _load(tid)
    with reviewmod._io_lock(tid):
        m = Manifest.load(d)
        if pipeline.is_chain_mode(m):
            return _set_chain_seg_mode(tid, d, m, s, mode)
        if not pipeline.is_anchor_mode(m):
            raise OranteError(
                "这个任务是逐段首帧，每段都单独起头",
                "段级切换只在「锚点+接片」任务里可用。")
        sg = m.seg(int(s))
        if not sg:
            raise OranteError("没有 %s" % _lab(s))
        want_role = "bridge" if mode == "bridge" else "anchor"
        want_role = _effective_role(m, sg, want_role)
        cur_role = sg.get("role") or "anchor"
        if cur_role == want_role:
            return {
                "s": int(sg["s"]),
                "role": cur_role,
                "mode": "bridge" if cur_role == "bridge" else "single",
                "changed": False,
                "hint": "%s 已是%s" % (_lab(sg["s"]), pipeline.role_word(cur_role)),
                "roles": [x.get("role") for x in m.segments],
                "job_id": None,
            }
        pipeline._reset_seg_planned(d, m, sg, why="改生成方式重拍")
        pipeline.clear_bridge_acct(sg)
        r = pipeline.toggle_role(m, int(sg["s"]))
        if sg.get("role") != "bridge":
            sg.pop("bridge", None)
        i = next((k for k, x in enumerate(m.segments or [])
                  if int(x.get("s") or 0) == int(sg["s"])), -1)
        default = pipeline.default_role_at(m.segments, i) if i >= 0 else "anchor"
        sg["role_manual"] = (sg.get("role") or "anchor") != default
        _save(tid, d, m)
    job_id = _kick_generate(tid, m)
    out = dict(r)
    out["mode"] = "bridge" if sg.get("role") == "bridge" else "single"
    out["changed"] = True
    out["job_id"] = job_id
    out["role_manual"] = bool(sg.get("role_manual"))
    return out


def fallback_single(tid: str, s) -> dict:
    """一条接片改成单图首帧重拍。可逆：再 toggle_role / set_seg_mode(bridge)。"""
    return set_seg_mode(tid, s, "single")


def preview_tails(tid: str, s) -> dict:
    """抽出上一段倒数 1~15 帧，给滑块实时预览。自动挑的那张标 auto。"""
    t = taskmod.get(tid)
    d = Path(t["dir"])
    m = Manifest.load(d)
    sg = _bridge_of(m, s)
    prev = pipeline.prev_of(m, sg)
    if not prev:
        raise OranteError("%s 没有上一段" % _lab(s))
    clip = tails.clip_of(d, m, prev)
    if not clip:
        raise OranteError("%s 还没有成片" % _lab(prev.get("s")), "收片后再挑尾帧。")
    work = tails.tails_dir(d) / ("_scan_s%03d" % int(prev["s"]))
    cands = tails.scan_tail_candidates(clip, TAIL_PICK_N, work)
    auto_k = int((_br(sg).get("tail_from_end") or 0) or 0)
    if not auto_k:
        picked = tails.pick_sharpest(cands, tails.FROM_END_LO, tails.FROM_END_HI)
        auto_k = int((picked or {}).get("k") or 0)
    frames = []
    for c in cands:
        k = int(c.get("k") or 0)
        p = c.get("path")
        rel = ""
        url = ""
        if p and Path(p).is_file():
            rel = str(Path(p).relative_to(d)).replace("\\", "/")
            url = _media(tid, rel, _mtime(Path(p)))
        frames.append({
            "k": k,
            "url": url,
            "rel": rel,
            "sharp": float(c.get("sharp") or 0),
            "auto": k == auto_k,
            "error": c.get("error"),
        })
    return {
        "s": int(sg["s"]),
        "from_seg": int(prev["s"]),
        "auto_k": auto_k,
        "current_k": int(_br(sg).get("tail_from_end") or auto_k or 0),
        "frames": frames,
    }


def set_tail(tid: str, s, k) -> dict:
    """手挑倒数第 k 帧当尾帧，重问 pose、重算跨度。"""
    try:
        k = int(k)
    except (TypeError, ValueError):
        raise OranteError("帧序号不对", "倒数 1~%d。" % TAIL_PICK_N)
    if k < 1 or k > TAIL_PICK_N:
        raise OranteError("只能挑倒数 1~%d 帧" % TAIL_PICK_N)
    d, m = _load(tid)
    with reviewmod._io_lock(tid):
        m = Manifest.load(d)
        sg = _bridge_of(m, s)
        prev = pipeline.prev_of(m, sg)
        if not prev:
            raise OranteError("%s 没有上一段" % _lab(s))
        info = tails.extract_k(d, prev, k, inspect=True)
        if pipeline.is_chain_mode(m):
            # 规格 95：接龙——尾帧文件已换，更新上一段 chain 记录 + 登记图副本，放行下一段
            pch = prev.get("chain") if isinstance(prev.get("chain"), dict) else {}
            pch["tail_file"] = info.get("rel") or pch.get("tail_file") or ""
            pch["tail_sharp"] = float(info.get("sharp") or 0)
            pch["tail_from_end"] = int(k)
            pch["tail_severe"] = ""
            pch.pop("tail_error", None)
            if info.get("pose"):
                pch["tail_pose"] = info.get("pose")
            tails.stamp_tail_identity(pch, info.get("path"))
            prev["chain"] = pch
            tails.register_tail_image(d, m, prev, policy=tails.chain_desc_policy(m))
            _br(sg)["tail_from_end"] = int(k)
            _clear_pass(sg)
            mark_acted(sg)
            pipeline.stamp_waits(m, d)
            _save(tid, d, m)
            job_id = _kick_generate(tid, m)
            return {"item": _chain_item(tid, d, m, sg), "k": k, "job_id": job_id}
        tails._fill_bridge(
            m, d, sg, prev, info,
            {"face_ok": (info.get("checks") or {}).get("face_ok"),
             "in_frame": (info.get("checks") or {}).get("in_frame"),
             "pose": info.get("pose") or {}})
        _clear_pass(sg)
        mark_acted(sg)
        src = prev
        pipeline.refresh_bridge_ready(m, src, sg)
        tails.apply_sharp_flags(m, d)
        _save(tid, d, m)
    job_id = _kick_generate(tid, m)
    return {"item": _item(tid, d, m, sg), "k": k, "job_id": job_id}


def after_prompt_edit(tid: str, s) -> dict:
    """改提示词也算做过动作，红色项随后可以点通过。"""
    d, m = _load(tid)
    with reviewmod._io_lock(tid):
        m = Manifest.load(d)
        sg = _bridge_of(m, s)
        mark_acted(sg)
        _save(tid, d, m)
    return {"item": _item(tid, d, m, sg)}


def retry_bridge(tid: str, s) -> dict:
    """检查台「重试接片」：清对不上/超时/缩略图账，打回 planned。"""
    d, m = _load(tid)
    with reviewmod._io_lock(tid):
        m = Manifest.load(d)
        pipeline.migrate_bridge_acct_53(m)
        sg = _bridge_of(m, s)
        pipeline.clear_bridge_acct(sg)
        sg["status"] = "planned"
        sg["error"] = None
        sg["gen_id"] = None
        sg.pop("gen_conv", None)
        src = pipeline.prev_of(m, sg)
        if src:
            pipeline.refresh_bridge_ready(m, src, sg)
        _save(tid, d, m)
    job_id = _kick_generate(tid, m)
    item = _item(tid, d, m, sg)
    return {"item": item, "job_id": job_id, "log": "· %s 检查台点了重试接片，对不上/超时记录清零" % _lab(s)}


def after_regen_src(tid: str, s) -> dict:
    """重生上一段点下去时先记下 acted；收片后 tails.after_collect 会重抽。"""
    d, m = _load(tid)
    with reviewmod._io_lock(tid):
        m = Manifest.load(d)
        sg = _bridge_of(m, s)
        _clear_pass(sg)
        mark_acted(sg)
        _save(tid, d, m)
    return {"item": _item(tid, d, m, sg), "from_seg": _br(sg).get("from_seg")}

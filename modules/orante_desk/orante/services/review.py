# -*- coding: utf-8 -*-
"""收片：体检 job、标记、过时判定。不占账号。

真值在任务自己的 manifest.json：每段 check / mark。不另写 marks.json。
重生走已有的 kind=regenerate（services/generate.py），这里不另写一套。
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from .. import events, paths, tasks as taskmod
from ..errors import OranteError
from ..jobs import register
from ..models import Manifest
from . import checks

_ZH: dict | None = None


def _zh(key: str, **kw) -> str:
    """读中文包。批量和前端 finishBlockers 用同一套文案。"""
    global _ZH
    if _ZH is None:
        try:
            _ZH = json.loads((paths.I18N / "zh.json").read_text(encoding="utf-8"))
        except Exception:
            _ZH = {}
    s = (_ZH or {}).get(key) or key
    for k, v in kw.items():
        s = s.replace("{" + k + "}", str(v))
    return s

_LOUD_CACHE: dict = {}

# 同一任务同一时间只允许一个校验线程（来回切面板不重复跑）
_VERIFY_GUARD = threading.Lock()
_VERIFY_SEM = threading.Semaphore(2)
_VERIFY_THREADS: dict[str, threading.Thread] = {}
_VERIFY_PROG: dict[str, dict] = {}
_IO_LOCKS: dict[str, threading.Lock] = {}


def _io_lock(tid: str) -> threading.Lock:
    """manifest 短写锁：后台校验落盘时不盖掉用户刚改的标记 / 提示词。"""
    with _VERIFY_GUARD:
        lk = _IO_LOCKS.get(tid)
        if lk is None:
            lk = threading.Lock()
            _IO_LOCKS[tid] = lk
        return lk


def _task_dir(tid: str) -> Path:
    t = taskmod.get(tid)
    if not t.get("dir"):
        raise OranteError("这个任务还没准备", "先在任务面板点「准备」。")
    d = Path(t["dir"])
    if not d.is_dir():
        raise OranteError("任务文件夹不在了", str(d))
    return d


def _label(seg: dict) -> str:
    return "s%03d" % int(seg.get("s") or 0)


def clip_path(task_dir: Path, m: Manifest, seg: dict) -> Path | None:
    """当前该播/该查的那一条片子：takes 里最后一条；没有 takes 就试约定文件名。"""
    takes = seg.get("takes") or []
    if takes:
        rel = (takes[-1].get("file") or "").replace("\\", "/")
        return (task_dir / rel) if rel else None
    p = task_dir / "clips" / m.clip_name(seg)
    return p if p.is_file() else None


def _exists(path: Path | None) -> bool:
    return bool(path) and path.is_file()


def _image_path(task_dir: Path, m: Manifest, seg: dict) -> Path | None:
    im = m.image(seg.get("image") or "")
    if not im:
        return None
    rel = (im.get("file") or "").replace("\\", "/")
    if not rel:
        return None
    p = task_dir / rel
    return p if p.is_file() else None


def _loud(path: Path):
    try:
        st = path.stat()
    except OSError:
        return None
    key = (str(path), st.st_size, int(st.st_mtime))
    if key in _LOUD_CACHE:
        return _LOUD_CACHE[key]
    val = checks.loudness(str(path))
    _LOUD_CACHE[key] = val
    return val


def _bridge_view(sg: dict) -> dict | None:
    """收片墙用的精简 bridge：角标、灰字衔接、ⓘ 说明。"""
    if (sg.get("role") or "") != "bridge":
        return None
    br = sg.get("bridge") if isinstance(sg.get("bridge"), dict) else {}
    return {
        "from_seg": br.get("from_seg"),
        "to_image": br.get("to_image") or "",
        "tail_frame": br.get("tail_frame") or "",
        "gap_score": br.get("gap_score"),
    }


def _wait_view(m, sg: dict) -> dict:
    """当场算 wait_reason，不依赖上次 stamp 留下的旧文案。"""
    from . import pipeline
    if not pipeline.is_anchor_mode(m):
        return {"wait_why": "", "wait_code": "", "wait_on": None}
    why = pipeline.wait_reason(m, sg)
    if not why:
        return {"wait_why": "", "wait_code": "", "wait_on": None}
    return {
        "wait_why": why.get("text") or "",
        "wait_code": why.get("code") or "",
        "wait_on": why.get("from"),
    }


def _file_mtime(path: Path | None) -> int:
    """给 <video>/<img> src 加 ?v=mtime 用（毫秒）。文件不在就 0。"""
    from . import images as imgmod
    return imgmod.file_mtime_ms(path)


def _identity_ref_view(task_dir: Path, m: Manifest, sg: dict) -> dict | None:
    """换图卡片用的身份参考缩略图。失败就空，不挡收片面板。"""
    try:
        from . import desc_cards
        return desc_cards.identity_ref_view(m, task_dir, sg)
    except Exception:
        return None


def _read_fps_field(sg: dict) -> float | None:
    """段上已缓存的 fps。没有或非法就当没有，让调用方去探。"""
    try:
        f = float(sg.get("fps") or 0)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def _probe_clip_fps(path: Path) -> float | None:
    """用 tails.video_info 读帧率。ffmpeg 没有、片子不是真视频，都当失败。"""
    try:
        from . import tails
        info = tails.video_info(path)
        f = float((info or {}).get("fps") or 0)
        return f if f > 0 else None
    except Exception:
        return None


def _ensure_fps(sg: dict, path: Path | None, exists: bool) -> float:
    """收片逐帧用的 fps：已有字段直接用；没有就探一次写回段上；实在没有按 24。

    探失败不把 24 写进 manifest，下次还有机会再探。
    """
    cached = _read_fps_field(sg)
    if cached is not None:
        return cached
    if exists and path is not None:
        probed = _probe_clip_fps(path)
        if probed is not None:
            sg["fps"] = probed
            return probed
    return 24.0


def _flush_fps(tid: str, d: Path, segments: list, archived: bool) -> None:
    """把刚探到的 fps 落盘。归档只读不写。失败吞掉，下次打开再探。"""
    if archived:
        return
    fps_map = {}
    for sg in segments or []:
        f = _read_fps_field(sg)
        if f is not None:
            try:
                fps_map[int(sg.get("s") or 0)] = f
            except (TypeError, ValueError):
                continue
    if not fps_map:
        return
    try:
        with _io_lock(tid):
            m2 = Manifest.load(d)
            dirty = False
            for sg in m2.segments:
                try:
                    s = int(sg.get("s") or 0)
                except (TypeError, ValueError):
                    continue
                f = fps_map.get(s)
                if f is None:
                    continue
                if _read_fps_field(sg) is None:
                    sg["fps"] = f
                    dirty = True
            if dirty:
                m2.save(d)
    except Exception:
        pass


def _seg_payload(task_dir: Path, m: Manifest, sg: dict, lite: bool = False) -> dict:
    path = clip_path(task_dir, m, sg)
    exists = _exists(path)
    mtime = _file_mtime(path) if exists else 0
    takes = list(sg.get("takes") or [])
    # 把当前片子的 mtime 贴到最后一条 take 上（拷一份，不改 manifest）
    if takes and exists:
        last = dict(takes[-1])
        last["mtime"] = mtime
        takes = takes[:-1] + [last]
    img_p = _image_path(task_dir, m, sg)
    wait = _wait_view(m, sg)
    fps = (_read_fps_field(sg) or 24.0) if lite else _ensure_fps(sg, path, exists)
    return {
        "s": sg["s"],
        "text": sg.get("text") or "",
        "duration": sg.get("duration"),
        "image": sg.get("image") or "",
        "status": sg.get("status") or "planned",
        "takes": takes,
        "check": sg.get("check") or {"issues": []},
        "prompt_final": sg.get("prompt_final") or "",
        "prompt_locked": bool(sg.get("prompt_locked")),
        "mark": bool(sg.get("mark")),
        "exists": exists,
        "mtime": mtime,
        "error": sg.get("error"),
        "shot": sg.get("shot") or "",
        "role": sg.get("role") or "",
        "bridge": _bridge_view(sg),
        "wait_why": wait["wait_why"],
        "wait_code": wait["wait_code"],
        "wait_on": wait["wait_on"],
        # 配图的文件还在不在盘上：不在就等于没图，收片面板要给「换图 / 替补」的选择
        "image_ok": bool(sg.get("image")) and img_p is not None,
        "image_mtime": _file_mtime(img_p) if img_p is not None else 0,
        # 重生出来还没复查的片子：收片面板上打「新」
        "fresh": sg.get("fresh") or "",
        # 80：片子是用换掉之前那张图生成的，图池已经是新图
        "image_replaced": sg.get("image_replaced") or None,
        "chain": sg.get("chain") if isinstance(sg.get("chain"), dict) else None,
        "gen_mode": sg.get("gen_mode") or "",
        "identity_ref": _identity_ref_view(task_dir, m, sg),
        # 逐帧步进用；已缓存就不再调 ffprobe
        "fps": fps,
    }


def _clip_count(d: Path, m: Manifest) -> int:
    n = 0
    for sg in m.segments:
        p = clip_path(d, m, sg)
        if _exists(p):
            n += 1
    return n


def wait_verify(tid: str, timeout: float = 120) -> bool:
    """验收用：等到这个任务的后台校验结束。"""
    th = None
    with _VERIFY_GUARD:
        th = _VERIFY_THREADS.get(tid)
    if th is None:
        return True
    th.join(timeout=timeout)
    return not th.is_alive()


def _verify_bg(tid: str) -> None:
    """守护线程：只补缺的凭证，算完推 review.verified。"""
    from . import verify
    total = 0
    try:
        d = _task_dir(tid)
        with _io_lock(tid):
            m = Manifest.load(d)
            total = _clip_count(d, m)
        with _VERIFY_GUARD:
            _VERIFY_PROG[tid] = {"running": True, "done": 0, "total": total}
        events.emit("review.verifying", tid=tid, done=0, total=total)

        def prog(done, tot):
            with _VERIFY_GUARD:
                _VERIFY_PROG[tid] = {"running": True, "done": done, "total": tot}
            events.emit("review.verifying", tid=tid, done=done, total=tot)

        with _VERIFY_SEM:
            results = verify.light_check(m, d, progress=prog)
        # 重新读盘再写凭证，免得盖掉这期间用户改的标记 / 提示词
        with _io_lock(tid):
            m2 = Manifest.load(d)
            verify.apply_results(m2, results)
            m2.save(d)
        with _VERIFY_GUARD:
            _VERIFY_PROG[tid] = {"running": False, "done": total, "total": total}
        events.emit("review.verified", tid=tid, results=results)
    except Exception as e:
        print(f"[review] 后台校验失败 {tid}: {e}", flush=True)
        with _VERIFY_GUARD:
            _VERIFY_PROG[tid] = {"running": False, "done": total, "total": total}
        events.emit("review.verified", tid=tid, results=[], error=str(e))
    finally:
        with _VERIFY_GUARD:
            cur = _VERIFY_THREADS.get(tid)
            if cur is threading.current_thread():
                _VERIFY_THREADS.pop(tid, None)
            p = dict(_VERIFY_PROG.get(tid) or {})
            p["running"] = False
            _VERIFY_PROG[tid] = p


def schedule_verify(tid: str, m: Manifest, d: Path) -> dict:
    """有缺再丢后台线程；全部命中缓存则零个子进程。返回 {running,done,total}。"""
    from . import verify
    total = _clip_count(d, m)
    with _VERIFY_GUARD:
        th = _VERIFY_THREADS.get(tid)
        if th is not None and th.is_alive():
            p = _VERIFY_PROG.get(tid) or {}
            return {
                "running": True,
                "done": int(p.get("done") or 0),
                "total": int(p.get("total") or total),
            }
        if not verify.needs_recompute(m, d):
            return {"running": False, "done": total, "total": total}
        _VERIFY_PROG[tid] = {"running": True, "done": 0, "total": total}
        th = threading.Thread(target=_verify_bg, args=(tid,), daemon=True,
                              name=f"verify-{tid}")
        _VERIFY_THREADS[tid] = th
        th.start()
        return {"running": True, "done": 0, "total": total}


def get_review(tid: str, lite: bool = False) -> dict:
    """给收片面板：段列表 + 图 + 标记。只读已有凭证，立刻返回；缺的后台补。"""
    t = taskmod.get(tid)
    dpath = Path(t["dir"]) if t.get("dir") else None
    if not dpath or not dpath.is_dir():
        from . import project
        return project.review_stub(t)
    if lite:
        d = dpath
        m = Manifest.load(d)
        sync_log = []
        st = {"running": False, "done": 0, "total": 0}
    else:
        from . import broll
        d, m, sync_log = broll.load_synced(tid)
        # 归档只读：不跑会写盘的后台校验，视频墙照常打开。
        if t.get("archived"):
            st = {"running": False, "done": 0, "total": 0}
        else:
            st = schedule_verify(tid, m, d)
    segs = [_seg_payload(d, m, sg, lite=lite) for sg in m.segments]
    if not lite:
        _flush_fps(tid, d, m.segments, bool(t.get("archived")))
    marks = {str(sg["s"]): True for sg in segs if sg["mark"]}
    from . import images as imgmod
    images = imgmod.annotate_images(d, m.images)
    return {
        "segments": segs,
        "images": images,
        "marks": marks,
        "engine": m.engine,
        "name": m.name,
        "task_id": m.task_id,
        "status": t.get("status"),
        "mode": getattr(m, "mode", None) or "first_frame",
        "sync_log": sync_log,
        "verifying": bool(st.get("running")),
        "verify_done": int(st.get("done") or 0),
        "verify_total": int(st.get("total") or 0),
        "archived": int(t.get("archived") or 0),
        "missing_dir": False,
    }


def set_mark(tid: str, s, mark) -> dict:
    """标记存 manifest 每段 mark 字段。"""
    taskmod.ensure_writable(tid)
    try:
        sn = int(s)
    except (TypeError, ValueError):
        raise OranteError("段号不对", "POST body 里要有整数 s。")
    d = _task_dir(tid)
    with _io_lock(tid):
        m = Manifest.load(d)
        sg = m.seg(sn)
        if not sg:
            raise OranteError(f"没有段 {sn}")
        sg["mark"] = bool(mark)
        m.save(d)
        marked = bool(sg["mark"])
    return {"s": sn, "mark": marked}


def set_image(tid: str, s, p: str) -> dict:
    """收片面板「换图」：把某段的配图换成现有的另一张，并同步钉进配图轨。

    · 配图轨里加一条钉住（编排面板能看到、以后重排也不会被冲掉）
    · 清掉成品提示词，下次出片按新图重拼
    · 已经收过片的段：标上「已标记」，方便直接进重生抽屉
    返回 {s, image, old, marked}。
    """
    taskmod.ensure_writable(tid)
    try:
        sn = int(s)
    except (TypeError, ValueError):
        raise OranteError("段号不对", "POST body 里要有整数 s。")
    from . import track as trackmod
    d = _task_dir(tid)
    with _io_lock(tid):
        m = Manifest.load(d)
        sg = m.seg(sn)
        if not sg:
            raise OranteError(f"没有段 {sn}")
        np = trackmod.fmt_p(p)
        if not np:
            raise OranteError("图号不对", "要 pNN 这种，比如 p03。")
        im = m.image(np)
        if not im:
            raise OranteError(f"没有图片 {np}")
        if im.get("role") == "空镜":
            raise OranteError(f"{np} 是空镜，不能配给段", "到编排面板把它改回主体，或换一张。")
        if im.get("status") == "blocked":
            raise OranteError(f"{np} 被 Flow 拒过", "换一张，或先补图。")
        rel = (im.get("file") or "").replace("\\", "/")
        if not rel or not (d / rel).is_file():
            raise OranteError(f"{np} 的文件不在盘上", rel or "—")
        old = sg.get("image") or ""
        sg["image"] = np
        sg.pop("image_note", None)
        kept_locked = bool(sg.get("prompt_locked"))
        if not kept_locked:
            sg["prompt_final"] = ""
        marked = False
        if (sg.get("takes") or []) or sg.get("status") in ("collected", "submitted"):
            sg["mark"] = True          # 片子还是旧图出的，标一下提醒重生
            marked = True
        tr = dict(m.track or {})
        pins = dict(tr.get("pins") or {})
        pins[str(sn)] = np
        tr["pins"] = pins
        m.track = tr
        from . import pipeline
        cas = None
        if pipeline.is_anchor_mode(m) and old != np:
            cas = pipeline.on_anchor_image_changed(d, m, sn)
        m.save(d)
    events.emit("tasks.changed")
    out = {"s": sn, "image": np, "old": old, "marked": marked, "kept_locked": kept_locked}
    if cas:
        out["cascade"] = cas
    return out


def _is_finished(tid: str) -> bool:
    t = taskmod.get(tid)
    return (t.get("status") or "") == "finished"


def set_prompt(tid: str, s, text=None, reset=False) -> dict:
    """收片面板改成品提示词。text 非空 → 锁定手写版；reset → 清掉手写、下次重拼。"""
    taskmod.ensure_writable(tid)
    if _is_finished(tid):
        raise OranteError("已完成的任务不能改提示词", "先点「撤销完成」。")
    try:
        sn = int(s)
    except (TypeError, ValueError):
        raise OranteError("段号不对", "POST body 里要有整数 s。")
    d = _task_dir(tid)
    with _io_lock(tid):
        m = Manifest.load(d)
        sg = m.seg(sn)
        if not sg:
            raise OranteError(f"没有段 {sn}")
        if reset:
            sg["prompt_final"] = ""
            sg["prompt_locked"] = False
            locked = False
            chars = 0
        else:
            body = "" if text is None else str(text)
            if not body.strip():
                raise OranteError("提示词是空的", "要丢掉手写版请用「恢复默认」。")
            sg["prompt_final"] = body
            sg["prompt_locked"] = True
            locked = True
            chars = len(body)
        m.save(d)
    events.emit("tasks.changed")
    return {"s": sn, "chars": chars, "locked": locked}


def clear_fresh(tid: str, s=None) -> dict:
    """清掉「新」标。s 给了就只清那一段，不给就清整个任务的。"""
    d = _task_dir(tid)
    with _io_lock(tid):
        m = Manifest.load(d)
        want = None
        if s is not None and s != "":
            try:
                want = int(s)
            except (TypeError, ValueError):
                raise OranteError("段号不对")
        n = 0
        for sg in m.segments:
            if want is not None and sg.get("s") != want:
                continue
            if sg.pop("fresh", None):
                n += 1
        if n:
            m.save(d)
    if n:
        events.emit("tasks.changed")
    return {"cleared": n}


def _active_jobs_for(tid: str) -> list[dict]:
    """规格 83：批量完成拦截只认 live_jobs()（含心跳/多开），不要只扫本进程内存。"""
    if not tid:
        return []
    try:
        from ..scheduler import live_jobs
        return [j for j in live_jobs() if (j.get("task_id") or "") == tid]
    except Exception:
        return []


def _has_active_job(tid: str) -> bool:
    """正在跑或还在排队的 job 都算活跃。批量完成时要先停下来。"""
    return bool(_active_jobs_for(tid))


def _finish_reason(b: dict) -> str:
    """和前端 finishReason / finishBlockers 同一套键。"""
    if b.get("ok"):
        return _zh("review.finish_tip_ok", n=int(b.get("n") or 0))
    parts = []
    if b.get("job"):
        parts.append(_zh("review.finish_tip_job"))
    if b.get("empty"):
        parts.append(_zh("review.finish_tip_empty"))
    elif not b.get("stOk"):
        parts.append(_zh("review.finish_tip_status"))
    miss = int(b.get("miss") or 0)
    if miss:
        ids = b.get("miss_ids") or []
        if ids:
            parts.append(_zh("review.finish_tip_missing_ids", n=miss, ids=" ".join(ids)))
        else:
            parts.append(_zh("review.finish_tip_missing", n=miss))
    mark = int(b.get("mark") or 0)
    if mark:
        parts.append(_zh("review.finish_tip_marked", n=mark))
    fail = int(b.get("fail") or 0)
    if fail:
        parts.append(_zh("review.finish_tip_failed", n=fail))
    fresh = int(b.get("fresh") or 0)
    if fresh:
        parts.append(_zh("review.finish_tip_fresh", n=fresh))
    return " / ".join(parts)


def finish_check(tid: str, apply: bool = True) -> dict:
    """判定这个任务能不能点完成。和前端 finishBlockers 同一套，不许放宽。

    apply=True（默认，兼容旧路由）：目录里有 字幕成品/*.mp4 就顺便把 status 设 finished。
    批量预检传 apply=False，只体检不改状态。
    """
    name = ""
    status = ""
    try:
        t = taskmod.get(tid)
        name = t.get("name") or ""
        status = t.get("status") or ""
    except OranteError as e:
        b = {
            "miss": 0, "miss_ids": [], "mark": 0, "fail": 0, "fresh": 0,
            "job": False, "stOk": False, "empty": True, "n": 0, "n_ok": 0,
            "ok": False,
        }
        return {
            "tid": tid, "name": "", "status": "",
            "finished": False, "ok": False,
            "reason": e.message, "blockers": b, "n": 0, "n_ok": 0,
        }

    d = None
    m = None
    segs = []
    try:
        d = _task_dir(tid)
        m = Manifest.load(d)
        segs = list(m.segments or [])
    except OranteError:
        if apply:
            raise
        d = None
        m = None
        segs = []

    miss_ids = []
    mark_n = 0
    fail_n = 0
    fresh_n = 0
    if d is not None and m is not None:
        for sg in segs:
            path = clip_path(d, m, sg)
            if not _exists(path):
                try:
                    miss_ids.append(_label(sg))
                except Exception:
                    miss_ids.append("s???")
            if sg.get("mark"):
                mark_n += 1
            if (sg.get("status") or "") == "failed":
                fail_n += 1
            if sg.get("fresh"):
                fresh_n += 1

    jobs_live = _active_jobs_for(tid)
    job = bool(jobs_live)
    st_ok = status in ("collected", "generating")
    empty = not segs
    n = len(segs)
    n_ok = max(0, n - len(miss_ids))
    # 缺片 / 标记 / 失败 / 状态 / 空任务：原 canFinish。
    # 新片未复查、活跃 job：规格 73 明确要拦，加严不是放宽。
    ok = (
        st_ok and not miss_ids and not mark_n and not fail_n
        and not empty and not fresh_n and not job
    )
    blockers = {
        "miss": len(miss_ids),
        "miss_ids": miss_ids,
        "mark": mark_n,
        "fail": fail_n,
        "fresh": fresh_n,
        "job": job,
        "jobs": jobs_live,
        "stOk": st_ok,
        "empty": empty,
        "n": n,
        "n_ok": n_ok,
        "ok": ok,
    }
    reason = _finish_reason(blockers)

    auto = False
    if d is not None:
        folder = d / "字幕成品"
        mp4s = []
        if folder.is_dir():
            mp4s = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".mp4"]
        if mp4s:
            if apply:
                taskmod.set_status(tid, "finished")
                events.emit("tasks.changed")
            auto = True

    return {
        "tid": tid,
        "name": name,
        "status": status,
        "finished": bool(auto and apply),
        "ok": ok,
        "reason": reason,
        "blockers": blockers,
        "n": n,
        "n_ok": n_ok,
    }


def finish_preview(tids) -> dict:
    """批量完成前的体检：每个 id 跑一遍 finish_check(apply=False)。"""
    ready = []
    blocked = []
    for tid in list(tids or []):
        tid = str(tid or "").strip()
        if not tid:
            continue
        chk = finish_check(tid, apply=False)
        blk = chk.get("blockers") or {}
        item = {
            "tid": chk.get("tid") or tid,
            "name": chk.get("name") or "",
            "reason": chk.get("reason") or "",
            "ok": bool(chk.get("ok")),
            "blockers": blk,
            "jobs": list(blk.get("jobs") or []),
            "n": int(chk.get("n") or 0),
            "n_ok": int(chk.get("n_ok") or 0),
        }
        (ready if item["ok"] else blocked).append(item)
    return {
        "ready": ready,
        "blocked": blocked,
        "n_ready": len(ready),
        "n_blocked": len(blocked),
    }


def finish_batch(tids) -> dict:
    """串行完成。每个都先 finish_check，不行的跳过；一个抛错不影响后面的。"""
    done = []
    failed = []
    skipped = []
    for tid in list(tids or []):
        tid = str(tid or "").strip()
        if not tid:
            continue
        try:
            chk = finish_check(tid, apply=False)
            if not chk.get("ok"):
                skipped.append({
                    "tid": tid,
                    "name": chk.get("name") or "",
                    "reason": chk.get("reason") or "",
                })
                continue
            taskmod.set_status(tid, "finished")
            done.append({"tid": tid, "name": chk.get("name") or ""})
        except Exception as e:
            msg = getattr(e, "message", None) or str(e)
            failed.append({"tid": tid, "error": msg})
    if done:
        events.emit("tasks.changed")
    n = len(done)
    return {
        "done": done,
        "failed": failed,
        "skipped": skipped,
        "n": n,
        "celebrate": n >= 1,
    }


def reveal_path(tid: str, s, what: str = "video") -> Path:
    """V/P：返回要在资源管理器里选中的文件。"""
    try:
        sn = int(s)
    except (TypeError, ValueError):
        raise OranteError("段号不对")
    d = _task_dir(tid)
    m = Manifest.load(d)
    sg = m.seg(sn)
    if not sg:
        raise OranteError(f"没有段 {sn}")
    what = (what or "video").lower()
    if what in ("image", "img", "p"):
        path = _image_path(d, m, sg)
        if not path:
            raise OranteError("这段的原图不在盘上", "到任务文件夹里看 images/。")
        return path.resolve()
    path = clip_path(d, m, sg)
    if not _exists(path):
        raise OranteError("这段还没有片子", "缺片的段没法在资源管理器里定位。")
    return path.resolve()


@register("check", account_bound=False)
def check(job):
    """落盘四查 + 音轨三查。结果写进每段 seg['check']，manifest 落盘。不占账号。"""
    d = _task_dir(job.task_id)
    m = Manifest.load(d)
    use_sound = bool(checks.find_ffmpeg())
    if use_sound:
        job.log("找到 ffmpeg，会做静音检测")
    else:
        job.log("没找到 ffmpeg，跳过静音检测（ROOT/bin → PATH → 老 grok_pipe.find_ffmpeg）")

    infos, louds, md5_owner = {}, {}, {}
    n = len(m.segments)
    for i, sg in enumerate(m.segments, 1):
        job.check()
        lb = _label(sg)
        path = clip_path(d, m, sg)
        exists = _exists(path)
        if not exists:
            continue
        info = checks.probe(str(path))
        infos[lb] = info
        if use_sound and (info or {}).get("has_audio"):
            louds[lb] = _loud(path)
        md5 = (info or {}).get("md5")
        if md5:
            md5_owner.setdefault(md5, lb)
        if i % 10 == 0:
            job.log(f"  … 已探 {i}/{n}")

    n_flagged = 0
    for sg in m.segments:
        job.check()
        lb = _label(sg)
        path = clip_path(d, m, sg)
        exists = _exists(path)
        fake = {
            "exists": exists,
            "dur_expect": sg.get("duration"),
            "label": lb,
        }
        iss = checks.issues_for(fake, infos.get(lb), md5_owner, louds.get(lb))
        if any(x["level"] in ("bad", "miss") for x in iss):
            n_flagged += 1
        info = infos.get(lb) or {}
        sg["check"] = {
            "issues": iss,
            "duration": info.get("duration"),
            "audio_dur": info.get("audio_dur"),
            "has_audio": info.get("has_audio"),
            "mean_db": (louds.get(lb) or {}).get("mean_db") if louds.get(lb) else None,
            "size": info.get("size"),
        }
    m.save(d)
    events.emit("task.update", task_id=job.task_id, counts=m.counts())
    job.log(f"体检完成 · 存疑 {n_flagged} 段" +
            ("（含静音检测）" if use_sound else "（没找到 ffmpeg，跳过静音检测）"))
    return {"n_flagged": n_flagged, "sound": use_sound, "total": n}

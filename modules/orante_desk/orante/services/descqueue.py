# -*- coding: utf-8 -*-
"""跨任务描述语队列：同一时刻只跑一个 describe，失败记下来继续下一个。

不占账号锁（describe 走 AI 接口池）。进度靠 events.emit('queue.describe')，
前端不要轮询。
"""
from __future__ import annotations

import copy
import os
import threading
import time
from pathlib import Path

from .. import config, events, locks, tasks as taskmod
from ..errors import OranteError
from ..models import Manifest
from ..scheduler import scheduler

_OWNER_STALE = 90.0  # 心跳停这么久，别的窗口可以接手队列

_LOCK = threading.RLock()
_CV = threading.Condition(_LOCK)


def _empty() -> dict:
    return {
        "on": False,
        "paused": False,
        "done": 0,
        "total": 0,
        "current": None,
        "results": [],
        "job_id": None,
        "summary": "",
    }


_state = _empty()
_pending: list[str] = []
_meta: dict[str, dict] = {}
_skip = False
_stop = False
_thread: threading.Thread | None = None


def _claim_owner() -> None:
    config.set("descqueue.owner_port", int(locks.OWNER_PORT or 0), touch_rev=False)
    config.set("descqueue.owner_pid", os.getpid(), touch_rev=False)
    config.set("descqueue.heartbeat", time.time(), touch_rev=False)


def _touch_owner() -> None:
    if not _state.get("on"):
        return
    config.set("descqueue.heartbeat", time.time(), touch_rev=False)


def _release_owner() -> None:
    config.set("descqueue.owner_port", 0, touch_rev=False)
    config.set("descqueue.owner_pid", 0, touch_rev=False)
    config.set("descqueue.heartbeat", 0, touch_rev=False)


def _foreign_snapshot() -> dict | None:
    """别的窗口正跑队列 → 给本窗口看的只读快照。"""
    try:
        port = int(config.get("descqueue.owner_port") or 0)
        hb = float(config.get("descqueue.heartbeat") or 0)
    except (TypeError, ValueError):
        return None
    if not port or not hb:
        return None
    if time.time() - hb >= _OWNER_STALE:
        return None
    if port == int(locks.OWNER_PORT or 0):
        return None
    return {
        "on": True,
        "paused": False,
        "done": 0,
        "total": 0,
        "current": None,
        "results": [],
        "job_id": None,
        "summary": "",
        "owner_port": port,
        "foreign": True,
    }


def snapshot() -> dict:
    with _LOCK:
        local = copy.deepcopy(_state)
    if local.get("on"):
        local["owner_port"] = int(locks.OWNER_PORT or 0)
        local["foreign"] = False
        return local
    foreign = _foreign_snapshot()
    if foreign:
        return foreign
    local["owner_port"] = 0
    local["foreign"] = False
    return local


def _emit() -> None:
    _touch_owner()
    events.emit("queue.describe", **snapshot())


def _counts(tid: str) -> tuple[int, int]:
    """已有描述语的主体图 / 主体图总数。"""
    try:
        t = taskmod.get(tid)
        d = Path(t["dir"]) if t.get("dir") else None
        if not d or not d.is_dir():
            return 0, 0
        m = Manifest.load(d)
    except Exception:
        return 0, 0
    from .simple86 import has_copy
    no_copy = (not has_copy(t)) and (not has_copy(m))
    if no_copy:
        subjects = [im for im in (m.images or [])
                    if (im.get("status") or "") not in ("missing", "blocked")]
    else:
        subjects = [im for im in (m.images or []) if im.get("role") == "主体"]
    if not subjects:
        return 0, 0
    had = sum(1 for im in subjects if str(im.get("desc") or "").strip())
    return had, len(subjects)


def _precheck(t: dict | None) -> str | None:
    if not t:
        return "没有这个任务"
    if not t.get("dir"):
        return "还没准备"
    d = Path(t["dir"])
    if not d.is_dir():
        return "任务文件夹不在了"
    try:
        m = Manifest.load(d)
    except Exception as e:  # noqa: BLE001
        return str(e)[:80] or "manifest 读不出来"
    from .simple86 import has_copy
    no_copy = (not has_copy(t)) and (not has_copy(m))
    if no_copy:
        usable = [im for im in (m.images or [])
                  if (im.get("status") or "") not in ("missing", "blocked")]
        if not usable:
            return "没有主体图"
        return None
    subjects = [im for im in (m.images or []) if im.get("role") == "主体"]
    if not subjects:
        return "没有主体图"
    return None


def _needs(t: dict) -> bool:
    """自动扫：有目录，且（没主体图 或 至少一张主体图 desc 为空）。"""
    if not t.get("dir"):
        return False
    d = Path(t["dir"])
    if not d.is_dir():
        return False
    try:
        m = Manifest.load(d)
    except Exception:
        return True
    from .simple86 import has_copy
    no_copy = (not has_copy(t)) and (not has_copy(m))
    if no_copy:
        usable = [im for im in (m.images or [])
                  if (im.get("status") or "") not in ("missing", "blocked")]
        if not usable:
            return True
        return any(not str(im.get("desc") or "").strip() for im in usable)
    subjects = [im for im in (m.images or []) if im.get("role") == "主体"]
    if not subjects:
        return True
    return any(not str(im.get("desc") or "").strip() for im in subjects)


def scan(only: list | None = None) -> list[dict]:
    """按 ① 任务表格的行序（row_order），不是按 id。"""
    only_ids = None
    if only:
        only_ids = [str(x).strip() for x in only if str(x).strip()]
        if not only_ids:
            only_ids = None
    rows = taskmod.list_all(include_finished=True)
    out = []
    for t in rows:
        if t.get("status") == "trash":
            continue
        tid = t.get("id") or ""
        if only_ids is not None:
            if tid not in only_ids:
                continue
            out.append(t)
            continue
        if _needs(t):
            out.append(t)
    return out


def _record(tid: str, name: str, ok: bool, error: str) -> None:
    with _LOCK:
        _state["results"].append({
            "id": tid, "name": name or "", "ok": bool(ok), "error": error or "",
        })


def _run_one(tid: str) -> None:
    global _skip
    try:
        t = taskmod.get(tid)
    except OranteError:
        t = _meta.get(tid)
    name = (t or {}).get("name") or ""
    err = _precheck(t)
    if err:
        _record(tid, name, False, err)
        return

    written, total_imgs = _counts(tid)
    with _LOCK:
        _state["current"] = {
            "id": tid, "name": name, "written": written, "total": total_imgs,
        }
        _state["job_id"] = None
        _skip = False
    _emit()

    from .. import jobs as jobmod
    job = jobmod.start("describe", tid)
    with _LOCK:
        _state["job_id"] = job.id
        if _state["current"] is not None:
            _state["current"]["job_id"] = job.id

    while job.status in ("queued", "running"):
        with _LOCK:
            if _stop or _skip:
                try:
                    scheduler.cancel(job.id)
                except Exception:
                    pass
        prog = getattr(job, "progress", None) or {}
        written, total_imgs = _counts(tid)
        with _LOCK:
            cur = _state.get("current") or {}
            cur["written"] = int(prog.get("done") if prog.get("total") else written)
            cur["total"] = int(prog.get("total") or total_imgs)
            _state["current"] = cur
        _emit()
        time.sleep(0.35)

    if _stop:
        _record(tid, name, False, "已停止")
        return
    if _skip or job.status == "cancelled":
        _record(tid, name, False, "跳过")
        return
    if job.status == "done":
        _record(tid, name, True, "")
        return
    _record(tid, name, False, (job.error or "失败").split("（")[0].strip() or "失败")


def _summarize() -> str:
    results = _state.get("results") or []
    total = _state.get("total") or len(results)
    oks = sum(1 for r in results if r.get("ok"))
    fails = [r for r in results if not r.get("ok") and r.get("error") not in ("跳过", "已停止")]
    skips = [r for r in results if r.get("error") == "跳过"]
    parts = [f"{total} 个任务", f"成功 {oks}"]
    if fails:
        detail = "；".join(f"{r['id']}：{r['error']}" for r in fails[:6])
        parts.append(f"失败 {len(fails)}（{detail}）")
    else:
        parts.append("失败 0")
    if skips:
        parts.append(f"跳过 {len(skips)}")
    return " · ".join(parts)


def _run() -> None:
    global _pending, _stop, _skip
    try:
        while True:
            with _CV:
                if _stop or not _state["on"]:
                    break
                while _state["paused"] and not _stop:
                    _CV.wait(timeout=0.5)
                if _stop or not _state["on"]:
                    break
                if not _pending:
                    break
                tid = _pending.pop(0)
            _run_one(tid)
            with _LOCK:
                _state["done"] = (_state.get("total") or 0) - len(_pending)
                _state["current"] = None
                _state["job_id"] = None
                _skip = False
            _emit()
    except Exception as e:  # noqa: BLE001
        with _LOCK:
            _state["summary"] = f"队列异常：{type(e).__name__}: {e}"
    finally:
        with _LOCK:
            stopped = _stop
            _state["on"] = False
            _state["paused"] = False
            _state["current"] = None
            _state["job_id"] = None
            _pending.clear()
            if not _state.get("summary"):
                _state["summary"] = _summarize()
            text = _state["summary"]
            fails = [r for r in (_state.get("results") or [])
                     if not r.get("ok") and r.get("error") not in ("跳过", "已停止")]
        _release_owner()
        _emit()
        if not stopped:
            # emit 的第一个参数就是事件名 kind，toast 样式不能再塞一个 kind
            events.emit("toast", text=text, hint="" if not fails else "失败的任务可以点进去")


def start(only: list | None = None) -> dict:
    """空 only = 自动扫缺描述语的任务；给了 id 列表就按行序跑这些。"""
    global _pending, _meta, _stop, _skip, _thread
    foreign = _foreign_snapshot()
    if foreign:
        raise OranteError(
            f"队列正在 :{foreign['owner_port']} 窗口运行",
            "到那个窗口里操作，或等它停。",
        )
    items = scan(only)
    if not items:
        raise OranteError(
            "没有需要写描述语的任务",
            "当前工作区里的任务描述语都写好了，或还没准备。")
    with _LOCK:
        if _state["on"]:
            return snapshot()
        _stop = False
        _skip = False
        _pending = [t["id"] for t in items]
        _meta = {t["id"]: t for t in items}
        _state.update({
            "on": True,
            "paused": False,
            "done": 0,
            "total": len(_pending),
            "current": None,
            "results": [],
            "job_id": None,
            "summary": "",
        })
        _thread = threading.Thread(target=_run, name="desc-queue", daemon=True)
        _thread.start()
    _claim_owner()
    _emit()
    return snapshot()


def pause(on: bool = True) -> dict:
    """on=True：当前任务跑完就停；on=False：继续。"""
    with _CV:
        if not _state["on"]:
            return snapshot()
        _state["paused"] = bool(on)
        _CV.notify_all()
    _emit()
    return snapshot()


def skip() -> dict:
    """停掉当前任务的 describe job，进下一个。"""
    global _skip
    with _LOCK:
        if not _state["on"]:
            return snapshot()
        _skip = True
        jid = _state.get("job_id")
    if jid:
        try:
            scheduler.cancel(jid)
        except Exception:
            pass
    _emit()
    return snapshot()


def stop() -> dict:
    """清空队列、取消当前 job。"""
    global _stop, _skip
    with _CV:
        _stop = True
        _skip = False
        _state["paused"] = False
        jid = _state.get("job_id")
        _pending.clear()
        _CV.notify_all()
    if jid:
        try:
            scheduler.cancel(jid)
        except Exception:
            pass
    _emit()
    return snapshot()

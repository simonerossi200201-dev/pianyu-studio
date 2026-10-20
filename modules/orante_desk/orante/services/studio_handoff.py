# -*- coding: utf-8 -*-
"""规格 98：Flow 拒掉的图 → 排给本地模型（studio_pending）→ Flow 这一轮跑完后自动开「换成本地模型重生」。

为什么不在 Flow 那一轮里直接交给本地模型：同一个任务同一时间只能有一个账号在写 manifest
（jobs._check_run_account），两边各存一份会互相覆盖。所以等 Flow 的 Job 结束（scheduler._finish
里调 kick），再用规格 97 的换账号重生通道接着跑。

kick 可以重复调用（幂等）：条件不满足就什么都不做，登记一下，ticker 每 60 秒再试。
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from .. import config, events

PENDING_KEY = "studio.pending_tasks"
_LOCK = threading.Lock()
_TOLD_OFFLINE: dict[str, float] = {}
TELL_EVERY = 30 * 60          # 本地模型没开的提示，同一个任务半小时说一次


def _pending() -> list[str]:
    v = config.get(PENDING_KEY, []) or []
    return [str(x) for x in v if x] if isinstance(v, list) else []


def remember(task_id: str) -> None:
    if not task_id:
        return
    with _LOCK:
        cur = _pending()
        if task_id not in cur:
            cur.append(task_id)
            config.set(PENDING_KEY, cur, touch_rev=False)


def forget(task_id: str) -> None:
    with _LOCK:
        cur = _pending()
        if task_id in cur:
            cur.remove(task_id)
            config.set(PENDING_KEY, cur, touch_rev=False)


def pending_segs(m) -> list[int]:
    return [int(s["s"]) for s in (m.segments or [])
            if s.get("studio_pending") and s.get("status") == "blocked"]


def _busy(task_id: str) -> bool:
    from ..scheduler import scheduler
    for j in scheduler.list(active_only=True):
        if j.get("task_id") == task_id and j.get("kind") in ("generate", "regenerate"):
            return True
    return False


def _toast(text: str, hint: str = "", level: str = "warn") -> None:
    try:
        events.emit("toast", text=text, hint=hint, level=level)
    except Exception:  # noqa: BLE001
        pass


def kick(task_id: str, *, manual: bool = False, log=None) -> dict:
    """把这个任务排给本地模型的段交出去。返回 {started: job_id} 或 {skip: 原因}。"""
    from .. import accounts, tasks as taskmod
    from ..models import Manifest

    def _log(msg):
        if callable(log):
            try:
                log(msg)
            except Exception:  # noqa: BLE001
                pass

    if not task_id:
        return {"skip": "no_task"}
    if not manual and not bool(config.get("engine.studio.fallback", True)):
        return {"skip": "fallback_off"}
    if not accounts.studio_enabled():
        if bool(config.get("engine.studio.fallback", True)) or manual:
            config.set("engine.studio.enabled", True, touch_rev=False)
        else:
            return {"skip": "disabled"}
    try:
        t = taskmod.get(task_id)
    except Exception:  # noqa: BLE001
        forget(task_id)
        return {"skip": "no_task"}
    if t.get("archived") or not t.get("dir") or not Path(t["dir"]).is_dir():
        forget(task_id)
        return {"skip": "no_dir"}
    if _busy(task_id):
        remember(task_id)            # 那个 Job 结束时还会再 kick
        return {"skip": "busy"}
    try:
        m = Manifest.load(Path(t["dir"]))
    except Exception:  # noqa: BLE001
        return {"skip": "manifest"}
    segs = pending_segs(m)
    if not segs:
        forget(task_id)
        return {"skip": "none"}

    # Flow 拒图转交本地模型 LTX 时：自动按本地模型（同 Grok）规则重新分段并配图
    from . import split as splitmod
    try:
        sp_res = splitmod.resplit_task_for_engine(
            task_id,
            "studio",
            new_account_id=accounts.STUDIO_ID,
            log=_log,
            mark_studio_pending=True,
            studio_reason="Flow 拒图自动唤醒本地模型 LTX",
        )
        m = Manifest.load(Path(t["dir"]))
        segs = pending_segs(m) or [
            int(s["s"]) for s in (m.segments or []) if s.get("status") != "collected"
        ]
        if sp_res.get("resplit"):
            _log(f"  · {task_id} 已按本地模型(Grok)规则自动重新分段：{sp_res.get('old_segments')} 段 → {sp_res.get('segments')} 段")
    except Exception as e:  # noqa: BLE001
        _log(f"  · 转本地模型重分段提示：{e}")

    h = accounts.studio_health(force=True)
    alive = bool(h.get("ok") and h.get("comfy"))
    if not alive:
        from ..engines import studio as st
        launcher = str(config.get("engine.studio.launcher", "") or "").strip()
        if launcher.lower() in ("off", "none", "no", "0"):
            launcher = ""
        elif not launcher:
            launcher = st.DEFAULT_LAUNCHER
        can_auto_wake = bool(
            (launcher and Path(launcher).is_file())
            or Path(r"D:\0_LTX\modules\studio_gateway\server.py").is_file()
        )
        if not can_auto_wake:
            remember(task_id)
            now = time.time()
            if manual or now - _TOLD_OFFLINE.get(task_id, 0) > TELL_EVERY:
                _TOLD_OFFLINE[task_id] = now
                _toast(f"本地模型没开，{task_id} 有 {len(segs)} 段等着",
                       "双击 D:\\0_LTX\\启动本地算力网关.bat，开好后 1 分钟内会自动开始。")
            return {"skip": "offline", "segs": segs}
        # 有本地网关或启动脚本：照样开 Job，StudioEngine.connect 会自动唤醒本地模型 LTX（最多等 90 秒）
    from .. import jobs as jobmod
    from ..errors import OranteError
    try:
        j = jobmod.start("regenerate", task_id, segs=segs, account_id=accounts.STUDIO_ID)
    except OranteError as e:
        remember(task_id)
        _log(f"  · 转交本地模型先等等：{e.message}")
        return {"skip": "start_failed", "error": e.message}
    forget(task_id)
    jid = getattr(j, "id", None) or ""
    ss = "、".join(f"s{n:03d}" for n in segs)
    _toast(f"{task_id} 已按本地模型规则重分为 {len(segs)} 段并唤醒 LTX 出片", f"{ss}（原图，不重新生图）", "ok")
    return {"started": jid, "segs": segs}


def on_job_finished(job) -> None:
    """scheduler._finish 调：Flow 的 generate / regenerate 结束后转交。用户点了停止就不转。"""
    try:
        if getattr(job, "kind", "") not in ("generate", "regenerate") or not getattr(job, "task_id", None):
            return
        if getattr(job, "status", "") == "cancelled":
            return
        if str(getattr(job, "account_id", "") or "") == "STUDIO":
            return                     # 本地模型自己的那一轮结束：失败的不会再转回来，不用 kick
        kick(job.task_id)
    except Exception:  # noqa: BLE001
        pass


def tick() -> None:
    """ticker 每 60 秒：登记表里的任务再试一次（本地模型刚开、或之前被别的 Job 挡住）。"""
    for tid in list(_pending()):
        try:
            kick(tid)
        except Exception:  # noqa: BLE001
            pass

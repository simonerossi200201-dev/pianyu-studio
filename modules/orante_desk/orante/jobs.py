# -*- coding: utf-8 -*-
"""Job 注册表：kind → 函数。服务层 POST /api/jobs {kind,...} 会到这里。

每个 kind 的函数签名：fn(job, **args)，用 job.log() 写日志、job.check() 响应取消。
占账号的 kind 要在 ACCOUNT_BOUND 里登记，调度器据此排队。
"""
from __future__ import annotations

import copy
import threading

from . import events, tasks as taskmod
from .errors import OranteError
from .scheduler import Job, scheduler

REGISTRY: dict[str, callable] = {}
ACCOUNT_BOUND: set[str] = set()

# Job 类实际在 scheduler.py。规格 14 要的进度字段挂在这里打补丁，
# 这样 /api/jobs 的 to_dict 会带上 progress，不必改调度器其它逻辑。
_MISSING = object()
_orig_job_init = Job.__init__
_orig_job_to_dict = Job.to_dict


def _job_init(self, *args, **kwargs):
    _orig_job_init(self, *args, **kwargs)
    self.progress = {
        "phase": "", "done": 0, "total": 0, "current": "", "items": [],
    }
    self._progress_lock = threading.RLock()


def _job_set_progress(self, **kw):
    """合并更新进度。线程安全，只写内存，不落盘。

    关键字：phase / done / total / current 覆盖对应字段；
    items=list 整表替换；item=dict 往 items 末尾追加。
    """
    with self._progress_lock:
        p = getattr(self, "progress", None)
        if p is None:
            p = {"phase": "", "done": 0, "total": 0, "current": "", "items": []}
            self.progress = p
        item = kw.pop("item", _MISSING)
        items = kw.pop("items", _MISSING)
        for k in ("phase", "done", "total", "current"):
            if k in kw:
                p[k] = kw[k]
        if items is not _MISSING:
            p["items"] = list(items or [])
        if item is not _MISSING and item is not None:
            p.setdefault("items", []).append(item)
    events.emit("job.state", job=self.to_dict())


def _job_to_dict(self):
    d = _orig_job_to_dict(self)
    lock = getattr(self, "_progress_lock", None)
    prog = getattr(self, "progress", None)
    if lock is not None and prog is not None:
        with lock:
            d["progress"] = copy.deepcopy(prog)
    else:
        d["progress"] = prog
    return d


Job.__init__ = _job_init
Job.set_progress = _job_set_progress
Job.to_dict = _job_to_dict


def register(kind: str, account_bound: bool = False):
    def deco(fn):
        REGISTRY[kind] = fn
        if account_bound:
            ACCOUNT_BOUND.add(kind)
        return fn
    return deco


# 归档任务只读：这些 kind 会改片子 / 配图 / 描述语，解冻之前不许跑。
_ARCHIVE_BLOCK = {
    "generate", "regenerate", "refill", "expand", "upscale", "upscale_tail",
    "card_swap", "arrange", "prepare", "describe", "check",
}


_GEN_RUN_KINDS = ("generate", "regenerate")


def _check_run_account(kind: str, task_id: str, t: dict, account_id: str | None) -> None:
    """重生可以临时换账号（比如 Flow 的任务改用 Grok 重生几段）；本地模型也可以直接当任务账号。

    1. 换的账号要存在、启用着、是能出视频的引擎（flow / grok）。
    2. 同一个任务不能同时被两个账号出片 / 重生——两边各拿一份 manifest，后存的会把先存的盖掉。
       同一个账号的照旧排队（调度器本来就一个账号一个一个来）。"""
    from . import accounts
    if kind == "regenerate" and account_id and account_id != t.get("account_id"):
        a = accounts.get(account_id)
        name = a.get("name") or account_id
        if accounts.is_virtual(account_id) and not accounts.studio_enabled():
            raise OranteError("本地模型（Studio Zero）没启用", "到 设置 → 本地模型 里打开它，再选它重生。")
        if a.get("enabled") in (0, False, "0"):
            raise OranteError(f"账号「{name}」已停用", "到「账号」里启用它，或换一个账号重生。")
        if str(a.get("engine") or "").lower() not in ("flow", "grok", "studio"):
            raise OranteError(f"账号「{name}」不能出视频", "重生只能选 Flow、Grok 账号或本地模型。")
    if kind == "generate" and accounts.is_virtual(account_id or "") and not accounts.studio_enabled():
        raise OranteError("本地模型（Studio Zero）没启用",
                          "到「账号」抽屉最下面的本地模型那一栏点「启用」，再开始生成。")
    for j in scheduler.list(active_only=True):
        if j.get("task_id") != task_id or j.get("kind") not in _GEN_RUN_KINDS:
            continue
        other = j.get("account_id") or ""
        if other != (account_id or ""):
            try:
                oname = accounts.get(other).get("name") or other
            except Exception:
                oname = other or "别的账号"
            raise OranteError(
                f"这个任务正在用「{oname}」出片 / 重生",
                "同一个任务不能两个账号同时跑。等它跑完（或先点停止）再换账号重生。")


def start(kind: str, task_id: str | None, **args):
    fn = REGISTRY.get(kind)
    if not fn:
        raise OranteError(f"未知的任务类型 {kind}", f"可用: {', '.join(sorted(REGISTRY))}")
    account_id = args.pop("account_id", None)
    if task_id and kind in _ARCHIVE_BLOCK:
        taskmod.ensure_writable(task_id)
    if kind in ACCOUNT_BOUND:
        if not task_id:
            raise OranteError(f"{kind} 需要 task_id")
        t = taskmod.get(task_id)
        if not account_id:
            if kind in ("expand", "refill", "upscale", "upscale_tail", "card_swap"):
                from .services.expand import resolve_account_id
                account_id = resolve_account_id(t)
            else:
                account_id = t["account_id"]
        if kind in ("generate", "regenerate"):
            _check_run_account(kind, task_id, t, account_id)
    return scheduler.submit(kind, fn, task_id=task_id, account_id=account_id, **args)


# ---- 内建的几个 kind（其余在 services/* 里 register） ----
@register("noop")
def _noop(job, seconds: float = 3):
    """测试用：睁眼数几秒。"""
    for i in range(int(seconds)):
        job.check()
        job.log(f"tick {i + 1}/{int(seconds)}")
        job.sleep(1)
    return {"ticks": int(seconds)}


def load_all_services() -> None:
    """import 所有 services 让它们完成 register。"""
    from .services import prepare, generate, describe, arrange, expand, review, tails, bench, desc_cards  # noqa: F401

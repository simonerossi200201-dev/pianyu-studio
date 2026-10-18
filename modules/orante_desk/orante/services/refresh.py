# -*- coding: utf-8 -*-
"""全局刷新：重扫当前任务目录、可选深度清缓存并探账号。

浅层只读盘、不烧 ffmpeg。深度才清 ahash / 进程内 _CACHE、重算缺的首帧、
再探一遍每个账号的 Chrome 端口。
"""
from __future__ import annotations

from pathlib import Path

from .. import accounts, tasks as taskmod
from ..errors import OranteError


def _task_dir(tid: str) -> Path | None:
    """任务没准备或目录不在了 → None，刷新整体不因此失败。"""
    try:
        t = taskmod.get(tid)
    except OranteError:
        return None
    d = t.get("dir")
    if not d:
        return None
    p = Path(d)
    return p if p.is_dir() else None


def rescan_files(tid: str) -> int:
    """读 clips/、images/ 顶层文件的存在性、大小、mtime。返回扫到的文件数。

    不吃任何缓存：每次都 os.stat。隐藏子目录（_弃用 等）不进这次计数。
    """
    d = _task_dir(tid)
    if d is None:
        return 0
    n = 0
    for folder in ("clips", "images"):
        p = d / folder
        if not p.is_dir():
            continue
        try:
            names = list(p.iterdir())
        except OSError:
            continue
        for f in names:
            if not f.is_file():
                continue
            try:
                f.stat()
            except OSError:
                continue
            n += 1
    return n


def _frames_stale(tid: str) -> int:
    """当前任务有多少段的片子 size/mtime 对不上或还没首帧哈希。"""
    from ..models import Manifest
    from . import review, verify
    d = _task_dir(tid)
    if d is None:
        return 0
    try:
        m = Manifest.load(d)
    except Exception:
        return 0
    n = 0
    for sg in m.segments:
        path = review.clip_path(d, m, sg)
        if not path or not path.is_file():
            continue
        tks = sg.get("takes") or []
        tk = tks[-1] if tks else {}
        try:
            st = path.stat()
        except OSError:
            continue
        if verify._tk_stale(tk, st) or not tk.get("frame_ahash"):
            n += 1
    return n


def _deep_verify(tid: str) -> int:
    """清完缓存后跑一次轻量校验，把缺的首帧补上。返回重算了几段。"""
    from . import review, verify
    d = _task_dir(tid)
    if d is None:
        return 0
    from ..models import Manifest
    try:
        m = Manifest.load(d)
    except Exception:
        return 0
    n = _frames_stale(tid)
    if n == 0 and not verify.needs_recompute(m, d):
        return 0
    try:
        results = verify.light_check(m, d)
        with review._io_lock(tid):
            m2 = Manifest.load(d)
            verify.apply_results(m2, results)
            m2.save(d)
    except Exception:
        return n
    return n


def run(deep: bool = False, tid: str | None = None) -> dict:
    """POST /api/refresh 的业务。

    浅层：只 stat 文件（存在性 + mtime + 大小），不探端口、不算哈希、
    不问多模态、不重建图库。深度才清缓存、探端口、重算首帧。
    """
    import time as _time
    t0 = _time.perf_counter()
    tid = (tid or "").strip() or None
    files_rescanned = rescan_files(tid) if tid else 0
    ahash_dropped = 0
    frames = 0

    if deep:
        from . import checks, verify
        ahash_dropped = verify.prune_ahash_cache()
        verify.clear_mem_cache()
        checks.clear_mem_cache()
        if tid:
            frames = _deep_verify(tid)

    # 浅层不探 Chrome 端口：每个死端口要卡 0.4s，账号一多就远超 300ms
    accs = accounts.list_all(probe=bool(deep))
    alive = sum(1 for a in accs if a.get("alive"))
    tasks_n = len(taskmod.list_all())
    ms = int((_time.perf_counter() - t0) * 1000)
    return {
        "tasks": tasks_n,
        "files_rescanned": files_rescanned,
        "ahash_dropped": ahash_dropped,
        "accounts_alive": f"{alive}/{len(accs)}",
        "frames_recomputed": frames,
        "deep": bool(deep),
        "ms": ms,
    }

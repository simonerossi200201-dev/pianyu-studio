# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q152-E · AI 点跟踪：图层上的设置、后端（TAPNext++ / 测试用模板匹配）、跟踪任务。

视频片段上存一个 dict ``tracking``::

    {
      "session": "t1a2b3c4d5",                 # track_store 会话（结果缓存）
      "model": "track-tapnextpp-512",
      "points": [{"id": 1, "name": "点 1", "color": "#4FC3F7", "enabled": true}],
      "queries": {"120": [{"pt": 1, "x": 0.41, "y": 0.52}]},     # 源帧号 → 在这帧点下的位置（源帧归一化 0–1）
      "bindings": [ … track_bind.normalize_binding … ]          # 用这条运动数据驱动的目标（可「更新全部绑定」）
    }

一个点可以在多帧上各点一次（跟丢了在后面再点一下）：每一帧归离它最近的那次点选，
从那次点选往前 / 往后各跑一段，互不覆盖。对象跟踪不在这里存数据：直接用 AI 抠像的 mask（见 motion.object_motion）。
"""
from __future__ import annotations

import os
import threading
import time

import numpy as np

from cutora.core.log import get_logger
from cutora.fx import track_store as T

_LOG = get_logger("cutora.fx.tracking")
_MODEL_LOCK = threading.RLock()
_LOADED: dict = {}

DEFAULTS = {"session": "", "model": "track-tapnextpp-512", "points": [], "queries": {}, "bindings": []}
POINT_COLORS = ("#4FC3F7", "#F2B33D", "#81C784", "#EF7A7A", "#CE93D8", "#FFB74D", "#4DB6AC", "#F06292")
MAX_POINTS = 8


# ───────────────────────────── 设置 ─────────────────────────────

def spec(host) -> dict:
    raw = host.get("tracking") if isinstance(host, dict) else getattr(host, "tracking", None)
    return normalize(raw)


def normalize(raw) -> dict:
    out = {"session": "", "model": DEFAULTS["model"], "points": [], "queries": {}, "bindings": []}
    if not isinstance(raw, dict):
        return out
    out["session"] = str(raw.get("session") or "")
    out["model"] = str(raw.get("model") or DEFAULTS["model"])
    seen = set()
    for p in raw.get("points") or []:
        if not isinstance(p, dict):
            continue
        try:
            pid = int(p.get("id"))
        except (TypeError, ValueError):
            continue
        if pid in seen or pid <= 0:
            continue
        seen.add(pid)
        out["points"].append({"id": pid, "name": str(p.get("name") or ("点 %d" % pid)),
                              "color": str(p.get("color") or POINT_COLORS[(pid - 1) % len(POINT_COLORS)]),
                              "enabled": bool(p.get("enabled", True))})
    for k, lst in (raw.get("queries") or {}).items():
        try:
            fi = int(k)
        except (TypeError, ValueError):
            continue
        clean = []
        used = set()
        for q in lst or []:
            if not isinstance(q, dict):
                continue
            try:
                pid = int(q.get("pt"))
                x = max(0.0, min(1.0, float(q.get("x"))))
                y = max(0.0, min(1.0, float(q.get("y"))))
            except (TypeError, ValueError):
                continue
            if pid not in seen or pid in used:
                continue
            used.add(pid)
            clean.append({"pt": pid, "x": x, "y": y})
        if clean and fi >= 0:
            out["queries"][str(fi)] = clean
    from cutora.fx.track_bind import normalize_binding
    for b in raw.get("bindings") or []:
        nb = normalize_binding(b)
        if nb is not None:
            out["bindings"].append(nb)
    return out


def is_empty(sp: dict) -> bool:
    return not (sp["session"] or sp["points"] or sp["queries"] or sp["bindings"])


def new_point(sp: dict) -> dict:
    used = {p["id"] for p in sp["points"]}
    pid = 1
    while pid in used:
        pid += 1
    return {"id": pid, "name": "点 %d" % pid, "color": POINT_COLORS[(pid - 1) % len(POINT_COLORS)], "enabled": True}


def queries_at(sp: dict, frame_idx: int) -> list:
    return list(sp["queries"].get(str(int(frame_idx)), []))


def query_frames_of(sp: dict, point_id: int) -> list:
    out = []
    for k, lst in sp["queries"].items():
        if any(int(q["pt"]) == int(point_id) for q in lst):
            out.append(int(k))
    return sorted(out)


def plan_runs(sp: dict, frame_range: tuple, direction: str = "all") -> list:
    """把点选拆成若干次「从某帧出发、往某方向跑到某帧」的跟踪。

    返回 [{"start": 帧, "dir": "forward"/"backward", "end": 帧, "points": {点 id: (x, y, 这个点自己的终点)}}]。
    每帧归离它最近的那次点选（一样近归前面那次）。"""
    a, b = int(frame_range[0]), int(frame_range[1])
    enabled = {p["id"] for p in sp["points"] if p.get("enabled", True)}
    runs: dict = {}
    for pid in sorted(enabled):
        qs = [q for q in query_frames_of(sp, pid) if a <= q <= b]
        for i, q in enumerate(qs):
            pos = next(x for x in sp["queries"][str(q)] if int(x["pt"]) == pid)
            nxt = qs[i + 1] if i + 1 < len(qs) else None
            prv = qs[i - 1] if i > 0 else None
            fwd_end = b if nxt is None else (q + nxt) // 2
            bwd_end = a if prv is None else (prv + q) // 2 + 1
            if direction in ("forward", "all") and fwd_end >= q:
                r = runs.setdefault((q, "forward"), {"start": q, "dir": "forward", "end": q, "points": {}})
                r["points"][pid] = (pos["x"], pos["y"], fwd_end)
                r["end"] = max(r["end"], fwd_end)
            if direction in ("backward", "all") and bwd_end < q:
                r = runs.setdefault((q, "backward"), {"start": q, "dir": "backward", "end": q, "points": {}})
                r["points"][pid] = (pos["x"], pos["y"], bwd_end)
                r["end"] = min(r["end"], bwd_end)
    return [runs[k] for k in sorted(runs, key=lambda k: (k[0], k[1]))]


def run_length(run: dict) -> int:
    return abs(int(run["end"]) - int(run["start"])) + 1


# ───────────────────────────── 后端 ─────────────────────────────

def backend_name() -> str:
    """CUTORA_TRACK_BACKEND=fake 走模板匹配（测试 / 没有模型时的演示）。"""
    return str(os.environ.get("CUTORA_TRACK_BACKEND") or "tapnext").strip().lower()


class TapNextBackend:
    """TAPNext++（Google DeepMind，Apache 2.0）在线点跟踪：一帧一帧喂，所有点一次跟。"""

    name = "tapnext"

    def __init__(self, model_id: str = "track-tapnextpp-512", model_path: str | None = None,
                 device: str | None = None, input_resolution: int | None = None):
        self.model_id = model_id
        self.model_path = model_path
        self.device_pref = device
        self.input_resolution = input_resolution
        self.model = None
        self.device = "cpu"
        self.load_seconds = 0.0

    def _resolve(self):
        if self.model_path:
            return self.model_path, int(self.input_resolution or 256)
        from cutora.asr.models import get_model, model_folder, verify_model
        row = get_model(self.model_id)
        if row is None:
            raise RuntimeError("未登记的模型 %s" % self.model_id)
        if verify_model(row) != "ready":
            raise RuntimeError("模型 %s 未下载或校验失败，请到「设置 → 模型」下载" % self.model_id)
        path = os.path.join(model_folder(row), str(row.get("weight_file")))
        return path, int(self.input_resolution or row.get("input_resolution") or 256)

    def ensure_loaded(self):
        with _MODEL_LOCK:
            hit = _LOADED.get(self.model_id)
            if hit is not None:
                self.model, self.device = hit
                return
            t0 = time.perf_counter()
            from cutora.fx.segment import pick_device
            from cutora.third_party.tapnext.tapnextpp import TAPNextPP
            path, res = self._resolve()
            dev, _dt, _note = pick_device()
            dev = self.device_pref or dev
            self.model = TAPNextPP.from_checkpoint(path, device=dev, input_resolution=res)
            self.device = dev
            _LOADED[self.model_id] = (self.model, dev)
            self.load_seconds = time.perf_counter() - t0
            _LOG.info("TAPNext++ 已加载 %s device=%s 输入 %d 用时 %.1fs", self.model_id, dev, res, self.load_seconds)

    def track(self, frames, query_xy, on_frame, cancel=None) -> int:
        """frames: 可迭代 (源帧号, rgb)；query_xy: (N, 2) 第一帧上的像素坐标。
        on_frame(源帧号, xy (N, 2), 可见概率 (N,)) 返回 False 提前停。"""
        self.ensure_loaded()
        state = None
        n = 0
        q = np.asarray(query_xy, dtype=np.float32).reshape(-1, 2)
        for src_idx, rgb in frames:
            if cancel is not None and cancel.is_set():
                break
            if state is None:
                xy, _vis, prob, state = self.model.track_frame(rgb, query_points_xy=q)
                xy = q.copy()                      # 查询帧本身：就用用户点的位置
                prob = np.ones(len(q), np.float32)
            else:
                xy, _vis, prob, state = self.model.track_frame(rgb, state=state)
            n += 1
            if on_frame(int(src_idx), np.asarray(xy, np.float32), np.asarray(prob, np.float32)) is False:
                break
        return n


class FakePointBackend:
    """模板匹配（归一化相关）：上一帧位置附近找最像的那块。确定性，测试 / 演示用。"""

    name = "fake"

    def __init__(self, patch: int = 10, search: int = 24):
        self.patch = int(patch)
        self.search = int(search)

    def ensure_loaded(self):
        return None

    def _gray(self, rgb):
        import cv2
        return cv2.cvtColor(np.ascontiguousarray(rgb), cv2.COLOR_RGB2GRAY)

    def track(self, frames, query_xy, on_frame, cancel=None) -> int:
        import cv2
        pts = np.asarray(query_xy, dtype=np.float32).reshape(-1, 2).copy()
        tmpls = None
        n = 0
        r, s = self.patch, self.search
        for src_idx, rgb in frames:
            if cancel is not None and cancel.is_set():
                break
            g = self._gray(rgb)
            h, w = g.shape
            prob = np.ones(len(pts), np.float32)
            if tmpls is None:
                tmpls = []
                for (x, y) in pts:
                    xi, yi = int(round(x)), int(round(y))
                    x0, y0 = max(0, xi - r), max(0, yi - r)
                    tmpls.append((g[y0:yi + r + 1, x0:xi + r + 1].copy(), xi - x0, yi - y0))
            else:
                for i, (x, y) in enumerate(pts):
                    tp, ox, oy = tmpls[i]
                    xi, yi = int(round(x)), int(round(y))
                    x0, y0 = max(0, xi - r - s), max(0, yi - r - s)
                    x1, y1 = min(w, xi + r + s + 1), min(h, yi + r + s + 1)
                    win = g[y0:y1, x0:x1]
                    if win.shape[0] < tp.shape[0] or win.shape[1] < tp.shape[1] or tp.size == 0:
                        prob[i] = 0.0
                        continue
                    res = cv2.matchTemplate(win, tp, cv2.TM_CCOEFF_NORMED)
                    _mn, mx, _ml, loc = cv2.minMaxLoc(res)
                    if not np.isfinite(mx):
                        mx = 0.0
                    # 亚像素：峰值两侧抛物线插值
                    lx, ly = float(loc[0]), float(loc[1])
                    rx, ry = loc
                    if 0 < rx < res.shape[1] - 1:
                        l_, c_, r_ = res[ry, rx - 1], res[ry, rx], res[ry, rx + 1]
                        den = l_ - 2 * c_ + r_
                        if abs(den) > 1e-9:
                            lx += float(np.clip(0.5 * (l_ - r_) / den, -0.5, 0.5))
                    if 0 < ry < res.shape[0] - 1:
                        u_, c_, d_ = res[ry - 1, rx], res[ry, rx], res[ry + 1, rx]
                        den = u_ - 2 * c_ + d_
                        if abs(den) > 1e-9:
                            ly += float(np.clip(0.5 * (u_ - d_) / den, -0.5, 0.5))
                    pts[i] = (x0 + lx + ox, y0 + ly + oy)
                    prob[i] = float(max(0.0, min(1.0, mx)))
                    if mx > 0.8:
                        # 匹配得好就换成这一帧的样子（跟得住慢慢转 / 慢慢缩放的东西）
                        nx, ny = int(round(pts[i][0])), int(round(pts[i][1]))
                        tx0, ty0 = max(0, nx - r), max(0, ny - r)
                        new_t = g[ty0:ny + r + 1, tx0:nx + r + 1]
                        if new_t.shape == tp.shape:
                            tmpls[i] = (new_t.copy(), nx - tx0, ny - ty0)
            n += 1
            if on_frame(int(src_idx), pts.copy(), prob) is False:
                break
        return n

    def release(self):
        return None


def make_backend(model_id: str = "track-tapnextpp-512"):
    if backend_name() == "fake":
        return FakePointBackend()
    return TapNextBackend(model_id)


def release_models() -> None:
    with _MODEL_LOCK:
        _LOADED.clear()
    try:
        import gc
        gc.collect()
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


# ───────────────────────────── 任务 ─────────────────────────────

def track(backend, path: str, sp: dict, frame_range: tuple, direction: str = "all",
          on_progress=None, cancel=None) -> dict:
    """按 plan_runs 跑完，结果写 track_store。on_progress(done, total, 源帧号, 这一帧可见比例)。

    返回 {"frames": 处理帧数, "runs": n, "seconds": s, "cancelled": bool, "lost": 有点跟丢的帧数}。"""
    from cutora.fx.matte_jobs import FrameFeed
    t0 = time.perf_counter()
    runs = plan_runs(sp, frame_range, direction)
    if not runs:
        raise RuntimeError("这条片段的范围里没有跟踪点：先在画面上点一个点")
    total = sum(run_length(r) for r in runs)
    feed = FrameFeed(path)
    done = {"n": 0, "lost": 0, "cancelled": False}
    lost_frames = set()
    try:
        w, h = feed.w, feed.h
        if not w or not h:
            probe = feed.read_at(int(runs[0]["start"]))
            if probe is None:
                raise RuntimeError("读不到第 %d 帧" % runs[0]["start"])
            h, w = probe.shape[:2]
        with T.Session(sp["session"], fps=feed.fps, src_w=w, src_h=h, model=sp.get("model")) as sess:
            for run in runs:
                if cancel is not None and cancel.is_set():
                    done["cancelled"] = True
                    break
                pids = sorted(run["points"])
                q = np.array([[run["points"][p][0] * w, run["points"][p][1] * h] for p in pids], np.float32)
                limits = [run["points"][p][2] for p in pids]
                fwd = run["dir"] == "forward"
                frames = feed.iter_forward(run["start"], run["end"]) if fwd \
                    else feed.iter_backward(run["start"], run["end"])

                def _on_frame(fi, xy, prob, _pids=pids, _lim=limits, _fwd=fwd):
                    vis_n = 0
                    for k, pid in enumerate(_pids):
                        inside = fi <= _lim[k] if _fwd else fi >= _lim[k]
                        if not inside:
                            continue
                        x = float(np.clip(xy[k][0] / max(1, w), 0.0, 1.0))
                        y = float(np.clip(xy[k][1] / max(1, h), 0.0, 1.0))
                        sess.write(fi, pid, x, y, float(prob[k]))
                        if prob[k] >= T.VISIBLE:
                            vis_n += 1
                        else:
                            lost_frames.add(fi)
                    done["n"] += 1
                    if on_progress is not None:
                        on_progress(done["n"], total, int(fi), vis_n / float(max(1, len(_pids))))
                    if cancel is not None and cancel.is_set():
                        done["cancelled"] = True
                        return False
                    return True

                backend.track(frames, q, _on_frame, cancel=cancel)
                if done["cancelled"]:
                    break
    finally:
        feed.close()
    secs = time.perf_counter() - t0
    _LOG.info("AI 跟踪 %d 段 %d 帧 用时 %.1fs%s", len(runs), done["n"], secs, "（已取消）" if done["cancelled"] else "")
    return {"frames": done["n"], "runs": len(runs), "seconds": secs, "cancelled": done["cancelled"],
            "lost": len(lost_frames)}


# ───────────────────────────── 时间线条 / 跳转 ─────────────────────────────

def coverage_runs(clip) -> list:
    """[(t0, t1, lost)] 时间线秒：跟踪结果覆盖的段，lost = 这段有点被挡 / 跟丢（可见比例 < 1）。"""
    from cutora.fx.ai_matte import source_frame_to_timeline
    sp = clip.get("tracking") if isinstance(clip, dict) else getattr(clip, "tracking", None)
    if not isinstance(sp, dict) or not sp.get("session"):
        return []
    sp = normalize(sp)
    data = T.load(sp["session"])
    if not data:
        return []
    pts = [p["id"] for p in sp["points"] if p.get("enabled", True)]
    cov = T.coverage(sp["session"], pts)
    if not cov:
        return []
    fps = float(data.get("fps") or 30.0)
    frames = sorted(cov)
    runs = []
    a = prev = frames[0]
    bad = cov[a] < 1.0
    for f in frames[1:]:
        b = cov[f] < 1.0
        if f == prev + 1 and b == bad:
            prev = f
            continue
        runs.append((a, prev, bad))
        a = prev = f
        bad = b
    runs.append((a, prev, bad))
    return [(source_frame_to_timeline(clip, x, fps), source_frame_to_timeline(clip, y + 1, fps), lo)
            for x, y, lo in runs]


def lost_frames(sp: dict) -> list:
    if not sp.get("session"):
        return []
    pts = [p["id"] for p in sp["points"] if p.get("enabled", True)]
    return sorted(f for f, v in T.coverage(sp["session"], pts).items() if v < 1.0)


def neighbour_lost_frame(sp: dict, current: int, direction: int):
    lows = lost_frames(sp)
    if not lows:
        return None
    starts = [f for i, f in enumerate(lows) if i == 0 or lows[i - 1] != f - 1]
    if direction > 0:
        cand = [f for f in starts if f > current]
        return cand[0] if cand else None
    cand = [f for f in starts if f < current]
    return cand[-1] if cand else None

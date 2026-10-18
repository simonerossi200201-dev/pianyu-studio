# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q152-E · 点跟踪结果缓存。和 AI 抠像缓存放一起（``<user_cache>/matte`` 的兄弟目录 ``track``）。

一个会话一个 JSON::

    {"fps": 30.0, "src_w": 1920, "src_h": 1080, "model": "track-tapnextpp-512",
     "frames": {"123": {"1": [0.412, 0.537, 0.98], "2": [...]}}}     # 源帧号 → 点 id → [x, y, 可见概率]（x / y 归一化）

工程里只存会话 id 和查询点；缓存丢了重跟一遍就行。日志只记帧数和耗时。
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid

_LOCK = threading.RLock()
_CACHE: dict = {}            # sid -> ((mtime_ns, size), data)
VISIBLE = 0.5                # 可见概率阈值：低于它算「跟丢 / 被挡」


def root() -> str:
    env = os.environ.get("CUTORA_TRACK_CACHE")
    if env:
        return os.path.abspath(env)
    env = os.environ.get("CUTORA_MATTE_CACHE")
    if env:
        return os.path.join(os.path.dirname(os.path.abspath(env)), "track")
    from cutora.core.paths import user_cache_dir
    return os.path.join(user_cache_dir(), "track")


def new_session_id() -> str:
    return "t" + uuid.uuid4().hex[:10]


def _path(sid: str) -> str:
    sid = str(sid or "").strip()
    if not sid or any(ch in sid for ch in "/\\.") or len(sid) > 40:
        raise ValueError("非法跟踪会话 id")
    return os.path.join(root(), sid + ".json")


def load(sid: str) -> dict | None:
    if not sid:
        return None
    try:
        p = _path(sid)
        st = os.stat(p)
    except (OSError, ValueError):
        with _LOCK:
            _CACHE.pop(sid, None)
        return None
    key = (st.st_mtime_ns, st.st_size)
    with _LOCK:
        hit = _CACHE.get(sid)
        if hit is not None and hit[0] == key:
            return hit[1]
    try:
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    data.setdefault("frames", {})
    with _LOCK:
        _CACHE[sid] = (key, data)
    return data


def version(sid: str):
    try:
        st = os.stat(_path(sid))
    except (OSError, ValueError):
        return None
    return (st.st_mtime_ns, st.st_size)


def points_at(sid: str, frame_idx: int) -> dict:
    """{点 id: (x, y, 可见概率)}（归一化坐标）。"""
    data = load(sid) or {}
    rec = (data.get("frames") or {}).get(str(int(frame_idx))) or {}
    out = {}
    for k, v in rec.items():
        try:
            out[int(k)] = (float(v[0]), float(v[1]), float(v[2]))
        except (TypeError, ValueError, IndexError):
            continue
    return out


def frames(sid: str) -> list:
    data = load(sid) or {}
    try:
        return sorted(int(k) for k in (data.get("frames") or {}))
    except ValueError:
        return []


def coverage(sid: str, point_ids=None) -> dict:
    """{帧号: 这一帧可见点的比例 0–1}（只算 point_ids；None = 全部）。给时间线画条用。"""
    data = load(sid) or {}
    want = None if point_ids is None else {str(int(p)) for p in point_ids}
    out = {}
    for k, rec in (data.get("frames") or {}).items():
        items = [v for pid, v in rec.items() if want is None or pid in want]
        if not items:
            continue
        vis = sum(1 for v in items if float(v[2]) >= VISIBLE)
        try:
            out[int(k)] = vis / float(len(items))
        except ValueError:
            continue
    return out


def invalidate(sid: str | None = None) -> None:
    with _LOCK:
        if sid is None:
            _CACHE.clear()
        else:
            _CACHE.pop(sid, None)


class Session:
    """写入端：``with Session(sid, fps=…, src_w=…, src_h=…) as s: s.write(frame, pid, x, y, p)``。"""

    FLUSH_EVERY = 120

    def __init__(self, sid: str | None = None, *, fps: float | None = None, src_w: int | None = None,
                 src_h: int | None = None, model: str | None = None):
        self.sid = sid or new_session_id()
        self.path = _path(self.sid)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        cur = load(self.sid)
        self.data = json.loads(json.dumps(cur)) if cur else {"frames": {}}
        if fps:
            self.data["fps"] = float(fps)
        if src_w and src_h:
            self.data["src_w"], self.data["src_h"] = int(src_w), int(src_h)
        if model:
            self.data["model"] = str(model)
        self.data.setdefault("created", time.time())
        self._dirty = True
        self._n = 0

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.flush()
        return False

    def write(self, frame_idx: int, point_id: int, x: float, y: float, vis: float) -> None:
        rec = self.data["frames"].setdefault(str(int(frame_idx)), {})
        rec[str(int(point_id))] = [round(float(x), 6), round(float(y), 6), round(float(vis), 3)]
        self._dirty = True
        self._n += 1
        if self._n % self.FLUSH_EVERY == 0:
            self.flush()

    def remove_point(self, point_id: int) -> int:
        n = 0
        key = str(int(point_id))
        for k in list(self.data["frames"]):
            rec = self.data["frames"][k]
            if key in rec:
                rec.pop(key)
                n += 1
            if not rec:
                self.data["frames"].pop(k)
        self._dirty = True
        return n

    def clear(self) -> None:
        self.data["frames"] = {}
        self._dirty = True
        self.flush()

    def flush(self) -> None:
        if not self._dirty:
            return
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, separators=(",", ":"))
        os.replace(tmp, self.path)
        self._dirty = False
        invalidate(self.sid)


def delete_session(sid: str) -> bool:
    try:
        p = _path(sid)
    except ValueError:
        return False
    invalidate(sid)
    try:
        os.remove(p)
        return True
    except OSError:
        return False

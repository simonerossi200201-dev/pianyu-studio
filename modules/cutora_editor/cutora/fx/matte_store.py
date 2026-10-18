# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q152-D · AI 抠像的结果缓存（每个会话一个文件夹，每帧每对象一张 8 位 PNG）。

布局::

    <root>/<session_id>/index.json        {"fps", "src_w", "src_h", "model", "frames": {"123": {"score": 0.93, "objs": [1, 2]}}}
    <root>/<session_id>/f000123_o1.png    对象 1 在源第 123 帧的 alpha（0–255）

root 默认 ``user_cache_dir()/matte``，可用环境变量 ``CUTORA_MATTE_CACHE`` 覆盖。
工程文件里只存 session_id 和提示点；缓存丢了可以重新传播。

读的一侧（预览 / 导出进程）只用 :func:`read_alpha` / :func:`coverage`，按文件修改时间缓存 index，
按 LRU 缓存最近解码的几十张 mask。写的一侧用 :class:`Session`。
不记录任何画面内容到日志，只记帧数和耗时。
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from collections import OrderedDict

import numpy as np

_LOCK = threading.RLock()
_INDEX_CACHE: dict = {}          # sid -> (mtime_ns, size, index dict)
_MASK_CACHE: "OrderedDict[tuple, np.ndarray]" = OrderedDict()
_MASK_CACHE_MAX = 48
MASK_MAX_EDGE = 1920        # 缓存的 mask 最长边上限：4K 素材存 1920，渲染时按图层尺寸放大（边缘再经羽化 / 精修）


def root() -> str:
    env = os.environ.get("CUTORA_MATTE_CACHE")
    if env:
        return os.path.abspath(env)
    from cutora.core.paths import user_cache_dir
    return os.path.join(user_cache_dir(), "matte")


def new_session_id() -> str:
    return "m" + uuid.uuid4().hex[:10]


def session_dir(sid: str) -> str:
    sid = str(sid or "").strip()
    if not sid or any(ch in sid for ch in "/\\.."):
        raise ValueError("非法 session id")
    return os.path.join(root(), sid)


def _index_path(sid: str) -> str:
    return os.path.join(session_dir(sid), "index.json")


def _mask_name(frame_idx: int, obj_id: int) -> str:
    return "f%06d_o%d.png" % (int(frame_idx), int(obj_id))


def mask_dims(h: int, w: int, max_edge: int | None = None) -> tuple:
    """源帧 (h, w) → 缓存 mask 的 (h, w)：最长边不超过 MASK_MAX_EDGE，保持比例。"""
    m = int(max_edge or MASK_MAX_EDGE)
    h, w = int(h), int(w)
    long_edge = max(h, w)
    if long_edge <= m or long_edge <= 0:
        return h, w
    k = m / float(long_edge)
    return max(1, int(round(h * k))), max(1, int(round(w * k)))


def _speed_path() -> str:
    return os.path.join(root(), "speed.json")


def record_speed(model: str, device: str, sec_per_frame: float, frames: int) -> None:
    """记一次传播的实测速度（按 模型 + 设备），给下次「大约要多久」用。滑动平均，样本少的不记。"""
    if frames < 8 or sec_per_frame <= 0:
        return
    key = "%s|%s" % (model, device)
    data = {}
    try:
        with open(_speed_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh) or {}
    except (OSError, ValueError):
        data = {}
    old = data.get(key)
    v = float(sec_per_frame) if not old else 0.5 * float(old) + 0.5 * float(sec_per_frame)
    data[key] = round(v, 4)
    try:
        os.makedirs(root(), exist_ok=True)
        tmp = _speed_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, _speed_path())
    except OSError:
        pass


def measured_speed(model: str, device: str) -> float | None:
    try:
        with open(_speed_path(), "r", encoding="utf-8") as fh:
            v = (json.load(fh) or {}).get("%s|%s" % (model, device))
        return float(v) if v else None
    except (OSError, ValueError, TypeError):
        return None


def frame_index(src_t: float, fps: float) -> int:
    """源时间 → 源帧号（和解码器一样四舍五入）。"""
    try:
        return max(0, int(round(float(src_t) * max(1e-6, float(fps)))))
    except (TypeError, ValueError):
        return 0


# ───────────────────────────── 读 ─────────────────────────────

def load_index(sid: str) -> dict | None:
    """index.json（按 mtime 缓存）；没有会话 → None。"""
    if not sid:
        return None
    try:
        path = _index_path(sid)
    except ValueError:
        return None
    try:
        st = os.stat(path)
    except OSError:
        with _LOCK:
            _INDEX_CACHE.pop(sid, None)
        return None
    key = (st.st_mtime_ns, st.st_size)
    with _LOCK:
        hit = _INDEX_CACHE.get(sid)
        if hit is not None and hit[0] == key:
            return hit[1]
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    data.setdefault("frames", {})
    with _LOCK:
        _INDEX_CACHE[sid] = (key, data)
    return data


def index_version(sid: str):
    """index.json 的 (mtime_ns, size)：结果一变它就变，拿来当渲染缓存键的一部分。"""
    try:
        st = os.stat(_index_path(sid))
    except (OSError, ValueError):
        return None
    return (st.st_mtime_ns, st.st_size)


def coverage(sid: str) -> dict:
    """{frame_idx(int): score(float)} —— 给时间线画条用。"""
    idx = load_index(sid)
    if not idx:
        return {}
    out = {}
    for k, v in (idx.get("frames") or {}).items():
        try:
            out[int(k)] = float((v or {}).get("score", 1.0))
        except (TypeError, ValueError):
            continue
    return out


def nearest_frame(sid: str, frame_idx: int, tolerance: int = 1) -> int | None:
    """有结果的最近帧（容差 ±tolerance 帧，解码取帧的四舍五入差一帧很常见）。"""
    idx = load_index(sid)
    if not idx:
        return None
    frames = idx.get("frames") or {}
    if str(int(frame_idx)) in frames:
        return int(frame_idx)
    for d in range(1, int(tolerance) + 1):
        for cand in (frame_idx - d, frame_idx + d):
            if cand >= 0 and str(int(cand)) in frames:
                return int(cand)
    return None


def read_mask(sid: str, frame_idx: int, obj_id: int) -> np.ndarray | None:
    """一张 (h, w) uint8；没有 → None。"""
    key = (sid, int(frame_idx), int(obj_id))
    with _LOCK:
        hit = _MASK_CACHE.get(key)
        if hit is not None:
            _MASK_CACHE.move_to_end(key)
            return hit
    try:
        path = os.path.join(session_dir(sid), _mask_name(frame_idx, obj_id))
    except ValueError:
        return None
    if not os.path.isfile(path):
        return None
    import cv2
    data = np.fromfile(path, dtype=np.uint8)          # Windows 中文路径友好
    arr = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
    if arr is None:
        return None
    with _LOCK:
        _MASK_CACHE[key] = arr
        while len(_MASK_CACHE) > _MASK_CACHE_MAX:
            _MASK_CACHE.popitem(last=False)
    return arr


def read_alpha(sid: str, frame_idx: int, obj_ids=None, tolerance: int = 1):
    """合并若干对象 → (alpha float32 0–1 (h, w), used_frame_idx)；这帧没结果 → (None, None)。

    obj_ids None = index 里这一帧有的全部对象；多个对象取并集（max）。"""
    used = nearest_frame(sid, frame_idx, tolerance)
    if used is None:
        return None, None
    idx = load_index(sid) or {}
    rec = (idx.get("frames") or {}).get(str(used)) or {}
    have = [int(o) for o in (rec.get("objs") or [])]
    want = have if obj_ids is None else [int(o) for o in obj_ids if int(o) in have]
    total = None
    for o in want:
        m = read_mask(sid, used, o)
        if m is None:
            continue
        total = m if total is None else np.maximum(total, m)
    if total is None:
        return None, None
    return total.astype(np.float32) * np.float32(1.0 / 255.0), used


def invalidate(sid: str | None = None) -> None:
    with _LOCK:
        if sid is None:
            _INDEX_CACHE.clear()
            _MASK_CACHE.clear()
            return
        _INDEX_CACHE.pop(sid, None)
        for k in [k for k in _MASK_CACHE if k[0] == sid]:
            _MASK_CACHE.pop(k, None)


# ───────────────────────────── 写 ─────────────────────────────

class Session:
    """写入端。``with Session(sid) as s: s.write(...)``；退出时落盘 index。"""

    def __init__(self, sid: str | None = None, *, fps: float | None = None, src_w: int | None = None,
                 src_h: int | None = None, model: str | None = None):
        self.sid = sid or new_session_id()
        self.dir = session_dir(self.sid)
        os.makedirs(self.dir, exist_ok=True)
        self.index = load_index(self.sid) or {"frames": {}}
        if fps:
            self.index["fps"] = float(fps)
        if src_w and src_h:
            self.index["src_w"], self.index["src_h"] = int(src_w), int(src_h)
        if model:
            self.index["model"] = str(model)
        self.index.setdefault("created", time.time())
        self._dirty = True
        self._writes_since_flush = 0

    # with 支持
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.flush()
        return False

    @property
    def fps(self) -> float:
        return float(self.index.get("fps") or 30.0)

    def write(self, frame_idx: int, obj_id: int, mask_u8: np.ndarray, score: float = 1.0) -> None:
        import cv2
        arr = np.ascontiguousarray(mask_u8, dtype=np.uint8)
        ok, buf = cv2.imencode(".png", arr, [cv2.IMWRITE_PNG_COMPRESSION, 3])
        if not ok:
            raise RuntimeError("PNG 编码失败")
        path = os.path.join(self.dir, _mask_name(frame_idx, obj_id))
        tmp = path + ".tmp"
        buf.tofile(tmp)
        os.replace(tmp, path)
        rec = self.index["frames"].setdefault(str(int(frame_idx)), {"score": 1.0, "objs": [], "scores": {}})
        objs = [int(o) for o in rec.get("objs") or []]
        if int(obj_id) not in objs:
            objs.append(int(obj_id))
        rec["objs"] = sorted(objs)
        scores = rec.setdefault("scores", {})
        scores[str(int(obj_id))] = round(float(score), 3)
        rec["score"] = min(float(v) for v in scores.values())      # 这帧的置信度 = 最差的那个对象
        self._dirty = True
        self._writes_since_flush += 1
        if self._writes_since_flush >= 24:
            self.flush()
        with _LOCK:
            _MASK_CACHE.pop((self.sid, int(frame_idx), int(obj_id)), None)

    def remove_frames(self, frame_indices) -> int:
        n = 0
        for fi in list(frame_indices):
            rec = self.index["frames"].pop(str(int(fi)), None)
            if rec is None:
                continue
            for o in rec.get("objs") or []:
                try:
                    os.remove(os.path.join(self.dir, _mask_name(fi, o)))
                    n += 1
                except OSError:
                    pass
        self._dirty = True
        invalidate(self.sid)
        return n

    def remove_object(self, obj_id: int) -> int:
        n = 0
        for k, rec in list(self.index["frames"].items()):
            objs = [int(o) for o in rec.get("objs") or []]
            if int(obj_id) not in objs:
                continue
            try:
                os.remove(os.path.join(self.dir, _mask_name(int(k), obj_id)))
                n += 1
            except OSError:
                pass
            objs.remove(int(obj_id))
            (rec.get("scores") or {}).pop(str(int(obj_id)), None)
            if objs:
                rec["objs"] = objs
                rec["score"] = min([float(v) for v in (rec.get("scores") or {}).values()] or [1.0])
            else:
                self.index["frames"].pop(k, None)
        self._dirty = True
        invalidate(self.sid)
        return n

    def clear(self) -> None:
        for k, rec in list(self.index["frames"].items()):
            for o in rec.get("objs") or []:
                try:
                    os.remove(os.path.join(self.dir, _mask_name(int(k), o)))
                except OSError:
                    pass
        self.index["frames"] = {}
        self._dirty = True
        invalidate(self.sid)
        self.flush()

    def flush(self) -> None:
        if not self._dirty:
            return
        path = _index_path(self.sid)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.index, fh, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, path)
        self._dirty = False
        self._writes_since_flush = 0
        invalidate(self.sid)


def delete_session(sid: str) -> bool:
    """整个会话文件夹删掉（清除 / 工程里不再引用时）。"""
    import shutil
    try:
        d = session_dir(sid)
    except ValueError:
        return False
    invalidate(sid)
    if os.path.isdir(d):
        shutil.rmtree(d, ignore_errors=True)
        return True
    return False


def session_bytes(sid: str) -> int:
    try:
        d = session_dir(sid)
    except ValueError:
        return 0
    total = 0
    try:
        for name in os.listdir(d):
            try:
                total += os.path.getsize(os.path.join(d, name))
            except OSError:
                pass
    except OSError:
        return 0
    return total

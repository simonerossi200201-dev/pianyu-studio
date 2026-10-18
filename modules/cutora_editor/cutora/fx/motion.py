# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q152-E · 跟踪结果 → 一条「运动数据」：每个源帧相对参考帧的 平移 / 旋转 / 缩放。

坐标都是**源帧像素**（没裁切没翻转的那张）。输出 MotionTrack：
    frames  [f0 … f1] 连续源帧号
    ax, ay  锚点（参考帧上跟踪点的中心）在每帧的位置
    rot     相对参考帧转了多少度（顺时针为正，和画布一致）
    scale   相对参考帧的缩放倍数
    lost    这一帧没有足够的可见点 / 没有 mask（值是插值补出来的）→ 时间线画红
    anchor0 参考帧锚点
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from cutora.fx import track_store as T


@dataclass
class MotionTrack:
    frames: np.ndarray
    ax: np.ndarray
    ay: np.ndarray
    rot: np.ndarray
    scale: np.ndarray
    lost: np.ndarray
    ref_frame: int
    anchor0: tuple
    src_w: int
    src_h: int
    fps: float
    kind: str = "points"
    meta: dict = field(default_factory=dict)

    def __len__(self):
        return int(len(self.frames))

    def index_of(self, frame_idx: int):
        i = int(frame_idx) - int(self.frames[0]) if len(self.frames) else -1
        return i if 0 <= i < len(self.frames) else None

    def at(self, frame_idx: int):
        """(ax, ay, rot, scale, lost)；超出范围 → 夹到两端。"""
        if not len(self.frames):
            return None
        i = int(min(max(int(frame_idx) - int(self.frames[0]), 0), len(self.frames) - 1))
        return float(self.ax[i]), float(self.ay[i]), float(self.rot[i]), float(self.scale[i]), bool(self.lost[i])


# ───────────────────────────── 求解 ─────────────────────────────

def similarity(src: np.ndarray, dst: np.ndarray, weights=None):
    """两组点（N, 2）之间的相似变换（无翻转）：dst ≈ s·R(θ)·(src − cs) + cd。返回 (θ 度, s, cs, cd)。

    加权最小二乘（Umeyama 2D）。N = 1 → 只有平移。"""
    src = np.asarray(src, np.float64).reshape(-1, 2)
    dst = np.asarray(dst, np.float64).reshape(-1, 2)
    w = np.ones(len(src)) if weights is None else np.asarray(weights, np.float64).reshape(-1)
    w = np.clip(w, 1e-6, None)
    ws = w.sum()
    cs = (src * w[:, None]).sum(0) / ws
    cd = (dst * w[:, None]).sum(0) / ws
    if len(src) < 2:
        return 0.0, 1.0, cs, cd
    a = src - cs
    b = dst - cd
    # 复数表示：b ≈ z·a，z = s·e^{iθ}，加权最小二乘 z = Σw·conj(a)·b / Σw·|a|²
    za = a[:, 0] + 1j * a[:, 1]
    zb = b[:, 0] + 1j * b[:, 1]
    den = float((w * (np.abs(za) ** 2)).sum())
    if den < 1e-9:
        return 0.0, 1.0, cs, cd
    z = (w * np.conj(za) * zb).sum() / den
    s = float(abs(z))
    theta = math.degrees(math.atan2(z.imag, z.real))
    if s < 1e-6:
        s = 1.0
    return theta, s, cs, cd


def _fill_gaps(values: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """无效帧线性插值；两头按最近的有效值保持。"""
    out = values.astype(np.float64).copy()
    idx = np.nonzero(valid)[0]
    if len(idx) == 0:
        return out
    allx = np.arange(len(out))
    out[~valid] = np.interp(allx[~valid], idx, out[idx])
    return out


def _unwrap_deg(a: np.ndarray) -> np.ndarray:
    return np.degrees(np.unwrap(np.radians(a)))


def smooth(values: np.ndarray, window: int) -> np.ndarray:
    """居中滑动平均（两端按镜像补）。window ≤ 1 = 不平滑。"""
    k = int(window)
    if k <= 1 or len(values) < 3:
        return values.astype(np.float64)
    k = min(k, len(values) if len(values) % 2 else len(values) - 1)
    if k % 2 == 0:
        k += 1
    if k <= 1:
        return values.astype(np.float64)
    pad = k // 2
    v = np.pad(values.astype(np.float64), (pad, pad), mode="reflect")
    ker = np.ones(k) / k
    return np.convolve(v, ker, mode="valid")


def _finish(frames, ax, ay, rot, sc, valid, ref_frame, anchor0, meta, smooth_frames, kind):
    lost = ~valid
    ax = _fill_gaps(ax, valid)
    ay = _fill_gaps(ay, valid)
    r = np.asarray(rot, np.float64).copy()
    vi = np.nonzero(valid)[0]
    if len(vi):
        r[vi] = _unwrap_deg(r[vi])           # 只在有效帧之间展开（±180° 跳变折回去）
    rot = _fill_gaps(r, valid)
    lsc = _fill_gaps(np.log(np.clip(sc, 1e-4, None)), valid)
    if smooth_frames and smooth_frames > 1:
        ax, ay, rot, lsc = (smooth(v, smooth_frames) for v in (ax, ay, rot, lsc))
        # 平滑以后参考帧可能偏开一点：整体平移回去，保证参考帧完全不动
        i0 = int(ref_frame) - int(frames[0])
        if 0 <= i0 < len(frames):
            ax += anchor0[0] - ax[i0]
            ay += anchor0[1] - ay[i0]
            rot -= rot[i0]
            lsc -= lsc[i0]
    return MotionTrack(frames=np.asarray(frames, np.int64), ax=ax, ay=ay, rot=rot, scale=np.exp(lsc), lost=lost,
                       ref_frame=int(ref_frame), anchor0=(float(anchor0[0]), float(anchor0[1])),
                       src_w=int(meta.get("src_w") or 0), src_h=int(meta.get("src_h") or 0),
                       fps=float(meta.get("fps") or 30.0), kind=kind, meta=meta)


def point_motion(sid: str, point_ids, frame_range: tuple, ref_frame: int, *, smooth_frames: int = 0,
                 min_visible: float = T.VISIBLE) -> MotionTrack | None:
    """点跟踪 → 运动数据。1 个点 = 只有平移；2 个以上 = 平移 + 旋转 + 缩放（加权最小二乘）。

    参考帧上所有选中的点都要有位置（被挡也行，模型会给出估计位置）。"""
    data = T.load(sid)
    if not data:
        return None
    W, H = float(data.get("src_w") or 1), float(data.get("src_h") or 1)
    a, b = int(frame_range[0]), int(frame_range[1])
    pids = [int(p) for p in point_ids]
    if not pids or b < a:
        return None
    ref = T.points_at(sid, ref_frame)
    if not all(p in ref for p in pids):
        # 参考帧缺点：换成离它最近、所有点都在的帧
        cands = [f for f in T.frames(sid) if a <= f <= b and all(p in T.points_at(sid, f) for p in pids)]
        if not cands:
            return None
        ref_frame = min(cands, key=lambda f: abs(f - ref_frame))
        ref = T.points_at(sid, ref_frame)
    src0 = np.array([[ref[p][0] * W, ref[p][1] * H] for p in pids])
    anchor0 = src0.mean(0)
    n = b - a + 1
    ax, ay, rot, sc = np.zeros(n), np.zeros(n), np.zeros(n), np.ones(n)
    valid = np.zeros(n, bool)
    for i, f in enumerate(range(a, b + 1)):
        cur = T.points_at(sid, f)
        idx = [k for k, p in enumerate(pids) if p in cur]
        if not idx:
            continue
        dst = np.array([[cur[pids[k]][0] * W, cur[pids[k]][1] * H] for k in idx])
        vis = np.array([cur[pids[k]][2] for k in idx])
        good = vis >= min_visible
        if not good.any():
            continue
        if len(pids) == 1:
            ax[i], ay[i] = dst[0]
            valid[i] = True
            continue
        use = good if good.sum() >= 2 else None
        if use is None:
            # 只剩一个点看得见：用它的平移，旋转 / 缩放沿用插值
            k = int(np.nonzero(good)[0][0])
            ax[i], ay[i] = anchor0 + (dst[k] - src0[idx[k]])
            valid[i] = True
            rot[i], sc[i] = np.nan, np.nan
            continue
        theta, s, cs, cd = similarity(src0[idx][use], dst[use], vis[use])
        # 锚点（全部点的中心）在这一帧的位置：cd + s·R·(anchor0 − cs)
        c, sn = math.cos(math.radians(theta)), math.sin(math.radians(theta))
        d = anchor0 - cs
        ax[i] = cd[0] + s * (c * d[0] - sn * d[1])
        ay[i] = cd[1] + s * (sn * d[0] + c * d[1])
        rot[i], sc[i] = theta, s
        valid[i] = True
    # 「只剩一个点」的帧：旋转 / 缩放用相邻帧插值
    partial = valid & (np.isnan(rot) | np.isnan(sc))
    full = valid & ~partial
    if partial.any() and full.any():
        rot = _fill_gaps(np.nan_to_num(rot), full)
        sc = np.exp(_fill_gaps(np.log(np.clip(np.nan_to_num(sc, nan=1.0), 1e-4, None)), full))
    else:
        rot = np.nan_to_num(rot)
        sc = np.nan_to_num(sc, nan=1.0)
    if not valid.any():
        return None
    meta = {"src_w": int(W), "src_h": int(H), "fps": float(data.get("fps") or 30.0), "points": pids}
    return _finish(list(range(a, b + 1)), ax, ay, rot, sc, valid, ref_frame, anchor0, meta, smooth_frames, "points")


def object_motion(matte_spec: dict, frame_range: tuple, ref_frame: int, *, smooth_frames: int = 0,
                  with_rotation: bool = False) -> MotionTrack | None:
    """AI 抠像的 mask → 运动数据：质心 = 位置，√(面积比) = 缩放，二阶矩主轴 = 粗旋转。"""
    from cutora.fx import ai_matte as A
    from cutora.fx import matte_store as S
    sid = matte_spec.get("session")
    idx = S.load_index(sid) if sid else None
    if not idx:
        return None
    W, H = float(idx.get("src_w") or 1), float(idx.get("src_h") or 1)
    a, b = int(frame_range[0]), int(frame_range[1])
    n = b - a + 1
    if n <= 0:
        return None
    ax, ay, rot, area = np.zeros(n), np.zeros(n), np.zeros(n), np.zeros(n)
    valid = np.zeros(n, bool)
    import cv2
    for i, f in enumerate(range(a, b + 1)):
        m = A.combined_raw(matte_spec, f, tolerance=0)
        if m is None:
            continue
        mh, mw = m.shape[:2]
        mom = cv2.moments((m > 0.5).astype(np.uint8), binaryImage=True)
        if mom["m00"] < 4:
            continue
        kx, ky = W / float(mw), H / float(mh)
        ax[i] = mom["m10"] / mom["m00"] * kx
        ay[i] = mom["m01"] / mom["m00"] * ky
        area[i] = mom["m00"] * kx * ky
        rot[i] = 0.5 * math.degrees(math.atan2(2 * mom["mu11"], mom["mu20"] - mom["mu02"]))
        valid[i] = True
    if not valid.any():
        return None
    i0 = int(ref_frame) - a
    if not (0 <= i0 < n) or not valid[i0]:
        good = np.nonzero(valid)[0]
        i0 = int(good[np.argmin(np.abs(good - (int(ref_frame) - a)))])
    ref_frame = a + i0
    sc = np.where(valid, np.sqrt(np.clip(area, 1e-6, None) / max(1e-6, area[i0])), 1.0)
    if with_rotation:
        # 主轴角度只定义到 ±90°：相邻帧跳变时折回去
        r = rot.copy()
        for k in range(1, n):
            if valid[k] and valid[k - 1]:
                while r[k] - r[k - 1] > 90:
                    r[k] -= 180
                while r[k] - r[k - 1] < -90:
                    r[k] += 180
        rot = r - r[i0]
    else:
        rot = np.zeros(n)
    meta = {"src_w": int(W), "src_h": int(H), "fps": float(idx.get("fps") or 30.0)}
    return _finish(list(range(a, b + 1)), ax, ay, rot, sc, valid, ref_frame, (ax[i0], ay[i0]), meta,
                   smooth_frames, "object")


def lost_runs(track: MotionTrack) -> list:
    """[(首帧, 末帧)] 跟丢（插值补出来）的连续段。"""
    out = []
    start = None
    for i, f in enumerate(track.frames):
        if track.lost[i] and start is None:
            start = int(f)
        if not track.lost[i] and start is not None:
            out.append((start, int(track.frames[i - 1])))
            start = None
    if start is not None:
        out.append((start, int(track.frames[-1])))
    return out

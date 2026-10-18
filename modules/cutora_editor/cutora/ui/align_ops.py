# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Q128：多选对齐 / 等距分布的纯几何（不依赖 Qt）。

输入是一组 (x, y, w, h) 的框，输出每个框应该平移多少 (dx, dy)。
调用方负责把位移写回去（画布走关键帧感知的 _nudge_host_param）。

约定跟 Canva / Figma 一样：
* 选了 ≥2 个：相对彼此的包围盒对齐；
* 只选 1 个（或 ``to_bounds=True``）：相对画布对齐；
* 等距分布至少要 3 个，首尾不动，中间的按「间隙相等」摆。
"""
from __future__ import annotations

from typing import Iterable, Sequence

Box = tuple[float, float, float, float]

ALIGN_MODES = ("left", "hcenter", "right", "top", "vcenter", "bottom")
DISTRIBUTE_AXES = ("h", "v")

ALIGN_LABELS = {
    "left": "左对齐", "hcenter": "水平居中", "right": "右对齐",
    "top": "顶对齐", "vcenter": "垂直居中", "bottom": "底对齐",
}
DISTRIBUTE_LABELS = {"h": "水平等距", "v": "垂直等距"}


def union(boxes: Iterable[Box]) -> Box | None:
    boxes = [tuple(float(v) for v in b) for b in boxes]
    if not boxes:
        return None
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[0] + b[2] for b in boxes)
    y1 = max(b[1] + b[3] for b in boxes)
    return (x0, y0, x1 - x0, y1 - y0)


def align(boxes: Sequence[Box], mode: str, bounds: Box | None = None,
          *, to_bounds: bool = False) -> list[tuple[float, float]]:
    """每个框的 (dx, dy)。``bounds`` 是画布 (0, 0, w, h)。"""
    if mode not in ALIGN_MODES:
        raise ValueError("align mode 必须是 %s，收到 %r" % (ALIGN_MODES, mode))
    boxes = [tuple(float(v) for v in b) for b in boxes]
    if not boxes:
        return []
    ref = None
    if to_bounds or len(boxes) == 1:
        ref = tuple(float(v) for v in bounds) if bounds is not None else None
    if ref is None:
        ref = union(boxes)
    rx, ry, rw, rh = ref
    out: list[tuple[float, float]] = []
    for (x, y, w, h) in boxes:
        dx = dy = 0.0
        if mode == "left":
            dx = rx - x
        elif mode == "hcenter":
            dx = (rx + rw * 0.5) - (x + w * 0.5)
        elif mode == "right":
            dx = (rx + rw) - (x + w)
        elif mode == "top":
            dy = ry - y
        elif mode == "vcenter":
            dy = (ry + rh * 0.5) - (y + h * 0.5)
        elif mode == "bottom":
            dy = (ry + rh) - (y + h)
        out.append((dx, dy))
    return out


def distribute(boxes: Sequence[Box], axis: str) -> list[tuple[float, float]]:
    """首尾不动，中间的框按相等间隙排开。少于 3 个时全是 (0, 0)。"""
    if axis not in DISTRIBUTE_AXES:
        raise ValueError("distribute axis 必须是 h 或 v，收到 %r" % axis)
    boxes = [tuple(float(v) for v in b) for b in boxes]
    n = len(boxes)
    if n < 3:
        return [(0.0, 0.0)] * n
    pos = 0 if axis == "h" else 1
    size = 2 if axis == "h" else 3
    order = sorted(range(n), key=lambda i: (boxes[i][pos] + boxes[i][size] * 0.5, i))
    first, last = boxes[order[0]], boxes[order[-1]]
    span = (last[pos] + last[size]) - first[pos]
    total = sum(boxes[i][size] for i in order)
    gap = (span - total) / float(n - 1)
    out = [(0.0, 0.0)] * n
    cursor = first[pos] + first[size] + gap
    for k in order[1:-1]:
        b = boxes[k]
        delta = cursor - b[pos]
        out[k] = (delta, 0.0) if axis == "h" else (0.0, delta)
        cursor += b[size] + gap
    return out

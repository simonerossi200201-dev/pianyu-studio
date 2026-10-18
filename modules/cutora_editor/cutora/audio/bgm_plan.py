# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""给一批视频配乐时尽量少重复。文件比视频少，才按用过次数从少到多再轮一圈。"""
from __future__ import annotations


def assign_least_repeat(n_videos: int, paths: list, used: set | None = None) -> list:
    """返回长度 n_videos 的路径列表。先用还没用过的，用完再挑用得最少的。"""
    pool = [p for p in (paths or []) if p]
    if n_videos <= 0 or not pool:
        return []
    used = set(used or ())
    fresh = [p for p in pool if p not in used]
    order = fresh + [p for p in pool if p in used]
    counts = {p: (1 if p in used else 0) for p in order}
    out = []
    for _i in range(int(n_videos)):
        pick = min(order, key=lambda p: (counts[p], order.index(p)))
        out.append(pick)
        counts[pick] = counts.get(pick, 0) + 1
    return out

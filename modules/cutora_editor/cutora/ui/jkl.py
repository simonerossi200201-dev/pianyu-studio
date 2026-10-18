# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""JKL shuttle rates. Reverse is timed seek, not reverse-decode."""


def next_rate(current: int, going_fwd: bool) -> int:
    cur = int(current or 0)
    if going_fwd:
        if cur <= 0:
            return 1
        return min(8, cur * 2)
    if cur >= 0:
        return -1
    return max(-8, cur * 2)


def rate_label(rate: int) -> str:
    r = int(rate or 0)
    if r == 0:
        return "停止"
    sign = "−" if r < 0 else ""
    return "%s%s×" % (sign, abs(r))

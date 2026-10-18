# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""配乐循环的接缝交叉淡化。只有 clip.seam_xfade 为真才插进滤镜链。

老工程没有这个键：仍然是原来的 aloop，再在整段开头淡入、结尾淡出。
新配上的曲子：每一圈的尾巴和下一圈的头重叠交叉淡化，整段的头尾淡化还在。
"""
from __future__ import annotations


def want_seam(track) -> bool:
    if not isinstance(track, dict):
        return False
    clip = track.get("_clip")
    if isinstance(clip, dict):
        return bool(clip.get("seam_xfade"))
    if clip is not None:
        return bool(getattr(clip, "seam_xfade", False))
    return bool(track.get("seam_xfade"))


def seam_seconds(one_loop: float) -> float:
    try:
        span = float(one_loop)
    except (TypeError, ValueError):
        return 0.0
    if span <= 0.08:
        return 0.0
    return min(0.30, span * 0.25)


def seam_prefix(label_in: str, looped: str, *, trim_in: float, target_sr: int,
                speed: float, one_loop: float, trim_len: float, xf: float,
                atempo) -> str:
    """从输入标签到 [looped] 的滤镜图。末尾没有分号。"""
    body = max(0.02, float(one_loop) - float(xf))
    samples = max(1, int(round(float(one_loop) * int(target_sr))))
    pre = "pre" + looped
    h0, b0, t0 = "h0" + looped, "b0" + looped, "t0" + looped
    head, mid, tail = "hd" + looped, "bd" + looped, "tl" + looped
    seam, unit = "sm" + looped, "un" + looped
    chain = "[%s]" % label_in
    if trim_in > 0:
        chain += "atrim=start=%s," % _num(trim_in)
    chain += "asetpts=N/SR/TB,aresample=%d" % int(target_sr)
    if abs(float(speed) - 1.0) >= 1e-9:
        chain += atempo(float(speed))
    chain += "[%s];" % pre
    chain += (
        "[%s]asplit=3[%s][%s][%s];"
        "[%s]atrim=0:%s,asetpts=PTS-STARTPTS[%s];"
        "[%s]atrim=0:%s,asetpts=PTS-STARTPTS[%s];"
        "[%s]atrim=start=%s:duration=%s,asetpts=PTS-STARTPTS[%s];"
        "[%s][%s]acrossfade=d=%s:c1=tri:c2=tri[%s];"
        "[%s][%s]concat=n=2:v=0:a=1[%s];"
        "[%s]aloop=loop=-1:size=%d:start=0,atrim=duration=%s,asetpts=N/SR/TB[%s]"
        % (
            pre, h0, b0, t0,
            h0, _num(xf), head,
            b0, _num(body), mid,
            t0, _num(body), _num(xf), tail,
            tail, head, _num(xf), seam,
            mid, seam, unit,
            unit, samples, _num(trim_len), looped,
        )
    )
    return chain


def _num(v) -> str:
    return "%.6f" % float(v)

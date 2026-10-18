# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Animation registry, unit timing, seeded RNG. Functions are pure."""
from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass
from typing import Any, Callable

from cutora.render.anim_engine import (
    EASING_MAP,
    compute_floating_offset,
    compute_pop_scale,
    ease_out_bounce,
    ease_out_cubic,
)

from cutora.render.textfx.spec import (
    POP_PEAK,
    POP_SIDE_EM,
    AnimSpec,
    FxSpec,
)


@dataclass
class Transform:
    alpha: float = 1.0
    scale_x: float = 1.0
    scale_y: float = 1.0
    dx_em: float = 0.0
    dy_em: float = 0.0
    rot_deg: float = 0.0
    clip: float = 1.0
    clip_dir: str = "ltr"
    blur_sigma_em: float = 0.0
    tracking_em: float = 0.0
    fill_progress: float = 1.0
    extra_color: str | None = None


@dataclass
class AnimDef:
    label_zh: str
    phases: tuple[str, ...]
    units: tuple[str, ...]
    fn: Callable[[float, dict], Transform]


LOOP_PERIOD_S = 2.0
SLIDE_DIST_EM = 1.5
# Peak-to-peak zoom ≤ 3% (1 ± 0.015). Float half-amplitude 0.075 em → peak-to-peak 0.15 em.
ZOOM_SLOW_AMP = 0.015
FLOAT_LOOP_EM = 0.075
POP_IN_PEAK = 1.18
IMPACT_PEAK = 1.30
STOMP_PEAK = 1.70
DROP_DY_EM = 2.0
FLOAT_DY_EM = 0.6
STOMP_SHAKE_EM = 0.06
REVEAL_ZOOM_SCALE0 = 1.35
REVEAL_ZOOM_BLUR_EM = 0.5
REVEAL_ZOOM_ALPHA0 = 0.4
LINE_DRAW_EM = 0.08
LINE_DRAW_DUR_S = 0.180
LINE_DRAW_UNDER_EM = 0.18
SCROLL_GAP_EM = 0.5
BLUR_SKIP_PX = 0.3
FLIP_BACK_MIX = 0.40
STACK_DY_EM = 1.2
TRACKING_IN_EM = 0.35
ROTATE_FLIP_DEG = 14.0
COLOUR_PEAK = 1.15
FLIP_STAGGER_S = 0.04


def _none_fn(u: float, ctx: dict) -> Transform:
    return Transform()


def _fade_fn(u: float, ctx: dict) -> Transform:
    return Transform(alpha=max(0.0, min(1.0, u)))


def _float_in_up_fn(u: float, ctx: dict) -> Transform:
    return Transform(dy_em=(1.0 - u) * FLOAT_DY_EM, alpha=u)


def _float_in_down_fn(u: float, ctx: dict) -> Transform:
    return Transform(dy_em=-(1.0 - u) * FLOAT_DY_EM, alpha=u)


def _scale_in_fn(u: float, ctx: dict) -> Transform:
    s = 0.6 + 0.4 * u
    return Transform(scale_x=s, scale_y=s, alpha=u)


def _drop_in_fn(u: float, ctx: dict) -> Transform:
    uu = max(0.0, min(1.0, u))
    bounced = ease_out_bounce(uu)
    dy = -(1.0 - bounced) * DROP_DY_EM
    if uu > 0.85:
        p = (uu - 0.85) / 0.15
        sy = 0.92 + 0.08 * p
    else:
        sy = 1.0
    return Transform(dy_em=dy, scale_y=sy, alpha=min(1.0, uu / 0.2))


def _pop_scale_peak(u: float, peak: float) -> float:
    raw = compute_pop_scale(max(0.0, min(1.0, u)), peak)
    # compute_pop_scale is ease_out_back in 0..~1.18, 1 at u=1.
    if raw <= 1.0:
        return raw
    back_peak = 1.1755
    return 1.0 + (raw - 1.0) * (peak - 1.0) / max(1e-6, back_peak - 1.0)


def _pop_in_fn(u: float, ctx: dict) -> Transform:
    s = _pop_scale_peak(u, POP_IN_PEAK)
    alpha = min(1.0, u / 0.3) if u < 0.3 else 1.0
    return Transform(scale_x=s, scale_y=s, alpha=alpha)


def _impact_pop_fn(u: float, ctx: dict) -> Transform:
    s = _pop_scale_peak(u, IMPACT_PEAK)
    rng = rng_for(ctx.get("seg_id", ""), int(ctx.get("k") or 0), int(ctx.get("seed") or 0))
    sign = -1.0 if rng.random() < 0.5 else 1.0
    rot = sign * 4.0 * (1.0 - u)
    alpha = min(1.0, u / 0.3) if u < 0.3 else 1.0
    return Transform(scale_x=s, scale_y=s, rot_deg=rot, alpha=alpha)


def _ease_in_expo(t: float) -> float:
    t = max(0.0, min(1.0, t))
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    return 2.0 ** (10.0 * t - 10.0)


def _stomp_fn(u: float, ctx: dict) -> Transform:
    uu = max(0.0, min(1.0, u))
    s = STOMP_PEAK + (1.0 - STOMP_PEAK) * _ease_in_expo(uu)
    dx = dy = 0.0
    if uu > 0.8:
        fall = 1.0 - (uu - 0.8) / 0.2
        rng = rng_for(
            ctx.get("seg_id", ""), int(ctx.get("k") or 0), int(ctx.get("seed") or 0))
        dx = STOMP_SHAKE_EM * fall * rng.uniform(-1.0, 1.0)
        dy = STOMP_SHAKE_EM * fall * rng.uniform(-1.0, 1.0)
    return Transform(scale_x=s, scale_y=s, dx_em=dx, dy_em=dy, alpha=min(1.0, uu / 0.15))


def _slide_fn(u: float, ctx: dict) -> Transform:
    direction = str(ctx.get("dir") or "left").lower()
    dist = (1.0 - u) * SLIDE_DIST_EM
    dx = dy = 0.0
    if direction == "right":
        dx = dist
    elif direction == "up":
        dy = -dist
    elif direction == "down":
        dy = dist
    else:
        dx = -dist
    alpha = min(1.0, u / 0.2) if u < 0.2 else 1.0
    return Transform(dx_em=dx, dy_em=dy, alpha=alpha)


def _zoom_slow_fn(u: float, ctx: dict) -> Transform:
    k = math.sin(u * 2.0 * math.pi)
    s = 1.0 + ZOOM_SLOW_AMP * k
    return Transform(scale_x=s, scale_y=s)


def _float_loop_fn(u: float, ctx: dict) -> Transform:
    # u is 0-1 over LOOP_PERIOD_S; match compute_floating_offset shape.
    amp_px = 1.0
    dy_px = compute_floating_offset(u * LOOP_PERIOD_S, 0.0, amplitude=amp_px, period=LOOP_PERIOD_S)
    dy_em = FLOAT_LOOP_EM * (dy_px / amp_px)
    return Transform(dy_em=dy_em)


def _reveal_wipe_fn(u: float, ctx: dict) -> Transform:
    return Transform(clip=max(0.0, min(1.0, u)), clip_dir="ltr")


def _word_runs(layout) -> list:
    if layout is None:
        return []
    out = []
    for line in getattr(layout, "lines", []) or []:
        for run in getattr(line, "runs", []) or []:
            if str(getattr(run, "text", "") or "").strip():
                out.append(run)
    return out


def _typewriter_fn(u: float, ctx: dict) -> Transform:
    """Word-stepped LTR clip. Unrevealed words stay fully hidden (no partial glyphs)."""
    uu = max(0.0, min(1.0, u))
    layout = ctx.get("layout")
    runs = _word_runs(layout)
    n = len(runs)
    if n <= 0:
        return Transform(clip=uu, clip_dir="ltr")
    k = 0 if uu <= 0.0 else min(n, int(math.ceil(uu * n - 1e-12)))
    if k <= 0:
        return Transform(clip=0.0, clip_dir="ltr")
    if k >= n:
        return Transform(clip=1.0, clip_dir="ltr")
    last = runs[k - 1]
    ox = float(layout.text_x)
    tw = max(1.0, float(layout.text_w))
    clip = (float(last.x) + float(last.w) - ox) / tw
    return Transform(clip=max(0.0, min(1.0, clip)), clip_dir="ltr")


def _line_draw_fn(u: float, ctx: dict) -> Transform:
    """Outline stroke along the path. No-op without stroke.width_em > 0."""
    if float(ctx.get("stroke_em") or 0.0) <= 0.0:
        return Transform()
    uu = max(0.0, min(1.0, u))
    return Transform(clip=uu, clip_dir="path", fill_progress=uu)


def _scroll_fn(u: float, ctx: dict) -> Transform:
    """Horizontal marquee inside the text box. Linear in u so 0 / 0.25 / 0.75 differ."""
    layout = ctx.get("layout")
    em = float(ctx.get("em") or 0.0)
    if em <= 0.0:
        em = float(getattr(layout, "em", 1.0) or 1.0)
    if layout is not None and em > 0.0:
        span_em = float(layout.text_w) / em + SCROLL_GAP_EM
    else:
        span_em = 8.0 + SCROLL_GAP_EM
    uu = max(0.0, min(1.0, u))
    return Transform(dx_em=-uu * span_em, clip=1.0, clip_dir="box")


def _back_color(ctx: dict) -> str:
    face = str(ctx.get("face_hex") or "#F4EBD8")
    from cutora.render.textfx.ramps import mix_hex
    from cutora.render.textfx.spec import is_pure_black
    col = mix_hex(face, "#000000", FLIP_BACK_MIX)
    if is_pure_black(col):
        return "#3A2A18"
    return col


def _flip_clock_fn(u: float, ctx: dict) -> Transform:
    uu = max(0.0, min(1.0, u))
    sy = 2.0 * uu - 1.0
    extra = _back_color(ctx) if uu < 0.5 else None
    return Transform(scale_y=sy, extra_color=extra)


def _rotate_flip_fn(u: float, ctx: dict) -> Transform:
    uu = max(0.0, min(1.0, u))
    sx = 2.0 * uu - 1.0
    rot = -ROTATE_FLIP_DEG * (1.0 - uu)
    extra = _back_color(ctx) if uu < 0.5 else None
    return Transform(scale_x=sx, rot_deg=rot, alpha=uu, extra_color=extra)


def _stack_fn(u: float, ctx: dict) -> Transform:
    uu = max(0.0, min(1.0, u))
    line_h = float(ctx.get("line_h_em") or 1.2)
    extra = -float(ctx.get("started_after") or 0.0) * line_h
    dy = (1.0 - uu) * STACK_DY_EM + extra
    return Transform(dy_em=dy, alpha=min(1.0, uu / 0.15) if uu < 0.15 else 1.0)


def _tracking_in_fn(u: float, ctx: dict) -> Transform:
    uu = max(0.0, min(1.0, u))
    return Transform(tracking_em=(1.0 - uu) * TRACKING_IN_EM)


def _outline_to_fill_fn(u: float, ctx: dict) -> Transform:
    uu = max(0.0, min(1.0, u))
    if uu < 0.6:
        fill = 0.0
    else:
        fill = (uu - 0.6) / 0.4
    return Transform(fill_progress=fill)


def _karaoke_fn(u: float, ctx: dict) -> Transform:
    # Colour is a post-cache LTR tint (word_fx). fill_progress here would
    # bake the whole ROI on the first cache miss and freeze it.
    return Transform()


def _highlight_fn(u: float, ctx: dict) -> Transform:
    return Transform()


def _box_highlight_fn(u: float, ctx: dict) -> Transform:
    return Transform()


def _colour_scale(local_u: float) -> float:
    """1 → 1.15 → 1 over the word, bounce-ish (triangle on first 200ms of u)."""
    u = max(0.0, min(1.0, float(local_u)))
    # Map first COLOUR_BOUNCE_S of a ~0.2s word to the bounce; if word is longer, first 200ms.
    # local_u is 0-1 over the word. Peak at u=0.15, back at u=0.4, then 1.0.
    if u < 0.15:
        p = u / 0.15
        return 1.0 + (COLOUR_PEAK - 1.0) * p
    if u < 0.40:
        p = (u - 0.15) / 0.25
        return COLOUR_PEAK + (1.0 - COLOUR_PEAK) * p
    return 1.0


def _colour_highlight_fn(u: float, ctx: dict) -> Transform:
    s = _colour_scale(float(ctx.get("word_u") if ctx.get("word_u") is not None else u))
    return Transform(scale_x=s, scale_y=s)


def _rotate_highlight_fn(u: float, ctx: dict) -> Transform:
    return Transform()


def _reveal_zoom_blur_fn(u: float, ctx: dict) -> Transform:
    uu = max(0.0, min(1.0, u))
    s = REVEAL_ZOOM_SCALE0 + (1.0 - REVEAL_ZOOM_SCALE0) * uu
    sigma = REVEAL_ZOOM_BLUR_EM * (1.0 - uu)
    alpha = REVEAL_ZOOM_ALPHA0 + (1.0 - REVEAL_ZOOM_ALPHA0) * uu
    return Transform(scale_x=s, scale_y=s, alpha=alpha, blur_sigma_em=sigma)


ANIMS: dict[str, AnimDef] = {
    "none": AnimDef("无", ("in", "out", "loop"), (), _none_fn),
    "fade": AnimDef("淡入淡出", ("in", "out"), ("any",), _fade_fn),
    "float_in_up": AnimDef("上浮入", ("in", "out"), ("any",), _float_in_up_fn),
    "float_in_down": AnimDef("下浮入", ("in", "out"), ("any",), _float_in_down_fn),
    "scale_in": AnimDef("缩放入", ("in", "out"), ("any",), _scale_in_fn),
    "drop_in": AnimDef("落下", ("in", "out"), ("word", "line"), _drop_in_fn),
    "pop_in": AnimDef("弹出", ("in", "out"), ("any",), _pop_in_fn),
    "impact_pop": AnimDef("冲击弹出", ("in",), ("word",), _impact_pop_fn),
    "stomp": AnimDef("重踏", ("in",), ("word", "line"), _stomp_fn),
    "slide": AnimDef("滑入", ("in", "out"), ("any",), _slide_fn),
    "zoom_slow": AnimDef("慢速缩放", ("loop",), ("segment",), _zoom_slow_fn),
    "float_loop": AnimDef("漂浮", ("loop",), ("segment",), _float_loop_fn),
    "reveal_wipe": AnimDef("擦除揭示", ("in", "out"), ("segment", "line"), _reveal_wipe_fn),
    "typewriter": AnimDef("打字机", ("in",), ("word",), _typewriter_fn),
    "line_draw": AnimDef("划线（需描边）", ("in",), ("any",), _line_draw_fn),
    "scroll": AnimDef("滚动歌词", ("loop",), ("segment",), _scroll_fn),
    "reveal_zoom_blur": AnimDef(
        "大→正常·模糊→清晰", ("in",), ("segment",), _reveal_zoom_blur_fn),
    "flip_clock": AnimDef("逐字翻牌", ("in",), ("char",), _flip_clock_fn),
    "rotate_flip": AnimDef("旋转翻转", ("in",), ("word",), _rotate_flip_fn),
    "stack": AnimDef("堆叠", ("in",), ("line",), _stack_fn),
    "tracking_in": AnimDef("字距收拢", ("in",), ("segment", "line"), _tracking_in_fn),
    "outline_to_fill": AnimDef("描边→填充", ("in",), ("any",), _outline_to_fill_fn),
    "karaoke": AnimDef("卡拉OK", ("loop",), ("word",), _karaoke_fn),
    "highlight": AnimDef("高亮当前词", ("loop",), ("word",), _highlight_fn),
    "box_highlight": AnimDef("框高亮", ("loop",), ("word",), _box_highlight_fn),
    "line_bar": AnimDef("跟随句子", (), ("line",), _box_highlight_fn),
    "colour_highlight": AnimDef("彩色高亮", ("loop",), ("word",), _colour_highlight_fn),
    "rotate_highlight": AnimDef("旋转高亮", ("loop",), ("word",), _rotate_highlight_fn),
    # v1 逐行出现: per-line alpha fade + stagger. Same fn as fade; UNIT_WARP
    # in word_fx applies it per line (plain fade is a whole-ROI warp).
    "bullet_reveal": AnimDef("逐行出现", ("in", "out"), ("line", "any"), _fade_fn),
}

OUT_MIRROR = frozenset({
    "fade", "float_in_up", "float_in_down", "scale_in", "pop_in",
    "drop_in", "slide", "reveal_wipe", "bullet_reveal",
})

SLIDE_DIRS = (
    ("left", "滑入←"),
    ("right", "滑入→"),
    ("up", "滑入↑"),
    ("down", "滑入↓"),
)


def stable_seed(*parts: Any) -> int:
    """Process-stable seed. Python hash() is randomized via PYTHONHASHSEED."""
    blob = "|".join(str(p) for p in parts).encode("utf-8")
    digest = hashlib.blake2s(blob, digest_size=8).digest()
    return int.from_bytes(digest, "little") & 0x7FFFFFFF


def rng_for(seg_id: Any, unit_index: int, seed: int) -> random.Random:
    return random.Random(stable_seed(seg_id, unit_index, seed))


def ease(u: float, name: str) -> float:
    fn = EASING_MAP.get(name, ease_out_cubic)
    t = max(0.0, min(1.0, float(u)))
    try:
        return float(fn(t))
    except Exception:
        return t


def _anim_id(name: str) -> str:
    text = str(name or "none").split("+", 1)[0].strip()
    return text if text in ANIMS else "none"


def layout_tracking_em(spec: FxSpec) -> float:
    """Peak tracking used for line-break. tracking_in measures at 0.35 em."""
    peak = float(spec.tracking_em)
    for part in _anim_ids(spec.anim.in_name):
        if part == "tracking_in":
            peak = max(peak, TRACKING_IN_EM)
    return peak


def _anim_ids(name: str) -> list[str]:
    out = []
    for part in str(name or "none").split("+"):
        p = part.strip()
        if p in ANIMS and p != "none" and p not in out:
            out.append(p)
        if len(out) >= 8:
            break
    return out or ["none"]


def _anim_param(name: str) -> str:
    bits = str(name or "").split("+", 1)
    aid = bits[0].strip()
    if aid in ANIMS and len(bits) > 1 and bits[1].strip() not in ANIMS:
        return bits[1].strip()
    if len(bits) > 1 and bits[1].strip() in ANIMS:
        return ""
    return bits[1].strip() if len(bits) > 1 else ""


def phase_anim_items(phase: str) -> list[tuple[str, str]]:
    """Dropdown (label, value) for implemented anims of this phase only."""
    items = [("无", "none")]
    for aid, defn in ANIMS.items():
        if aid == "none" or phase not in defn.phases:
            continue
        if aid == "slide":
            for key, lab in SLIDE_DIRS:
                items.append((lab, "slide+" + key))
        else:
            items.append((defn.label_zh, aid))
    return items


def is_static_hold(t: float, event: dict, spec: FxSpec) -> bool:
    """True when entrance is done, exit has not started, and loop is none."""
    anim = spec.anim
    if spec.emphasis.anim in ("pop", "line_draw") and (
            spec.emphasis.words or spec.emphasis.auto):
        from cutora.render.textfx.emphasis import detect_emphasis
        raw = event.get("edited_text")
        if raw is None:
            raw = event.get("text") or ""
        hits = detect_emphasis(
            str(raw), spec, lines=event.get("lines") or None)
        if hits:
            words = event.get("words") or []
            from cutora.render.textfx.spec import POP_DUR_S
            win = POP_DUR_S if spec.emphasis.anim == "pop" else LINE_DRAW_DUR_S
            for hit in hits:
                if hit.index < len(words) and isinstance(words[hit.index], dict):
                    ts = float(words[hit.index].get("start") or 0.0)
                else:
                    ts = float(event.get("start") or 0.0)
                if ts <= t <= ts + win:
                    return False
    if _anim_id(anim.loop) != "none":
        return False
    start = float(event.get("start") or 0.0)
    end = float(event.get("end") or (start + 1.0))
    if _anim_id(anim.in_name) != "none" and t < start + anim.in_s:
        return False
    if _anim_id(anim.out_name) != "none" and t > end - anim.out_s:
        return False
    return True


def _compressed_timing(n: int, spec: AnimSpec, start: float, end: float) -> tuple[float, float, float]:
    dur = max(0.001, end - start)
    min_hold = max(0.15, 0.2 * dur)
    in_s, out_s, stagger = spec.in_s, spec.out_s, spec.stagger_s
    if _anim_id(spec.in_name) == "stack":
        stagger = in_s
    elif _anim_id(spec.in_name) == "flip_clock":
        stagger = min(stagger, FLIP_STAGGER_S) if stagger else FLIP_STAGGER_S
    in_total = in_s + stagger * max(0, n - 1)
    out_total = out_s + stagger * max(0, n - 1)
    if in_total + out_total + min_hold > dur:
        scale = (dur - min_hold) / max(0.001, in_total + out_total)
        in_s *= scale
        out_s *= scale
        stagger *= scale
    return in_s, out_s, stagger


def unit_progress(
    t: float,
    k: int,
    n: int,
    spec: AnimSpec,
    event: dict,
    word: dict | None = None,
) -> tuple[str, float]:
    """Return (phase, eased_u). phase is in / hold / out."""
    start = float(event.get("start") or 0.0)
    end = float(event.get("end") or (start + 1.0))
    n = max(1, int(n))
    k = max(0, min(n - 1, int(k)))
    in_s, out_s, stagger = _compressed_timing(n, spec, start, end)
    k_start = start + k * stagger
    if spec.unit == "word" and isinstance(word, dict):
        wstart = float(word.get("start") or k_start)
        k_start = max(k_start, wstart - in_s)
    in_end = k_start + in_s
    out_start = end - out_s - (n - 1 - k) * stagger
    if _anim_id(spec.in_name) != "none" and t < in_end:
        u = (t - k_start) / max(0.001, in_s)
        return "in", ease(u, spec.easing)
    if _anim_id(spec.out_name) != "none" and t >= out_start:
        u = (t - out_start) / max(0.001, out_s)
        return "out", ease(u, spec.easing)
    if _anim_id(spec.loop) != "none":
        uu = (float(t) - start) / LOOP_PERIOD_S
        frac = uu - math.floor(uu)
        return "hold", frac
    return "hold", 1.0


def _compose_xf(parts: list[Transform]) -> Transform:
    out = Transform()
    for xf in parts:
        out = Transform(
            alpha=out.alpha * xf.alpha,
            scale_x=out.scale_x * xf.scale_x,
            scale_y=out.scale_y * xf.scale_y,
            dx_em=out.dx_em + xf.dx_em,
            dy_em=out.dy_em + xf.dy_em,
            rot_deg=out.rot_deg + xf.rot_deg,
            clip=min(out.clip, xf.clip),
            clip_dir=xf.clip_dir if xf.clip < 0.999 else out.clip_dir,
            blur_sigma_em=max(out.blur_sigma_em, xf.blur_sigma_em),
            tracking_em=out.tracking_em + xf.tracking_em,
            fill_progress=min(out.fill_progress, xf.fill_progress),
            extra_color=xf.extra_color or out.extra_color,
        )
    return out


def transform_for(phase: str, u: float, spec: AnimSpec, ctx: dict | None = None) -> Transform:
    ctx = dict(ctx or {})
    ctx.setdefault("seed", spec.seed)
    if phase == "in":
        ctx.setdefault("dir", _anim_param(spec.in_name) or "left")
        ids = _anim_ids(spec.in_name)
        return _compose_xf([ANIMS[aid].fn(u, ctx) for aid in ids])
    if phase == "out":
        ctx.setdefault("dir", _anim_param(spec.out_name) or "left")
        ids = _anim_ids(spec.out_name)
        parts = []
        for aid in ids:
            raw_u = (1.0 - u) if aid in OUT_MIRROR else u
            parts.append(ANIMS[aid].fn(raw_u, ctx))
        return _compose_xf(parts)
    if phase == "hold" and _anim_id(spec.loop) != "none":
        return ANIMS[_anim_id(spec.loop)].fn(u, ctx)
    return Transform()


def transform_at(t: float, event: dict, spec: FxSpec, layout=None) -> Transform:
    n = int(getattr(layout, "n_units", 1) or 1)
    phase, u = unit_progress(t, 0, n, spec.anim, event)
    ctx = {
        "seg_id": getattr(layout, "seg_id", "") if layout is not None else "",
        "k": 0,
        "seed": spec.anim.seed,
        "layout": layout,
        "em": float(getattr(layout, "em", 1.0) or 1.0) if layout is not None else 1.0,
        "stroke_em": float(spec.stroke.width_em) if spec.stroke.live() else 0.0,
        "face_hex": "#F4EBD8",
        "emph_color": spec.emphasis.color,
    }
    try:
        from cutora.render.textfx.paint_fill import face_fill_hex
        ctx["face_hex"] = face_fill_hex(spec)
    except Exception:
        pass
    return transform_for(phase, u, spec.anim, ctx)


def xf_is_identity(xf: Transform) -> bool:
    clip_dir = str(xf.clip_dir or "ltr")
    # path clip is consumed at paint time, not by warp_layer_xf.
    clip_ok = xf.clip >= 0.999 or clip_dir == "path"
    return (
        abs(xf.alpha - 1.0) < 1e-4
        and abs(xf.scale_x - 1.0) < 1e-4
        and abs(xf.scale_y - 1.0) < 1e-4
        and abs(xf.dx_em) < 1e-6
        and abs(xf.dy_em) < 1e-6
        and abs(xf.rot_deg) < 1e-3
        and clip_ok
        and clip_dir != "box"
        and xf.blur_sigma_em <= 1e-6
        and not xf.extra_color
    )


def _pad_scale(peak: float, wide_em: float = 8.0, tall_em: float = 1.6) -> tuple[float, float, float, float]:
    extra = 0.5 * max(0.0, peak - 1.0)
    lr = extra * wide_em + 0.08
    tb = extra * tall_em + 0.08
    return (lr, tb, lr, tb)


def _merge(a, b):
    return (max(a[0], b[0]), max(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def transform_bounds_em(spec: FxSpec) -> tuple[float, float, float, float]:
    """(left, top, right, bottom) extra pad in em. Includes pop/scatter."""
    import math as _math
    pad = (0.0, 0.0, 0.0, 0.0)
    names = (spec.anim.in_name, spec.anim.out_name, spec.anim.loop)
    for raw in names:
      for aid in _anim_ids(raw):
        if aid == "none":
            continue
        if aid == "float_in_up":
            pad = _merge(pad, (0.0, 0.0, 0.0, FLOAT_DY_EM + 0.08))
        elif aid == "float_in_down":
            pad = _merge(pad, (0.0, FLOAT_DY_EM + 0.08, 0.0, 0.0))
        elif aid == "drop_in":
            pad = _merge(pad, (0.08, DROP_DY_EM + 0.12, 0.08, 0.12))
        elif aid == "pop_in":
            pad = _merge(pad, _pad_scale(POP_IN_PEAK))
        elif aid == "impact_pop":
            extra = _pad_scale(IMPACT_PEAK)
            rot = 4.0 * _math.pi / 180.0
            s, c = abs(_math.sin(rot)), abs(_math.cos(rot))
            extra_r = 0.5 * (8.0 * s + 1.6 * (1.0 - c)) + 0.08
            pad = _merge(pad, (
                extra[0] + extra_r, extra[1] + extra_r,
                extra[2] + extra_r, extra[3] + extra_r))
        elif aid == "stomp":
            extra = _pad_scale(STOMP_PEAK)
            sh = STOMP_SHAKE_EM + 0.08
            pad = _merge(pad, (
                extra[0] + sh, extra[1] + sh, extra[2] + sh, extra[3] + sh))
        elif aid == "slide":
            d = _anim_param(raw) or "left"
            if d == "right":
                pad = _merge(pad, (0.0, 0.08, SLIDE_DIST_EM + 0.08, 0.08))
            elif d == "up":
                pad = _merge(pad, (0.08, SLIDE_DIST_EM + 0.08, 0.08, 0.0))
            elif d == "down":
                pad = _merge(pad, (0.08, 0.0, 0.08, SLIDE_DIST_EM + 0.08))
            else:
                pad = _merge(pad, (SLIDE_DIST_EM + 0.08, 0.08, 0.0, 0.08))
        elif aid == "zoom_slow":
            pad = _merge(pad, _pad_scale(1.0 + ZOOM_SLOW_AMP))
        elif aid == "float_loop":
            pad = _merge(pad, (0.08, FLOAT_LOOP_EM + 0.08, 0.08, FLOAT_LOOP_EM + 0.08))
        elif aid in ("reveal_wipe", "typewriter", "scroll", "bullet_reveal"):
            pad = _merge(pad, (0.08, 0.08, 0.08, 0.08))
        elif aid == "line_draw":
            if spec.stroke.live():
                pad = _merge(pad, (0.12, 0.12, 0.12, 0.12))
        elif aid == "reveal_zoom_blur":
            extra = 0.5 * (REVEAL_ZOOM_SCALE0 - 1.0)
            blur = 3.0 * REVEAL_ZOOM_BLUR_EM
            lr = extra * 8.0 + blur + 0.12
            tb = extra * 1.6 + blur + 0.12
            pad = _merge(pad, (lr, tb, lr, tb))
        elif aid == "flip_clock":
            pad = _merge(pad, (0.12, 0.12, 0.12, 0.12))
        elif aid == "rotate_flip":
            rot = ROTATE_FLIP_DEG * _math.pi / 180.0
            s, c = abs(_math.sin(rot)), abs(_math.cos(rot))
            extra_r = 0.5 * (8.0 * s + 1.6 * (1.0 - c)) + 0.12
            pad = _merge(pad, (extra_r, extra_r, extra_r, extra_r))
        elif aid == "stack":
            pad = _merge(pad, (0.12, STACK_DY_EM + 1.6 + 0.12, 0.12, STACK_DY_EM + 0.12))
        elif aid == "tracking_in":
            pad = _merge(pad, (0.08, 0.08, 0.08, 0.08))
        elif aid == "colour_highlight":
            pad = _merge(pad, _pad_scale(COLOUR_PEAK))
        elif aid == "rotate_highlight":
            rot = 6.0 * _math.pi / 180.0
            s, c = abs(_math.sin(rot)), abs(_math.cos(rot))
            extra_r = 0.5 * (8.0 * s + 1.6 * (1.0 - c)) + 0.08
            pad = _merge(pad, (extra_r, extra_r, extra_r, extra_r))
        elif aid in ("karaoke", "highlight", "box_highlight", "outline_to_fill"):
            pad = _merge(pad, (0.12, 0.12, 0.12, 0.12))
    if spec.emphasis.anim == "pop" and (
            spec.emphasis.words or spec.emphasis.auto):
        extra = 0.5 * (POP_PEAK - 1.0)
        pad = _merge(pad, (
            extra * 8.0 + POP_SIDE_EM + 0.08,
            extra * 1.6 + 0.08,
            extra * 8.0 + POP_SIDE_EM + 0.08,
            extra * 0.8 + 0.08,
        ))
    if spec.emphasis.anim == "line_draw" and (
            spec.emphasis.words or spec.emphasis.auto):
        pad = _merge(pad, (
            0.08, 0.08, 0.08,
            LINE_DRAW_UNDER_EM + LINE_DRAW_EM + 0.08,
        ))
    if spec.scatter.enabled:
        rad = abs(spec.scatter.rot_jitter_deg) * _math.pi / 180.0
        s, c = abs(_math.sin(rad)), abs(_math.cos(rad))
        nw = 8.0 * c + 1.4 * s
        nh = 8.0 * s + 1.4 * c
        extra_lr = max(0.0, 0.5 * (nw - 8.0)) + 0.08
        extra_tb = max(0.0, 0.5 * (nh - 1.4)) + 0.08
        pad = _merge(pad, (extra_lr, extra_tb, extra_lr, extra_tb))
    return pad

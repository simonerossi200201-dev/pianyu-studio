# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""FxSpec: v2 style.fx defaults, clamp, unknown-key warnings, pure-black check."""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from cutora.core.log import get_logger

logger = get_logger("cutora.render.textfx.spec")

PURE_BLACK_MAX = 0x10
_HEX8 = re.compile(r"^#([0-9A-Fa-f]{8})$")
_HEX6 = re.compile(r"^#([0-9A-Fa-f]{6})$")
_HEX3 = re.compile(r"^#([0-9A-Fa-f]{3})$")
_WARNED: set[str] = set()

FILL_TYPES = ("solid", "gradient", "metal")
EMPH_FILL_TYPES = ("solid", "gradient")
METAL_PRESETS = ("gold", "silver", "chrome", "rose")
GLOW_MODES = ("soft", "neon")
SHADOW_LIGHT = ("parallel", "point")
SHADOW_NEW_KEYS = frozenset({
    "light", "angle_deg", "distance_em", "size_em", "falloff", "strength",
    "enabled",
})
SHADOW_OLD_KEYS = frozenset({"sigma_em", "dx_em", "dy_em", "color", "alpha"})
# Visible defaults when the 投影 section header turns on (not user knobs).
SHADOW_ON_DISTANCE_EM = 0.18
SHADOW_ON_SIZE_EM = 0.10
SHADOW_ON_STRENGTH = 0.6
# Same idea for 描边 / 发光 / 背景. Stored values stay; the header writes enabled.
STROKE_ON_WIDTH_EM = 0.08
GLOW_ON_SIGMA_EM = 0.18
BG_ON_TYPE = "box"
# Contact floor so distance_em=0 still has a penumbra (Q43).
SHADOW_CONTACT_EM = 0.02
# Point-light radial scale. Shared by paint_shadow and expansion_px.
SHADOW_POINT_K = 0.85
SHADOW_DIST_MIN_EM = 0.15
BG_TYPES = ("none", "box", "glass", "texture", "bubble")
BG_SIZE_MODES = ("fit_text", "fixed", "hug_glyphs", "line_hug")
# Outset of a hug mask, in em. line_hug may be negative (shrink).
# hug_glyphs clamps the used value at 0. Old projects omit the key.
HUG_PAD_EM_DEFAULT = 0.18
HUG_PAD_EM_MIN = -0.3
HUG_PAD_EM_MAX = 1.0
# Morphological close that bridges neighbouring glyphs. em.
# Separate from radius_em: a corner radius of 0.5 fills counters such as H.
# Old projects omit the key. Nothing shipped hug_glyphs before this field.
HUG_JOIN_EM_DEFAULT = 0.06
HUG_JOIN_EM_MAX = 0.15
# Canvas pixels. 0 on a fixed axis means "use the text box on that axis".
BG_SIZE_PX_MAX = 10000.0
BG_OFFSET_PX_MAX = 5000.0
BG_GRAD_TYPES = ("none", "linear_h", "linear_v", "linear_angle", "center", "fresnel")
BG_BLEND = ("normal", "multiply", "screen", "overlay", "soft_light")
BG_ALIGN = ("center", "left", "right", "alternate")
TEXTURES = ("paper", "linen", "grid")
JOIN_TYPES = ("round", "miter")
UNITS = ("segment", "line", "word", "char")
LETTER_CASES = ("none", "upper")
EMPH_AUTO = ("numbers", "caps", "glossary", "longest", "list")
EMPH_FONT = ("body", "emphasis")
EMPH_ANIM = ("none", "line_draw", "pop")
LINE_BAR_STYLES = ("solid", "dashed", "dotted", "wave", "brush")
LINE_BAR_ANIMS = ("none", "draw")
DEFAULT_LINE_BAR = "#F2C14E"

FX_KEYS = frozenset({
    "fill", "stroke", "glow", "shadow", "extrude", "bg", "fonts",
    "emphasis", "anim", "scatter", "tracking_em", "letter_case",
    "follow_word", "follow_word_tint", "follow_word_box",
    "follow_sentence", "line_bar", "box_highlight",
})
DEFAULT_FILL = "#F4EBD8"
DEFAULT_STROKE = "#141A26"
DEFAULT_GLOW = "#22D3EE"
DEFAULT_SHADOW = "#141A26"
DEFAULT_BG = "#0B1A33"
DEFAULT_EMPH = "#22D3EE"
DEFAULT_TINT = "#F1E9D8"
# Current-word box product default. Same hex as DEFAULT_BG so a new
# follow_word (tint+box both on) still looks like Q51's tint_also path,
# but it is NOT spec.bg.color — changing 背景色块 must not move the box.
DEFAULT_FOLLOW_BOX = DEFAULT_BG
# T04f-3-fix: pop peak was 1.16 and ate the previous word / ROI edge.
# Intensity knobs stay hardcoded until T04f-4 designs anim strength.
POP_PEAK = 1.08
POP_SIDE_EM = 0.04
POP_DUR_S = 0.28


def warn_once(key: str, msg: str) -> None:
    if key in _WARNED:
        return
    _WARNED.add(key)
    logger.warning(msg)


def parse_color(value: Any, default: str) -> str:
    """Return #RRGGBB or #RRGGBBAA, or *default* when not a hex color."""
    if not isinstance(value, str):
        return default
    raw = value.strip()
    m = _HEX8.match(raw)
    if m:
        return "#" + m.group(1).upper()
    m = _HEX6.match(raw)
    if m:
        return "#" + m.group(1).upper()
    m = _HEX3.match(raw)
    if m:
        a, b, c = m.group(1)
        return ("#%s%s%s%s%s%s" % (a, a, b, b, c, c)).upper()
    return default


def rgb_tuple(color: str) -> tuple[int, int, int]:
    c = parse_color(color, "#FFFFFF").lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def hex_with_alpha(color: str, alpha: float) -> str:
    """Multiply *alpha* onto the color's own AA (missing AA = opaque)."""
    r, g, b, a = rgba_tuple(color)
    a = max(0.0, min(1.0, float(a) * float(alpha)))
    return "#%02X%02X%02X%02X" % (r, g, b, int(round(a * 255.0)))


def emphasis_colors(spec: "FxSpec") -> list[str]:
    emph = getattr(spec, "emphasis", None)
    if emph is None:
        return [DEFAULT_EMPH]
    cols = [str(c) for c in (getattr(emph, "colors", None) or []) if c]
    if not cols and getattr(emph, "color", ""):
        cols = [str(emph.color)]
    return cols or [DEFAULT_EMPH]


def emphasis_is_gradient(spec: "FxSpec") -> bool:
    """True when emphasis paints a 2+ stop ramp (not a per-word palette)."""
    emph = getattr(spec, "emphasis", None)
    if emph is None:
        return False
    if str(getattr(emph, "type", "solid") or "solid") != "gradient":
        return False
    return len(emphasis_colors(spec)) >= 2


def follow_tint_colors(spec: "FxSpec") -> list[str]:
    """Current-word glyph colors. Product default, never emphasis / bg."""
    fw = getattr(spec, "follow_word", None)
    tint = getattr(fw, "tint", None) if fw is not None else None
    cols = [str(c) for c in (getattr(tint, "colors", None) or []) if c]
    if cols:
        return cols
    return [DEFAULT_EMPH]


def follow_tint_is_gradient(spec: "FxSpec") -> bool:
    fw = getattr(spec, "follow_word", None)
    tint = getattr(fw, "tint", None) if fw is not None else None
    if tint is None:
        return False
    if str(getattr(tint, "type", "solid") or "solid") != "gradient":
        return False
    return len(follow_tint_colors(spec)) >= 2


def follow_tint_angle(spec: "FxSpec") -> float:
    fw = getattr(spec, "follow_word", None)
    tint = getattr(fw, "tint", None) if fw is not None else None
    try:
        raw = getattr(tint, "angle", 90.0)
        if raw is None:
            return 90.0
        return float(raw)
    except (TypeError, ValueError):
        return 90.0


def follow_box_color(spec: "FxSpec") -> str:
    """Current-word box color. Product default, never emphasis / bg."""
    fw = getattr(spec, "follow_word", None)
    box = getattr(fw, "box", None) if fw is not None else None
    raw = str(getattr(box, "color", "") or "").strip()
    if raw:
        return parse_color(raw, DEFAULT_FOLLOW_BOX)
    bh = getattr(spec, "box_highlight", None)
    raw_bh = str(getattr(bh, "color", "") or "").strip() if bh is not None else ""
    if raw_bh:
        return parse_color(raw_bh, DEFAULT_FOLLOW_BOX)
    return DEFAULT_FOLLOW_BOX


def rgba_tuple(color: str) -> tuple[int, int, int, float]:
    """RGB 0–255 and alpha 0–1. Missing AA means opaque."""
    c = parse_color(color, "#FFFFFF").lstrip("#")
    r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    a = int(c[6:8], 16) / 255.0 if len(c) >= 8 else 1.0
    return r, g, b, a


def is_pure_black(color: Any) -> bool:
    """True when all three channels are ≤ 0x10. Invalid colors are not black."""
    if not isinstance(color, str):
        return False
    parsed = parse_color(color, "")
    if not parsed:
        return False
    r, g, b = rgb_tuple(parsed)
    return r <= PURE_BLACK_MAX and g <= PURE_BLACK_MAX and b <= PURE_BLACK_MAX


def iter_color_fields(obj: Any, path: str = "") -> Iterable[tuple[str, str]]:
    """Yield (json-path, value) for every '#…' string in a nested dict/list."""
    if isinstance(obj, dict):
        for key, val in obj.items():
            child = "%s.%s" % (path, key) if path else str(key)
            yield from iter_color_fields(val, child)
    elif isinstance(obj, (list, tuple)):
        for i, val in enumerate(obj):
            yield from iter_color_fields(val, "%s[%d]" % (path, i))
    elif isinstance(obj, str) and obj.startswith("#"):
        yield path, obj


def collect_pure_black(obj: Any, path: str = "") -> list[tuple[str, str]]:
    return [(p, v) for p, v in iter_color_fields(obj, path) if is_pure_black(v)]


def clamp_num(name: str, value: Any, lo: float, hi: float, default: float) -> float:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    if x < lo or x > hi:
        warn_once("range:%s" % name, "fx.%s clamped to [%.3f, %.3f]" % (name, lo, hi))
        return max(lo, min(hi, x))
    return x


def clamp_int(name: str, value: Any, lo: int, hi: int, default: int) -> int:
    return int(round(clamp_num(name, value, lo, hi, default)))


def pick_enum(name: str, value: Any, allowed: tuple[str, ...], default: str) -> str:
    if value is None:
        return default
    text = str(value)
    if text in allowed:
        return text
    warn_once("enum:%s" % name, "fx.%s=%r unknown, using %s" % (name, value, default))
    return default


def take_known(raw: Any, known: frozenset[str], prefix: str) -> dict:
    if not isinstance(raw, dict):
        return {}
    for key in raw:
        if key not in known:
            warn_once("unk:%s.%s" % (prefix, key),
                      "unknown fx key %s.%s ignored" % (prefix, key))
    return {k: raw[k] for k in known if k in raw}


@dataclass
class FillSpec:
    type: str = "solid"
    colors: list[str] = field(default_factory=lambda: [DEFAULT_FILL])
    angle: float = 90.0
    metal: str = "gold"


@dataclass
class StrokeSpec:
    width_em: float = 0.0
    color: str = DEFAULT_STROKE
    join: str = "round"
    enabled: bool = False

    def live(self) -> bool:
        return bool(self.enabled) and float(self.width_em or 0) > 0


@dataclass
class GlowSpec:
    sigma_em: float = 0.0
    spread_em: float = 0.02
    color: str = DEFAULT_GLOW
    strength: float = 0.8
    mode: str = "soft"
    enabled: bool = False

    def live(self) -> bool:
        return bool(self.enabled) and float(self.sigma_em or 0) > 0


@dataclass
class ShadowSpec:
    light: str = "parallel"
    angle_deg: float = 45.0
    distance_em: float = 0.0
    size_em: float = SHADOW_ON_SIZE_EM
    falloff: float = 0.0
    color: str = DEFAULT_SHADOW
    strength: float = SHADOW_ON_STRENGTH
    enabled: bool = False
    # Compat input / byte-identical paint for old projects.
    sigma_em: float = 0.0
    dx_em: float = 0.04
    dy_em: float = 0.06
    alpha: float = 0.6
    _legacy: bool = True


@dataclass
class ExtrudeSpec:
    depth_em: float = 0.0
    angle: float = 135.0
    color: str = ""


@dataclass
class FadeSpec:
    top: float = 0.0
    bottom: float = 0.0
    left: float = 0.0
    right: float = 0.0
    top_offset: float = 0.0
    bottom_offset: float = 0.0
    left_offset: float = 0.0
    right_offset: float = 0.0


@dataclass
class BgSpec:
    type: str = "none"
    color: str = DEFAULT_BG
    alpha: float = 0.55
    radius_em: float = 0.4
    pad_em: tuple[float, float] = (0.5, 0.25)
    glass_sigma_em: float = 0.7
    glass_border: bool = True
    glass_thickness_em: float = 0.14
    glass_refraction: float = 0.55
    glass_bevel: float = 0.7
    glass_sheen: float = 0.45
    glass_light_deg: float = 315.0
    texture: str = "paper"
    texture_tint: str = DEFAULT_TINT
    align: str = "center"
    line_gap_em: float = 0.18
    fade: FadeSpec = field(default_factory=FadeSpec)
    grad_type: str = "none"
    grad_colors: list = field(default_factory=list)
    grad_angle: float = 0.0
    grad_highlight: bool = False
    blend: str = "normal"
    # fit_text: pad around the glyphs (historical). fixed: canvas-pixel box.
    # hug_glyphs: clip the same fill to a dilated glyph mask.
    # line_hug: one slab per line (em ascent/descent), union, then round.
    size_mode: str = "fit_text"
    width_px: float = 0.0
    height_px: float = 0.0
    offset_x: float = 0.0
    offset_y: float = 0.0
    hug_pad_em: float = HUG_PAD_EM_DEFAULT
    # Close radius for hug_glyphs only. Does not round the fit_text / fixed box.
    hug_join_em: float = HUG_JOIN_EM_DEFAULT
    # Q129：四边各自向外扩展（em），左 / 上 / 右 / 下。任何 size_mode 都生效，
    # 默认全 0 = 老几何一像素不变。
    grow_em: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    enabled: bool = False

    def grow_px(self, em: float) -> tuple[float, float, float, float]:
        g = self.grow_em or (0.0, 0.0, 0.0, 0.0)
        return tuple(float(v) * float(em) for v in g)

    def kind(self) -> str:
        """Paint type. Off (enabled=False) is 'none' even if type is kept."""
        if not self.enabled:
            return "none"
        t = str(self.type or "none")
        return t if t in BG_TYPES else "none"


@dataclass
class FontsSpec:
    body: str = ""
    emphasis: str = ""


@dataclass
class EmphasisSpec:
    auto: list[str] = field(default_factory=list)
    words: list[str] = field(default_factory=list)
    type: str = "solid"
    colors: list[str] = field(default_factory=lambda: [DEFAULT_EMPH])
    angle: float = 90.0
    color: str = DEFAULT_EMPH
    palette: list[str] = field(default_factory=list)
    scale: float = 1.25
    font: str = "emphasis"
    anim: str = "none"
    max_per_cue: int = 2


@dataclass
class AnimSpec:
    in_name: str = "none"
    loop: str = "none"
    out_name: str = "none"
    unit: str = "segment"
    stagger_s: float = 0.05
    in_s: float = 0.35
    out_s: float = 0.25
    easing: str = "ease_out"
    seed: int = 0


@dataclass
class ScatterSpec:
    enabled: bool = False
    size_jitter: float = 0.35
    pos_jitter_em: tuple[float, float] = (1.5, 0.8)
    rot_jitter_deg: float = 6.0


@dataclass
class LineBarSpec:
    color: str = DEFAULT_LINE_BAR
    height_em: float = 0.22
    pad_em: float = 0.18
    offset_y_em: float = 0.06
    radius_em: float = 0.4
    glow_em: float = 0.0
    style: str = "solid"
    anim: str = "none"
    # Compat: 6-hex colour + separate alpha; missing radius uses bg.radius_em.
    _alpha: float = 0.7
    _use_bg_radius: bool = True


@dataclass
class BoxHighlightSpec:
    """Word box. Missing fields stay None so old readers can tell 'unset'."""
    color: str = ""
    alpha: float | None = None
    pad_em: float | None = None
    offset_y_em: float | None = None
    radius_em: float | None = None
    stroke_width: float = 0.0
    glow_radius: float = 0.0
    overshoot: float = 0.0
    duration: float = 0.0


@dataclass
class FollowTintSpec:
    """Current-word glyph color. Independent of fx.emphasis."""
    enabled: bool = False
    type: str = "solid"
    colors: list[str] = field(default_factory=lambda: [DEFAULT_EMPH])
    angle: float = 90.0


@dataclass
class FollowBoxSpec:
    """Current-word box. Independent of fx.bg and fx.emphasis."""
    enabled: bool = False
    color: str = DEFAULT_FOLLOW_BOX
    alpha: float = 0.55
    pad_em: float = 0.12
    offset_y_em: float = 0.0
    radius_em: float = 0.4
    stroke_width: float = 0.0
    glow_radius: float = 0.0
    overshoot: float = 0.0
    duration: float = 0.0


@dataclass
class FollowWordSpec:
    tint: FollowTintSpec = field(default_factory=FollowTintSpec)
    box: FollowBoxSpec = field(default_factory=FollowBoxSpec)

    def __bool__(self) -> bool:
        return bool(self.tint.enabled or self.box.enabled)


@dataclass
class FxSpec:
    fill: FillSpec = field(default_factory=FillSpec)
    stroke: StrokeSpec = field(default_factory=StrokeSpec)
    glow: GlowSpec = field(default_factory=GlowSpec)
    shadow: ShadowSpec = field(default_factory=ShadowSpec)
    extrude: ExtrudeSpec = field(default_factory=ExtrudeSpec)
    bg: BgSpec = field(default_factory=BgSpec)
    fonts: FontsSpec = field(default_factory=FontsSpec)
    emphasis: EmphasisSpec = field(default_factory=EmphasisSpec)
    anim: AnimSpec = field(default_factory=AnimSpec)
    scatter: ScatterSpec = field(default_factory=ScatterSpec)
    tracking_em: float = 0.0
    letter_case: str = "none"
    follow_word: FollowWordSpec = field(default_factory=FollowWordSpec)
    follow_word_tint: bool = True
    follow_word_box: bool = True
    follow_sentence: bool = False
    line_bar: LineBarSpec = field(default_factory=LineBarSpec)
    box_highlight: BoxHighlightSpec = field(default_factory=BoxHighlightSpec)
    has_pure_black: bool = False

    def max_transform_bounds(self) -> tuple[float, float, float, float]:
        """Max extra pad (left, top, right, bottom) in em over the anim cycle.

        Raster-only: layout.origin shifts so glyphs stay at the same
        screen position. Zero when nothing scales or moves.
        Numbers must match anim.py peaks (POP_IN 1.18, IMPACT 1.30, STOMP 1.70).
        """
        import math
        l = t = r = b = 0.0

        def _merge(nl, nt, nr, nb):
            nonlocal l, t, r, b
            l, t, r, b = max(l, nl), max(t, nt), max(r, nr), max(b, nb)

        def _scale_pad(peak, wide=8.0, tall=1.6):
            extra = 0.5 * max(0.0, peak - 1.0)
            lr = extra * wide + 0.08
            tb = extra * tall + 0.08
            _merge(lr, tb, lr, tb)

        def _aids(name):
            out = []
            for part in str(name or "none").split("+"):
                p = part.strip()
                if p and p != "none":
                    out.append(p)
                if len(out) >= 2:
                    break
            return out

        def _param(name):
            bits = str(name or "").split("+", 1)
            return bits[1].strip() if len(bits) > 1 else ""

        for raw in (self.anim.in_name, self.anim.out_name, self.anim.loop):
            for aid in _aids(raw):
                if aid == "float_in_up":
                    _merge(0.0, 0.0, 0.0, 0.68)
                elif aid == "float_in_down":
                    _merge(0.0, 0.68, 0.0, 0.0)
                elif aid == "drop_in":
                    _merge(0.08, 2.12, 0.08, 0.12)
                elif aid == "pop_in":
                    _scale_pad(1.18)
                elif aid == "impact_pop":
                    _scale_pad(1.30)
                    rot = 4.0 * math.pi / 180.0
                    s, c = abs(math.sin(rot)), abs(math.cos(rot))
                    extra_r = 0.5 * (8.0 * s + 1.6 * (1.0 - c)) + 0.08
                    _merge(extra_r, extra_r, extra_r, extra_r)
                elif aid == "stomp":
                    _scale_pad(1.70)
                    _merge(0.14, 0.14, 0.14, 0.14)
                elif aid == "slide":
                    d = _param(raw) or "left"
                    if d == "right":
                        _merge(0.0, 0.08, 1.58, 0.08)
                    elif d == "up":
                        _merge(0.08, 1.58, 0.08, 0.0)
                    elif d == "down":
                        _merge(0.08, 0.0, 0.08, 1.58)
                    else:
                        _merge(1.58, 0.08, 0.0, 0.08)
                elif aid == "zoom_slow":
                    _scale_pad(1.015)
                elif aid == "float_loop":
                    _merge(0.08, 0.155, 0.08, 0.155)
                elif aid in ("reveal_wipe", "typewriter", "scroll"):
                    _merge(0.08, 0.08, 0.08, 0.08)
                elif aid == "line_draw":
                    if self.stroke.live():
                        _merge(0.12, 0.12, 0.12, 0.12)
                elif aid == "reveal_zoom_blur":
                    extra = 0.5 * (1.35 - 1.0)
                    blur = 3.0 * 0.5
                    _merge(
                        extra * 8.0 + blur + 0.12,
                        extra * 1.6 + blur + 0.12,
                        extra * 8.0 + blur + 0.12,
                        extra * 1.6 + blur + 0.12,
                    )
                elif aid == "flip_clock":
                    _merge(0.12, 0.12, 0.12, 0.12)
                elif aid == "rotate_flip":
                    rot = 14.0 * math.pi / 180.0
                    s, c = abs(math.sin(rot)), abs(math.cos(rot))
                    extra_r = 0.5 * (8.0 * s + 1.6 * (1.0 - c)) + 0.12
                    _merge(extra_r, extra_r, extra_r, extra_r)
                elif aid == "stack":
                    _merge(0.12, 1.2 + 1.6 + 0.12, 0.12, 1.2 + 0.12)
                elif aid == "tracking_in":
                    _merge(0.08, 0.08, 0.08, 0.08)
                elif aid == "colour_highlight":
                    _scale_pad(1.15)
                elif aid == "rotate_highlight":
                    rot = 6.0 * math.pi / 180.0
                    s, c = abs(math.sin(rot)), abs(math.cos(rot))
                    extra_r = 0.5 * (8.0 * s + 1.6 * (1.0 - c)) + 0.08
                    _merge(extra_r, extra_r, extra_r, extra_r)
                elif aid in (
                        "karaoke", "highlight", "box_highlight",
                        "outline_to_fill"):
                    _merge(0.12, 0.12, 0.12, 0.12)
        if self.emphasis.anim == "pop" and (
                self.emphasis.words or self.emphasis.auto):
            extra = 0.5 * (POP_PEAK - 1.0)
            _merge(
                extra * 8.0 + POP_SIDE_EM + 0.08,
                extra * 1.6 + 0.08,
                extra * 8.0 + POP_SIDE_EM + 0.08,
                extra * 0.8 + 0.08,
            )
        if self.emphasis.anim == "line_draw" and (
                self.emphasis.words or self.emphasis.auto):
            # Match anim.py LINE_DRAW_UNDER_EM=0.18, LINE_DRAW_EM=0.08.
            _merge(0.08, 0.08, 0.08, 0.18 + 0.08 + 0.08)
        if self.scatter.enabled:
            rad = abs(self.scatter.rot_jitter_deg) * math.pi / 180.0
            s, c = abs(math.sin(rad)), abs(math.cos(rad))
            nw = 8.0 * c + 1.4 * s
            nh = 8.0 * s + 1.4 * c
            extra_lr = max(0.0, 0.5 * (nw - 8.0)) + 0.08
            extra_tb = max(0.0, 0.5 * (nh - 1.4)) + 0.08
            _merge(extra_lr, extra_tb, extra_lr, extra_tb)
        return (l, t, r, b)

    def cache_hash(self) -> str:
        memo = self.__dict__.get("_hash_memo")
        if memo:
            return memo
        payload = asdict(self)
        payload.pop("has_pure_black", None)
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        digest = hashlib.blake2s(blob, digest_size=8).hexdigest()
        self.__dict__["_hash_memo"] = digest
        return digest


def _colors_list(raw: Any, default: list[str], nmin: int = 1, nmax: int = 3) -> list[str]:
    if not isinstance(raw, (list, tuple)) or not raw:
        return list(default)
    out = []
    fallback = default[0] if default else DEFAULT_FILL
    for item in raw[:nmax]:
        out.append(parse_color(item, fallback))
    while len(out) < nmin:
        out.append(default[0])
    return out


def _parse_fill(raw: Any) -> FillSpec:
    d = take_known(raw, frozenset({"type", "colors", "angle", "metal"}), "fill")
    colors = _colors_list(d.get("colors"), [DEFAULT_FILL], nmin=1, nmax=5)
    return FillSpec(
        type=pick_enum("fill.type", d.get("type"), FILL_TYPES, "solid"),
        colors=colors,
        angle=clamp_num("fill.angle", d.get("angle", 90), 0, 360, 90),
        metal=pick_enum("fill.metal", d.get("metal"), METAL_PRESETS, "gold"),
    )


def _parse_stroke(raw: Any) -> StrokeSpec:
    d = take_known(
        raw, frozenset({"width_em", "color", "join", "enabled"}), "stroke")
    width_em = clamp_num("stroke.width_em", d.get("width_em", 0), 0, 0.3, 0)
    if "enabled" in d:
        enabled = bool(d.get("enabled"))
    else:
        # Old projects: width_em == 0 meant the section was off. Keep the
        # stored width (0) so unmodified paint stays byte-identical; the
        # panel seeds STROKE_ON_WIDTH_EM when the user turns it on.
        enabled = float(width_em or 0) > 0
    return StrokeSpec(
        width_em=width_em,
        color=parse_color(d.get("color"), DEFAULT_STROKE),
        join=pick_enum("stroke.join", d.get("join"), JOIN_TYPES, "round"),
        enabled=enabled,
    )


def _parse_glow(raw: Any) -> GlowSpec:
    d = take_known(
        raw, frozenset({
            "sigma_em", "spread_em", "color", "strength", "mode", "enabled",
        }), "glow")
    sigma_em = clamp_num("glow.sigma_em", d.get("sigma_em", 0), 0, 0.6, 0)
    if "enabled" in d:
        enabled = bool(d.get("enabled"))
    else:
        enabled = float(sigma_em or 0) > 0
    return GlowSpec(
        sigma_em=sigma_em,
        spread_em=clamp_num("glow.spread_em", d.get("spread_em", 0.02), 0, 0.2, 0.02),
        color=parse_color(d.get("color"), DEFAULT_GLOW),
        strength=clamp_num("glow.strength", d.get("strength", 0.8), 0, 1.5, 0.8),
        mode=pick_enum("glow.mode", d.get("mode"), GLOW_MODES, "soft"),
        enabled=enabled,
    )


def _legacy_keys_still_drive(
    d: dict, sigma: float, dx: float, dy: float, alpha: float,
) -> bool:
    """True when old paint inputs are present and new knobs have not diverged.

    Panel load/collect injects derived light/angle/distance/size/strength
    while keeping sigma_em/dx_em/dy_em/alpha. Unmodified round-trip must
    stay on _legacy paint.
    """
    if sigma <= 1e-9:
        return False
    if not any(k in d for k in SHADOW_NEW_KEYS):
        return True
    if "enabled" in d and not bool(d.get("enabled")):
        return False
    if str(d.get("light") or "parallel") != "parallel":
        return False
    if float(d.get("falloff") or 0) > 0.02:
        return False
    if abs(float(d.get("size_em") or 0) - sigma) > 0.02:
        return False
    if abs(float(d.get("strength") or 0) - alpha) > 0.02:
        return False
    dist_legacy = math.hypot(dx, dy)
    dist_new = float(d.get("distance_em") or 0)
    if abs(dist_new - dist_legacy) > 0.03 and abs(
            dist_new - max(dist_legacy, 0.05)) > 0.03:
        return False
    ang_legacy = (math.degrees(math.atan2(dy, dx)) + 360.0) % 360.0
    if dist_legacy <= 1e-9:
        ang_legacy = 45.0
    ang_new = float(d.get("angle_deg") if d.get("angle_deg") is not None else ang_legacy)
    da = abs((ang_new - ang_legacy + 180.0) % 360.0 - 180.0)
    return da <= 2.5


def point_light_scale(distance_em: float, t: float = 1.0) -> float:
    """Far-layer radial scale. Same formula as paint_shadow."""
    dist_scale = max(float(distance_em), SHADOW_DIST_MIN_EM)
    return 1.0 + SHADOW_POINT_K * float(t) / dist_scale


def _parse_shadow(raw: Any) -> ShadowSpec:
    d = take_known(raw, SHADOW_NEW_KEYS | SHADOW_OLD_KEYS, "shadow")
    has_new = isinstance(raw, dict) and any(k in raw for k in SHADOW_NEW_KEYS)
    sigma = clamp_num("shadow.sigma_em", d.get("sigma_em", 0), 0, 0.4, 0)
    dx = clamp_num("shadow.dx_em", d.get("dx_em", 0.04), -0.3, 0.3, 0.04)
    dy = clamp_num("shadow.dy_em", d.get("dy_em", 0.06), -0.3, 0.3, 0.06)
    alpha = clamp_num("shadow.alpha", d.get("alpha", 0.6), 0, 1, 0.6)
    color = parse_color(d.get("color"), DEFAULT_SHADOW)
    dist_legacy = math.hypot(dx, dy)
    ang_legacy = (math.degrees(math.atan2(dy, dx)) + 360.0) % 360.0
    if dist_legacy <= 1e-9:
        ang_legacy = 45.0
    use_legacy = (not has_new) or _legacy_keys_still_drive(d, sigma, dx, dy, alpha)
    if not use_legacy:
        if "enabled" in d:
            enabled = bool(d.get("enabled"))
        else:
            # Pre-Q41: distance_em == 0 meant the section was off.
            enabled = float(d.get("distance_em") or 0) > 0
        size_fallback = sigma if sigma > 0 else SHADOW_ON_SIZE_EM
        return ShadowSpec(
            light=pick_enum("shadow.light", d.get("light"), SHADOW_LIGHT, "parallel"),
            angle_deg=clamp_num(
                "shadow.angle_deg", d.get("angle_deg", ang_legacy), 0, 360, 45),
            distance_em=clamp_num(
                "shadow.distance_em", d.get("distance_em", 0), 0, 3, 0),
            size_em=clamp_num(
                "shadow.size_em", d.get("size_em", size_fallback),
                0, 1, SHADOW_ON_SIZE_EM),
            falloff=clamp_num("shadow.falloff", d.get("falloff", 0), 0, 1, 0),
            color=color,
            strength=clamp_num(
                "shadow.strength", d.get("strength", alpha), 0, 1.5,
                SHADOW_ON_STRENGTH),
            enabled=enabled,
            sigma_em=sigma,
            dx_em=dx,
            dy_em=dy,
            alpha=alpha,
            _legacy=False,
        )
    return ShadowSpec(
        light="parallel",
        angle_deg=ang_legacy,
        distance_em=dist_legacy if sigma > 0 else 0.0,
        size_em=sigma if sigma > 0 else SHADOW_ON_SIZE_EM,
        falloff=0.0,
        color=color,
        strength=alpha,
        enabled=False,
        sigma_em=sigma,
        dx_em=dx,
        dy_em=dy,
        alpha=alpha,
        _legacy=True,
    )


def _parse_extrude(raw: Any) -> ExtrudeSpec:
    d = take_known(raw, frozenset({"depth_em", "angle", "color"}), "extrude")
    color = d.get("color")
    return ExtrudeSpec(
        depth_em=clamp_num("extrude.depth_em", d.get("depth_em", 0), 0, 0.4, 0),
        angle=clamp_num("extrude.angle", d.get("angle", 135), 0, 360, 135),
        color=parse_color(color, "") if color else "",
    )


def _parse_pad(raw: Any) -> tuple[float, float]:
    if isinstance(raw, (list, tuple)) and len(raw) >= 2:
        x = clamp_num("bg.pad_em[0]", raw[0], 0, 2, 0.5)
        y = clamp_num("bg.pad_em[1]", raw[1], 0, 2, 0.25)
        return (x, y)
    if isinstance(raw, (int, float)):
        v = clamp_num("bg.pad_em", raw, 0, 2, 0.5)
        return (v, v)
    return (0.5, 0.25)


BG_GROW_EM_MIN = -0.5
BG_GROW_EM_MAX = 4.0


def _parse_grow(raw: Any) -> tuple[float, float, float, float]:
    """bg.grow_em：[左, 上, 右, 下]（em）。缺省 / 坏值 → 0。"""
    names = ("left", "top", "right", "bottom")
    out = [0.0, 0.0, 0.0, 0.0]
    if isinstance(raw, dict):
        for i, n in enumerate(names):
            out[i] = clamp_num("bg.grow_em.%s" % n, raw.get(n, 0.0), BG_GROW_EM_MIN, BG_GROW_EM_MAX, 0.0)
    elif isinstance(raw, (list, tuple)):
        for i in range(min(4, len(raw))):
            out[i] = clamp_num("bg.grow_em[%d]" % i, raw[i], BG_GROW_EM_MIN, BG_GROW_EM_MAX, 0.0)
    elif isinstance(raw, (int, float)) and not isinstance(raw, bool):
        v = clamp_num("bg.grow_em", raw, BG_GROW_EM_MIN, BG_GROW_EM_MAX, 0.0)
        out = [v, v, v, v]
    return (out[0], out[1], out[2], out[3])


def _parse_fade(raw: Any) -> FadeSpec:
    from cutora.render.textfx.fade import FADE_CAP, OFFSET_CAP
    keys = frozenset({
        "top", "bottom", "left", "right",
        "top_offset", "bottom_offset", "left_offset", "right_offset",
    })
    d = take_known(raw, keys, "bg.fade")
    return FadeSpec(
        top=clamp_num("bg.fade.top", d.get("top", 0), 0, FADE_CAP, 0),
        bottom=clamp_num("bg.fade.bottom", d.get("bottom", 0), 0, FADE_CAP, 0),
        left=clamp_num("bg.fade.left", d.get("left", 0), 0, FADE_CAP, 0),
        right=clamp_num("bg.fade.right", d.get("right", 0), 0, FADE_CAP, 0),
        top_offset=clamp_num(
            "bg.fade.top_offset", d.get("top_offset", 0),
            -OFFSET_CAP, OFFSET_CAP, 0),
        bottom_offset=clamp_num(
            "bg.fade.bottom_offset", d.get("bottom_offset", 0),
            -OFFSET_CAP, OFFSET_CAP, 0),
        left_offset=clamp_num(
            "bg.fade.left_offset", d.get("left_offset", 0),
            -OFFSET_CAP, OFFSET_CAP, 0),
        right_offset=clamp_num(
            "bg.fade.right_offset", d.get("right_offset", 0),
            -OFFSET_CAP, OFFSET_CAP, 0),
    )


def _parse_bg(raw: Any) -> BgSpec:
    keys = frozenset({
        "type", "color", "alpha", "radius_em", "pad_em", "glass_sigma_em",
        "glass_border", "glass_thickness_em", "glass_refraction", "glass_bevel",
        "glass_sheen", "glass_light_deg",
        "texture", "texture_tint", "align", "line_gap_em",
        "fade", "grad_type", "grad_colors", "grad_angle", "grad_highlight", "blend",
        "size_mode", "width_px", "height_px", "offset_x", "offset_y",
        "hug_pad_em", "hug_join_em", "grow_em", "enabled",
    })
    d = take_known(raw, keys, "bg")
    border = d.get("glass_border", True)
    bg_type = pick_enum("bg.type", d.get("type"), BG_TYPES, "none")
    if "enabled" in d:
        enabled = bool(d.get("enabled"))
    else:
        enabled = bg_type not in ("none", "")
    return BgSpec(
        type=bg_type,
        color=parse_color(d.get("color"), DEFAULT_BG),
        alpha=clamp_num("bg.alpha", d.get("alpha", 0.55), 0, 1, 0.55),
        radius_em=clamp_num("bg.radius_em", d.get("radius_em", 0.4), 0, 1, 0.4),
        pad_em=_parse_pad(d.get("pad_em")),
        glass_sigma_em=clamp_num(
            "bg.glass_sigma_em", d.get("glass_sigma_em", 0.7), 0.2, 1.5, 0.7),
        glass_border=bool(border) if border is not None else True,
        glass_thickness_em=clamp_num(
            "bg.glass_thickness_em", d.get("glass_thickness_em", 0.14), 0, 0.5, 0.14),
        glass_refraction=clamp_num(
            "bg.glass_refraction", d.get("glass_refraction", 0.55), 0, 1, 0.55),
        glass_bevel=clamp_num(
            "bg.glass_bevel", d.get("glass_bevel", 0.7), 0, 1, 0.7),
        glass_sheen=clamp_num(
            "bg.glass_sheen", d.get("glass_sheen", 0.45), 0, 1, 0.45),
        glass_light_deg=clamp_num(
            "bg.glass_light_deg", d.get("glass_light_deg", 315), 0, 360, 315),
        texture=pick_enum("bg.texture", d.get("texture"), TEXTURES, "paper"),
        texture_tint=parse_color(d.get("texture_tint"), DEFAULT_TINT),
        align=pick_enum("bg.align", d.get("align"), BG_ALIGN, "center"),
        line_gap_em=clamp_num("bg.line_gap_em", d.get("line_gap_em", 0.18), 0, 1, 0.18),
        fade=_parse_fade(d.get("fade")),
        grad_type=pick_enum(
            "bg.grad_type", d.get("grad_type"), BG_GRAD_TYPES, "none"),
        grad_colors=[
            c for c in _colors_list(d.get("grad_colors"), [], nmin=0, nmax=3)
            if not is_pure_black(c)
        ],
        grad_angle=clamp_num("bg.grad_angle", d.get("grad_angle", 0.0), 0.0, 360.0, 0.0),
        grad_highlight=bool(d.get("grad_highlight")),
        blend=pick_enum("bg.blend", d.get("blend"), BG_BLEND, "normal"),
        size_mode=pick_enum(
            "bg.size_mode", d.get("size_mode"), BG_SIZE_MODES, "fit_text"),
        width_px=clamp_num(
            "bg.width_px", d.get("width_px", 0), 0, BG_SIZE_PX_MAX, 0),
        height_px=clamp_num(
            "bg.height_px", d.get("height_px", 0), 0, BG_SIZE_PX_MAX, 0),
        offset_x=clamp_num(
            "bg.offset_x", d.get("offset_x", 0),
            -BG_OFFSET_PX_MAX, BG_OFFSET_PX_MAX, 0),
        offset_y=clamp_num(
            "bg.offset_y", d.get("offset_y", 0),
            -BG_OFFSET_PX_MAX, BG_OFFSET_PX_MAX, 0),
        hug_pad_em=clamp_num(
            "bg.hug_pad_em", d.get("hug_pad_em", HUG_PAD_EM_DEFAULT),
            HUG_PAD_EM_MIN, HUG_PAD_EM_MAX, HUG_PAD_EM_DEFAULT),
        hug_join_em=clamp_num(
            "bg.hug_join_em", d.get("hug_join_em", HUG_JOIN_EM_DEFAULT),
            0, HUG_JOIN_EM_MAX, HUG_JOIN_EM_DEFAULT),
        grow_em=_parse_grow(d.get("grow_em")),
        enabled=enabled,
    )


def _parse_fonts(raw: Any, fallback: str) -> FontsSpec:
    d = take_known(raw, frozenset({"body", "emphasis"}), "fonts")
    body = str(d.get("body") or fallback or "")
    emph = str(d.get("emphasis") or body)
    return FontsSpec(body=body, emphasis=emph)


def _parse_emphasis(raw: Any) -> EmphasisSpec:
    d = take_known(raw, frozenset({
        "auto", "words", "color", "palette", "scale", "font", "anim",
        "max_per_cue", "type", "colors", "angle",
    }), "emphasis")
    auto_raw = d.get("auto") if isinstance(d.get("auto"), list) else []
    auto = [x for x in auto_raw if x in EMPH_AUTO]
    words = [str(x) for x in (d.get("words") or []) if str(x).strip()]
    palette = _colors_list(d.get("palette"), [], nmin=0, nmax=8) if d.get("palette") else []
    colors_raw = d.get("colors")
    if isinstance(colors_raw, (list, tuple)) and colors_raw:
        colors = _colors_list(colors_raw, [DEFAULT_EMPH], nmin=1, nmax=5)
    else:
        colors = [parse_color(d.get("color"), DEFAULT_EMPH)]
    return EmphasisSpec(
        auto=auto,
        words=words,
        type=pick_enum("emphasis.type", d.get("type"), EMPH_FILL_TYPES, "solid"),
        colors=colors,
        angle=clamp_num("emphasis.angle", d.get("angle", 90), 0, 360, 90),
        color=colors[0],
        palette=palette,
        scale=clamp_num("emphasis.scale", d.get("scale", 1.25), 0.8, 2.0, 1.25),
        font=pick_enum("emphasis.font", d.get("font"), EMPH_FONT, "emphasis"),
        anim=pick_enum("emphasis.anim", d.get("anim"), EMPH_ANIM, "none"),
        max_per_cue=clamp_int("emphasis.max_per_cue", d.get("max_per_cue", 2), 0, 8, 2),
    )


def _parse_anim(raw: Any) -> AnimSpec:
    d = take_known(raw, frozenset({
        "in", "loop", "out", "unit", "stagger_s", "in_s", "out_s", "easing", "seed",
    }), "anim")
    return AnimSpec(
        in_name=str(d.get("in") or "none"),
        loop=str(d.get("loop") or "none"),
        out_name=str(d.get("out") or "none"),
        unit=pick_enum("anim.unit", d.get("unit"), UNITS, "segment"),
        stagger_s=clamp_num("anim.stagger_s", d.get("stagger_s", 0.05), 0, 0.3, 0.05),
        in_s=clamp_num("anim.in_s", d.get("in_s", 0.35), 0.05, 1.5, 0.35),
        out_s=clamp_num("anim.out_s", d.get("out_s", 0.25), 0.05, 1.5, 0.25),
        easing=str(d.get("easing") or "ease_out"),
        seed=clamp_int("anim.seed", d.get("seed", 0), -10**9, 10**9, 0),
    )


def _parse_line_bar(raw: Any) -> LineBarSpec:
    d = take_known(raw, frozenset({
        "color", "height_em", "pad_em", "offset_y_em", "radius_em",
        "glow_em", "style", "anim", "alpha",
    }), "line_bar")
    color_raw = d.get("color")
    if isinstance(color_raw, str) and color_raw.strip():
        color = parse_color(color_raw, DEFAULT_LINE_BAR)
    else:
        color = DEFAULT_LINE_BAR
    hex_body = color.lstrip("#")
    if len(hex_body) >= 8:
        alpha = rgba_tuple(color)[3]
    elif "alpha" in d:
        alpha = clamp_num("line_bar.alpha", d.get("alpha"), 0, 1, 0.7)
    else:
        alpha = 0.7
    has_radius = "radius_em" in d
    if "anim" in d:
        anim = pick_enum("line_bar.anim", d.get("anim"), LINE_BAR_ANIMS, "draw")
    else:
        anim = "none"
    return LineBarSpec(
        color=color,
        height_em=clamp_num(
            "line_bar.height_em", d.get("height_em", 0.22), 0.02, 0.6, 0.22),
        pad_em=clamp_num("line_bar.pad_em", d.get("pad_em", 0.18), 0, 0.6, 0.18),
        offset_y_em=clamp_num(
            "line_bar.offset_y_em", d.get("offset_y_em", 0.06), -0.5, 0.5, 0.06),
        radius_em=clamp_num(
            "line_bar.radius_em", d.get("radius_em", 0.4), 0, 0.5, 0.4),
        glow_em=clamp_num("line_bar.glow_em", d.get("glow_em", 0), 0, 0.4, 0),
        style=pick_enum("line_bar.style", d.get("style"), LINE_BAR_STYLES, "solid"),
        anim=anim,
        _alpha=alpha,
        _use_bg_radius=not has_radius,
    )


def _parse_follow_word(raw: Any) -> bool:
    """True when the current-word feature is on (bool or nested dict)."""
    if isinstance(raw, dict):
        if "tint" in raw or "box" in raw:
            tint = raw.get("tint") if isinstance(raw.get("tint"), dict) else {}
            box = raw.get("box") if isinstance(raw.get("box"), dict) else {}
            tint_en = tint.get("enabled")
            box_en = box.get("enabled")
            if tint_en is None and box_en is None:
                return True
            return bool(tint_en) or bool(box_en)
        d = take_known(raw, frozenset({"enabled"}), "follow_word")
        return bool(d.get("enabled"))
    return bool(raw)


def _parse_follow_tint(raw: Any, *, enabled: bool, type_: str,
                       colors: list[str], angle: float) -> FollowTintSpec:
    d = take_known(raw, frozenset({
        "enabled", "type", "colors", "angle",
    }), "follow_word.tint") if isinstance(raw, dict) else {}
    if "enabled" in d:
        enabled = bool(d.get("enabled"))
    if "type" in d:
        type_ = pick_enum("follow_word.tint.type", d.get("type"),
                          EMPH_FILL_TYPES, "solid")
    if "colors" in d:
        colors = _colors_list(d.get("colors"), [DEFAULT_EMPH], nmin=1, nmax=5)
    elif "color" in d and isinstance(d.get("color"), str):
        colors = [parse_color(d.get("color"), DEFAULT_EMPH)]
    if "angle" in d:
        angle = clamp_num("follow_word.tint.angle", d.get("angle"), 0, 360, 90)
    if not colors:
        colors = [DEFAULT_EMPH]
    return FollowTintSpec(
        enabled=bool(enabled),
        type=type_ if type_ in EMPH_FILL_TYPES else "solid",
        colors=list(colors),
        angle=float(angle),
    )


def _parse_follow_box(raw: Any, *, enabled: bool, color: str,
                      alpha: float, pad_em: float, offset_y_em: float,
                      radius_em: float, stroke_width: float, glow_radius: float,
                      overshoot: float, duration: float) -> FollowBoxSpec:
    d = take_known(raw, frozenset({
        "enabled", "color", "alpha", "pad_em", "radius_em", "offset_y_em",
        "stroke_width", "glow_radius", "overshoot", "duration",
    }), "follow_word.box") if isinstance(raw, dict) else {}
    if "enabled" in d:
        enabled = bool(d.get("enabled"))
    if "color" in d and isinstance(d.get("color"), str) and str(d.get("color")).strip():
        color = parse_color(d.get("color"), DEFAULT_FOLLOW_BOX)
    if "alpha" in d and d.get("alpha") is not None:
        alpha = clamp_num("follow_word.box.alpha", d.get("alpha"), 0, 1, 0.55)
    if "pad_em" in d and d.get("pad_em") is not None:
        pad_em = clamp_num("follow_word.box.pad_em", d.get("pad_em"), 0, 1, 0.12)
    if "offset_y_em" in d and d.get("offset_y_em") is not None:
        offset_y_em = clamp_num(
            "follow_word.box.offset_y_em", d.get("offset_y_em"), -1, 1, 0)
    if "radius_em" in d and d.get("radius_em") is not None:
        radius_em = clamp_num(
            "follow_word.box.radius_em", d.get("radius_em"), 0, 1, 0.4)
    if "stroke_width" in d:
        stroke_width = clamp_num(
            "follow_word.box.stroke_width", d.get("stroke_width"), 0, 40, 0)
    if "glow_radius" in d:
        glow_radius = clamp_num(
            "follow_word.box.glow_radius", d.get("glow_radius"), 0, 40, 0)
    if "overshoot" in d:
        overshoot = clamp_num(
            "follow_word.box.overshoot", d.get("overshoot"), 0, 4, 0)
    if "duration" in d:
        duration = clamp_num(
            "follow_word.box.duration", d.get("duration"), 0, 2, 0)
    return FollowBoxSpec(
        enabled=bool(enabled),
        color=color or DEFAULT_FOLLOW_BOX,
        alpha=float(alpha),
        pad_em=float(pad_em),
        offset_y_em=float(offset_y_em),
        radius_em=float(radius_em),
        stroke_width=float(stroke_width),
        glow_radius=float(glow_radius),
        overshoot=float(overshoot),
        duration=float(duration),
    )


def _migrate_follow_box_color(
        *, tint_on: bool, bh: BoxHighlightSpec, bg: BgSpec,
        emph: EmphasisSpec) -> str:
    """Q51 fallback: user box color, else bg when tint is also on, else emphasis."""
    raw_bh = str(getattr(bh, "color", "") or "").strip()
    if raw_bh:
        return parse_color(raw_bh, DEFAULT_FOLLOW_BOX)
    bg_c = str(getattr(bg, "color", "") or "").strip()
    emph_c = str(getattr(emph, "color", "") or "").strip() or DEFAULT_EMPH
    if tint_on:
        return parse_color(bg_c, DEFAULT_FOLLOW_BOX) if bg_c else (
            parse_color(emph_c, DEFAULT_EMPH))
    return parse_color(emph_c, DEFAULT_EMPH) if emph_c else (
        parse_color(bg_c, DEFAULT_FOLLOW_BOX) if bg_c else DEFAULT_FOLLOW_BOX)


def _migrate_follow_box_alpha(
        *, tint_on: bool, bh: BoxHighlightSpec, bg: BgSpec) -> float:
    if bh is not None and bh.alpha is not None:
        return float(bh.alpha)
    if str(getattr(bg, "type", "none") or "none") != "none" or tint_on:
        return float(getattr(bg, "alpha", 0.55) or 0.55)
    return 0.55


_WORD_COLOR_IDS = frozenset({
    "karaoke", "highlight", "box_highlight",
    "colour_highlight", "rotate_highlight",
})


def _glyph_tint_in_loop(loop: str) -> bool:
    """True when anim.loop already tints the current word (not just the box)."""
    from cutora.render.textfx.anim import _anim_ids
    ids = _anim_ids(loop)
    return any(i in _WORD_COLOR_IDS and i != "box_highlight" for i in ids)


def _parse_follow_word_spec(
        raw: Any, *, sibling_tint: bool, sibling_box: bool,
        emph: EmphasisSpec, bg: BgSpec,
        bh: BoxHighlightSpec, loop_tint: bool = False) -> FollowWordSpec:
    """Nested follow_word, or Q51 bool + sibling flags + emphasis/bg fallbacks."""
    emph_colors = [str(c) for c in (emph.colors or []) if c] or [
        emph.color or DEFAULT_EMPH]
    emph_type = str(emph.type or "solid")
    if emph_type not in EMPH_FILL_TYPES:
        emph_type = "solid"
    emph_angle = float(emph.angle if emph.angle is not None else 90.0)
    bh_alpha = float(bh.alpha) if bh.alpha is not None else 0.55
    bh_pad = float(bh.pad_em) if bh.pad_em is not None else 0.12
    bh_off = float(bh.offset_y_em) if bh.offset_y_em is not None else 0.0
    bh_rad = float(bh.radius_em) if bh.radius_em is not None else float(
        getattr(bg, "radius_em", 0.4) or 0.4)
    bh_stroke = float(bh.stroke_width or 0.0)
    bh_glow = float(bh.glow_radius or 0.0)
    bh_over = float(bh.overshoot or 0.0)
    bh_dur = float(bh.duration or 0.0)

    nested = isinstance(raw, dict) and ("tint" in raw or "box" in raw)
    if nested:
        parent_on = True
        if isinstance(raw, dict) and "enabled" in raw:
            parent_on = bool(raw.get("enabled"))
        tint_raw = raw.get("tint") if isinstance(raw, dict) else None
        box_raw = raw.get("box") if isinstance(raw, dict) else None
        tint_default_en = bool(parent_on and sibling_tint)
        box_default_en = bool(parent_on and sibling_box)
        # Nested color missing → product default, not bg / emphasis.
        tint = _parse_follow_tint(
            tint_raw, enabled=tint_default_en, type_="solid",
            colors=[DEFAULT_EMPH], angle=90.0)
        box_color = DEFAULT_FOLLOW_BOX
        box_alpha = 0.55
        box_pad, box_off, box_rad = 0.12, 0.0, 0.4
        box_stroke, box_glow, box_over, box_dur = 0.0, 0.0, 0.0, 0.0
        if isinstance(box_raw, dict):
            pass
        else:
            # No nested box: keep Q51 geometry from box_highlight / bg.
            will_tint = bool(tint.enabled or loop_tint)
            box_color = _migrate_follow_box_color(
                tint_on=will_tint, bh=bh, bg=bg, emph=emph)
            box_alpha = _migrate_follow_box_alpha(
                tint_on=will_tint, bh=bh, bg=bg)
            box_pad, box_off, box_rad = bh_pad, bh_off, bh_rad
            box_stroke, box_glow, box_over, box_dur = (
                bh_stroke, bh_glow, bh_over, bh_dur)
        box = _parse_follow_box(
            box_raw, enabled=box_default_en, color=box_color,
            alpha=box_alpha, pad_em=box_pad, offset_y_em=box_off,
            radius_em=box_rad, stroke_width=box_stroke,
            glow_radius=box_glow, overshoot=box_over, duration=box_dur)
        if not parent_on:
            tint.enabled = False
            box.enabled = False
        return FollowWordSpec(tint=tint, box=box)

    parent_on = _parse_follow_word(raw)
    tint_en = bool(parent_on and sibling_tint)
    box_en = bool(parent_on and sibling_box)
    # Old paint used tint_also = highlight already in loop, not just follow_word_tint.
    will_tint = bool(tint_en or loop_tint)
    box_color = _migrate_follow_box_color(
        tint_on=will_tint, bh=bh, bg=bg, emph=emph)
    box_alpha = _migrate_follow_box_alpha(tint_on=will_tint, bh=bh, bg=bg)
    tint = FollowTintSpec(
        enabled=tint_en,
        type=emph_type,
        colors=list(emph_colors),
        angle=emph_angle,
    )
    box = FollowBoxSpec(
        enabled=box_en,
        color=box_color,
        alpha=box_alpha,
        pad_em=bh_pad,
        offset_y_em=bh_off,
        radius_em=bh_rad,
        stroke_width=bh_stroke,
        glow_radius=bh_glow,
        overshoot=bh_over,
        duration=bh_dur,
    )
    return FollowWordSpec(tint=tint, box=box)


def _box_highlight_from_follow(box: FollowBoxSpec) -> BoxHighlightSpec:
    return BoxHighlightSpec(
        color=box.color,
        alpha=box.alpha,
        pad_em=box.pad_em,
        offset_y_em=box.offset_y_em,
        radius_em=box.radius_em,
        stroke_width=box.stroke_width,
        glow_radius=box.glow_radius,
        overshoot=box.overshoot,
        duration=box.duration,
    )


def _parse_follow_sentence(raw: Any) -> bool:
    if isinstance(raw, dict):
        d = take_known(raw, frozenset({"enabled"}), "follow_sentence")
        return bool(d.get("enabled"))
    return bool(raw)


def _parse_opt_bool(raw: Any, default: bool) -> bool:
    """Missing key keeps *default* (old projects: tint+box both on)."""
    if raw is None:
        return default
    if isinstance(raw, dict):
        if "enabled" not in raw:
            return default
        return bool(raw.get("enabled"))
    return bool(raw)


def _parse_box_highlight(raw: Any) -> BoxHighlightSpec:
    d = take_known(raw, frozenset({
        "color", "alpha", "pad_em", "offset_y_em", "radius_em",
        "stroke_width", "glow_radius", "overshoot", "duration",
    }), "box_highlight")
    color_raw = d.get("color")
    if isinstance(color_raw, str) and color_raw.strip():
        color = parse_color(color_raw, "")
    else:
        color = ""
    alpha = None
    if "alpha" in d and d.get("alpha") is not None:
        alpha = clamp_num("box_highlight.alpha", d.get("alpha"), 0, 1, 0.7)
    pad_em = None
    if "pad_em" in d and d.get("pad_em") is not None:
        pad_em = clamp_num("box_highlight.pad_em", d.get("pad_em"), 0, 1, 0.12)
    offset_y_em = None
    if "offset_y_em" in d and d.get("offset_y_em") is not None:
        offset_y_em = clamp_num(
            "box_highlight.offset_y_em", d.get("offset_y_em"), -1, 1, 0)
    radius_em = None
    if "radius_em" in d and d.get("radius_em") is not None:
        radius_em = clamp_num(
            "box_highlight.radius_em", d.get("radius_em"), 0, 1, 0.2)
    return BoxHighlightSpec(
        color=color,
        alpha=alpha,
        pad_em=pad_em,
        offset_y_em=offset_y_em,
        radius_em=radius_em,
        stroke_width=clamp_num(
            "box_highlight.stroke_width", d.get("stroke_width", 0), 0, 40, 0),
        glow_radius=clamp_num(
            "box_highlight.glow_radius", d.get("glow_radius", 0), 0, 40, 0),
        overshoot=clamp_num(
            "box_highlight.overshoot", d.get("overshoot", 0), 0, 4, 0),
        duration=clamp_num(
            "box_highlight.duration", d.get("duration", 0), 0, 2, 0),
    )


def _parse_scatter(raw: Any) -> ScatterSpec:
    d = take_known(raw, frozenset({
        "enabled", "size_jitter", "pos_jitter_em", "rot_jitter_deg",
    }), "scatter")
    pos = d.get("pos_jitter_em")
    if isinstance(pos, (list, tuple)) and len(pos) >= 2:
        pj = (
            clamp_num("scatter.pos_jitter_em[0]", pos[0], 0, 8, 1.5),
            clamp_num("scatter.pos_jitter_em[1]", pos[1], 0, 8, 0.8),
        )
    else:
        pj = (1.5, 0.8)
    return ScatterSpec(
        enabled=bool(d.get("enabled", False)),
        size_jitter=clamp_num("scatter.size_jitter", d.get("size_jitter", 0.35), 0, 0.6, 0.35),
        pos_jitter_em=pj,
        rot_jitter_deg=clamp_num(
            "scatter.rot_jitter_deg", d.get("rot_jitter_deg", 6), 0, 20, 6),
    )


def from_style(style: dict | None) -> FxSpec:
    """Build FxSpec from a task/pack style dict. Missing fx → all defaults."""
    s = style if isinstance(style, dict) else {}
    fx = s.get("fx") if isinstance(s.get("fx"), dict) else {}
    sibling_tint = _parse_opt_bool(fx.get("follow_word_tint"), True)
    sibling_box = _parse_opt_bool(fx.get("follow_word_box"), True)
    follow_sentence = _parse_follow_sentence(fx.get("follow_sentence"))
    take_known(fx, FX_KEYS, "fx")
    bg = _parse_bg(fx.get("bg"))
    emphasis = _parse_emphasis(fx.get("emphasis"))
    box_highlight = _parse_box_highlight(fx.get("box_highlight"))
    anim = _parse_anim(fx.get("anim"))
    follow_word = _parse_follow_word_spec(
        fx.get("follow_word"),
        sibling_tint=sibling_tint,
        sibling_box=sibling_box,
        emph=emphasis,
        bg=bg,
        bh=box_highlight,
        loop_tint=_glyph_tint_in_loop(anim.loop),
    )
    box_highlight = _box_highlight_from_follow(follow_word.box)
    spec = FxSpec(
        fill=_parse_fill(fx.get("fill")),
        stroke=_parse_stroke(fx.get("stroke")),
        glow=_parse_glow(fx.get("glow")),
        shadow=_parse_shadow(fx.get("shadow")),
        extrude=_parse_extrude(fx.get("extrude")),
        bg=bg,
        fonts=_parse_fonts(fx.get("fonts"), str(s.get("font_family") or "")),
        emphasis=emphasis,
        anim=anim,
        scatter=_parse_scatter(fx.get("scatter")),
        tracking_em=clamp_num("tracking_em", fx.get("tracking_em", 0), -0.05, 0.5, 0),
        letter_case=pick_enum("letter_case", fx.get("letter_case"), LETTER_CASES, "none"),
        follow_word=follow_word,
        follow_word_tint=bool(follow_word.tint.enabled) if bool(follow_word) else sibling_tint,
        follow_word_box=bool(follow_word.box.enabled) if bool(follow_word) else sibling_box,
        follow_sentence=follow_sentence,
        line_bar=_parse_line_bar(fx.get("line_bar")),
        box_highlight=box_highlight,
    )
    spec.has_pure_black = bool(collect_pure_black(asdict(spec)))
    from cutora.render.textfx.anim import _anim_ids
    loop_ids = [i for i in _anim_ids(spec.anim.loop) if i not in ("none", "line_bar")]
    if follow_word:
        # Nested UI flags default True so old follow_word projects keep both.
        if follow_word.tint.enabled:
            if "highlight" not in loop_ids:
                loop_ids.append("highlight")
        else:
            loop_ids = [i for i in loop_ids if i != "highlight"]
        if follow_word.box.enabled:
            if "box_highlight" not in loop_ids:
                loop_ids.append("box_highlight")
        else:
            loop_ids = [i for i in loop_ids if i != "box_highlight"]
    if follow_sentence:
        loop_ids.append("line_bar")
    spec.anim.loop = "+".join(loop_ids) if loop_ids else "none"
    try:
        from cutora.subtitle.font_coverage import SHAPING_SCRIPTS
        script = str(s.get("script") or "").strip().lower()
        if not script:
            from cutora.asr.languages import get_language
            from cutora.core import config as cfg
            lang = get_language(s.get("asr_language") or getattr(cfg, "LANGUAGE", "en"))
            script = str((lang or {}).get("script") or "")
        if script in SHAPING_SCRIPTS and spec.anim.unit in ("char", "word"):
            spec.anim.unit = "segment"
    except Exception:
        pass
    return spec


def expansion_px(spec: FxSpec, em: float, *, for_wrap: bool = False) -> float:
    """Pixels to grow the text bbox for glow / shadow / bg / extrude.

    ``for_wrap=True`` keeps line-breaking independent of shadow length so a
    3 em point light does not collapse the wrap column (ROI still uses the
    full pad).
    """
    g = 0.0
    if spec.glow.live():
        g = (3.0 * spec.glow.sigma_em + spec.glow.spread_em) * em
    sh = 0.0
    shadow = spec.shadow
    if shadow._legacy:
        if shadow.sigma_em > 0:
            sh = (3.0 * shadow.sigma_em
                  + abs(shadow.dx_em) + abs(shadow.dy_em)) * em
    elif (not shadow._legacy) and shadow.enabled and shadow.strength > 0:
        dist = shadow.distance_em
        if for_wrap:
            dist = min(dist, 0.35)
        sh = (dist + shadow.size_em * 3.0) * em + 2.0
        if shadow.light == "point" and not for_wrap:
            scale = point_light_scale(shadow.distance_em, t=1.0)
            extra_em = (scale - 1.0) * (
                max(shadow.distance_em, SHADOW_DIST_MIN_EM) + 2.0)
            sh += extra_em * em
    bg = 0.0
    bg_kind = spec.bg.kind()
    grow = max(0.0, max(spec.bg.grow_px(em))) if bg_kind != "none" else 0.0
    if bg_kind != "none":
        if spec.bg.size_mode == "hug_glyphs":
            stroke_px = (
                float(spec.stroke.width_em) * em if spec.stroke.live() else 0.0)
            off = max(
                abs(float(spec.bg.offset_x or 0.0)),
                abs(float(spec.bg.offset_y or 0.0)))
            # Dilate, plus the close kernel so the morphology is not clipped
            # at the ROI edge. The kernel never exceeds hug_join_em * em.
            # +8 covers the 1 px feather. Negative pad would eat strokes;
            # the mask clamps it to 0, and so does this margin.
            join_px = max(0.0, float(spec.bg.hug_join_em)) * em
            pad_em = max(0.0, float(spec.bg.hug_pad_em))
            bg = pad_em * em + stroke_px + off + join_px + 8.0
        elif spec.bg.size_mode == "line_hug":
            off = max(
                abs(float(spec.bg.offset_x or 0.0)),
                abs(float(spec.bg.offset_y or 0.0)))
            # Slabs grow by hug_pad_em (negative shrinks). Closing rounds
            # concave corners inward, but the dilate step needs `r` of room
            # or the ROI clips it. +8 covers the 1 px feather.
            r = max(0.0, float(spec.bg.radius_em)) * em
            bg = float(spec.bg.hug_pad_em) * em + r + off + 8.0
        else:
            pad = max(spec.bg.pad_em[0], spec.bg.pad_em[1]) * em
            bg = pad + spec.bg.radius_em * em + 2.0
            if bg_kind == "texture" and spec.bg.texture == "paper":
                bg += 0.12 * em + 2.0
        bg += grow
    ext = spec.extrude.depth_em * em
    lb_pad = 0.0
    if (not for_wrap) and "line_bar" in str(spec.anim.loop or ""):
        lb = spec.line_bar
        glow = float(lb.glow_em or 0)
        lb_pad = (
            abs(float(lb.offset_y_em)) + float(lb.height_em)
            + float(lb.pad_em) + 3.0 * glow + 0.08
        ) * em + 4.0
    return max(g, sh, bg, ext, lb_pad, 2.0)


def task_has_glass_bg(task: dict | None) -> bool:
    """True if any style on the task asks for fx.bg.type == glass."""
    if not isinstance(task, dict):
        return False
    styles: list[Any] = [task.get("style")]
    roles = task.get("subtitle_roles") or {}
    if isinstance(roles, dict):
        for info in roles.values():
            if isinstance(info, dict):
                styles.append(info.get("style"))
    for style in styles:
        if not isinstance(style, dict):
            continue
        fx = style.get("fx")
        if not isinstance(fx, dict):
            continue
        bg = fx.get("bg")
        if _bg_dict_is_glass(bg):
            return True
    for key in ("text_overlays", "overlays"):
        for ov in task.get(key) or []:
            if not isinstance(ov, dict):
                continue
            if _style_is_glass(ov.get("style") or ov):
                return True
    return False


def _style_is_glass(style) -> bool:
    if not isinstance(style, dict):
        return False
    fx = style.get("fx")
    if not isinstance(fx, dict):
        return False
    bg = fx.get("bg")
    return _bg_dict_is_glass(bg)


def _bg_dict_is_glass(bg) -> bool:
    if not isinstance(bg, dict) or bg.get("type") != "glass":
        return False
    if "enabled" in bg:
        return bool(bg.get("enabled"))
    return True


def ensure_section_enabled(fx: dict) -> None:
    """Fill missing stroke/glow/bg.enabled from legacy value-zero semantics.

    Does not rewrite width_em / sigma_em / type. Paint uses from_style
    inference; this only lets the panel toggle read the same truth.
    """
    if not isinstance(fx, dict):
        return
    st = fx.get("stroke")
    if isinstance(st, dict) and "enabled" not in st:
        try:
            st["enabled"] = float(st.get("width_em") or 0) > 0
        except (TypeError, ValueError):
            st["enabled"] = False
    gl = fx.get("glow")
    if isinstance(gl, dict) and "enabled" not in gl:
        try:
            gl["enabled"] = float(gl.get("sigma_em") or 0) > 0
        except (TypeError, ValueError):
            gl["enabled"] = False
    bg = fx.get("bg")
    if isinstance(bg, dict) and "enabled" not in bg:
        bg["enabled"] = str(bg.get("type") or "none") not in ("none", "")


def seed_section_defaults(fx: dict, family: str) -> None:
    """When a section header turns on and the value is still the off-zero, fill the on-default."""
    if not isinstance(fx, dict):
        return
    d = fx.get(family)
    if not isinstance(d, dict):
        d = {}
        fx[family] = d
    if family == "stroke":
        try:
            empty = float(d.get("width_em") or 0) <= 0
        except (TypeError, ValueError):
            empty = True
        if empty:
            d["width_em"] = STROKE_ON_WIDTH_EM
    elif family == "glow":
        try:
            empty = float(d.get("sigma_em") or 0) <= 0
        except (TypeError, ValueError):
            empty = True
        if empty:
            d["sigma_em"] = GLOW_ON_SIGMA_EM
    elif family == "bg":
        if str(d.get("type") or "none") in ("none", ""):
            d["type"] = BG_ON_TYPE


def frame_has_glass_bg(task: dict | None, t: float) -> bool:
    """True only if a glass subtitle is on screen at *t*."""
    if not task_has_glass_bg(task):
        return False
    segs = task.get("segments") or []
    roles = task.get("subtitle_roles") if isinstance(task.get("subtitle_roles"), dict) else {}
    hit = False
    for seg in segs:
        if not isinstance(seg, dict):
            continue
        start = float(seg.get("start") or 0.0)
        end = float(seg.get("end") or start)
        if t < start or t >= end:
            continue
        hit = True
        style = seg.get("style")
        if _style_is_glass(style):
            return True
        role = seg.get("role")
        info = roles.get(role) if role else None
        if isinstance(info, dict) and _style_is_glass(info.get("style")):
            return True
    if hit and _style_is_glass(task.get("style")):
        return True
    overlays = list(task.get("text_overlays") or [])
    for ov in overlays:
        if not isinstance(ov, dict):
            continue
        start = float(ov.get("start") or 0.0)
        end = float(ov.get("end") or start)
        if t < start or t >= end:
            continue
        if _style_is_glass(ov.get("style") or ov):
            return True
    return False

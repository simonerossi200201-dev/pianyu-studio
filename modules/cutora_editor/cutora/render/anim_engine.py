# SPDX-License-Identifier: GPL-3.0-or-later
"""
anim_engine.py  –  Stateless per-frame animation computation for subtitles & text overlays.

All functions are pure: given current_time and timing parameters,
they return a value that the renderer can use directly.
No state machines, no caches, no side effects.
"""

import math

# ───────────────────────────────────────────────────────
# 1.  Easing Functions   (input t ∈ [0, 1] → output ∈ [0, 1])
# ───────────────────────────────────────────────────────

def linear(t):
    return t

def ease_in_cubic(t):
    return t * t * t

def ease_out_cubic(t):
    return 1.0 - (1.0 - t) ** 3

def ease_in_out_cubic(t):
    if t < 0.5:
        return 4.0 * t * t * t
    else:
        return 1.0 - (-2.0 * t + 2.0) ** 3 / 2.0

def ease_out_bounce(t):
    n1 = 7.5625
    d1 = 2.75
    if t < 1.0 / d1:
        return n1 * t * t
    elif t < 2.0 / d1:
        t -= 1.5 / d1
        return n1 * t * t + 0.75
    elif t < 2.5 / d1:
        t -= 2.25 / d1
        return n1 * t * t + 0.9375
    else:
        t -= 2.625 / d1
        return n1 * t * t + 0.984375

def ease_out_elastic(t):
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    c4 = (2.0 * math.pi) / 3.0
    return 2.0 ** (-10.0 * t) * math.sin((t * 10.0 - 0.75) * c4) + 1.0

def ease_out_back(t):
    """Overshoot then settle – great for 'pop' effects."""
    c1 = 1.70158
    c3 = c1 + 1.0
    return 1.0 + c3 * (t - 1.0) ** 3 + c1 * (t - 1.0) ** 2

def ease_in_back(t):
    c1 = 1.70158
    c3 = c1 + 1.0
    return c3 * t * t * t - c1 * t * t

def ease_in_out_back(t):
    """Smooth overshoot on both ends — very natural for UI."""
    c1 = 1.70158
    c2 = c1 * 1.525
    if t < 0.5:
        return ((2 * t) ** 2 * ((c2 + 1) * 2 * t - c2)) / 2
    else:
        return ((2 * t - 2) ** 2 * ((c2 + 1) * (t * 2 - 2) + c2) + 2) / 2

def ease_in_quad(t):
    return t * t

def ease_out_quad(t):
    return 1.0 - (1.0 - t) ** 2

def ease_in_out_quad(t):
    if t < 0.5:
        return 2.0 * t * t
    else:
        return 1.0 - (-2.0 * t + 2.0) ** 2 / 2.0

def ease_out_expo(t):
    """Fast start, very smooth landing."""
    if t >= 1.0:
        return 1.0
    return 1.0 - 2.0 ** (-10.0 * t)

def ease_in_out_expo(t):
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    if t < 0.5:
        return 2.0 ** (20.0 * t - 10.0) / 2.0
    else:
        return (2.0 - 2.0 ** (-20.0 * t + 10.0)) / 2.0

EASING_MAP = {
    "linear":        linear,
    "ease_in":       ease_in_cubic,
    "ease_out":      ease_out_cubic,
    "ease_in_out":   ease_in_out_cubic,
    "ease_in_quad":  ease_in_quad,
    "ease_out_quad": ease_out_quad,
    "ease_in_out_quad": ease_in_out_quad,
    "ease_out_expo": ease_out_expo,
    "ease_in_out_expo": ease_in_out_expo,
    "bounce":        ease_out_bounce,
    "elastic":       ease_out_elastic,
    "back":          ease_out_back,
    "back_in_out":   ease_in_out_back,
}

# Chinese labels → internal names  (used by UI combo boxes)
EASING_LABELS = [
    ("线性",       "linear"),
    ("渐出(柔和)", "ease_out"),
    ("渐入",       "ease_in"),
    ("渐入渐出",   "ease_in_out"),
    ("渐出(轻)",   "ease_out_quad"),
    ("渐入渐出(轻)", "ease_in_out_quad"),
    ("渐出(快)",   "ease_out_expo"),
    ("渐入渐出(快)", "ease_in_out_expo"),
    ("弹跳",       "bounce"),
    ("弹性",       "elastic"),
    ("回弹",       "back"),
    ("回弹渐入渐出", "back_in_out"),
]

# Animation type labels  (Chinese → internal)
ANIM_TYPE_LABELS = [
    ("无",         "none"),
    ("淡入淡出",   "fade"),
    ("上滑",       "slide_up"),
    ("下滑",       "slide_down"),
    ("左滑",       "slide_left"),
    ("右滑",       "slide_right"),
    ("弹出",       "pop"),
    ("打字机",     "typewriter"),
    ("逐字弹跳",   "char_bounce"),
    # --- Phase 2 ---
    ("逐字放大",   "letter_jump"),
    ("节奏逐词",   "metronome"),
    ("模糊渐清",   "blur_sharp"),
    ("圣光",       "holy_glow"),  # kept for old projects; not in UI_ANIM_TYPE_LABELS
    ("漂浮",       "floating"),
    ("逐行出现",   "bullet_reveal"),
    ("闪光高亮",   "flash_highlight"),
    # audio_spectrum 已升级为独立功能（spectrum_enabled），不再占用入场动画槽位
]

# T04f-1: holy_glow stays in ANIM_TYPE_LABELS (old projects) but not in the UI list.
UI_ANIM_TYPE_LABELS = [
    (lab, name) for lab, name in ANIM_TYPE_LABELS if name != "holy_glow"
]

# ───────────────────────────────────────────────────────
# 1.5  Transition presets (for video/image/text clips)
# ───────────────────────────────────────────────────────
DEFAULT_TRANSITION_DURATION = 0.35

# Chinese labels → internal preset (type_scope)
TRANSITION_PRESETS = [
    ("无",               "none"),
    ("淡入淡出(仅开头)",  "fade_in"),
    ("淡入淡出(仅结尾)",  "fade_out"),
    ("淡入淡出(双向)",    "fade_both"),
    ("突入突出(仅开头)",  "pop_in"),
    ("突入突出(仅结尾)",  "pop_out"),
    ("突入突出(双向)",    "pop_both"),
    ("左摇右晃(仅开头)",  "sway_in"),
    ("左摇右晃(仅结尾)",  "sway_out"),
    ("左摇右晃(双向)",    "sway_both"),
    ("左滑(仅开头)",      "slide_left_in"),
    ("右滑(仅开头)",      "slide_right_in"),
    ("上滑(仅开头)",      "slide_up_in"),
    ("下滑(仅开头)",      "slide_down_in"),
    ("左滑(仅结尾)",      "slide_left_out"),
    ("右滑(仅结尾)",      "slide_right_out"),
    ("上滑(仅结尾)",      "slide_up_out"),
    ("下滑(仅结尾)",      "slide_down_out"),
    ("左滑(双向)",        "slide_left_both"),
    ("右滑(双向)",        "slide_right_both"),
    ("上滑(双向)",        "slide_up_both"),
    ("下滑(双向)",        "slide_down_both"),
    ("交叉溶解(仅结尾)",  "cross_dissolve_out"),
    ("交叉溶解(双向)",    "cross_dissolve_both"),
    ("Zoom In(仅结尾)",   "zoom_in_out"),
    ("Zoom In(双向)",     "zoom_in_both"),
]

# T20c NLE tape library. Default duration 0.35s. 黑场 is #0B0B0E, never #000.
STANDALONE_TRANSITION_LIBRARY = [
    ("交叉溶解", "cross_dissolve"),
    ("黑场", "fade_dark"),
    ("白场", "fade_white"),
    ("推左", "push_left"),
    ("推右", "push_right"),
    ("推上", "push_up"),
    ("推下", "push_down"),
    ("擦", "wipe_left"),
    ("缩放溶解", "zoom_dissolve"),
    ("模糊溶解", "blur_dissolve"),
    ("闪白", "flash_white"),
]
from cutora.render.transitions import EXTRA_TRANSITION_LIBRARY
STANDALONE_TRANSITION_LIBRARY = list(STANDALONE_TRANSITION_LIBRARY) + list(EXTRA_TRANSITION_LIBRARY)
STANDALONE_TRANSITION_DEFAULT_DUR = 0.35
FADE_DARK_RGB = (0x0B, 0x0B, 0x0E)

_EXISTING_PRESET_KEYS = {p for _l, p in TRANSITION_PRESETS}
for _lab, _p in STANDALONE_TRANSITION_LIBRARY:
    if _p not in _EXISTING_PRESET_KEYS:
        TRANSITION_PRESETS.append((f"{_lab}(双向)", _p))
        _EXISTING_PRESET_KEYS.add(_p)
    for _scope_lab, _scope in (("仅开头", "in"), ("仅结尾", "out"), ("双向", "both")):
        _sp = f"{_p}_{_scope}"
        if _sp not in _EXISTING_PRESET_KEYS:
            TRANSITION_PRESETS.append((f"{_lab}({_scope_lab})", _sp))
            _EXISTING_PRESET_KEYS.add(_sp)

_STANDALONE_BASE_SET = {p for _l, p in STANDALONE_TRANSITION_LIBRARY}


def is_standalone_transition_preset(preset) -> bool:
    if not preset or preset == "none":
        return False
    base, _scope = _parse_transition_preset(preset)
    return bool(base and base in _STANDALONE_BASE_SET and base not in ("cross_dissolve",))

# T20-f audio tape library. Seam fade is 30 ms; others default 0.35 s.
AUDIO_TRANSITION_LIBRARY = [
    ("等功率交叉淡化", "audio_xfade_equal"),
    ("线性交叉淡化", "audio_xfade_linear"),
    ("淡入", "audio_fade_in"),
    ("淡出", "audio_fade_out"),
    ("接缝淡化", "audio_seam_fade"),
]
AUDIO_SEAM_FADE_DUR = 0.030
AUDIO_TRANSITION_DEFAULT_DUR = 0.35


def audio_transition_duration(preset) -> float:
    if str(preset or "") == "audio_seam_fade":
        return AUDIO_SEAM_FADE_DUR
    return AUDIO_TRANSITION_DEFAULT_DUR


def _get_easing(name):
    """Return easing function by name, default ease_out_cubic."""
    return EASING_MAP.get(name, ease_out_cubic)

# ───────────────────────────────────────────────────────
# 2.  Core progress computation
# ───────────────────────────────────────────────────────

def compute_anim_progress(current_time, seg_start, seg_end,
                          anim_in_dur, anim_out_dur,
                          easing_in="ease_out", easing_out="ease_in_out"):
    """
    Compute a visibility progress value in [0.0, 1.0].

    0.0 = fully hidden (start of entry / end of exit)
    1.0 = fully visible (steady state between entry & exit)

    Parameters
    ----------
    current_time : float   – seconds
    seg_start    : float   – segment / overlay start time
    seg_end      : float   – segment / overlay end time
    anim_in_dur  : float   – entry animation duration (seconds), 0 = instant
    anim_out_dur : float   – exit animation duration (seconds), 0 = instant
    easing_in    : str     – easing name for entry
    easing_out   : str     – easing name for exit

    Returns  (in_progress, out_progress)
        in_progress  : 0→1 during entry, 1.0 after entry
        out_progress : 1.0 before exit, 1→0 during exit
    """
    # clamp
    anim_in_dur = max(0.0, float(anim_in_dur))
    anim_out_dur = max(0.0, float(anim_out_dur))

    dt_from_start = current_time - seg_start
    dt_to_end = seg_end - current_time

    # --- entry ---
    if anim_in_dur > 0 and dt_from_start < anim_in_dur:
        raw = max(0.0, min(1.0, dt_from_start / anim_in_dur))
        in_progress = _get_easing(easing_in)(raw)
    else:
        in_progress = 1.0

    # --- exit ---
    if anim_out_dur > 0 and dt_to_end < anim_out_dur:
        raw = max(0.0, min(1.0, dt_to_end / anim_out_dur))
        out_progress = _get_easing(easing_out)(raw)
    else:
        out_progress = 1.0

    return in_progress, out_progress

def compute_typewriter_progress(current_time, seg_start, seg_end,
                                total_chars, tw_speed=1.0):
    """
    Return the number of revealed characters (int) for a typewriter effect.

    Characters are revealed linearly over the segment duration,
    scaled by *tw_speed* (1.0 = fill entire duration; 2.0 = twice as fast, etc.)
    """
    if total_chars <= 0:
        return 0
    seg_dur = max(0.001, seg_end - seg_start)
    elapsed = max(0.0, current_time - seg_start)
    reveal_dur = seg_dur / max(0.1, tw_speed)
    frac = min(1.0, elapsed / reveal_dur)
    return int(frac * total_chars)

def compute_char_bounce_offset(current_time, char_index,
                               seg_start, seg_dur,
                               total_chars, bounce_height=20,
                               stagger=0.05, easing_name="bounce"):
    """
    Return a vertical offset (pixels, negative = up) for a single character
    in a staggered bounce animation.

    Each character starts its bounce at  seg_start + char_index * stagger,
    and the bounce lasts for a short window.
    """
    bounce_dur = 0.3  # each char bounces for 0.3s
    char_start = seg_start + char_index * stagger
    dt = current_time - char_start

    if dt < 0:
        return 0.0  # hasn't started yet
    if dt > bounce_dur:
        return 0.0  # already settled

    raw = dt / bounce_dur  # 0→1
    eased = _get_easing(easing_name)(raw)
    # bounce: go up then come back  → use  sin(π * eased)
    return -bounce_height * math.sin(math.pi * eased)

def compute_dyn_box_scale(current_time, word_start, word_end,
                          overshoot=1.3, anim_duration=0.15,
                          easing_name="back"):
    """
    Return a scale factor for the dynamic highlight box.

    Starts at 0, overshoots to *overshoot*, settles to 1.0.
    """
    dt = current_time - word_start
    if dt < 0:
        return 0.0

    if anim_duration <= 0:
        return 1.0

    if dt >= anim_duration:
        return 1.0

    raw = dt / anim_duration  # 0→1
    eased = _get_easing(easing_name)(raw)
    # ease_out_back naturally overshoots, then the final value = 1.0
    return eased

def compute_slide_offset(progress, slide_type, distance=80):
    """
    Given progress (0→1), return (dx, dy) pixel offset for slide animations.
    progress=0 → fully off-screen;  progress=1 → at final position.
    An extra smooth cubic is applied to make it feel natural even if
    progress is already eased.
    """
    # 额外平滑：用 smoothstep 让起止更舒服
    p = max(0.0, min(1.0, progress))
    smooth = p * p * (3.0 - 2.0 * p)  # smoothstep
    remaining = 1.0 - smooth
    if slide_type == "slide_up":
        return 0.0, remaining * distance
    elif slide_type == "slide_down":
        return 0.0, -remaining * distance
    elif slide_type == "slide_left":
        return remaining * distance, 0.0
    elif slide_type == "slide_right":
        return -remaining * distance, 0.0
    return 0.0, 0.0

def compute_pop_scale(progress, overshoot=1.15):
    """
    Given progress 0→1, return a scale factor for pop/zoom animation.
    Uses ease_out_back for a nice overshoot-then-settle feel.
    Works for both entry (0→1) and exit (1→0).
    """
    if progress >= 1.0:
        return 1.0
    if progress <= 0.0:
        return 0.0
    return ease_out_back(progress)

def _parse_transition_preset(preset):
    """Return (type_name, scope) from preset string."""
    if not preset or preset == "none":
        return None, None
    parts = str(preset).split("_")
    if len(parts) >= 2 and parts[-1] in ("in", "out", "both"):
        scope = parts[-1]
        type_name = "_".join(parts[:-1])
    else:
        scope = "both"
        type_name = str(preset)
    return type_name, scope

def compute_sway_offset(progress, amplitude=24.0, cycles=2.5, decay=4.0):
    """
    Return an inertial left-right sway offset (pixels).
    progress: 0→1 during transition window.
    """
    p = max(0.0, min(1.0, float(progress)))
    if p >= 1.0:
        return 0.0
    phase = math.pi / 2.0
    return amplitude * math.sin(2.0 * math.pi * cycles * p + phase) * math.exp(-decay * p)

def compute_transition_params(current_time, seg_start, seg_end,
                              preset, duration=DEFAULT_TRANSITION_DURATION,
                              slide_distance=80.0):
    """
    Compute transition parameters for a clip/overlay.
    Returns (opacity_factor, scale, dx, dy).
    """
    if current_time is None:
        return 1.0, 1.0, 0.0, 0.0
    type_name, scope = _parse_transition_preset(preset)
    if not type_name:
        return 1.0, 1.0, 0.0, 0.0

    dur = max(0.0, float(duration))
    in_dur = dur if scope in ("in", "both") else 0.0
    out_dur = dur if scope in ("out", "both") else 0.0
    in_prog, out_prog = compute_anim_progress(
        float(current_time), float(seg_start), float(seg_end),
        in_dur, out_dur, "ease_out", "ease_out"
    )

    opacity_factor = 1.0
    scale = 1.0
    dx = 0.0
    dy = 0.0

    if type_name == "fade":
        if scope in ("in", "both"):
            opacity_factor = min(opacity_factor, in_prog)
        if scope in ("out", "both"):
            opacity_factor = min(opacity_factor, out_prog)

    if type_name == "pop":
        pop_p = 1.0
        if scope in ("in", "both"):
            pop_p = min(pop_p, compute_pop_scale(in_prog))
        if scope in ("out", "both"):
            pop_p = min(pop_p, compute_pop_scale(out_prog))
        scale *= pop_p

    if type_name.startswith("slide_"):
        slide_type = type_name
        if scope in ("in", "both"):
            sx, sy = compute_slide_offset(in_prog, slide_type, distance=slide_distance)
            dx += sx; dy += sy
        if scope in ("out", "both"):
            sx, sy = compute_slide_offset(out_prog, slide_type, distance=slide_distance)
            dx += sx; dy += sy

    if type_name == "sway":
        if scope in ("in", "both"):
            dx += compute_sway_offset(in_prog)
        if scope in ("out", "both"):
            dx += compute_sway_offset(1.0 - out_prog)

    if type_name == "cross_dissolve":
        # 线性透明度过渡（交叉溶解）
        if scope in ("in", "both"):
            opacity_factor = min(opacity_factor, in_prog)
        if scope in ("out", "both"):
            opacity_factor = min(opacity_factor, out_prog)

    if type_name == "zoom_in":
        # Zoom In：缩放 1.0→1.3 + 透明度淡出（达苬奇风格）
        zoom_target = 1.3
        if scope in ("out", "both"):
            # out_prog: 1.0 → 0.0（快结束时 out_prog 趋近0）
            zoom_progress = ease_in_out_cubic(1.0 - out_prog)  # 0→1
            scale *= 1.0 + (zoom_target - 1.0) * zoom_progress
            opacity_factor = min(opacity_factor, out_prog)
        if scope in ("in", "both"):
            zoom_progress = ease_in_out_cubic(1.0 - in_prog)
            scale *= 1.0 + (zoom_target - 1.0) * zoom_progress
            opacity_factor = min(opacity_factor, in_prog)

    return opacity_factor, scale, dx, dy

# ───────────────────────────────────────────────────────
# 3.  Phase 2 animation helpers
# ───────────────────────────────────────────────────────

def compute_floating_offset(current_time, seg_start, amplitude=8.0, period=2.0):
    """
    Sinusoidal vertical float. Returns dy in pixels (oscillates ±amplitude).
    """
    dt = current_time - seg_start
    return amplitude * math.sin(2.0 * math.pi * dt / max(0.1, period))

def compute_bullet_reveal_line_alpha(current_time, seg_start, seg_end,
                                     line_index, total_lines, stagger=0.15):
    """
    Return opacity 0.0~1.0 for a specific line in bullet-reveal mode.
    Each line fades in with *stagger* seconds delay after the previous.
    """
    if total_lines <= 0:
        return 1.0
    seg_dur = max(0.001, seg_end - seg_start)
    line_start = seg_start + line_index * stagger
    fade_dur = min(0.3, seg_dur / max(1, total_lines))
    dt = current_time - line_start
    if dt < 0:
        return 0.0
    if dt >= fade_dur:
        return 1.0
    return ease_out_cubic(dt / fade_dur)

def compute_metronome_word_visible(current_time, seg_start, seg_end,
                                   word_index, total_words, bpm=120.0):
    """
    Return True if word should be visible based on fixed BPM cadence.
    Words appear one per beat.
    """
    if total_words <= 0:
        return True
    beat_interval = 60.0 / max(1.0, bpm)
    word_appear_time = seg_start + word_index * beat_interval
    return current_time >= word_appear_time

def compute_flash_highlight(current_time, seg_start, flash_duration=0.1):
    """
    Return flash intensity 0.0~1.0.  Peaks at seg_start, decays to 0 in flash_duration.
    """
    dt = current_time - seg_start
    if dt < 0 or dt > flash_duration:
        return 0.0
    return 1.0 - (dt / flash_duration)

def compute_holy_glow(current_time, seg_start, seg_end,
                      glow_radius=6, fade_period=3.0):
    """
    Return (glow_alpha_factor, glow_expansion).
    glow_alpha_factor: 0.3~1.0 slow pulsing.
    glow_expansion: additional radius for outer glow.
    """
    dt = current_time - seg_start
    pulse = 0.65 + 0.35 * math.sin(2.0 * math.pi * dt / max(0.1, fade_period))
    return pulse, glow_radius

def compute_letter_jump_scale(current_time, word_start, word_end,
                              jump_scale=1.5, anim_duration=0.2):
    """
    Return scale factor for the currently-spoken word in letter-jump mode.
    Goes from 1.0 → jump_scale → 1.0 with elastic feel.
    """
    dt = current_time - word_start
    word_dur = max(0.001, word_end - word_start)
    if dt < 0 or dt > word_dur:
        return 1.0
    half = anim_duration / 2.0
    if dt < half:
        raw = dt / half  # 0→1  (growing)
        return 1.0 + (jump_scale - 1.0) * ease_out_cubic(raw)
    elif dt < anim_duration:
        raw = (dt - half) / half  # 0→1  (shrinking)
        return jump_scale - (jump_scale - 1.0) * ease_out_cubic(raw)
    else:
        return 1.0

def compute_blur_sharp_radius(current_time, seg_start, seg_end,
                              max_blur=20, clear_frac=0.4):
    """
    Return blur radius (int).  Starts at max_blur, reaches 0 at clear_frac of duration.
    """
    seg_dur = max(0.001, seg_end - seg_start)
    dt = current_time - seg_start
    clear_time = seg_dur * clear_frac
    if dt >= clear_time:
        return 0
    frac = dt / clear_time  # 0→1
    return int(max_blur * (1.0 - ease_out_cubic(frac)))

def compute_multi_stroke_layers(base_width, layer_count, expansion_step,
                                feather_width):
    """
    Return list of (stroke_width, alpha_factor) for multi-layer stroke expansion.
    Innermost layer = full alpha, outermost = feathered to 0.
    """
    layers = []
    for i in range(layer_count):
        w = base_width + i * expansion_step
        if feather_width > 0 and layer_count > 1:
            alpha = 1.0 - (i / (layer_count - 1))  # 1.0 → 0.0
        else:
            alpha = 1.0
        layers.append((w, alpha))
    return layers

# ───────────────────────────────────────────────────────
# 4.  Audio spectrum animation helpers
# ───────────────────────────────────────────────────────

def compute_spectrum_bar_heights(bars, max_height, smoothing=0.7):
    """
    Given a list of raw bar amplitudes [0~1], return pixel heights
    with optional smoothing for visual appeal.

    Parameters
    ----------
    bars       : list[float]  raw amplitude values (0~1)
    max_height : float        maximum bar height in pixels
    smoothing  : float        0=no smoothing, 1=full smoothing (previous frame)

    Returns
    -------
    list[float]  bar heights in pixels
    """
    return [min(max_height, b * max_height) for b in bars]

def compute_spectrum_colors(num_bars, base_hue=0.55, saturation=0.8,
                            lightness=0.6, hue_spread=0.3):
    """Generate a list of (r, g, b) tuples for spectrum bars."""
    import colorsys
    colors = []
    for i in range(num_bars):
        h = (base_hue + (i / max(1, num_bars - 1)) * hue_spread) % 1.0
        r, g, b = colorsys.hls_to_rgb(h, lightness, saturation)
        colors.append((int(r * 255), int(g * 255), int(b * 255)))
    return colors

# ───────────────────────────────────────────────────────
# 5.  Dynamic image attachment effects
# ───────────────────────────────────────────────────────

def compute_dyn_img_wobble(current_time, period=0.8, amplitude=3.0):
    """Return (angle_deg, dx, dy) for subtle wobble/shake animation."""
    t = current_time * 2 * math.pi / max(0.1, period)
    angle = amplitude * math.sin(t) * 0.5
    dx = amplitude * math.sin(t * 1.3) * 0.3
    dy = amplitude * math.cos(t * 0.9) * 0.3
    return angle, dx, dy

def compute_dyn_img_pulse(current_time, period=1.0, min_scale=0.9, max_scale=1.1):
    """Return scale factor for breathing/pulse effect."""
    t = current_time * 2 * math.pi / max(0.1, period)
    frac = (math.sin(t) + 1.0) / 2.0  # 0~1
    return min_scale + frac * (max_scale - min_scale)

def compute_dyn_img_breathe_alpha(current_time, period=1.5, min_alpha=0.4, max_alpha=1.0):
    """Return alpha factor for breathing opacity effect."""
    t = current_time * 2 * math.pi / max(0.1, period)
    frac = (math.sin(t) + 1.0) / 2.0
    return min_alpha + frac * (max_alpha - min_alpha)

def compute_box_color_transition(current_time, seg_start, seg_end,
                                 words_info, color_from, color_to):
    """
    Return (r, g, b, a) for box color that transitions from color_from to color_to
    based on reading progress through the segment.
    """
    if not words_info:
        return color_from
    seg_dur = max(0.001, seg_end - seg_start)
    progress = max(0.0, min(1.0, (current_time - seg_start) / seg_dur))
    r = int(color_from[0] + (color_to[0] - color_from[0]) * progress)
    g = int(color_from[1] + (color_to[1] - color_from[1]) * progress)
    b = int(color_from[2] + (color_to[2] - color_from[2]) * progress)
    a = int(color_from[3] + (color_to[3] - color_from[3]) * progress)
    return (r, g, b, a)

# Spectrum shape types
SPECTRUM_SHAPES = [
    ("频谱条", "bars"),
    ("曲线", "curve"),
    ("折线", "zigzag"),
    ("圆点", "dots"),
    ("爱心", "hearts"),
]

# ───────────────────────────────────────────────────────
# 7.  Scrolling Lyrics Mode  (歌词滚动模式)
# ───────────────────────────────────────────────────────

def spring_interp(t, damping=0.8, stiffness=300.0, mass=1.0):
    """
    Critically / under-damped spring interpolator.

    t       : normalised progress ∈ [0, 1]
    damping : ζ  (0 = no damping, 1 = critical, <1 = underdamped bounce)
    stiffness : k  (spring constant – higher = snappier)
    mass    : m

    Returns a value that starts at 0.0, overshoots slightly when ζ<1,
    and settles towards 1.0.  Used for the "maglev snap" feeling.
    """
    t = max(0.0, min(1.0, float(t)))
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0

    omega = math.sqrt(max(0.01, stiffness) / max(0.01, mass))  # ω₀
    zeta = max(0.0, min(1.5, float(damping)))                  # ζ

    if zeta >= 1.0:
        # Over-damped / critically-damped  → no oscillation
        # Use an aggressive ease-out-expo as physical approximation
        return 1.0 - math.exp(-omega * zeta * t) * (1.0 + omega * zeta * t * 0.5)
    else:
        # Under-damped  → beautiful springy overshoot
        omega_d = omega * math.sqrt(max(1e-6, 1.0 - zeta * zeta))  # ωd
        decay = math.exp(-zeta * omega * t)
        return 1.0 - decay * math.cos(omega_d * t)

def compute_lyrics_scroll_offset(current_time, active_seg_start,
                                  prev_seg_start=None,
                                  transition_dur=0.4, damping=0.8):
    """
    Compute the spring-based scroll transition progress for lyrics mode.

    Returns a float ∈ [0, 1]:
        0.0 = still showing the previous line position
        1.0 = fully settled on the new (current) line position

    During a gap (no active segment), returns 1.0 so the display
    stays on the last known position without jarring jumps.
    """
    if active_seg_start is None:
        return 1.0  # gap period – stay put
    transition_dur = max(0.05, float(transition_dur))
    dt = float(current_time) - float(active_seg_start)
    if dt < 0:
        return 0.0
    if dt >= transition_dur:
        return 1.0
    raw = dt / transition_dur  # 0→1
    return spring_interp(raw, damping=damping)

def compute_lyrics_line_alpha(line_offset, decay_rate=0.30):
    """
    Per-line alpha decay for scrolling lyrics.  Based on DISCRETE line
    distance from the centre slot (the currently-being-read line).

    line_offset : float – distance from centre in line units
                          (0 = centre, ±1 = adjacent, ±2 = two away, …)
                          Can be fractional during spring transition.
    decay_rate  : float – alpha reduction per line step
                          (0.0 = no decay, 0.30 = each line keeps 70%)

    Returns alpha ∈ [0, 1]:
        offset=0 → 1.0
        offset=1 → 1.0 * (1 - decay_rate)
        offset=2 → (1 - decay_rate)²
        …
    """
    d = abs(float(line_offset))
    if d < 0.001:
        return 1.0
    decay_rate = max(0.0, min(0.95, float(decay_rate)))
    if decay_rate < 0.001:
        return 1.0
    return max(0.0, (1.0 - decay_rate) ** d)

def compute_lyrics_line_scale(line_offset, spring_progress=1.0,
                               center_scale=1.15, normal_scale=1.0):
    """
    Scale factor for scrolling lyrics.  STRICTLY only the centre line
    (the one being read) is scaled up.  All other lines stay at normal_scale.

    During spring transition:
    - The new centre line (|offset| < 0.5) grows from normal → center_scale
    - The outgoing centre line (|offset| ≈ 1) shrinks from center_scale → normal

    line_offset      : float – distance from centre (0 = centre).
                               Fractional during spring transition.
    spring_progress  : 0→1 spring transition progress
    center_scale     : target scale for the centre line
    normal_scale     : scale for all non-centre lines

    Returns scale factor (float).
    """
    d = abs(float(line_offset))
    sp = max(0.0, min(1.0, float(spring_progress)))

    if d < 0.5:
        # This IS (or is becoming) the centre line → scale UP with spring
        return normal_scale + (center_scale - normal_scale) * sp
    elif d < 1.5 and sp < 1.0:
        # This is the OUTGOING centre line → shrink BACK with spring
        # At d=0.5 blend=1.0, at d=1.5 blend=0.0
        blend = max(0.0, 1.0 - (d - 0.5))
        outgoing_scale = normal_scale + (center_scale - normal_scale) * (1.0 - sp)
        return normal_scale + (outgoing_scale - normal_scale) * blend
    else:
        # All other lines: fixed at normal scale
        return normal_scale

# ───────────────────────────────────────────────────────
# 6.  Standalone NLE Transition Compositor
# ───────────────────────────────────────────────────────

def compose_standalone_transition(prog, preset, out_img, in_img, canvas_w, canvas_h,
                                  zoom_scale_start=0.7, motion_blur=0.4,
                                  intensity=1.0, easing="ease_in_out", direction=None,
                                  angle=None, feather=None):
    """
    NLE Gen-2 独立转场合成器。

    Parameters
    ----------
    prog             : float  – 转场进度 ∈ [0, 1)。0=转场刚开始, 1=转场结束。
    preset           : str    – 转场预设名，例如 "cross_dissolve_both", "zoom_in_both"
    out_img          : QImage – 退场视频在当前帧的渲染结果（ARGB32 格式）
    in_img           : QImage – 进场视频在当前帧的渲染结果（ARGB32 格式）
    canvas_w         : int    – 输出画布宽度（像素）
    canvas_h         : int    – 输出画布高度（像素）
    zoom_scale_start : float  – 进场缩放起始比例（0.5~1.0），默认 0.7
    motion_blur      : float  – 运动模糊强度（0.0~1.0），默认 0.4

    Returns
    -------
    QImage   – 合成后的最终帧（ARGB32 格式），直接可由 QPainter 绘制到屏幕
    """
    try:
        from PySide6.QtGui import (
            QImage,
            QPainter,
            QColor,
        )
        from PySide6.QtCore import QRectF
    except ImportError:
        from PyQt5.QtGui import (
            QImage,
            QPainter,
        )
        from PyQt5.QtCore import QRectF

    prog = max(0.0, min(1.0, float(prog)))
    zoom_scale_start = max(0.3, min(1.0, float(zoom_scale_start)))
    motion_blur = max(0.0, min(1.0, float(motion_blur)))

    from cutora.render.transitions import apply_transition
    extra = apply_transition(
        str(preset or ""), out_img, in_img, prog, canvas_w, canvas_h,
        zoom_scale_start=zoom_scale_start, motion_blur=motion_blur,
        intensity=intensity, easing=easing, direction=direction,
        angle=angle, feather=feather)
    if extra is not None:
        return extra

    # 解析 preset 类型
    parts = preset.split("_")
    if parts and parts[-1] in ("in", "out", "both"):
        base = "_".join(parts[:-1])
    else:
        base = preset

    # 准备合成画布
    result = QImage(canvas_w, canvas_h, QImage.Format.Format_ARGB32_Premultiplied)
    result.fill(0)  # 透明底

    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    try:
        # ── 平滑缓入缓出曲线（让转场更柔和）──
        p_smooth = prog * prog * (3.0 - 2.0 * prog)  # smoothstep

        if base == "cross_dissolve":
            # Opaque A, then B at progress. Dual independent alphas over a
            # transparent buffer punched through to the black export canvas
            # whenever either handle was missing (半透明画面半黑).
            if out_img and not out_img.isNull():
                painter.setOpacity(1.0)
                painter.drawImage(QRectF(0, 0, canvas_w, canvas_h), out_img,
                                  QRectF(0, 0, out_img.width(), out_img.height()))
            if in_img and not in_img.isNull():
                painter.setOpacity(p_smooth)
                painter.drawImage(QRectF(0, 0, canvas_w, canvas_h), in_img,
                                  QRectF(0, 0, in_img.width(), in_img.height()))

        elif base == "zoom_in":
            # Opaque A (scale >= 1 covers the canvas), then B at progress.
            # Dual independent alphas over fill(0) leaked through to black.
            zoom_out_scale = 1.0 + 0.3 * p_smooth   # 1.0 → 1.3
            in_alpha = p_smooth
            scale_range = 1.0 - zoom_scale_start
            zoom_in_scale = zoom_scale_start + scale_range * p_smooth

            if out_img and not out_img.isNull():
                painter.save()
                painter.setOpacity(1.0)
                cx, cy = canvas_w / 2.0, canvas_h / 2.0
                painter.translate(cx, cy)
                painter.scale(zoom_out_scale, zoom_out_scale)
                painter.translate(-canvas_w / 2.0, -canvas_h / 2.0)
                painter.drawImage(QRectF(0, 0, canvas_w, canvas_h), out_img,
                                  QRectF(0, 0, out_img.width(), out_img.height()))
                painter.restore()

            if in_img and not in_img.isNull():
                iw, ih = in_img.width(), in_img.height()
                src = QRectF(0, 0, iw, ih)
                mir_h  = in_img.mirrored(True, False)
                mir_v  = in_img.mirrored(False, True)
                mir_hv = in_img.mirrored(True, True)

                def _draw_in_frame_at_scale(s, opacity_factor):
                    """以指定 scale 画进场帧 + 镜像填充，opacity_factor 叠乘总透明度。"""
                    fw = canvas_w * s
                    fh = canvas_h * s
                    x0 = (canvas_w - fw) / 2.0
                    y0 = (canvas_h - fh) / 2.0
                    painter.setOpacity(in_alpha * opacity_factor)
                    painter.drawImage(QRectF(x0 - fw, y0 - fh, fw, fh), mir_hv, src)
                    painter.drawImage(QRectF(x0 + fw, y0 - fh, fw, fh), mir_hv, src)
                    painter.drawImage(QRectF(x0 - fw, y0 + fh, fw, fh), mir_hv, src)
                    painter.drawImage(QRectF(x0 + fw, y0 + fh, fw, fh), mir_hv, src)
                    painter.drawImage(QRectF(x0 - fw, y0,      fw, fh), mir_h,  src)
                    painter.drawImage(QRectF(x0 + fw, y0,      fw, fh), mir_h,  src)
                    painter.drawImage(QRectF(x0,      y0 - fh, fw, fh), mir_v,  src)
                    painter.drawImage(QRectF(x0,      y0 + fh, fw, fh), mir_v,  src)
                    painter.drawImage(QRectF(x0, y0, fw, fh), in_img, src)

                if motion_blur > 0.01:
                    n_blur = max(2, int(motion_blur * 7))
                    blur_range = scale_range * motion_blur * 0.35
                    for i in range(n_blur, 0, -1):
                        frac = i / float(n_blur)
                        ghost_scale = zoom_in_scale - blur_range * frac
                        ghost_scale = max(zoom_scale_start * 0.6, ghost_scale)
                        ghost_opacity = motion_blur * 0.25 * (1.0 - frac * 0.5)
                        _draw_in_frame_at_scale(ghost_scale, ghost_opacity)

                painter.save()
                _draw_in_frame_at_scale(zoom_in_scale, 1.0)
                painter.restore()

        elif base in ("fade_dark", "fade_black", "fade_white"):
            from PySide6.QtGui import QColor
            if base == "fade_white":
                fill = QColor(255, 255, 255)
            else:
                fill = QColor(*FADE_DARK_RGB)
            if p_smooth < 0.5:
                a = p_smooth * 2.0
                if out_img and not out_img.isNull():
                    painter.setOpacity(1.0)
                    painter.drawImage(QRectF(0, 0, canvas_w, canvas_h), out_img,
                                      QRectF(0, 0, out_img.width(), out_img.height()))
                painter.setOpacity(a)
                painter.fillRect(0, 0, canvas_w, canvas_h, fill)
            else:
                a = (p_smooth - 0.5) * 2.0
                painter.fillRect(0, 0, canvas_w, canvas_h, fill)
                if in_img and not in_img.isNull():
                    painter.setOpacity(a)
                    painter.drawImage(QRectF(0, 0, canvas_w, canvas_h), in_img,
                                      QRectF(0, 0, in_img.width(), in_img.height()))

        elif base in ("push_left", "push_right", "push_up", "push_down"):
            dx = dy = 0.0
            if base.endswith("left"):
                dx = -canvas_w * p_smooth
                in_dx = canvas_w + dx
            elif base.endswith("right"):
                dx = canvas_w * p_smooth
                in_dx = -canvas_w + dx
            elif base.endswith("up"):
                dy = -canvas_h * p_smooth
                in_dy = canvas_h + dy
                in_dx = 0.0
            else:
                dy = canvas_h * p_smooth
                in_dy = -canvas_h + dy
                in_dx = 0.0
            if "up" not in base and "down" not in base:
                in_dy = 0.0
            if out_img and not out_img.isNull():
                painter.setOpacity(1.0)
                painter.drawImage(QRectF(dx, dy, canvas_w, canvas_h), out_img,
                                  QRectF(0, 0, out_img.width(), out_img.height()))
            if in_img and not in_img.isNull():
                painter.setOpacity(1.0)
                painter.drawImage(QRectF(in_dx, in_dy, canvas_w, canvas_h), in_img,
                                  QRectF(0, 0, in_img.width(), in_img.height()))

        elif base in ("wipe_left", "wipe_right", "wipe_up", "wipe_down"):
            if out_img and not out_img.isNull():
                painter.setOpacity(1.0)
                painter.drawImage(QRectF(0, 0, canvas_w, canvas_h), out_img,
                                  QRectF(0, 0, out_img.width(), out_img.height()))
            if in_img and not in_img.isNull() and p_smooth > 0:
                painter.setOpacity(1.0)
                if base.endswith("right"):
                    ww = int(round(canvas_w * p_smooth))
                    painter.setClipRect(canvas_w - ww, 0, ww, canvas_h)
                elif base.endswith("up"):
                    hh = int(round(canvas_h * p_smooth))
                    painter.setClipRect(0, 0, canvas_w, hh)
                elif base.endswith("down"):
                    hh = int(round(canvas_h * p_smooth))
                    painter.setClipRect(0, canvas_h - hh, canvas_w, hh)
                else:
                    ww = int(round(canvas_w * p_smooth))
                    painter.setClipRect(0, 0, ww, canvas_h)
                painter.drawImage(QRectF(0, 0, canvas_w, canvas_h), in_img,
                                  QRectF(0, 0, in_img.width(), in_img.height()))
                painter.setClipping(False)

        elif base in ("zoom_dissolve", "blur_dissolve"):
            extra = 1.0 + 0.08 * p_smooth if base == "zoom_dissolve" else 1.0
            if out_img and not out_img.isNull():
                painter.save()
                painter.setOpacity(1.0)
                if extra != 1.0:
                    painter.translate(canvas_w / 2.0, canvas_h / 2.0)
                    painter.scale(extra, extra)
                    painter.translate(-canvas_w / 2.0, -canvas_h / 2.0)
                painter.drawImage(QRectF(0, 0, canvas_w, canvas_h), out_img,
                                  QRectF(0, 0, out_img.width(), out_img.height()))
                painter.restore()
            if in_img and not in_img.isNull():
                if base == "blur_dissolve":
                    for ox, oy, a in ((-2, 0, 0.25), (2, 0, 0.25), (0, -2, 0.25), (0, 2, 0.25)):
                        painter.setOpacity(p_smooth * a)
                        painter.drawImage(QRectF(ox, oy, canvas_w, canvas_h), in_img,
                                          QRectF(0, 0, in_img.width(), in_img.height()))
                painter.setOpacity(p_smooth)
                painter.drawImage(QRectF(0, 0, canvas_w, canvas_h), in_img,
                                  QRectF(0, 0, in_img.width(), in_img.height()))

        elif base == "flash_white":
            peak = 1.0 - abs(p_smooth - 0.5) * 2.0
            if p_smooth < 0.5 and out_img and not out_img.isNull():
                painter.setOpacity(1.0)
                painter.drawImage(QRectF(0, 0, canvas_w, canvas_h), out_img,
                                  QRectF(0, 0, out_img.width(), out_img.height()))
            elif in_img and not in_img.isNull():
                painter.setOpacity(1.0)
                painter.drawImage(QRectF(0, 0, canvas_w, canvas_h), in_img,
                                  QRectF(0, 0, in_img.width(), in_img.height()))
            painter.setOpacity(peak)
            painter.fillRect(0, 0, canvas_w, canvas_h, QColor(255, 255, 255))

        else:
            # 未知预设 → 回退为交叉溶解（不透明底 + 叠加，禁止双 alpha）
            if out_img and not out_img.isNull():
                painter.setOpacity(1.0)
                painter.drawImage(QRectF(0, 0, canvas_w, canvas_h), out_img,
                                  QRectF(0, 0, out_img.width(), out_img.height()))
            if in_img and not in_img.isNull():
                painter.setOpacity(p_smooth)
                painter.drawImage(QRectF(0, 0, canvas_w, canvas_h), in_img,
                                  QRectF(0, 0, in_img.width(), in_img.height()))

    finally:
        painter.end()

    return result


def apply_overlay_standalone_transition(layer_img, current_time, seg_start, seg_end,
                                        preset, duration=DEFAULT_TRANSITION_DURATION):
    """Apply standalone NLE transition to an image or text overlay layer_img (preserving alpha)."""
    if layer_img is None or layer_img.isNull() or current_time is None:
        return layer_img
    if not is_standalone_transition_preset(preset):
        return layer_img
    base, scope = _parse_transition_preset(preset)
    if not base:
        return layer_img
    dur = max(0.01, float(duration or DEFAULT_TRANSITION_DURATION))
    dt_in = float(current_time) - float(seg_start)
    dt_out = float(seg_end) - float(current_time)
    entering = None
    prog = None
    if scope in ("in", "both") and 0.0 <= dt_in < dur:
        entering = True
        prog = max(0.0, min(1.0, dt_in / dur))
    elif scope in ("out", "both") and 0.0 <= dt_out < dur:
        entering = False
        prog = max(0.0, min(1.0, 1.0 - (dt_out / dur)))
    if entering is None or prog is None:
        return layer_img

    w = int(layer_img.width())
    h = int(layer_img.height())
    if w <= 1 or h <= 1:
        return layer_img

    try:
        import numpy as np
        from PySide6.QtGui import QImage, QPainter, QColor
        conv = layer_img.convertToFormat(QImage.Format.Format_RGBA8888)
        bpl = conv.bytesPerLine()
        raw = np.frombuffer(conv.constBits(), dtype=np.uint8).reshape((h, bpl))[:, :w * 4].reshape((h, w, 4)).copy()
        alpha_orig = raw[..., 3].astype(np.float32)

        # Build opaque present image over #0B0B0E and empty #0B0B0E image
        bg_rgb = np.array([0x0B, 0x0B, 0x0E], dtype=np.float32)
        a_norm = alpha_orig[..., None] / 255.0
        rgb_present = np.clip(raw[..., :3].astype(np.float32) * a_norm + bg_rgb * (1.0 - a_norm), 0, 255).astype(np.uint8)
        rgba_present = np.empty((h, w, 4), dtype=np.uint8)
        rgba_present[..., :3] = rgb_present
        rgba_present[..., 3] = 255
        img_present = QImage(rgba_present.data, w, h, w * 4, QImage.Format.Format_RGBA8888).convertToFormat(
            QImage.Format.Format_ARGB32_Premultiplied
        )
        img_empty = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        img_empty.fill(QColor(0x0B, 0x0B, 0x0E))

        # Build alpha-tracking grayscale images (11..255 mapped so _blank #0B0B0E = 0 alpha)
        a_mapped = np.clip(11.0 + (alpha_orig / 255.0) * (255.0 - 11.0), 11, 255).astype(np.uint8)
        mask_rgba = np.empty((h, w, 4), dtype=np.uint8)
        mask_rgba[..., 0] = a_mapped
        mask_rgba[..., 1] = a_mapped
        mask_rgba[..., 2] = a_mapped
        mask_rgba[..., 3] = 255
        mask_present = QImage(mask_rgba.data, w, h, w * 4, QImage.Format.Format_RGBA8888).convertToFormat(
            QImage.Format.Format_ARGB32_Premultiplied
        )

        out_rgb = img_empty if entering else img_present
        in_rgb = img_present if entering else img_empty
        out_msk = img_empty if entering else mask_present
        in_msk = mask_present if entering else img_empty

        comp_rgb_img = compose_standalone_transition(prog, base, out_rgb, in_rgb, w, h)
        comp_msk_img = compose_standalone_transition(prog, base, out_msk, in_msk, w, h)
        if comp_rgb_img is None or comp_rgb_img.isNull() or comp_msk_img is None or comp_msk_img.isNull():
            return layer_img

        c_rgb_conv = comp_rgb_img.convertToFormat(QImage.Format.Format_RGBA8888)
        c_msk_conv = comp_msk_img.convertToFormat(QImage.Format.Format_RGBA8888)
        c_rgb = np.frombuffer(c_rgb_conv.constBits(), dtype=np.uint8).reshape((h, c_rgb_conv.bytesPerLine()))[:, :w * 4].reshape((h, w, 4))[:, :, :3].astype(np.float32)
        c_msk = np.frombuffer(c_msk_conv.constBits(), dtype=np.uint8).reshape((h, c_msk_conv.bytesPerLine()))[:, :w * 4].reshape((h, w, 4))[:, :, :3].astype(np.float32)

        # Extract transitioned alpha from mask green channel (11 -> 0, 255 -> 255)
        trans_alpha = np.clip((c_msk[..., 1] - 11.0) * (255.0 / 244.0), 0.0, 255.0)
        # For flash/white/light transitions where mask is brightened globally, bound by original silhouette + fade
        if base in ("fade_white", "flash_white", "light_leak", "film_burn", "fade_dark"):
            fade_env = prog if entering else (1.0 - prog)
            trans_alpha = np.minimum(trans_alpha, alpha_orig * max(0.0, min(1.0, fade_env * 1.35)))

        # Un-blend #0B0B0E background from RGB where alpha > 0
        ta_norm = np.maximum(trans_alpha[..., None] / 255.0, 1e-4)
        out_unpremult = np.clip((c_rgb - bg_rgb * (1.0 - ta_norm)) / ta_norm, 0.0, 255.0)
        final_rgba = np.empty((h, w, 4), dtype=np.uint8)
        final_rgba[..., :3] = out_unpremult.astype(np.uint8)
        final_rgba[..., 3] = trans_alpha.astype(np.uint8)
        res = QImage(final_rgba.data, w, h, w * 4, QImage.Format.Format_RGBA8888).copy()
        return res.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    except Exception:
        return layer_img



# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Style packs: directory format, zip import/export, apply to a task."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import zipfile
from datetime import datetime, timezone

from cutora.core.log import get_logger
from cutora.core.paths import RESOURCES, user_presets_dir

logger = get_logger("cutora.subtitle.stylepack")
_paint_task_hint = ""


def set_paint_task_hint(path: str | None) -> None:
    global _paint_task_hint
    _paint_task_hint = str(path or "")


def ensure_style_has_fx(style, *, seg_idx=None, cue="") -> dict:
    """Load-time migrate should have set fx. Missing fx at paint is upstream.

    Logs an error, then still migrates so export does not crash.
    """
    if isinstance((style or {}).get("fx"), dict):
        return style if isinstance(style, dict) else {}
    logger.error(
        "subtitle style has no fx (upstream). task=%s seg_idx=%s cue=%s",
        _paint_task_hint or "?",
        seg_idx,
        (cue or "")[:80],
    )
    return migrate_v1_bg_to_fx(dict(style or {}))


def diag_enabled() -> bool:
    return os.environ.get("CUTORA_DEBUG_STYLEPACK", "") == "1"


def style_snapshot(style, layout=None) -> dict:
    """Key fields for T04e apply-vs-preview diagnosis."""
    s = style if isinstance(style, dict) else {}
    out = {
        "obj_id": id(style) if style is not None else 0,
        "font": s.get("font_family") or s.get("font"),
        "size": s.get("fontsize") or s.get("size"),
        "color": s.get("color_text") or s.get("color"),
        "stroke_w": s.get("border_width") or s.get("stroke_width"),
        "stroke_c": s.get("color_outline") or s.get("stroke_color"),
        "bg_op": s.get("opacity_bg") or s.get("bg_opacity"),
        "anim_in_type": s.get("anim_in_type"),
        "use_box": s.get("use_box"),
        "use_stroke": s.get("use_stroke"),
        "pos_x": s.get("pos_x"),
        "pos_y": s.get("pos_y"),
    }
    if layout is not None:
        out["layout_rules"] = dict(layout) if isinstance(layout, dict) else layout
    return out


def diag(msg: str, **payload) -> None:
    if not diag_enabled():
        return
    line = "T04e-diag %s %s" % (
        msg, json.dumps(payload, ensure_ascii=False, default=str))
    logger.info(line)

REF_W = 1080
REF_H = 1920
VERSION = 1
PACK_STYLE = "style.json"
PACK_META = "meta.json"
PACK_PREVIEW = "preview.png"
PACK_THUMB = "thumb.png"
PACK_THUMB_HASH = "thumb.hash"
THUMB_SIZE = 64
ALLOWED_ZIP_EXT = {".json", ".png", ".ttf", ".otf"}
MAX_MEMBER = 8 * 1024 * 1024
MAX_TOTAL = 32 * 1024 * 1024
SAMPLE = "片语 Studio · 让每一句话，找到它的位置。"
SAMPLE_EN = "Find its place"
SAMPLE_ZH = "让每一句话"
GALLERY_CARD_W = 180
GALLERY_ASPECT = 5.6
GALLERY_FIT_PAD = 2
GALLERY_HOVER_HOLD_S = 0.6
SAFE_MARGIN = 0.08
DEFAULT_WRAP_WIDTH_PERCENT = 86
PACK_OFFSET_Y_PCT = -0.42


def sample_event(text: str, start: float = 0.0, end: float = 10.0) -> dict:
    """Timed words from *text*. Empty ``words`` makes v1 ``draw_subtitle`` skip glyphs."""
    parts = [p for p in str(text or "").split() if p]
    if not parts:
        parts = [SAMPLE_EN]
    t0 = float(start)
    t1 = float(end) if float(end) > t0 else t0 + 10.0
    step = (t1 - t0) / float(len(parts))
    words = []
    t = t0
    for i, word in enumerate(parts):
        words.append({
            "word": word, "start": t, "end": t + step, "wid": i,
        })
        t += step
    return {
        "text": " ".join(parts), "start": t0, "end": t1, "words": words,
    }


def gallery_column_count(panel_w: int) -> int:
    return 1 if int(panel_w) < 320 else 2


def gallery_column_width(panel_w: int, spacing: int = 10) -> int:
    """Card width from panel width. Columns first, then divide — never the reverse."""
    cols = gallery_column_count(panel_w)
    inner = int(panel_w) - int(spacing) * (cols - 1)
    return max(80, inner // cols)


def gallery_grid_width(panel_w: int) -> int:
    """IconMode cell width; ``cols * cell <= panel_w`` so two columns actually wrap."""
    cols = gallery_column_count(panel_w)
    return max(80, int(panel_w) // cols)


def gallery_band_h(canvas_w: int, canvas_h: int) -> float:
    """Strip height for the 5.6:1 window. Never grows for a tall bbox."""
    w = float(max(1, canvas_w))
    h = float(max(1, canvas_h))
    return max(1.0, min(w / GALLERY_ASPECT, h))


def gallery_shared_band_h(
    canvas_w: int, canvas_h: int, bbox_heights=None,
) -> float:
    """All cards share the 5.6:1 strip. *bbox_heights* is ignored."""
    return gallery_band_h(canvas_w, canvas_h)


def gallery_crop_band(
    canvas_w: int, canvas_h: int,
    bbox_cy: float | None = None, bbox_cx: float | None = None,
    bbox_h: float | None = None, band_h: float | None = None,
) -> tuple[float, float, float, float]:
    """Full-width 5.6:1 strip, vertically centered on bbox."""
    w = float(max(1, canvas_w))
    h = float(max(1, canvas_h))
    bh = float(band_h) if band_h is not None else gallery_band_h(int(w), int(h))
    bh = max(1.0, min(bh, h))
    cy = float(bbox_cy) if bbox_cy is not None else (h * 0.58)
    y0 = max(0.0, min(cy - bh / 2.0, h - bh))
    return (0.0, y0, w, bh)


def gallery_content_band(
    canvas_w: int, canvas_h: int,
    bbox_cy: float | None = None, bbox_h: float | None = None,
) -> tuple[tuple[float, float, float, float], bool]:
    """Crop rect plus whether the bbox overflows the 5.6 window (then fit)."""
    x, y, bw, bh = gallery_crop_band(
        canvas_w, canvas_h, bbox_cy=bbox_cy, bbox_h=bbox_h)
    if bbox_h is None or float(bbox_h) <= bh:
        return (x, y, bw, bh), False
    need = min(float(bbox_h), float(max(1, canvas_h)))
    cy = float(bbox_cy) if bbox_cy is not None else (canvas_h * 0.58)
    y0 = max(0.0, min(cy - need / 2.0, float(canvas_h) - need))
    return (0.0, y0, float(max(1, canvas_w)), need), True


def gallery_card_size(
    canvas_w: int = 0, canvas_h: int = 0,
    bbox_cx: float | None = None, bbox_cy: float | None = None,
    col_w: int | None = None, band_h: float | None = None,
    bbox_h: float | None = None,
) -> tuple[int, int]:
    """Card width is the column width; height is always width / 5.6."""
    w = max(80, int(col_w) if col_w else GALLERY_CARD_W)
    return w, max(1, int(round(w / GALLERY_ASPECT)))


def max_chars_from_width(
    canvas_w: int, avg_char_w: float, cap: int, margin: float = SAFE_MARGIN,
) -> int:
    """``min(pack cap, floor(usable_width / avg_char_width))``. Qt-free."""
    usable = float(max(1, canvas_w)) * (1.0 - 2.0 * float(margin))
    cap_i = max(1, int(cap))
    if avg_char_w <= 0:
        return cap_i
    return max(1, min(cap_i, int(usable // float(avg_char_w))))


def effective_max_lines(style: dict | None, task: dict | None = None) -> int:
    """User ``wrap_lines`` wins, else pack/task ``layout_rules.max_lines``."""
    s = style if isinstance(style, dict) else {}
    raw = s.get("wrap_lines")
    rules = s.get("layout_rules") if isinstance(s.get("layout_rules"), dict) else {}
    if raw is None:
        raw = rules.get("max_lines")
    if raw is None and isinstance(task, dict):
        tr = task.get("layout_rules") if isinstance(task.get("layout_rules"), dict) else {}
        raw = tr.get("max_lines")
        if raw is None:
            ts = task.get("style") if isinstance(task.get("style"), dict) else {}
            raw = ts.get("wrap_lines")
    try:
        return max(1, int(raw if raw is not None else 2))
    except (TypeError, ValueError):
        return 2


def wrap_width_percent_value(style: dict | None) -> float | None:
    """0–100 if the style sets wrap width; else None (SAFE_MARGIN fallback)."""
    raw = (style or {}).get("wrap_width_percent")
    if isinstance(raw, (int, float)):
        return max(0.0, min(100.0, float(raw)))
    return None


def line_max_width_px(
    style: dict | None, canvas_w: int, *, em: float | None = None,
    extra_pad: float = 0.0,
) -> float:
    """Usable line width: wrap_width_percent of canvas, else leftover wrap_left/right, else SAFE_MARGIN."""
    s = style or {}
    pad = float(extra_pad)
    floor = (float(em) if em else 20.0) * 2.0
    canvas = float(max(1, int(canvas_w)))
    pct = wrap_width_percent_value(s)
    if pct is not None:
        return max(floor, canvas * (pct / 100.0) - 2.0 * pad)
    wrap_left = s.get("wrap_left")
    wrap_right = s.get("wrap_right")
    if isinstance(wrap_left, (int, float)) and isinstance(wrap_right, (int, float)):
        if float(wrap_right) > float(wrap_left) + 50:
            return max(floor, float(wrap_right) - float(wrap_left) - 2.0 * pad)
    margin = SAFE_MARGIN
    if s.get("safe_margin_pct") is not None:      # Q137：用户定的安全边距
        try:
            margin = max(0.0, min(0.45, float(s.get("safe_margin_pct")) / 100.0))
        except (TypeError, ValueError):
            margin = SAFE_MARGIN
    max_w = canvas * (1.0 - 2.0 * margin) - 2.0 * pad
    return max(floor, max_w)


def estimate_words_per_line(style: dict | None, canvas_w: int, text: str) -> int:
    """Live hint: how many words of *text* fit on one line at this wrap width."""
    from PySide6.QtGui import QFont, QFontMetricsF
    from cutora.ui.panels.fonts import get_font_manager

    s = style or {}
    family = str(s.get("font_family") or "Arimo")
    try:
        fs = max(8, int(round(float(s.get("fontsize") or 34))))
    except (TypeError, ValueError):
        fs = 34
    font = get_font_manager().get_font(
        family, fs,
        bold=bool(s.get("bold", True)),
        italic=bool(s.get("italic", False)),
    )
    tracking = 0.0
    fx = s.get("fx") if isinstance(s.get("fx"), dict) else {}
    try:
        tracking = float(fx.get("tracking_em") or 0)
    except (TypeError, ValueError):
        tracking = 0.0
    if tracking:
        font.setLetterSpacing(
            QFont.SpacingType.PercentageSpacing, 100.0 + tracking * 100.0)
    fm = QFontMetricsF(font)
    words = [w for w in str(text or "").split() if w]
    if not words:
        words = ["word"]
    space = float(fm.horizontalAdvance(" "))
    total = 0.0
    for w in words:
        total += float(fm.horizontalAdvance(w))
    avg = total / float(len(words)) + space
    # Q137：提示要和渲染 / 断句量的是同一个宽度（含安全边距、描边外扩），
    # 否则面板说「每行 6 词」实际只放得下 4 个。
    from cutora.subtitle.reflow import max_words_per_line, wrap_style_for_measure
    ms = wrap_style_for_measure(s, canvas_w)
    pad = 0.0
    try:
        from cutora.render.textfx.spec import expansion_px, from_style
        pad = expansion_px(from_style(s), float(fs), for_wrap=True) + float(s.get("box_padding_x") or 0)
    except Exception:
        pad = 0.0
    avail = line_max_width_px(ms, canvas_w, em=float(fs), extra_pad=pad)
    if avg <= 0:
        return 1
    n = max(1, int(avail / avg))
    cap = max_words_per_line(s)
    return min(n, cap) if cap else n


def is_single_word_mode(style: dict | None) -> bool:
    s = style if isinstance(style, dict) else {}
    mode = str(s.get("word_mode") or "").strip().lower()
    if mode in ("single", "word", "one"):
        return True
    rules = s.get("layout_rules") if isinstance(s.get("layout_rules"), dict) else {}
    mode = str(rules.get("word_mode") or "").strip().lower()
    return mode in ("single", "word", "one")


def layout_max_chars_cap(style: dict | None, default: int = 42) -> int:
    s = style or {}
    rules = s.get("layout_rules") if isinstance(s.get("layout_rules"), dict) else {}
    raw = rules.get("max_chars_per_line")
    if raw is None:
        raw = s.get("max_chars_per_line")
    try:
        return max(1, int(raw if raw is not None else default))
    except (TypeError, ValueError):
        return default


def avg_char_width_px(
    family: str, fontsize: float, bold: bool = True, italic: bool = False,
) -> float:
    """Mean Latin letter width via QFontMetricsF. Needs a QApplication."""
    from PySide6.QtGui import QFontMetricsF
    from cutora.ui.panels.fonts import get_font_manager, sanitize_font_family
    font = get_font_manager().get_font(
        family or "Arimo",
        max(8, int(round(float(fontsize)))),
        bold=bold, italic=italic)
    advance = float(QFontMetricsF(font).horizontalAdvance(
        "abcdefghijklmnopqrstuvwxyz"))
    return advance / 26.0


def effective_max_chars(
    style: dict | None, canvas_w: int, cap: int | None = None,
    margin: float = SAFE_MARGIN, canvas_h: int | None = None,
) -> int:
    h = int(canvas_h) if canvas_h else int(canvas_w)
    s = resolve_style_geometry(style, canvas_w, h)
    fs = float(s.get("fontsize") or 34)
    family = str(s.get("font_family") or "Arimo")
    avg = avg_char_width_px(
        family, fs, bool(s.get("bold", True)), bool(s.get("italic", False)))
    if cap is None:
        cap = layout_max_chars_cap(s)
    usable = line_max_width_px(s, canvas_w, em=fs)
    cap_i = max(1, int(cap))
    if avg <= 0:
        return cap_i
    return max(1, min(cap_i, int(usable // float(avg))))


def wrap_for_canvas(
    text: str, style: dict | None, canvas_w: int, canvas_h: int,
    lang: str | None = None,
) -> list[str]:
    """Measured-width wrap. ``max_lines`` is a hard cap (T04f-1d-3)."""
    from cutora.subtitle.layout import LayoutRules, break_lines
    cap = layout_max_chars_cap(style)
    try:
        n = effective_max_chars(style, canvas_w, cap, canvas_h=canvas_h)
    except Exception:
        n = cap
    rules = (style or {}).get("layout_rules") if isinstance(
        (style or {}).get("layout_rules"), dict) else {}
    lang = lang or rules.get("lang") or "en"
    max_l = effective_max_lines(style)
    wrapped = break_lines(
        {"text": text, "lang": lang},
        LayoutRules(
            max_chars_per_line=n,
            max_lines=max_l,
            balance_lines=True,
            lang=lang,
            line_break=str(rules.get("line_break") or "space"),
        ),
    )
    if wrapped and len(wrapped) > max_l:
        return wrapped[:max_l]
    return wrapped


def _relayout_with_measured_cap(
    task, style, canvas_w, rules, canvas_h=None,
) -> None:
    segs = task.get("segments") if isinstance(task, dict) else None
    if not segs or not isinstance(rules, dict):
        return
    try:
        from PySide6.QtWidgets import QApplication
        if QApplication.instance() is None:
            return
        cap = layout_max_chars_cap(
            {"layout_rules": rules, **(style or {})})
        measured = effective_max_chars(
            style, canvas_w, cap, canvas_h=canvas_h)
        from cutora.subtitle.layout import LayoutRules, relayout_task
        relayout_task(task, LayoutRules(
            max_chars_per_line=measured,
            max_lines=effective_max_lines(style, task),
            balance_lines=bool(rules.get("balance_lines", True)),
            lang=rules.get("lang") or "en",
            line_break=str(rules.get("line_break") or "space"),
        ))
    except Exception:
        return


def gallery_hover_t(
    elapsed: float, in_s: float, hold_s: float = GALLERY_HOVER_HOLD_S,
) -> float:
    """Loop t for anim.in: 0 → in_s → hold *hold_s* → repeat."""
    if in_s <= 0.0:
        return 0.4
    cycle = float(in_s) + float(hold_s)
    u = float(elapsed) % cycle
    return u if u < in_s else float(in_s)

ANIM_KEYS = frozenset({
    "anim_in_type", "anim_in_duration", "anim_in_easing",
    "anim_out_type", "anim_out_duration", "anim_out_easing",
    "fade_in_out_enabled", "fade_in_out_duration",
    "tw_revealed_color", "tw_revealed_stroke_color",
    "tw_revealed_opacity", "tw_revealed_stroke_opacity",
    "tw_unrevealed_color", "tw_unrevealed_stroke_color",
    "tw_unrevealed_opacity",
    "char_bounce_height", "char_bounce_stagger",
    "holy_glow_radius", "holy_glow_color", "holy_glow_period",
})

GEOM_KEYS = (
    "pos_x", "pos_y", "fontsize", "fontsize_pct",
    "anchor", "offset_x_pct", "offset_y_pct",
    "pos_x_pct", "pos_y_pct",
)

DEFAULT_LAYOUT_RULES = {
    "max_chars_per_line": 32,
    "max_lines": 2,
    "balance_lines": True,
    "line_break": "space",
}


def default_fx() -> dict:
    """Documented FxSpec defaults in pack JSON shape (anim uses in/out)."""
    return {
        "fill": {"type": "solid", "colors": ["#F4EBD8"]},
        "stroke": {"width_em": 0, "color": "#141A26", "join": "round"},
        "glow": {
            "sigma_em": 0, "spread_em": 0.02, "color": "#22D3EE",
            "strength": 0.8, "mode": "soft",
        },
        "shadow": {
            "sigma_em": 0, "dx_em": 0.04, "dy_em": 0.06,
            "color": "#141A26", "alpha": 0.6,
        },
        "extrude": {"depth_em": 0, "angle": 135, "color": ""},
        "bg": {
            "type": "none", "color": "#0B1A33", "alpha": 0.55,
            "radius_em": 0.4, "pad_em": [0.5, 0.25],
            "texture": "paper", "texture_tint": "#F1E9D8", "align": "center",
            "grad_type": "none", "grad_colors": [], "grad_highlight": False,
        },
        "fonts": {"body": "", "emphasis": ""},
        "emphasis": {
            "auto": [], "words": [], "color": "#22D3EE", "palette": [],
            "scale": 1.25, "font": "emphasis", "anim": "none", "max_per_cue": 2,
        },
        "anim": {
            "in": "none", "loop": "none", "out": "none", "unit": "segment",
            "stagger_s": 0.05, "in_s": 0.35, "out_s": 0.25,
            "easing": "ease_out", "seed": 0,
        },
        "scatter": {
            "enabled": False, "size_jitter": 0.35,
            "pos_jitter_em": [1.5, 0.8], "rot_jitter_deg": 6.0,
        },
        "tracking_em": 0.0,
        "letter_case": "none",
    }


DEFAULT_PACK_ID = "basic-white-red"


def bind_default_paint_style(task: dict) -> dict:
    """Default role has no private style: task.style is the only object."""
    if not isinstance(task, dict):
        return {}
    base = task.get("style") if isinstance(task.get("style"), dict) else {}
    task["style"] = base
    roles = task.get("subtitle_roles")
    if not isinstance(roles, dict):
        roles = {}
        task["subtitle_roles"] = roles
    info = roles.get("default")
    if not isinstance(info, dict):
        info = {}
        roles["default"] = info
    info["style"] = base
    return base


def resolve_paint_style(task_data: dict, role_id: str | None) -> dict:
    """Style object the preview, export, and subtitle panel all share.

    ``default`` is always ``task.style`` (same id the panel writes).
    Other roles keep their own ``role.style``.
    """
    bind_default_paint_style(task_data)
    base = task_data.get("style") if isinstance(task_data.get("style"), dict) else {}
    rid = str(role_id or "default")
    if rid == "default":
        return base
    roles = task_data.get("subtitle_roles") or {}
    info = roles.get(rid, {}) if isinstance(roles, dict) else {}
    role_style = info.get("style") if isinstance(info, dict) else None
    if isinstance(role_style, dict) and role_style:
        return role_style
    return base


V1_BG_UNMAPPED = ("box_blur", "box_transition", "box_transition_color_to")


def v1_bg_unmapped_fields(style: dict | None) -> list[str]:
    """v1 box keys with no fx.bg equivalent. Not dropped; listed so they
    are not silently lost. box_blur is not glass_sigma_em."""
    s = style if isinstance(style, dict) else {}
    out = []
    for key in V1_BG_UNMAPPED:
        val = s.get(key)
        if val in (None, "", 0, 0.0, False):
            continue
        if key == "box_transition_color_to" and not s.get("box_transition"):
            continue
        out.append(key)
    return out


def migrate_v1_bg_to_fx(style: dict) -> dict:
    """Move v1 box / bg_gradient_* onto fx.bg.

    Existing PATH fx (including ``bg.type=none``) is left alone — none means
    no box, not "unset". Only v1-only styles (no fx dict) get a new fx.bg.
    """
    if not isinstance(style, dict):
        return style
    from cutora.render.textfx.spec import is_pure_black, parse_color
    fx = style.get("fx")
    had_fx = isinstance(fx, dict)
    if had_fx:
        # Existing PATH fx: never touch fx.bg, including type=none / missing type.
        return style
    fx = default_fx()
    style["fx"] = fx
    fill_c = parse_color(style.get("color_text") or "", "#F4EBD8")
    if is_pure_black(fill_c):
        fill_c = "#F4EBD8"
    fx["fill"] = {"type": "solid", "colors": [fill_c]}
    fam = str(style.get("font_family") or "")
    if fam:
        fx.setdefault("fonts", {})["body"] = fam
    if style.get("use_stroke"):
        em = max(8.0, float(style.get("fontsize") or 60))
        try:
            w = float(style.get("border_width") or 0) / em
        except (TypeError, ValueError):
            w = 0.0
        sc = parse_color(style.get("color_outline") or "", "#141A26")
        if is_pure_black(sc):
            sc = "#141A26"
        fx["stroke"] = {"width_em": max(0.0, w), "color": sc, "join": "round"}
    bg = fx.get("bg")
    if not isinstance(bg, dict):
        bg = {}
        fx["bg"] = bg
    already = str(bg.get("type") or "none")
    unmapped = v1_bg_unmapped_fields(style)
    if unmapped:
        logger.debug("v1 bg unmapped (kept on style, not painted): %s", ",".join(unmapped))
    if already != "none":
        return style
    if not style.get("use_box"):
        if style.get("bg_gradient_enabled"):
            bg["type"] = "box"
        else:
            return style
    else:
        bg["type"] = "box"
    color = parse_color(style.get("color_bg") or bg.get("color") or "#e0c3fc", "#e0c3fc")
    if is_pure_black(color):
        color = "#e0c3fc"
    bg["color"] = color
    try:
        bg["alpha"] = max(0.0, min(1.0, float(style.get("opacity_bg") or 191) / 255.0))
    except (TypeError, ValueError):
        bg.setdefault("alpha", 0.55)
    em = max(8.0, float(style.get("fontsize") or 60))
    try:
        bg["radius_em"] = max(0.0, float(style.get("box_radius") or 0) / em)
    except (TypeError, ValueError):
        pass
    try:
        px = float(style.get("box_padding_x") or 0) / em
        py = float(style.get("box_padding_y") or 0) / em
        bg["pad_em"] = [px, py]
    except (TypeError, ValueError):
        pass
    if style.get("bg_gradient_enabled"):
        raw = str(style.get("bg_gradient_colors") or "")
        cols = [parse_color(c.strip(), color) for c in raw.split(",") if c.strip()]
        cols = [c for c in cols if not is_pure_black(c)]
        if len(cols) >= 2:
            bg["grad_type"] = str(style.get("bg_gradient_type") or "linear_h")
            bg["grad_colors"] = cols[:3]
            bg["grad_highlight"] = bool(style.get("bg_gradient_highlight"))
    return style


def ensure_default_pack(task: dict) -> None:
    """New / empty-style tasks get basic-white-red (or user default pack). Never overwrite an existing fx."""
    if not isinstance(task, dict):
        return
    if task.get("stylepack_id"):
        return
    style = task.get("style") if isinstance(task.get("style"), dict) else {}
    if isinstance(style.get("fx"), dict):
        return
    pack = default_pack()
    if not pack:
        for path in list_pack_dirs():
            if os.path.basename(path.rstrip("\\/")) == DEFAULT_PACK_ID:
                pack = load_pack(path)
                break
    if not pack:
        migrate_v1_bg_to_fx(style)
        if "fx" not in style:
            style["fx"] = default_fx()
        task["style"] = style
        return
    cw, ch = 1080, 1920
    dims = task.get("dims") or ()
    if isinstance(dims, (list, tuple)) and len(dims) >= 2:
        try:
            cw, ch = int(dims[0]), int(dims[1])
        except (TypeError, ValueError):
            pass
    apply_pack_to_task(task, pack)
    bind_default_paint_style(task)


def default_subtitle_style() -> dict:
    """Product defaults for apply_pack_to_task rebuild. No leftover keys."""
    out = {
        "pos_x": 540, "pos_y": 1450, "fontsize": 60,
        "font_family": "Arimo",
        "bold": True, "italic": False,
        "color_text": "#F4EBD8", "color_high": "#FF0000", "color_bg": "#e0c3fc",
        "color_shadow": "#141A26",
        "opacity_bg": 191, "opacity_shadow": 0, "shadow_enabled": False,
        "use_box": True, "box_padding_x": 20, "box_padding_y": 20,
        "box_radius": 20,
        "dynamic_box": False, "rotation": 0,
        "wrap_width_percent": DEFAULT_WRAP_WIDTH_PERCENT,
        "wrap_lines": 2, "line_spacing": 0,
        "anim_in_type": "none", "anim_in_duration": 0.3, "anim_in_easing": "ease_out",
        "anim_out_type": "none", "anim_out_duration": 0.3, "anim_out_easing": "ease_out",
        "fade_in_out_enabled": False, "fade_in_out_duration": 0.3,
        "tw_revealed_color": "#F4EBD8", "tw_revealed_stroke_color": "#141A26",
        "tw_revealed_opacity": 255, "tw_revealed_stroke_opacity": 255,
        "tw_unrevealed_color": "#808080", "tw_unrevealed_stroke_color": "#404040",
        "tw_unrevealed_opacity": 80,
        "char_bounce_height": 20, "char_bounce_stagger": 0.05,
        "dyn_box_anim": False, "dyn_box_anim_overshoot": 1.3,
        "dyn_box_anim_duration": 0.15,
        "holy_glow_radius": 6, "holy_glow_color": "#FFFFCC", "holy_glow_period": 3.0,
        "geom_user_set": False,
        "font_style": "", "opacity_text_global": 255,
        "color_high_bg": "#FF0000", "opacity_high_bg": 180, "dynamic_radius": 5,
        "shadow_blur": 0, "use_underline": False, "color_underline": "#141A26",
        "high_padding": 5, "high_offset_y": 0, "bg_padding": 20,
        "dyn_box_style": 0, "dyn_box_stroke_width": 4, "dyn_box_glow_radius": 0,
        "dyn_box_font_enabled": False, "dyn_box_font_family": "", "dyn_box_font_size": 60,
        "floating_amplitude": 8, "floating_period": 2.0, "bullet_stagger": 0.15,
        "metronome_bpm": 120, "flash_color": "#FFFF00", "flash_duration": 0.1,
        "letter_jump_scale": 1.5, "letter_jump_duration": 0.2,
        "blur_sharp_max": 20, "blur_sharp_clear_frac": 0.4,
        "stroke_expand_enabled": False, "stroke_expand_layers": 3,
        "stroke_expand_step": 4, "stroke_expand_feather": 8,
        "stroke_expand_colors": "#FF0000,#00FF00,#0000FF",
        "stroke_expand_layer_widths": [], "stroke_expand_layer_feathers": [],
        "bg_gradient_enabled": False, "bg_gradient_type": "linear_h",
        "bg_gradient_colors": "#e0c3fc,#8ec5fc", "bg_gradient_highlight": False,

        "metro_read_color": "#FFFFFF", "metro_read_stroke_color": "#141A26",
        "metro_read_stroke_width": 2, "metro_read_opacity": 255,
        "metro_unread_color": "#808080", "metro_unread_stroke_color": "#404040",
        "metro_unread_stroke_width": 1, "metro_unread_opacity": 100,
        "advanced_textbox_enabled": False, "advanced_textbox_x": 0,
        "advanced_textbox_y": 0, "advanced_textbox_w": 0, "advanced_textbox_h": 0,
        "advanced_textbox_align": "center",
        "adv_bg_enabled": False, "adv_bg_offset_x": 0, "adv_bg_offset_y": 0,
        "adv_bg_w": 1080, "adv_bg_h": 280, "adv_bg_radius": 20,
        "adv_bg_color": "#e0c3fc", "adv_bg_opacity": 191,
        "adv_bg_gradient_enabled": False, "adv_bg_gradient_type": "linear_h",
        "adv_bg_gradient_colors": "#e0c3fc,#8ec5fc", "adv_bg_gradient_highlight": False,
        "adv_line_spacing": 0, "adv_text_align": "center",
        "adv_stroke_enabled": False, "adv_stroke_width": 2,
        "adv_stroke_color": "#141A26", "adv_stroke_opacity": 255,
        "spectrum_enabled": False, "spectrum_bands": 32, "spectrum_bar_width": 4,
        "spectrum_bar_gap": 2, "spectrum_max_height": 80,
        "spectrum_color_bottom": "#00AAFF", "spectrum_color_top": "#FF4444",
        "spectrum_position": "below", "spectrum_shape": "bars",
        "spectrum_dual": False, "spectrum_top_shape": "bars",
        "spectrum_opacity": 200, "spectrum_roundness": 2,
        "adv_bar_enabled": False, "adv_bar_opacity": 180, "adv_bar_padding": 8,
        "adv_bar_offset_y": 0, "adv_bar_radius": 8, "adv_bar_glow": 0,
        "adv_bar_style": "fill", "adv_bar_border_width": 2, "adv_bar_color": "#FFFFFF",
        "lyrics_scroll_enabled": False, "lyrics_scroll_damping": 0.8,
        "lyrics_scroll_transition": 0.4, "lyrics_center_scale": 1.15,
        "lyrics_edge_feather": 40, "lyrics_alpha_decay": 30,
        "lyrics_line_gap": 20, "lyrics_center_offset": 0, "lyrics_center_color": "",
        "shadow_opacity": 0, "shadow_offset_x": 2, "shadow_offset_y": 2,
        "shadow_color": "#141A26", "shadow_spread": 0,
        "keyword_rules": [], "word_mode": "",
    }
    from cutora.subtitle.style_schema import DEPRECATED_UNPAINTED_KEYS
    for dead in DEPRECATED_UNPAINTED_KEYS:
        out.pop(dead, None)
    return out


def _merge_fx_onto_defaults(pack_fx: dict) -> dict:
    base = default_fx()
    for key, val in (pack_fx or {}).items():
        if isinstance(val, dict) and isinstance(base.get(key), dict):
            slot = dict(base[key])
            slot.update(val)
            base[key] = slot
        else:
            base[key] = copy.deepcopy(val)
    return base


def _snapshot_geom(style: dict) -> dict:
    return {k: copy.deepcopy(style[k]) for k in GEOM_KEYS if k in style}


class StylepackError(ValueError):
    """Pack could not be read, written, or applied."""


def builtin_dir() -> str:
    return os.path.join(RESOURCES, "stylepacks")


def user_dir() -> str:
    path = os.path.join(user_presets_dir(), "stylepacks")
    os.makedirs(path, exist_ok=True)
    return path


def _is_pack_dir(path: str) -> bool:
    return os.path.isfile(os.path.join(path, PACK_STYLE)) and os.path.isfile(
        os.path.join(path, PACK_META))


def list_pack_dirs() -> list[str]:
    ensure_subtitle_presets_migrated()
    found = []
    for root in (builtin_dir(), user_dir()):
        if not os.path.isdir(root):
            continue
        for name in sorted(os.listdir(root)):
            path = os.path.join(root, name)
            if os.path.isdir(path) and _is_pack_dir(path):
                found.append(path)
    return found


def load_pack(path: str) -> dict:
    if not _is_pack_dir(path):
        raise StylepackError("不是风格包目录: %s" % path)
    with open(os.path.join(path, PACK_META), encoding="utf-8") as fh:
        meta = json.load(fh)
    with open(os.path.join(path, PACK_STYLE), encoding="utf-8") as fh:
        body = json.load(fh)
    if not isinstance(meta, dict) or not isinstance(body, dict):
        raise StylepackError("风格包 JSON 损坏")
    preview = os.path.join(path, PACK_PREVIEW)
    thumb = os.path.join(path, PACK_THUMB)
    return {
        "id": os.path.basename(path.rstrip("\\/")),
        "path": os.path.abspath(path),
        "builtin": _is_under(path, builtin_dir()),
        "meta": meta,
        "style": migrate_pack_style(dict(body.get("style") or {})),
        "layout_rules": dict(body.get("layout_rules") or {}),
        "keyword_rules": list(body.get("keyword_rules") or []),
        "animation": dict(body.get("animation") or {}),
        "version": int(body.get("version") or VERSION),
        "preview": preview if os.path.isfile(preview) else "",
        "thumb": thumb if os.path.isfile(thumb) else "",
        "font_missing": False,
    }


def pack_content_hash(path: str) -> str:
    h = hashlib.sha256()
    for name in (PACK_META, PACK_STYLE):
        h.update(name.encode("utf-8"))
        h.update(b"\0")
        fp = os.path.join(path, name)
        if os.path.isfile(fp):
            with open(fp, "rb") as fh:
                h.update(fh.read())
        h.update(b"\0")
    return h.hexdigest()


def render_pack_thumb(path: str, dest: str) -> str:
    """64×64 crop of SAMPLE_EN painted with the pack. Needs QApplication."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    if QApplication.instance() is None:
        raise StylepackError("pack thumb needs QApplication")
    pack = load_pack(path)
    from cutora.ui.panels.style_thumbs import paint_pack_on_frame, neutral_frame
    base = neutral_frame(1080, 1920)
    painted = paint_pack_on_frame(base, pack, SAMPLE_EN, t=0.4)
    scaled = painted.scaled(
        THUMB_SIZE, THUMB_SIZE,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation)
    x = max(0, (scaled.width() - THUMB_SIZE) // 2)
    y = max(0, (scaled.height() - THUMB_SIZE) // 2)
    cropped = scaled.copy(x, y, THUMB_SIZE, THUMB_SIZE)
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    if not cropped.save(dest, "PNG"):
        raise StylepackError("无法写入 thumb.png")
    return dest


def ensure_pack_thumb(path: str, *, force: bool = False) -> str:
    """Render thumb.png when missing or style/meta hash changed."""
    dest = os.path.join(path, PACK_THUMB)
    hash_path = os.path.join(path, PACK_THUMB_HASH)
    digest = pack_content_hash(path)
    if not force and os.path.isfile(dest) and os.path.isfile(hash_path):
        try:
            old = open(hash_path, encoding="utf-8").read().strip()
        except OSError:
            logger.exception("pack thumb hash read failed %s", hash_path)
            old = ""
        if old == digest:
            return dest
    try:
        render_pack_thumb(path, dest)
    except Exception:
        logger.exception("pack thumb render failed %s", path)
        if os.path.isfile(dest):
            return dest
        return ""
    try:
        with open(hash_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(digest + "\n")
    except OSError:
        logger.exception("pack thumb hash write failed %s", hash_path)
    return dest


def _is_under(path: str, root: str) -> bool:
    ap, ar = os.path.abspath(path), os.path.abspath(root)
    return ap == ar or ap.startswith(ar + os.sep)


def sanitize_pack_fonts(pack: dict) -> dict:
    from cutora.ui.panels.fonts import ALLOWED_FONTS, load_embedded_fonts, sanitize_font_family
    try:
        from PySide6.QtWidgets import QApplication
        if QApplication.instance() is None:
            pack = dict(pack)
            pack["font_missing"] = False
            return pack
        if len(ALLOWED_FONTS) <= 4:
            load_embedded_fonts()
    except Exception:
        pack = dict(pack)
        pack["font_missing"] = False
        return pack
    style = dict(pack.get("style") or {})
    wanted = str(style.get("font_family") or "")
    got = sanitize_font_family(wanted)
    missing = bool(wanted) and got != wanted
    style["font_family"] = got
    dyn = str(style.get("dyn_box_font_family") or "")
    if dyn:
        style["dyn_box_font_family"] = sanitize_font_family(dyn)
    pack = dict(pack)
    pack["style"] = style
    pack["font_missing"] = missing
    return pack


def user_has_custom_animation(style: dict | None) -> bool:
    """True if v1 top-level anim or v2 fx.anim differs from 'none'."""
    s = style if isinstance(style, dict) else {}
    cur = str(s.get("anim_in_type") or "none")
    if cur not in ("", "none"):
        return True
    out = str(s.get("anim_out_type") or "none")
    if out not in ("", "none"):
        return True
    if s.get("fade_in_out_enabled") or s.get("dyn_box_anim"):
        return True
    fx = s.get("fx") if isinstance(s.get("fx"), dict) else {}
    anim = fx.get("anim") if isinstance(fx.get("anim"), dict) else {}
    for key in ("in", "loop", "out", "in_name", "out_name"):
        val = str(anim.get(key) or "none")
        if val not in ("", "none"):
            return True
    return False


def _has_relative_geom(style: dict) -> bool:
    return bool(
        style.get("anchor")
        or style.get("pos_x_pct") is not None
        or style.get("pos_y_pct") is not None
        or style.get("fontsize_pct") is not None
    )


def rendered_center_px(style: dict | None, canvas_w: int, canvas_h: int) -> tuple[float, float]:
    """Anchor the painter and the canvas drag both read.

    Relative fields resolve first. Missing ``pos_x`` / ``pos_y`` fall back to
    the horizontal center and 75% down — the same defaults ``layout_event``
    used to inline. Do not substitute 0; that is the top edge.
    """
    w = max(1, int(canvas_w or 1))
    h = max(1, int(canvas_h or 1))
    s = resolve_style_geometry(style, w, h)
    cx = float(s["pos_x"]) if s.get("pos_x") is not None else w / 2.0
    cy = float(s["pos_y"]) if s.get("pos_y") is not None else h * 0.75
    return cx, cy


def resolve_style_geometry(style: dict | None, canvas_w: int, canvas_h: int) -> dict:
    """Pixel pos_x / pos_y / fontsize for this canvas. Relative fields win.

    ``fontsize_pct`` multiplies ``min(canvas_w, canvas_h)`` so a 1080-tall
    landscape matches a 1080-wide portrait. Absolute-only styles are
    returned unchanged (golden / user edits).
    """
    s = dict(style or {})
    if not _has_relative_geom(s):
        return s
    w = max(1, int(canvas_w or REF_W))
    h = max(1, int(canvas_h or REF_H))
    if s.get("anchor") or s.get("pos_x_pct") is not None or s.get("pos_y_pct") is not None:
        if s.get("anchor"):
            ax, ay = _anchor_point(str(s.get("anchor")), w, h)
            s["pos_x"] = ax + float(s.get("offset_x_pct") or 0) * w
            s["pos_y"] = ay + float(s.get("offset_y_pct") or 0) * h
        else:
            s["pos_x"] = float(s.get("pos_x_pct") if s.get("pos_x_pct") is not None else 0.5) * w
            s["pos_y"] = float(s.get("pos_y_pct") if s.get("pos_y_pct") is not None else 0.75) * h
    if s.get("fontsize_pct") is not None:
        # min(w, h): portrait/square 1080-wide stay identical; landscape
        # no longer scales with the long side (T04f-1c).
        s["fontsize"] = max(8, int(round(float(s["fontsize_pct"]) * min(w, h))))
    fs = int(s.get("fontsize") or 34)
    pad_y = int(s.get("box_padding_y") or 20)
    half_h = fs * 1.4 + pad_y
    half_w = max(fs * 2.0, 40)
    cx = float(s.get("pos_x") if s.get("pos_x") is not None else w / 2.0)
    cy = float(s.get("pos_y") if s.get("pos_y") is not None else h * 0.75)
    cx = min(max(cx, half_w), w - half_w)
    cy = min(max(cy, half_h), h - half_h)
    s["pos_x"] = cx
    s["pos_y"] = cy
    s["fontsize"] = fs
    return s


def _canvas_dims(canvas_w: int, canvas_h: int) -> tuple[int, int]:
    return max(1, int(canvas_w or REF_W)), max(1, int(canvas_h or REF_H))


def _clamp_center(px: float, py: float, fs: float, style: dict, w: int, h: int):
    pad_y = int(style.get("box_padding_y") or 20)
    half_h = float(fs) * 1.4 + pad_y
    half_w = max(float(fs) * 2.0, 40.0)
    return (
        min(max(float(px), half_w), w - half_w),
        min(max(float(py), half_h), h - half_h),
    )


def set_position_px(style: dict, px: float, py: float,
                    canvas_w: int, canvas_h: int, *, user: bool = True) -> None:
    """Write a canvas-pixel position using the style's own representation.

    Inverse of ``resolve_style_geometry``: relative styles store
    ``offset_*_pct`` (or ``pos_*_pct``); absolute styles store ``pos_x/pos_y``.
    """
    w, h = _canvas_dims(canvas_w, canvas_h)
    fs = int(style.get("fontsize") or 34)
    if style.get("fontsize_pct") is not None:
        fs = max(8, int(round(float(style["fontsize_pct"]) * min(w, h))))
    px, py = _clamp_center(px, py, fs, style, w, h)
    if style.get("anchor"):
        ax, ay = _anchor_point(str(style.get("anchor")), w, h)
        style["offset_x_pct"] = (px - ax) / float(w)
        style["offset_y_pct"] = (py - ay) / float(h)
    elif style.get("pos_x_pct") is not None or style.get("pos_y_pct") is not None:
        style["pos_x_pct"] = px / float(w)
        style["pos_y_pct"] = py / float(h)
    style["pos_x"] = px
    style["pos_y"] = py
    if user:
        style["geom_user_set"] = True


def set_fontsize_px(style: dict, fs: float, canvas_w: int, canvas_h: int,
                    *, user: bool = True) -> None:
    """Write a pixel fontsize; relative styles store ``fontsize_pct``."""
    w, h = _canvas_dims(canvas_w, canvas_h)
    fs = max(8.0, float(fs))
    if style.get("fontsize_pct") is not None:
        style["fontsize_pct"] = fs / float(min(w, h))
    style["fontsize"] = max(8, int(round(fs)))
    if user:
        style["geom_user_set"] = True


def _anchor_point(anchor: str, w: int, h: int) -> tuple[float, float]:
    table = {
        "top-left": (0.0, 0.0),
        "top-center": (w / 2.0, 0.0),
        "top-right": (float(w), 0.0),
        "center-left": (0.0, h / 2.0),
        "center": (w / 2.0, h / 2.0),
        "center-right": (float(w), h / 2.0),
        "bottom-left": (0.0, float(h)),
        "bottom-center": (w / 2.0, float(h)),
        "bottom-right": (float(w), float(h)),
    }
    return table.get(anchor, (w / 2.0, float(h)))


def migrate_pack_style(style: dict | None, canvas_w: int | None = None, canvas_h: int | None = None) -> dict:
    """Turn absolute geometry into anchor + pct. Default ref is 1080×1920."""
    out = dict(style or {})
    ref_w = max(1, int(canvas_w or REF_W))
    ref_h = max(1, int(canvas_h or REF_H))
    has_rel = (
        out.get("anchor")
        or out.get("pos_x_pct") is not None
        or out.get("pos_y_pct") is not None
    )
    if not has_rel:
        px = float(out.get("pos_x") if out.get("pos_x") is not None else ref_w / 2)
        py = float(out.get("pos_y") if out.get("pos_y") is not None else ref_h * 0.75)
        out["anchor"] = "bottom-center"
        out["offset_x_pct"] = (px - ref_w / 2.0) / float(ref_w)
        out["offset_y_pct"] = (py - ref_h) / float(ref_h)
    if out.get("fontsize_pct") is None:
        fs = float(out.get("fontsize") or 34)
        out["fontsize_pct"] = fs / float(min(ref_w, ref_h))
    from cutora.subtitle.style_schema import DEPRECATED_UNPAINTED_KEYS
    for dead in DEPRECATED_UNPAINTED_KEYS:
        out.pop(dead, None)
    return out


def subtitle_bbox(style: dict | None, canvas_w: int, canvas_h: int, lines=None):
    """Axis-aligned box around the subtitle (center = pos). Product helper."""
    s = resolve_style_geometry(style, canvas_w, canvas_h)
    cx = float(s["pos_x"])
    cy = float(s["pos_y"])
    fs = int(s["fontsize"])
    pad_x = int(s.get("box_padding_x") or 20)
    pad_y = int(s.get("box_padding_y") or 20)
    rows = list(lines or ["Hello friends."])
    nline = max(1, len(rows))
    max_chars = max((len(str(r)) for r in rows), default=12)
    text_w = max(fs * 0.55 * max_chars, fs * 2)
    text_h = nline * fs * 1.25
    bw = text_w + pad_x * 2
    bh = text_h + pad_y * 2
    if isinstance(s.get("fx"), dict):
        from cutora.render.textfx.spec import expansion_px, from_style
        extra = expansion_px(from_style(s), fs)
        bw += extra * 2.0
        bh += extra * 2.0
    x = cx - bw / 2.0
    y = cy - bh / 2.0
    return (x, y, bw, bh)


def apply_pack_to_task(task: dict, pack: dict, *, apply_animation: bool = True) -> dict:
    """Rebuild style from product defaults, then overlay the pack.

    Previous-pack leftovers are discarded. Exceptions: ``geom_user_set``
    keeps the user's position/fontsize; ``apply_animation=False`` keeps
    v1 ANIM_KEYS and v2 ``fx.anim`` from the current style.
    """
    pack = sanitize_pack_fonts(pack)
    before = task.get("style")
    from cutora.subtitle.io import canvas_size
    cw, ch = canvas_size(task)
    diag(
        "apply_pack.before",
        task_id=id(task),
        canvas_w=cw,
        canvas_h=ch,
        style=style_snapshot(before, task.get("layout_rules")),
        pack_id=pack.get("id"),
        apply_animation=bool(apply_animation),
    )
    old = dict(before) if isinstance(before, dict) else {}
    user_geom = bool(old.get("geom_user_set"))
    kept_geom = _snapshot_geom(old) if user_geom else {}
    kept_anim = {k: old.get(k) for k in ANIM_KEYS if k in old}
    kept_fx_anim = None
    if isinstance(old.get("fx"), dict) and isinstance(old["fx"].get("anim"), dict):
        kept_fx_anim = copy.deepcopy(old["fx"]["anim"])

    style = default_subtitle_style()
    incoming = migrate_pack_style(dict(pack.get("style") or {}))
    incoming.update(pack.get("animation") or {})
    pack_fx = incoming.pop("fx", None) if isinstance(incoming.get("fx"), dict) else None
    style.update(incoming)
    from cutora.subtitle.style_schema import migrate_subtitle_style
    migrate_subtitle_style(style)
    if pack_fx is not None:
        style["fx"] = _merge_fx_onto_defaults(pack_fx)
    else:
        style.pop("fx", None)

    if not apply_animation:
        for key, val in kept_anim.items():
            if val is not None:
                style[key] = val
        if kept_fx_anim is not None:
            fx = style.get("fx")
            if not isinstance(fx, dict):
                fx = default_fx()
                style["fx"] = fx
            fx["anim"] = kept_fx_anim

    if user_geom:
        for key, val in kept_geom.items():
            style[key] = val
        style["geom_user_set"] = True
    else:
        style["geom_user_set"] = False
    resolved = resolve_style_geometry(style, cw, ch)
    if user_geom:
        style["pos_x"] = kept_geom.get("pos_x", resolved["pos_x"])
        style["pos_y"] = kept_geom.get("pos_y", resolved["pos_y"])
        style["fontsize"] = kept_geom.get("fontsize", resolved["fontsize"])
    else:
        style["pos_x"] = resolved["pos_x"]
        style["pos_y"] = resolved["pos_y"]
        style["fontsize"] = resolved["fontsize"]
    from cutora.subtitle.style_schema import (
        CANONICAL_STYLE_KEYS, DEPRECATED_UNPAINTED_KEYS, PACK_STYLE_EXTRAS,
    )
    keep = set(CANONICAL_STYLE_KEYS) | PACK_STYLE_EXTRAS
    style = {k: v for k, v in style.items() if k in keep}
    for dead in DEPRECATED_UNPAINTED_KEYS:
        style.pop(dead, None)
    task["style"] = style
    task["stylepack_id"] = pack.get("id")
    rules = dict(DEFAULT_LAYOUT_RULES)
    if pack.get("layout_rules"):
        rules.update(dict(pack.get("layout_rules") or {}))
    task["layout_rules"] = rules
    style["layout_rules"] = dict(rules)
    style["keyword_rules"] = list(pack.get("keyword_rules") or [])
    task["style"] = style
    bind_default_paint_style(task)
    _relayout_with_measured_cap(task, style, cw, rules, canvas_h=ch)
    after = task.get("style") or {}
    py = after.get("pos_y")
    diag(
        "apply_pack.after",
        task_id=id(task),
        canvas_w=cw,
        canvas_h=ch,
        pos_y=py,
        pos_y_outside=py is not None and (float(py) < 0 or float(py) > ch),
        same_style_obj=after is before,
        style=style_snapshot(after, task.get("layout_rules")),
    )
    meta = pack.get("meta") or {}
    return {
        "id": pack.get("id"),
        "name": meta.get("name") or pack.get("id"),
        "apply_animation": bool(apply_animation),
        "font_missing": bool(pack.get("font_missing")),
    }


def restore_pack_geometry(task: dict, canvas_w: int, canvas_h: int) -> dict:
    """Clear geom_user_set and re-apply the last pack's position/fontsize."""
    pack_id = str(task.get("stylepack_id") or "")
    style = task.get("style") if isinstance(task.get("style"), dict) else {}
    incoming = {}
    if pack_id:
        for path in list_pack_dirs():
            if os.path.basename(path.rstrip("\\/")) == pack_id:
                pack = load_pack(path)
                incoming = migrate_pack_style(dict(pack.get("style") or {}))
                break
    defaults = default_subtitle_style()
    for key in GEOM_KEYS:
        if key in incoming:
            style[key] = incoming[key]
        elif key in defaults:
            style[key] = defaults[key]
        else:
            style.pop(key, None)
    style["geom_user_set"] = False
    resolved = resolve_style_geometry(style, canvas_w, canvas_h)
    style["pos_x"] = resolved["pos_x"]
    style["pos_y"] = resolved["pos_y"]
    style["fontsize"] = resolved["fontsize"]
    task["style"] = style
    return style


def delete_pack_dir(path: str) -> str:
    path = os.path.abspath(path)
    if _is_under(path, builtin_dir()):
        raise StylepackError("不能删除内置风格包")
    if not _is_pack_dir(path):
        raise StylepackError("不是风格包目录")
    shutil.rmtree(path)
    return path


def rename_pack(path: str, name: str) -> dict:
    path = os.path.abspath(path)
    name = str(name or "").strip()
    if not name:
        raise StylepackError("名称不能为空")
    if _is_under(path, builtin_dir()):
        raise StylepackError("不能重命名内置风格包")
    if not _is_pack_dir(path):
        raise StylepackError("不是风格包目录")
    meta_path = os.path.join(path, PACK_META)
    with open(meta_path, encoding="utf-8") as fh:
        meta = json.load(fh)
    if not isinstance(meta, dict):
        raise StylepackError("风格包 JSON 损坏")
    meta["name"] = name
    with open(meta_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return {"path": path, "name": name}


def save_pack_dir(dest: str, meta: dict, body: dict) -> str:
    os.makedirs(dest, exist_ok=True)
    with open(os.path.join(dest, PACK_META), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    with open(os.path.join(dest, PACK_STYLE), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(body, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return dest


def update_pack_dir(path: str, body: dict, meta_patch: dict | None = None) -> dict:
    """Overwrite a user pack in place. Keeps id/path. Refuses builtin."""
    path = os.path.abspath(path)
    if _is_under(path, builtin_dir()):
        raise StylepackError("不能覆盖内置风格包")
    if not _is_pack_dir(path):
        raise StylepackError("不是风格包目录")
    pack = load_pack(path)
    meta = dict(pack.get("meta") or {})
    if meta_patch:
        for key, val in meta_patch.items():
            if key in ("id",):
                continue
            meta[key] = val
    meta["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    save_pack_dir(path, meta, body)
    try:
        ensure_pack_thumb(path, force=True)
    except Exception:
        logger.exception("pack thumb refresh failed %s", path)
    return {"path": path, "id": pack.get("id"), "updated_at": meta["updated_at"]}


def export_cutorastyle(pack_dir: str, dest_zip: str) -> str:
    if not _is_pack_dir(pack_dir):
        raise StylepackError("不是风格包目录")
    os.makedirs(os.path.dirname(os.path.abspath(dest_zip)) or ".", exist_ok=True)
    with zipfile.ZipFile(dest_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in (PACK_META, PACK_STYLE, PACK_PREVIEW, PACK_THUMB):
            path = os.path.join(pack_dir, name)
            if os.path.isfile(path):
                zf.write(path, name)
    return os.path.abspath(dest_zip)


def _safe_member_path(dest: str, name: str) -> str:
    raw = str(name or "").replace("\\", "/")
    if raw.startswith("/") or raw.startswith("\\"):
        raise StylepackError("拒绝绝对路径")
    if ":" in raw.split("/")[0]:
        raise StylepackError("拒绝绝对路径")
    parts = [p for p in raw.split("/") if p and p != "."]
    if any(p == ".." for p in parts):
        raise StylepackError("拒绝父目录穿越")
    target = os.path.abspath(os.path.join(dest, *parts))
    root = os.path.abspath(dest)
    if target != root and not target.startswith(root + os.sep):
        raise StylepackError("路径穿越")
    ext = os.path.splitext(target)[1].lower()
    if ext not in ALLOWED_ZIP_EXT:
        raise StylepackError("不允许的扩展名: %s" % ext)
    return target


def import_cutorastyle(zip_path: str, dest_dir: str | None = None) -> str:
    if not zip_path or not os.path.isfile(zip_path):
        raise StylepackError("找不到风格包文件")
    dest_dir = dest_dir or user_dir()
    stem = os.path.splitext(os.path.basename(zip_path))[0] or "pack"
    dest = os.path.join(dest_dir, stem)
    n = 1
    while os.path.exists(dest):
        dest = os.path.join(dest_dir, "%s-%s" % (stem, n))
        n += 1
    os.makedirs(dest, exist_ok=True)
    total = 0
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            for info in zf.infolist():
                name = info.filename.replace("\\", "/")
                if name.endswith("/"):
                    continue
                if any(part == ".." for part in name.split("/")):
                    raise StylepackError("拒绝父目录穿越")
                if name.startswith("/") or name.startswith("\\"):
                    raise StylepackError("拒绝绝对路径")
                target = _safe_member_path(dest, os.path.basename(name))
                size = int(info.file_size or 0)
                if size > MAX_MEMBER:
                    raise StylepackError("单文件过大")
                total += size
                if total > MAX_TOTAL:
                    raise StylepackError("风格包过大")
                data = zf.read(info)
                with open(target, "wb") as fh:
                    fh.write(data)
        if not _is_pack_dir(dest):
            raise StylepackError("压缩包里没有 style.json / meta.json")
    except StylepackError:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    except zipfile.BadZipFile as exc:
        shutil.rmtree(dest, ignore_errors=True)
        raise StylepackError("不是有效的 zip") from exc
    return dest


APPLY_ANIMATION_KEY = "stylepack.apply_animation"
LEGACY_PRESET_JSON = "subtitle_style_presets.json"
LEGACY_PRESET_MIGRATED = "subtitle_style_presets.json.migrated"
MIGRATION_MARKER = "subtitle_style_presets.migrated.marker"
DELETE_BOTH_SIDES_TEXT = "删除这个风格包？风格列表和预设下拉都会移除。"
BUILTIN_KEEP_TEXT = "内置风格包不能删除，只能另存为成用户包。"
_migrating_presets = False


def saved_apply_animation() -> bool:
    """Saved gallery switch. Missing key means on, same as the style page."""
    from cutora.core.history import _load_app_settings
    raw = (_load_app_settings() or {}).get(APPLY_ANIMATION_KEY)
    if raw is None:
        return True
    return bool(raw)


def initial_apply_animation(style: dict | None) -> bool:
    """Checkbox state the style page loads: saved pref, forced off if the
    current style already has its own animation."""
    if user_has_custom_animation(style):
        return False
    return saved_apply_animation()


def apply_animation_for(window, task: dict | None = None) -> bool:
    """Live switch if the style page is open, else the same initial rule."""
    gal = getattr(window, "style_gallery", None) if window is not None else None
    chk = getattr(gal, "chk_anim", None) if gal is not None else None
    if chk is not None:
        return bool(chk.isChecked())
    if task is None and window is not None:
        tasks = getattr(window, "tasks", None) or []
        try:
            idx = int(getattr(window, "curr_idx", -1))
        except (TypeError, ValueError):
            idx = -1
        if 0 <= idx < len(tasks) and isinstance(tasks[idx], dict):
            task = tasks[idx]
    style = (task or {}).get("style") if isinstance(task, dict) else None
    return initial_apply_animation(style)


def _slug(name: str) -> str:
    slug = "".join(ch if ch.isalnum() else "-" for ch in str(name or "")).strip("-")
    return slug or "pack"


def _iter_user_pack_paths():
    root = user_dir()
    if not os.path.isdir(root):
        return
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name)
        if os.path.isdir(path) and _is_pack_dir(path):
            yield path


def find_user_pack_by_name(name: str) -> dict | None:
    want = str(name or "").strip()
    if not want:
        return None
    for path in _iter_user_pack_paths():
        try:
            pack = load_pack(path)
        except StylepackError:
            continue
        if str((pack.get("meta") or {}).get("name") or "") == want:
            return pack
    return None


def find_pack_by_name(name: str) -> dict | None:
    """Display name, user pack first. Id is a fallback for API callers."""
    want = str(name or "").strip()
    if not want:
        return None
    user_hit = None
    builtin_hit = None
    id_hit = None
    for path in list_pack_dirs():
        try:
            pack = load_pack(path)
        except StylepackError:
            continue
        meta_name = str((pack.get("meta") or {}).get("name") or "")
        if meta_name == want:
            if pack.get("builtin"):
                builtin_hit = builtin_hit or pack
            else:
                user_hit = user_hit or pack
        elif str(pack.get("id") or "") == want:
            id_hit = id_hit or pack
    return user_hit or builtin_hit or id_hit


def pack_body_from_style(style: dict | None, canvas_w: int | None = None,
                         canvas_h: int | None = None,
                         layout_rules: dict | None = None) -> dict:
    style = dict(style or {})
    # Old presets store paint on v1 keys. Packs keep that paint on fx.
    if not isinstance(style.get("fx"), dict):
        style = migrate_v1_bg_to_fx(style)
    style = migrate_pack_style(style, canvas_w, canvas_h)
    rules = layout_rules if isinstance(layout_rules, dict) else {}
    if not rules and isinstance(style.get("layout_rules"), dict):
        rules = dict(style.get("layout_rules") or {})
    try:
        max_lines = int(rules.get("max_lines") or style.get("wrap_lines") or 2)
    except (TypeError, ValueError):
        max_lines = 2
    try:
        max_chars = int(rules.get("max_chars_per_line") or 42)
    except (TypeError, ValueError):
        max_chars = 42
    layout = {
        "max_chars_per_line": max(1, max_chars),
        "max_lines": max(1, max_lines),
        "balance_lines": bool(rules.get("balance_lines", True)),
    }
    if rules.get("lang"):
        layout["lang"] = rules.get("lang")
    if rules.get("line_break"):
        layout["line_break"] = rules.get("line_break")
    return {
        "version": VERSION,
        "style": style,
        "layout_rules": layout,
        "keyword_rules": list(style.get("keyword_rules") or []),
        "animation": {k: style.get(k) for k in ANIM_KEYS if k in style},
    }


def _alloc_user_pack_dir(name: str) -> str:
    root = user_dir()
    base = _slug(name)
    dest = os.path.join(root, base)
    n = 2
    while os.path.exists(dest):
        dest = os.path.join(root, "%s-%d" % (base, n))
        n += 1
    return dest


def _tag_list(tags) -> list[str]:
    if isinstance(tags, str):
        tag_list = [t.strip() for t in tags.replace("，", ",").split(",") if t.strip()]
    elif isinstance(tags, list):
        tag_list = [str(t).strip() for t in tags if str(t).strip()]
    else:
        tag_list = ["用户"]
    if "用户" not in tag_list:
        tag_list.append("用户")
    return tag_list


def save_task_as_pack(task: dict, name: str, tags=None) -> dict:
    """One user pack for this display name. Same name updates that pack."""
    from cutora.subtitle.io import canvas_size
    name = str(name or "").strip()
    if not name:
        raise StylepackError("名称不能为空")
    if not isinstance(task, dict):
        raise StylepackError("没有当前视频")
    cw, ch = canvas_size(task)
    rules = task.get("layout_rules") if isinstance(task.get("layout_rules"), dict) else None
    body = pack_body_from_style(task.get("style") or {}, cw, ch, rules)
    existing = find_user_pack_by_name(name)
    if existing:
        updated = update_pack_dir(existing["path"], body)
        return {"path": os.path.abspath(updated["path"]), "name": name, "updated": True}
    dest = _alloc_user_pack_dir(name)
    meta = {
        "name": name,
        "tags": _tag_list(tags),
        "author": "CUTORA",
        "license": "OFL-1.1",
        "aspect": ["9:16", "16:9"],
    }
    save_pack_dir(dest, meta, body)
    return {"path": os.path.abspath(dest), "name": name, "updated": False}


def list_style_entries() -> list[dict]:
    """Builtin and user packs, one row each. One effective default."""
    packs = []
    for path in list_pack_dirs():
        try:
            packs.append(load_pack(path))
        except StylepackError:
            continue
    eff = None
    for pack in packs:
        if (pack.get("meta") or {}).get("default") and not pack.get("builtin"):
            eff = pack
            break
    if eff is None:
        for pack in packs:
            if (pack.get("meta") or {}).get("default"):
                eff = pack
                break
    eff_path = (eff or {}).get("path")
    rows = []
    for pack in packs:
        meta = pack.get("meta") or {}
        rows.append({
            "name": str(meta.get("name") or pack.get("id") or ""),
            "path": pack.get("path"),
            "id": pack.get("id"),
            "builtin": bool(pack.get("builtin")),
            "default": pack.get("path") == eff_path and bool(eff_path),
        })
    return rows


def set_pack_default(path: str) -> dict:
    """Mark one user pack. Clears the flag on every other user pack."""
    path = os.path.abspath(path)
    if _is_under(path, builtin_dir()):
        raise StylepackError("内置风格包不能标为默认，请先另存为")
    if not _is_pack_dir(path):
        raise StylepackError("不是风格包目录")
    for other in list(_iter_user_pack_paths()):
        meta_path = os.path.join(other, PACK_META)
        with open(meta_path, encoding="utf-8") as fh:
            meta = json.load(fh)
        if not isinstance(meta, dict):
            continue
        want = os.path.abspath(other) == path
        if want:
            if meta.get("default") is True:
                continue
            meta["default"] = True
        elif meta.get("default"):
            meta.pop("default", None)
        else:
            continue
        with open(meta_path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(meta, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
    pack = load_pack(path)
    return {
        "path": path,
        "name": str((pack.get("meta") or {}).get("name") or ""),
        "default": True,
    }


def default_pack() -> dict | None:
    for row in list_style_entries():
        if row.get("default"):
            try:
                return load_pack(row["path"])
            except StylepackError:
                return None
    return None


def default_pack_style() -> dict | None:
    """Style a new video should start from, when a pack is marked default."""
    pack = default_pack()
    if not pack:
        return None
    task = {"style": {}, "dims": (REF_W, REF_H), "segments": []}
    apply_pack_to_task(task, pack, apply_animation=True)
    style = task.get("style")
    return dict(style) if isinstance(style, dict) else None


def _legacy_preset_paths() -> tuple[str, str, str]:
    from cutora.core.paths import user_presets_dir
    root = user_presets_dir()
    os.makedirs(root, exist_ok=True)
    return (
        os.path.join(root, LEGACY_PRESET_JSON),
        os.path.join(root, LEGACY_PRESET_MIGRATED),
        os.path.join(root, MIGRATION_MARKER),
    )


def _write_migrated_pack(name: str, style: dict, *, is_default: bool) -> str | None:
    existing = find_user_pack_by_name(name)
    if existing:
        if is_default and not (existing.get("meta") or {}).get("default"):
            set_pack_default(existing["path"])
        return existing.get("path")
    dest = _alloc_user_pack_dir(name)
    meta = {
        "name": name,
        "tags": ["预设", "用户"],
        "author": "CUTORA",
        "license": "OFL-1.1",
        "aspect": ["9:16", "16:9"],
        "default": bool(is_default),
    }
    if not is_default:
        meta.pop("default", None)
    body = pack_body_from_style(style if isinstance(style, dict) else {})
    return save_pack_dir(dest, meta, body)


def ensure_subtitle_presets_migrated() -> bool:
    """Turn subtitle_style_presets.json into user packs. Once, then never read it.

    The renamed ``.migrated`` file is only for a person to open by hand.
    """
    global _migrating_presets
    if _migrating_presets:
        return False
    src, renamed, marker = _legacy_preset_paths()
    if os.path.isfile(marker):
        return False
    _migrating_presets = True
    try:
        if os.path.isfile(src):
            try:
                with open(src, encoding="utf-8") as fh:
                    data = json.load(fh)
            except (OSError, json.JSONDecodeError):
                logger.exception("subtitle preset migration could not read %s", src)
                data = None
            if isinstance(data, dict):
                presets = data.get("presets") if isinstance(data.get("presets"), dict) else {}
                default_name = str(data.get("default_preset_name") or "")
                for name, style in presets.items():
                    label = str(name or "").strip()
                    if not label or not isinstance(style, dict):
                        continue
                    _write_migrated_pack(
                        label, style, is_default=(label == default_name and bool(default_name)))
            dest = renamed
            n = 1
            while os.path.exists(dest):
                dest = renamed + ".%d" % n
                n += 1
            try:
                os.replace(src, dest)
            except OSError:
                logger.exception("subtitle preset migration could not rename %s", src)
                return False
        try:
            with open(marker, "w", encoding="utf-8", newline="\n") as fh:
                fh.write("ok\n")
        except OSError:
            logger.exception("subtitle preset migration marker failed %s", marker)
            return False
        return True
    finally:
        _migrating_presets = False


def convert_preset_to_pack(name: str, dest_dir: str | None = None) -> str:
    """Presets are packs. Return the existing directory; do not write a second copy."""
    pack = find_pack_by_name(name)
    if not pack:
        raise StylepackError("没有名为 %s 的风格包" % name)
    return str(pack.get("path") or "")


def render_preview_png(style: dict, dest: str) -> str:
    """480×270 dark/light sample. Needs a QApplication."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
    from cutora.ui.panels.fonts import render_font_family
    from cutora.ui.theme.fonts_ui import register_ui_fonts
    from cutora.ui.theme.tokens import FONT_UI_CJK

    register_ui_fonts()
    img = QImage(480, 270, QImage.Format.Format_ARGB32)
    img.fill(QColor("#141210"))
    painter = QPainter(img)
    try:
        painter.fillRect(0, 135, 480, 135, QColor("#EFE7DA"))
        family = render_font_family(style.get("font_family"))
        font = QFont()
        font.setFamilies([family, FONT_UI_CJK, "Microsoft YaHei UI", "Microsoft YaHei"])
        font.setPixelSize(18)
        font.setBold(bool(style.get("bold", True)))
        font.setItalic(bool(style.get("italic", False)))
        painter.setFont(font)
        flags = (
            Qt.AlignmentFlag.AlignHCenter
            | Qt.AlignmentFlag.AlignVCenter
            | Qt.TextFlag.TextWordWrap
        )
        for y, fg in ((8, QColor("#FFFFFF")), (143, QColor("#141210"))):
            painter.setPen(QPen(fg))
            painter.drawText(16, y, 448, 118, flags, SAMPLE)
    finally:
        painter.end()
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    if not img.save(dest, "PNG"):
        raise StylepackError("无法写入 preview.png")
    return dest

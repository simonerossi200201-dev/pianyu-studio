# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Roma palette + Studio Graphite metrics — single source for brand and UI.

High-saturation color is reserved for things that are happening
(playhead, progress, selected clip edges). Static chrome stays in
this stone-neutral set.

Do not hand-edit ``resources/theme/theme_dark.qss``; generate it with
``tools/build_qss.py``.
"""
from __future__ import annotations

import os

BG0 = "#141210"  # basalt paving — warm black, not pure black
BG1 = "#1C1815"  # panel
BG2 = "#262019"  # card / input
BG3 = "#332A22"  # hover
LINE = "#524944"  # divider (visible on bg1)
BORDER = "#5D5550"  # control stroke
FG0 = "#EFE7DA"  # travertine — primary text
FG1 = "#B3A695"  # secondary text
FG2 = "#92897F"  # tertiary / hex captions (readable on bg2)
FG_MUTED = "#7A6F63"  # true disabled
ACCENT = "#C8A24A"  # antique gold (gilded mosaic)
ACCENT_HI = "#E0BE68"  # hover / highlight
ACCENT_DIM = "#8A6E2E"  # pressed
INFO = "#8FA89B"  # verdigris (oxidised bronze; replaces neon cyan)
OK = "#7D9B6A"  # olive
WARN = "#C8913A"  # ochre
ERR = "#B4544A"  # terracotta chrome
ERR_TEXT = "#C2736B"  # error labels on bg2

# Q169 时间线片段的实色色块（对标专业 NLE：一眼分清轨道种类）。油画四色「红黄绿蓝」，
# 红换成紫免得太冲：视频 = 蓝、图片 = 紫、文字 = 赭黄、音频 = 绿、字幕 = 青绿（铜绿）。
# 全部中等偏低饱和度 / 中等偏低亮度（HSL S≈30–50%、L≈33–40%），fg0 的白字压上去对比度 ≥ 4.5。
# 六套主题共用这五个色，换主题换的是面板底色，轨道种类的颜色语义不变。
TRACK_VIDEO = "#405D82"
TRACK_AUDIO = "#3E6F53"
TRACK_SUBTITLE = "#396A6A"
TRACK_TEXT = "#7F6230"
TRACK_IMAGE = "#684B81"
# Q174：调整轨（纯色 / 局部效果）—— 银灰（无彩色，百搭），时间线上画成拉丝金属（高光 + 暗缝 + 底部阴影，
# 见 timeline/widget.metal_gradient），中灰的亮度不扎眼；上面的字 / 图标用主题色（不够深就沿主题色压暗到
# 对比度 ≥ 6，小字也清楚，见 track_ink）。各主题共用。
TRACK_ADJUST = "#989DA4"

# Soft splash glow (dusk on travertine). Never used at full opacity.
GLOW = "#3A2A18"

# Spacing grid (px). Compact half-steps are first-class, not exceptions.
SPACE_QUARTER = 2
SPACE_1 = 4
SPACE_1H = 6
SPACE_2 = 8
SPACE_3 = 12
SPACE_4 = 16
SPACE_5 = 24
SPACE_6 = 32
SPACE_ALLOWED = frozenset({0, 1, SPACE_QUARTER, SPACE_1, SPACE_1H, SPACE_2, SPACE_3, SPACE_4, SPACE_5, SPACE_6})

RADIUS_SM = 3
RADIUS_CTL = 5
RADIUS_CARD = 8
RADIUS_DLG = 12
RADIUS_ALLOWED = frozenset({RADIUS_SM, RADIUS_CTL, RADIUS_CARD, RADIUS_DLG})

FONT_UI = "Inter"
FONT_UI_CJK = "Noto Sans SC"
FONT_MONO = "JetBrains Mono"
FONT_MICRO = 11
FONT_LABEL = 12
FONT_BODY = 13
FONT_SECTION = 15
FONT_TITLE = 17
FONT_PAGE = 20
W_REGULAR = 400
W_MEDIUM = 500
W_SEMI = 600
# Back-compat aliases used by existing panels.
FONT_SIZE_SM = FONT_LABEL
FONT_SIZE_BODY = FONT_BODY
FONT_SIZE_TITLE = FONT_TITLE
FONT_SIZE_PAGE = FONT_PAGE

DUR_FAST = 100
DUR_BASE = 150
DUR_SLOW = 240
DURATION_MS = DUR_BASE
EASE = "OutCubic"

ROW_H = 24
CONTROL_H = 22
CONTROL_H_LG = 28
SECTION_HEAD_H = 26
ICON_SM = 14
ICON_MD = 16
SQUARE_SM = 20
SQUARE_MD = 24
RESET_PX = 20
SWATCH_PX = 24
TOGGLE_W = 36
TOGGLE_H = 20
# Inspector (right dock) sizing: one height authority for every single-line
# control. Qt QSS box model: min-height / max-height size the CONTENT box;
# padding and border are added on top. Inspector inputs use vertical padding 0
# and a 1px border and pin content to INSPECTOR_CONTROL_INNER_H, so the outer
# height is exactly INSPECTOR_CONTROL_H. Python code must not setFixedHeight
# these controls (a 38px QSS box inside a 22px Python cap clipped every field).
INSPECTOR_ROW_H = 32
INSPECTOR_CONTROL_H = 28
INSPECTOR_CONTROL_INNER_H = INSPECTOR_CONTROL_H - 2
MIN_HEIGHT_ALLOWED = frozenset({
    CONTROL_H, CONTROL_H_LG, ROW_H, SECTION_HEAD_H,
    INSPECTOR_ROW_H, INSPECTOR_CONTROL_H, INSPECTOR_CONTROL_INNER_H,
})

# Right dock is freely resizable (Luca 2026-09-21: no reason to cap it).
# MAX is Qt's QWIDGETSIZE_MAX, i.e. "no cap"; the splitter keeps the canvas.
DOCK_RIGHT_MIN = 300
DOCK_RIGHT_MAX = 16777215  # QWIDGETSIZE_MAX
DOCK_RIGHT_DEFAULT = 360

# Inspector (Resolve-style parameter panel). See UI_INSPECTOR_SPEC.md.
INSPECTOR_LABEL_MIN = SPACE_6              # 32
INSPECTOR_LABEL_MAX = SPACE_6 * 3 + SPACE_4 # 112; labels are short nouns, fields get the width
INSPECTOR_SPIN_WIDTH = SPACE_6 * 2 + SPACE_2  # 72
INSPECTOR_VALUE_W = SPACE_6 + SPACE_5      # 56; value box beside every slider
INSPECTOR_SWATCH_PX = SWATCH_PX            # 24
INSPECTOR_COMBO_MAX_W = SPACE_6 * 8        # 256
INSPECTOR_COLOR_MAX_W = SPACE_6 * 4        # 128 (chip + hex)
INSPECTOR_ROW_GAP = SPACE_1H               # 6
INSPECTOR_SECTION_GAP = SPACE_4            # 16
INSPECTOR_RESET_PX = RESET_PX              # 20
INSPECTOR_HEADER_H = SPACE_6                # 32
INSPECTOR_PAD_X = SPACE_3                  # 12
INSPECTOR_PAD_Y = SPACE_2                  # 8
INSPECTOR_FIELD_GAP = SPACE_1              # 4
INSPECTOR_PAIR_GAP = SPACE_2               # 8; between paired fields / slider and value
INSPECTOR_OPTION_GAP = SPACE_3             # 12; between checkbox options on one line
QWIDGETSIZE_MAX = 16777215                 # Qt's "no limit" for min/max sizes
INSPECTOR_SLIDER_MIN = SPACE_5             # 24
INSPECTOR_CHECKER_PX = SPACE_1             # 4
INSPECTOR_GRAD_BAR_H = SPACE_2             # 8
INSPECTOR_TEXT_H = SPACE_6 * 2             # 64
INSPECTOR_LABEL_FONT = FONT_LABEL          # 12
INSPECTOR_TITLE_FONT = FONT_SECTION        # 15
INSPECTOR_PANEL_W = SPACE_6 * 15           # 480 (width lock for max-width tests)
INSPECTOR_WHEEL_PX = SPACE_6 * 4           # 128; lift/gamma/gain colour wheel
INSPECTOR_WHEEL_MIN_PX = SPACE_6 * 2 + SPACE_2  # 72; wheels shrink to fit narrow docks
INSPECTOR_WHEEL_MAX_PX = SPACE_6 * 5       # 160; and grow on wide ones
INSPECTOR_ICON_PX = ICON_MD                # 16; glyph inside a 28px icon button

assert RESET_PX <= ROW_H
assert SWATCH_PX <= ROW_H
assert CONTROL_H <= ROW_H
assert INSPECTOR_CONTROL_H <= INSPECTOR_ROW_H
assert SWATCH_PX <= INSPECTOR_CONTROL_H
assert FONT_SECTION - FONT_LABEL >= 3
assert DOCK_RIGHT_DEFAULT >= DOCK_RIGHT_MIN
assert DOCK_RIGHT_MIN >= 300
assert (
    INSPECTOR_PAD_X * 2
    + INSPECTOR_LABEL_MAX
    + INSPECTOR_FIELD_GAP * 2
    + INSPECTOR_RESET_PX
    + INSPECTOR_SPIN_WIDTH
    <= DOCK_RIGHT_MIN
)

ROMA = {
    "bg0": BG0,
    "bg1": BG1,
    "bg2": BG2,
    "bg3": BG3,
    "line": LINE,
    "border": BORDER,
    "fg0": FG0,
    "fg1": FG1,
    "fg2": FG2,
    "fg_muted": FG_MUTED,
    "accent": ACCENT,
    "accent_hi": ACCENT_HI,
    "accent_dim": ACCENT_DIM,
    "info": INFO,
    "ok": OK,
    "warn": WARN,
    "err": ERR,
    "err_text": ERR_TEXT,
    "glow": GLOW,
}

TRACK = {
    "video": TRACK_VIDEO,
    "audio": TRACK_AUDIO,
    "subtitle": TRACK_SUBTITLE,
    "text": TRACK_TEXT,
    "image": TRACK_IMAGE,
}


def hex_rgb(color: str) -> tuple[int, int, int]:
    """``#RRGGBB`` or ``#RRGGBBAA`` → ``(r, g, b)``."""
    h = (color or "").lstrip("#")
    if len(h) == 8:
        h = h[:6]
    if len(h) == 3:
        h = h[0] * 2 + h[1] * 2 + h[2] * 2
    if len(h) != 6:
        raise ValueError("expected #RRGGBB, got %r" % color)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def mix_hex(a: str, b: str, amount: float) -> str:
    """Linear blend of two ``#RRGGBB`` tokens. ``amount`` 0 = ``a``, 1 = ``b``."""
    t = max(0.0, min(1.0, float(amount)))
    ar, ag, ab = hex_rgb(a)
    br, bg, bb = hex_rgb(b)
    r = int(round(ar + (br - ar) * t))
    g = int(round(ag + (bg - ag) * t))
    b_ = int(round(ab + (bb - ab) * t))
    return "#%02X%02X%02X" % (r, g, b_)


def lighten_hex(color: str, amount: float) -> str:
    """Move ``color`` toward white by ``amount`` (0–1)."""
    r, g, b = hex_rgb(color)
    t = max(0.0, min(1.0, float(amount)))
    r = min(255, int(round(r + (255 - r) * t)))
    g = min(255, int(round(g + (255 - g) * t)))
    b = min(255, int(round(b + (255 - b) * t)))
    return "#%02X%02X%02X" % (r, g, b)


SEP = lighten_hex(LINE, 0.12)  # dock separator; line lifted 12%


def darken_hex(color: str, amount: float) -> str:
    """Move ``color`` toward black by ``amount`` (0–1)."""
    r, g, b = hex_rgb(color)
    t = max(0.0, min(1.0, float(amount)))
    r = max(0, int(round(r * (1.0 - t))))
    g = max(0, int(round(g * (1.0 - t))))
    b = max(0, int(round(b * (1.0 - t))))
    return "#%02X%02X%02X" % (r, g, b)


def hex_rgba(color: str, alpha: int = 255) -> tuple[int, int, int, int]:
    r, g, b = hex_rgb(color)
    return r, g, b, int(alpha)


def rgba(color: str, alpha: float) -> str:
    """QSS ``rgba()`` from a hex token. ``alpha`` is 0–1 or 0–255."""
    r, g, b = hex_rgb(color)
    a = float(alpha)
    if a > 1.0:
        a = a / 255.0
    return "rgba(%d, %d, %d, %.3f)" % (r, g, b, a)


BUILTIN_THEME_IDS = (
    "roma-gold",
    "midnight-blue",
    "amethyst",
    "rose",
    "pine",
    "graphite",
)
DEFAULT_THEME_ID = "roma-gold"
_COLOR_KEYS = (
    "bg0", "bg1", "bg2", "bg3", "line", "border",
    "fg0", "fg1", "fg2", "fg_muted",
    "accent", "accent_hi", "accent_dim",
    "info", "ok", "warn", "err", "err_text",
    "track_video", "track_audio", "track_subtitle", "track_text", "track_image",
)
_current = None
_current_id = DEFAULT_THEME_ID


def palette_dir() -> str:
    from cutora.core.paths import RESOURCES
    return os.path.join(RESOURCES, "theme", "palettes")


def palette_path(theme_id: str) -> str:
    safe = os.path.basename(str(theme_id or DEFAULT_THEME_ID))
    return os.path.join(palette_dir(), safe + ".json")


class TokenSet:
    """Runtime palette. Frozen ROMA constants on this module stay the default."""

    def __init__(self, data: dict, theme_id: str):
        self.theme_id = theme_id
        self.name_zh = str(data.get("name_zh") or theme_id)
        self.name_en = str(data.get("name_en") or theme_id)
        self.is_dark = bool(data.get("is_dark", True))
        for key in _COLOR_KEYS:
            value = data.get(key)
            if not value:
                raise ValueError("palette %s missing %s" % (theme_id, key))
            setattr(self, key, str(value))
        self.glow = str(data.get("glow") or self.bg2)
        self.sep = lighten_hex(self.line, 0.12)


def load_palette_data(theme_id: str) -> dict:
    path = palette_path(theme_id)
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    import json
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("palette %s is not an object" % theme_id)
    return data


def load_theme(theme_id: str, accent_override: str | None = None) -> "TokenSet":
    """Set the process-wide current palette. Unknown id → roma-gold."""
    global _current, _current_id
    tid = str(theme_id or DEFAULT_THEME_ID)
    try:
        data = dict(load_palette_data(tid))
    except Exception:
        tid = DEFAULT_THEME_ID
        data = dict(load_palette_data(tid))
    if accent_override:
        data["accent"] = accent_override
        data["accent_hi"] = lighten_hex(accent_override, 0.18)
        data["accent_dim"] = darken_hex(accent_override, 0.30)
    _current = TokenSet(data, tid)
    _current_id = tid
    return _current


def current() -> TokenSet:
    if _current is None:
        load_theme(DEFAULT_THEME_ID)
    return _current


def list_themes() -> list[tuple[str, dict]]:
    rows = []
    for tid in BUILTIN_THEME_IDS:
        try:
            rows.append((tid, load_palette_data(tid)))
        except Exception:
            continue
    return rows


def qss_context() -> dict:
    """Values fed to the Jinja2 QSS template (from the current palette)."""
    t = current()
    ctx = {
        "bg0": t.bg0, "bg1": t.bg1, "bg2": t.bg2, "bg3": t.bg3,
        "line": t.line, "sep": t.sep, "border": t.border,
        "fg0": t.fg0, "fg1": t.fg1, "fg2": t.fg2,
        "fg_muted": t.fg_muted,
        "pressed_bg": darken_hex(t.bg2, 0.06),
        "accent": t.accent, "accent_hi": t.accent_hi, "accent_dim": t.accent_dim,
        "info": t.info, "ok": t.ok, "warn": t.warn, "err": t.err,
        "err_text": t.err_text,
        "glow": t.glow,
        "track_video": t.track_video,
        "track_audio": t.track_audio,
        "track_subtitle": t.track_subtitle,
        "track_text": t.track_text,
        "track_image": t.track_image,
        "space_quarter": SPACE_QUARTER,
        "space_1": SPACE_1,
        "space_1h": SPACE_1H,
        "space_2": SPACE_2,
        "space_3": SPACE_3,
        "space_4": SPACE_4,
        "space_5": SPACE_5,
        "space_6": SPACE_6,
        "radius_sm": RADIUS_SM,
        "radius_ctl": RADIUS_CTL,
        "radius_card": RADIUS_CARD,
        "radius_dlg": RADIUS_DLG,
        "font_ui": FONT_UI,
        "font_cjk": FONT_UI_CJK,
        "font_mono": FONT_MONO,
        "font_micro": FONT_MICRO,
        "font_label": FONT_LABEL,
        "font_sm": FONT_LABEL,
        "font_body": FONT_BODY,
        "font_section": FONT_SECTION,
        "font_title": FONT_TITLE,
        "font_page": FONT_PAGE,
        "w_regular": W_REGULAR,
        "w_medium": W_MEDIUM,
        "w_semi": W_SEMI,
        "duration_ms": DUR_BASE,
        "dur_fast": DUR_FAST,
        "dur_base": DUR_BASE,
        "dur_slow": DUR_SLOW,
        "row_h": ROW_H,
        "control_h": CONTROL_H,
        "insp_row_h": INSPECTOR_ROW_H,
        "insp_ctl_h": INSPECTOR_CONTROL_H,
        "insp_ctl_inner": INSPECTOR_CONTROL_INNER_H,
        "control_h_lg": CONTROL_H_LG,
        "section_head_h": SECTION_HEAD_H,
        "icon_sm": ICON_SM,
        "icon_md": ICON_MD,
        "square_sm": SQUARE_SM,
        "square_md": SQUARE_MD,
        "reset_px": RESET_PX,
        "swatch_px": SWATCH_PX,
        "toggle_w": TOGGLE_W,
        "toggle_h": TOGGLE_H,
        "inspector_spin_w": INSPECTOR_SPIN_WIDTH,
        "inspector_value_w": INSPECTOR_VALUE_W,
        "inspector_swatch": INSPECTOR_SWATCH_PX,
        "inspector_combo_max": INSPECTOR_COMBO_MAX_W,
        "inspector_color_max": INSPECTOR_COLOR_MAX_W,
        "inspector_row_gap": INSPECTOR_ROW_GAP,
        "inspector_section_gap": INSPECTOR_SECTION_GAP,
        "inspector_reset": INSPECTOR_RESET_PX,
        "inspector_header_h": INSPECTOR_HEADER_H,
        "inspector_pad_x": INSPECTOR_PAD_X,
        "inspector_pad_y": INSPECTOR_PAD_Y,
        "inspector_label_font": INSPECTOR_LABEL_FONT,
        "inspector_title_font": INSPECTOR_TITLE_FONT,
        "inspector_text_h": INSPECTOR_TEXT_H,
        "inspector_wheel": INSPECTOR_WHEEL_PX,
        "inspector_icon": INSPECTOR_ICON_PX,
        "fg0_08": rgba(t.fg0, 0.08),
        "fg0_10": rgba(t.fg0, 0.10),
        "fg0_12": rgba(t.fg0, 0.12),
        "fg0_14": rgba(t.fg0, 0.14),
        "fg0_16": rgba(t.fg0, 0.16),
        "fg0_22": rgba(t.fg0, 0.22),
        "fg0_25": rgba(t.fg0, 0.25),
        "fg0_28": rgba(t.fg0, 0.28),
        "fg0_35": rgba(t.fg0, 0.35),
        "bg0_78": rgba(t.bg0, 0.78),
        "accent_18": rgba(t.accent, 0.18),
        "accent_35": rgba(t.accent, 0.35),
        "accent_55": rgba(t.accent, 0.55),
        "ok_85": rgba(t.ok, 0.85),
        "info_25": rgba(t.info, 0.25),
        "qss_check": "qss_check.svg",
        "qss_check_disabled": "qss_check_disabled.svg",
        "qss_chevron_up": "qss_chevron_up.svg",
        "qss_chevron_down": "qss_chevron_down.svg",
    }
    return ctx


def track_ink(track_hex: str, accent_hex: str | None = None, min_ratio: float = 6.0) -> str:
    """Q174：亮色轨道（调整轨的银灰）上的字色 = 主题色；对比度不够就沿主题色一点点压暗，色相不变。"""
    from cutora.ui.theme.contrast import contrast_ratio
    acc = accent_hex or current().accent
    ink = acc
    for k in range(0, 21):
        ink = darken_hex(acc, k * 0.04) if k else acc
        if contrast_ratio(ink, track_hex) >= min_ratio:
            return ink
    return ink

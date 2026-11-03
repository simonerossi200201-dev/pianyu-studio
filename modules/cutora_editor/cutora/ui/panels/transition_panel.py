# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Left-dock transition library: video (T20c) + audio tapes. Drag / double-click.

Social-video layout (2026-11 upgrade, clean-room):

* Cards preview each transition on real demo frames from
  ``resources/transition_samples`` (project-owned, see PROVENANCE.json), rendered
  at the device pixel ratio. At rest every card shows a mid-transition frame.
* Hover plays the transition: frames are rendered lazily (a few ms per timer tick
  at most) into a shared per-preset LRU, then only blitted. Short holds at the
  start / end of each loop so the motion reads clearly.
* Grouping, recommended duration, paired SFX and search keywords come from
  ``cutora.render.transition_catalog``; presets it does not know yet fall back to
  a local category guess so the panel never looks empty.
* Category chips (热门 / 全部 / … / 收藏), search by 中文名 / 拼音首字母 / preset id /
  catalog keywords, favorites + recently used (persisted in app settings), and a
  「附带音效」 switch bound to ``transition_sfx_enabled``.

Drag payload (``TRANSITION_MIME``) and double-click apply are unchanged.
"""
from __future__ import annotations

import json
import math
import os
import time
from collections import OrderedDict, deque

from PySide6.QtCore import (
    QEvent, QMimeData, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal,
)
from PySide6.QtGui import (
    QBrush, QColor, QDrag, QFont, QImage, QLinearGradient, QPainter,
    QPainterPath, QPen, QPixmap, QPolygonF,
)
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLayout, QLineEdit,
    QMenu, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from cutora.core.log import get_logger
from cutora.render.anim_engine import (
    AUDIO_TRANSITION_LIBRARY,
    STANDALONE_TRANSITION_LIBRARY,
    audio_transition_duration,
    compose_standalone_transition,
)
from cutora.ui.i18n import tr

logger = get_logger("cutora.ui.panels.transition_panel")

VIDEO_ITEMS = list(STANDALONE_TRANSITION_LIBRARY)
AUDIO_ITEMS = list(AUDIO_TRANSITION_LIBRARY)
TRANSITION_MIME = "application/x-cutora-transition"

THUMB_W = 128
THUMB_H = 72
CARD_PAD = 6
CARD_W = THUMB_W + 2 * CARD_PAD
CARD_H = THUMB_H + 2 * CARD_PAD + 22
GRID_SPACING = 8
DEFAULT_VIDEO_DUR = 0.35

# Hover animation: ANIM_FRAMES transition frames played at ~30 fps, framed by a
# short hold on A and a longer hold on B.
ANIM_FRAMES = 24
ANIM_TICK_MS = 33
HOLD_START_TICKS = 9
HOLD_END_TICKS = 12
STATIC_PROG = 0.45
_STATIC_PROG_OVERRIDES = {
    "flash_white": 0.41,
    "flash_black": 0.41,
}
TICK_RENDER_BUDGET_S = 0.004
FRAME_CACHE_MAX_PRESETS = 40
FRAME_CACHE_MAX_BYTES = 64 * 1024 * 1024
RECENT_MAX = 8

SETTING_FAVORITES = "transition_favorites"
SETTING_RECENT = "transition_recent"
SETTING_SFX = "transition_sfx_enabled"
SETTING_TAB = "transition_panel_tab"

SAMPLE_PAIRS = ("camera", "rhythm", "graphic", "light", "dissolve")
_SAMPLE_SRC_W = 384  # size of the 1x sample files; @2x is 768

# Pair rotation per category: first entry is the category's own look, the rest
# keep neighbouring cards from looking identical.
_CATEGORY_PAIRS = {
    "camera": ("camera", "light", "rhythm", "dissolve"),
    "rhythm": ("rhythm", "camera", "graphic", "light"),
    "graphic": ("graphic", "dissolve", "light", "rhythm"),
    "light": ("light", "dissolve", "camera", "rhythm"),
    "motion": ("camera", "rhythm", "dissolve", "light"),
    "wipe": ("graphic", "dissolve", "camera", "rhythm"),
    "dissolve": ("dissolve", "light", "camera", "graphic"),
    "fx": ("rhythm", "light", "graphic", "camera"),
}

# Local guess for presets the catalog does not list yet (catalog wins).
_CATEGORY_MAP = {
    # 运镜
    "whip_pan_left": "camera",
    "whip_pan_up": "camera",
    "smash_zoom": "camera",
    "zoom_out_trans": "camera",
    "spin_in": "camera",
    "cross_zoom_blur": "camera",
    "zoom_in_cross": "camera",
    "zoom_out_cross": "camera",
    # 节奏卡点
    "flash_white": "rhythm",
    "flash_black": "rhythm",
    "shake_impact": "rhythm",
    # 图形
    "iris_diamond": "graphic",
    "iris_close": "graphic",
    "iris_rectangle": "graphic",
    "checker_wipe": "graphic",
    "split_interleave": "graphic",
    "venetian_diag": "graphic",
    "venetian_h": "graphic",
    "venetian_v": "graphic",
    "page_turn": "graphic",
    "page_curl": "graphic",
    # 光效胶片
    "light_leak": "light",
    "film_burn": "light",
    "bloom_dissolve": "light",
    "exposure_dissolve": "light",
    "bokeh_dissolve": "light",
    "bokeh_blur": "light",
    "luma_melt": "light",
    # 推拉滑动
    "push_left": "motion",
    "push_right": "motion",
    "push_up": "motion",
    "push_down": "motion",
    "push_out_left": "motion",
    "push_out_right": "motion",
    "push_out_up": "motion",
    "push_out_down": "motion",
    "push_split": "motion",
    "slide_left": "motion",
    "slide_right": "motion",
    "slide_up": "motion",
    "slide_down": "motion",
    "cover_left": "motion",
    "cover_down": "motion",
    "cube_spin": "motion",
    "cube_left": "motion",
    "cube_right": "motion",
    "flip_h": "motion",
    "flip_v": "motion",
    # 擦除遮罩
    "wipe_left": "wipe",
    "wipe_right": "wipe",
    "wipe_up": "wipe",
    "wipe_down": "wipe",
    "wipe_angle": "wipe",
    "wipe_circle": "wipe",
    "wipe_clock": "wipe",
    "wipe_blinds": "wipe",
    "wipe_radial": "wipe",
    "barn_door_h": "wipe",
    "barn_door_v": "wipe",
    "curtain_drop": "wipe",
    # 溶解渐变
    "cross_dissolve": "dissolve",
    "fade_dark": "dissolve",
    "fade_white": "dissolve",
    "zoom_dissolve": "dissolve",
    "blur_dissolve": "dissolve",
    "rotate_dissolve": "dissolve",
    "dip_color": "dissolve",
    "luma_fade": "dissolve",
    # 特效
    "glitch": "fx",
    "rgb_split": "fx",
    "chromatic": "fx",
    "pixelate": "fx",
    "scanlines": "fx",
    "blur_directional": "fx",
    "blur_radial": "fx",
    "wave_ripple": "fx",
}

# Used for 热门 only while the catalog marks nothing as hot.
_FALLBACK_HOT = (
    "whip_pan_left", "smash_zoom", "cross_zoom_blur", "spin_in", "shake_impact",
    "flash_white", "light_leak", "film_burn", "glitch", "rgb_split",
    "zoom_dissolve", "cross_dissolve",
)

_ALL_TAB = "all"
_FAV_TAB = "fav"


# ── catalog / sfx access (both are being filled in parallel; never hard-fail) ──

def _catalog():
    try:
        from cutora.render import transition_catalog
        return transition_catalog
    except Exception:
        return None


def _catalog_categories() -> list:
    cat = _catalog()
    rows = list(getattr(cat, "CATEGORIES", []) or []) if cat else []
    if not rows:
        rows = [
            ("hot", "热门"), ("camera", "运镜"), ("rhythm", "节奏卡点"),
            ("graphic", "图形"), ("light", "光效胶片"), ("motion", "推拉滑动"),
            ("wipe", "擦除遮罩"), ("dissolve", "溶解渐变"), ("fx", "特效"),
            ("audio", "音频"),
        ]
    return [(str(cid), str(name)) for cid, name in rows]


def _catalog_has(preset: str) -> bool:
    cat = _catalog()
    table = getattr(cat, "TRANSITION_META", None) if cat else None
    return bool(isinstance(table, dict) and str(preset or "") in table)


def _meta_of(preset: str) -> dict:
    cat = _catalog()
    if cat is not None:
        try:
            m = cat.meta_of(preset)
            if isinstance(m, dict):
                return m
        except Exception:
            pass
    return {
        "category": "fx", "dur": DEFAULT_VIDEO_DUR, "sfx": "", "keywords": "",
        "hot": False, "sample": "",
    }


def _recommended_duration(preset: str) -> float:
    cat = _catalog()
    if cat is not None:
        try:
            return float(cat.recommended_duration(preset, DEFAULT_VIDEO_DUR))
        except Exception:
            pass
    return DEFAULT_VIDEO_DUR


def _recommended_sfx(preset: str) -> str:
    cat = _catalog()
    if cat is not None:
        try:
            return str(cat.recommended_sfx(preset) or "")
        except Exception:
            pass
    return ""


_SFX_MOD = None
_SFX_MOD_RETRY_AT = 0.0


def _sfx_module():
    """``cutora.audio.sfx_social`` or None (failed imports retried every 30 s)."""
    global _SFX_MOD, _SFX_MOD_RETRY_AT
    if _SFX_MOD is not None:
        return _SFX_MOD
    now = time.monotonic()
    if now < _SFX_MOD_RETRY_AT:
        return None
    try:
        from cutora.audio import sfx_social
        _SFX_MOD = sfx_social
        return sfx_social
    except Exception:
        _SFX_MOD_RETRY_AT = now + 30.0
        return None


def sfx_label(sfx_id: str) -> str:
    """中文 label of a SFX id via ``sfx_social.CATALOG``; the id itself otherwise."""
    sid = str(sfx_id or "")
    if not sid:
        return ""
    mod = _sfx_module()
    cat = getattr(mod, "CATALOG", None) if mod is not None else None
    entry = None
    try:
        if isinstance(cat, dict):
            entry = cat.get(sid)
        elif isinstance(cat, (list, tuple)):
            for row in cat:
                if isinstance(row, dict) and str(row.get("id", "")) == sid:
                    entry = row
                    break
                if isinstance(row, (list, tuple)) and row and str(row[0]) == sid:
                    entry = row
                    break
                if getattr(row, "id", None) == sid:
                    entry = row
                    break
    except Exception:
        entry = None
    if isinstance(entry, str) and entry:
        return entry
    if isinstance(entry, dict):
        for key in ("name_zh", "zh", "label_zh", "label", "name", "title"):
            val = entry.get(key)
            if isinstance(val, str) and val:
                return val
    if isinstance(entry, (list, tuple)):
        for val in entry[1:]:
            if isinstance(val, str) and val:
                return val
    if entry is not None:
        for key in ("name_zh", "zh", "label_zh", "label", "name", "title"):
            val = getattr(entry, key, None)
            if isinstance(val, str) and val:
                return val
    return sid


# ── settings ──

def _load_settings() -> dict:
    try:
        from cutora.core.history import _load_app_settings
        s = _load_app_settings() or {}
        return s if isinstance(s, dict) else {}
    except Exception:
        return {}


def _save_settings_patch(patch: dict) -> None:
    try:
        from cutora.core.history import _load_app_settings, _save_app_settings
        s = _load_app_settings() or {}
        if not isinstance(s, dict):
            s = {}
        s.update(patch)
        _save_app_settings(s)
    except Exception:
        logger.debug("transition panel: settings save failed", exc_info=True)


def sfx_enabled() -> bool:
    mod = _sfx_module()
    fn = getattr(mod, "is_enabled", None) if mod is not None else None
    if callable(fn):
        try:
            return bool(fn())
        except Exception:
            pass
    return bool(_load_settings().get(SETTING_SFX, False))


def set_sfx_enabled(on: bool) -> None:
    mod = _sfx_module()
    fn = getattr(mod, "set_enabled", None) if mod is not None else None
    if callable(fn):
        try:
            fn(bool(on))
            return
        except Exception:
            logger.debug("sfx_social.set_enabled failed", exc_info=True)
    _save_settings_patch({SETTING_SFX: bool(on)})


# ── search helpers ──

_PY_BOUNDS = (
    (-20319, "a"), (-20283, "b"), (-19775, "c"), (-19218, "d"), (-18710, "e"),
    (-18526, "f"), (-18239, "g"), (-17922, "h"), (-17417, "j"), (-16474, "k"),
    (-16212, "l"), (-15640, "m"), (-15165, "n"), (-14922, "o"), (-14914, "p"),
    (-14630, "q"), (-14149, "r"), (-14090, "s"), (-13318, "t"), (-12838, "w"),
    (-12556, "x"), (-11847, "y"), (-11055, "z"),
)


def pinyin_initials(text: str) -> str:
    """拼音首字母 for GB2312 level-1 hanzi (no dependency); others kept / dropped."""
    out = []
    for ch in str(text or ""):
        if ch.isascii():
            if ch.isalnum():
                out.append(ch.lower())
            continue
        try:
            raw = ch.encode("gb2312")
        except Exception:
            continue
        if len(raw) != 2:
            continue
        code = (raw[0] << 8 | raw[1]) - 65536
        if code < -20319 or code > -10247:
            continue
        letter = ""
        for bound, ini in _PY_BOUNDS:
            if code >= bound:
                letter = ini
            else:
                break
        if letter:
            out.append(letter)
    return "".join(out)


def _fmt_dur(sec: float) -> str:
    sec = float(sec)
    if abs(sec * 10 - round(sec * 10)) < 1e-6:
        return "%.1fs" % sec
    return ("%.2f" % sec).rstrip("0").rstrip(".") + "s"


def _preset_category(preset: str, domain: str) -> str:
    if domain == "audio":
        return "audio"
    p = str(preset or "")
    if _catalog_has(p):
        cat = _catalog()
        try:
            return str(cat.category_of(p) or "fx")
        except Exception:
            pass
    return _CATEGORY_MAP.get(p, "fx")


def _sample_pair_for(preset: str, category: str, index: int):
    """(pair id, flipped) for a preset. Catalog ``sample`` leads the rotation."""
    want = str(_meta_of(preset).get("sample") or "").strip().lower()
    base = ""
    if want:
        cand = want.split("@")[0]
        for suf in ("_a", "_b"):
            if cand.endswith(suf):
                cand = cand[: -len(suf)]
        if cand in SAMPLE_PAIRS:
            base = cand
    default_seq = _CATEGORY_PAIRS.get(category) or _CATEGORY_PAIRS["dissolve"]
    if base:
        seq = (base,) + tuple(p for p in default_seq if p != base)
    else:
        seq = default_seq
    i = max(0, int(index))
    return seq[i % len(seq)], bool((i // len(seq)) % 2)


# ── demo images (shared, loaded once, scaled once per pixel size) ──

def _samples_dir() -> str:
    try:
        from cutora.core.paths import TRANSITION_SAMPLES_DIR
        return TRANSITION_SAMPLES_DIR
    except Exception:
        from cutora.core.paths import RESOURCES
        return os.path.join(RESOURCES, "transition_samples")


_SRC_IMAGES: dict = {}
_SCALED_IMAGES: dict = {}


def _load_src(pair: str, side: str, hi: bool):
    key = (pair, side, bool(hi))
    if key in _SRC_IMAGES:
        return _SRC_IMAGES[key]
    name = "%s_%s%s.jpg" % (pair, side, "@2x" if hi else "")
    path = os.path.join(_samples_dir(), name)
    img = QImage(path) if os.path.isfile(path) else QImage()
    out = None if img.isNull() else img
    _SRC_IMAGES[key] = out
    return out


def sample_image(pair: str, side: str, wpx: int, hpx: int):
    """Cover-scaled premultiplied demo frame, or None if the file is missing."""
    wpx, hpx = max(1, int(wpx)), max(1, int(hpx))
    key = (pair, side, wpx, hpx)
    img = _SCALED_IMAGES.get(key)
    if img is not None:
        return img
    hi = wpx > _SAMPLE_SRC_W
    src = _load_src(pair, side, hi) or _load_src(pair, side, not hi)
    if src is None:
        return None
    scaled = src.scaled(
        wpx, hpx, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation)
    if scaled.width() != wpx or scaled.height() != hpx:
        x = max(0, (scaled.width() - wpx) // 2)
        y = max(0, (scaled.height() - hpx) // 2)
        scaled = scaled.copy(x, y, wpx, hpx)
    img = scaled.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    _SCALED_IMAGES[key] = img
    return img


def _textured_thumb_pair(w, h):
    """Code-drawn fallback A/B, only used if the sample images are missing."""

    def _one(kind):
        img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        p = QPainter(img)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            g = QLinearGradient(0, 0, w, h)
            if kind == "a":
                g.setColorAt(0.0, QColor(36, 82, 140))
                g.setColorAt(1.0, QColor(18, 36, 64))
            else:
                g.setColorAt(0.0, QColor(196, 118, 42))
                g.setColorAt(1.0, QColor(92, 40, 18))
            p.fillRect(0, 0, w, h, QBrush(g))
            p.setPen(Qt.PenStyle.NoPen)
            if kind == "a":
                p.setBrush(QColor(240, 236, 210, 200))
                p.drawEllipse(QRectF(w * 0.12, h * 0.18, h * 0.4, h * 0.4))
            else:
                p.setBrush(QColor(250, 220, 80, 210))
                p.drawEllipse(QRectF(w * 0.62, h * 0.4, h * 0.44, h * 0.44))
        finally:
            p.end()
        return img

    return _one("a"), _one("b")


_FALLBACK_PAIRS: dict = {}


def _pair_images(pair: str, flip: bool, wpx: int, hpx: int):
    a = sample_image(pair, "a", wpx, hpx)
    b = sample_image(pair, "b", wpx, hpx)
    if a is None or b is None:
        key = (wpx, hpx)
        if key not in _FALLBACK_PAIRS:
            _FALLBACK_PAIRS[key] = _textured_thumb_pair(wpx, hpx)
        a, b = _FALLBACK_PAIRS[key]
    return (b, a) if flip else (a, b)


def _thumb_sources():
    """Back-compat: default A/B at logical thumb size."""
    return _pair_images("dissolve", False, THUMB_W, THUMB_H)


def samples_available() -> bool:
    return all(
        _load_src(p, s, False) is not None for p in SAMPLE_PAIRS for s in ("a", "b"))


# ── frame caches ──

def _pix_bytes(pix) -> int:
    try:
        return int(pix.width()) * int(pix.height()) * max(1, int(pix.depth()) // 8)
    except Exception:
        return 0


class _FrameLRU:
    """preset-variant → list of ANIM_FRAMES pixmaps (filled lazily). Capped."""

    def __init__(self, max_entries: int, max_bytes: int):
        self.max_entries = int(max_entries)
        self.max_bytes = int(max_bytes)
        self._d: OrderedDict = OrderedDict()
        self._bytes: dict = {}
        self.total_bytes = 0

    def frames(self, key, n: int) -> list:
        lst = self._d.get(key)
        if lst is None:
            lst = [None] * int(n)
            self._d[key] = lst
            self._bytes[key] = 0
            self._evict(protect=key)
        else:
            self._d.move_to_end(key)
        return lst

    def store(self, key, idx: int, pix) -> None:
        lst = self._d.get(key)
        if lst is None or not (0 <= idx < len(lst)):
            return
        if lst[idx] is None:
            nb = _pix_bytes(pix)
            self._bytes[key] = self._bytes.get(key, 0) + nb
            self.total_bytes += nb
        lst[idx] = pix
        self._evict(protect=key)

    def _evict(self, protect=None) -> None:
        while self._d and (len(self._d) > self.max_entries or self.total_bytes > self.max_bytes):
            oldest = next(iter(self._d))
            if oldest == protect:
                if len(self._d) == 1:
                    break
                self._d.move_to_end(oldest)
                oldest = next(iter(self._d))
            self._d.pop(oldest, None)
            self.total_bytes -= self._bytes.pop(oldest, 0)

    def clear(self) -> None:
        self._d.clear()
        self._bytes.clear()
        self.total_bytes = 0

    def __len__(self):
        return len(self._d)


_FRAMES = _FrameLRU(FRAME_CACHE_MAX_PRESETS, FRAME_CACHE_MAX_BYTES)
_STATIC: dict = {}
_PERF = {"tick_ms": deque(maxlen=2000), "paint_ms": deque(maxlen=2000)}


def perf_stats() -> dict:
    """Hover tick / paint timings (ms) and cache sizes — for diagnostics/tests."""

    def _summ(vals):
        v = sorted(vals)
        if not v:
            return {"n": 0, "median": 0.0, "p95": 0.0, "max": 0.0}
        return {
            "n": len(v), "median": v[len(v) // 2],
            "p95": v[min(len(v) - 1, int(len(v) * 0.95))], "max": v[-1],
        }

    static_bytes = sum(_pix_bytes(p) for p in _STATIC.values())
    sample_bytes = sum(int(i.sizeInBytes()) for i in _SCALED_IMAGES.values() if i is not None)
    sample_bytes += sum(int(i.sizeInBytes()) for i in _SRC_IMAGES.values() if i is not None)
    return {
        "tick": _summ(_PERF["tick_ms"]),
        "paint": _summ(_PERF["paint_ms"]),
        "anim_cache_presets": len(_FRAMES),
        "anim_cache_bytes": _FRAMES.total_bytes,
        "static_cache_count": len(_STATIC),
        "static_cache_bytes": static_bytes,
        "sample_bytes": sample_bytes,
    }


def reset_perf() -> None:
    _PERF["tick_ms"].clear()
    _PERF["paint_ms"].clear()


def _loop_ticks() -> int:
    return HOLD_START_TICKS + ANIM_FRAMES + HOLD_END_TICKS


def _frame_of_tick(pos: int) -> int:
    if pos < HOLD_START_TICKS:
        return 0
    pos -= HOLD_START_TICKS
    return pos if pos < ANIM_FRAMES else ANIM_FRAMES - 1


def _prog_of_frame(idx: int) -> float:
    return max(0.0, min(1.0, idx / float(ANIM_FRAMES - 1)))


# ── painting helpers ──

def _qc(hex_color: str, alpha: int | None = None) -> QColor:
    c = QColor(hex_color)
    if alpha is not None:
        c.setAlpha(int(alpha))
    return c


def _star_polygon(cx: float, cy: float, r_out: float, r_in: float) -> QPolygonF:
    pts = []
    for i in range(10):
        r = r_out if i % 2 == 0 else r_in
        a = -math.pi / 2 + i * math.pi / 5
        pts.append(QPointF(cx + r * math.cos(a), cy + r * math.sin(a)))
    return QPolygonF(pts)


def _draw_speaker(p: QPainter, rect: QRectF, color: QColor) -> None:
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    body = QPolygonF([
        QPointF(x, y + h * 0.36), QPointF(x + w * 0.22, y + h * 0.36),
        QPointF(x + w * 0.5, y + h * 0.12), QPointF(x + w * 0.5, y + h * 0.88),
        QPointF(x + w * 0.22, y + h * 0.64), QPointF(x, y + h * 0.64),
    ])
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    p.drawPolygon(body)
    pen = QPen(color, max(1.0, w * 0.1))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawArc(QRectF(x + w * 0.38, y + h * 0.28, w * 0.36, h * 0.44), -50 * 16, 100 * 16)
    p.drawArc(QRectF(x + w * 0.38, y + h * 0.1, w * 0.62, h * 0.8), -50 * 16, 100 * 16)


class TransitionThumb(QWidget):
    """Preview tile. Static = mid-transition frame; hover = cached frame loop."""

    STAR_SIZE = 18

    def __init__(self, preset, domain="visual", parent=None, pair=None, flip=False):
        super().__init__(parent)
        self.preset = str(preset or "")
        self.domain = "audio" if domain == "audio" else "visual"
        self.pair = str(pair or "dissolve")
        self.flip = bool(flip)
        self.duration_text = ""
        self.has_sfx = False
        self.favorite = False
        self.hovered = False
        self.selected = False
        self._static_p = _STATIC_PROG_OVERRIDES.get(self.preset, STATIC_PROG)
        self._prog = self._static_p
        self._playing = False
        self._pos = 0
        self._cur = None
        self.setFixedSize(THUMB_W, THUMB_H)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(ANIM_TICK_MS)
        self._timer.timeout.connect(self._tick)
        self._dpr = 0.0
        self._out = None
        self._inn = None
        self._ensure_sources()

    # ── sources / cache keys ──

    def _cur_dpr(self) -> float:
        try:
            d = float(self.devicePixelRatioF())
        except Exception:
            d = 1.0
        return round(max(1.0, d), 2)

    def _ensure_sources(self) -> None:
        dpr = self._cur_dpr()
        if dpr == self._dpr and self._out is not None:
            return
        self._dpr = dpr
        wpx, hpx = int(round(THUMB_W * dpr)), int(round(THUMB_H * dpr))
        self._out, self._inn = _pair_images(self.pair, self.flip, wpx, hpx)
        self._cur = None

    def _key(self):
        return (self.preset, self.pair, self.flip, self._dpr)

    def _render_prog(self, prog: float):
        a, b = self._out, self._inn
        if prog <= 0.0:
            img = a
        elif prog >= 1.0:
            img = b
        else:
            try:
                img = compose_standalone_transition(
                    prog, self.preset, a, b, a.width(), a.height())
            except Exception:
                logger.debug("thumb compose failed: %s", self.preset, exc_info=True)
                img = None
        if img is None or img.isNull():
            return None
        pix = QPixmap.fromImage(img)
        pix.setDevicePixelRatio(self._dpr)
        return pix

    def static_pixmap(self):
        self._ensure_sources()
        key = self._key()
        pix = _STATIC.get(key)
        if pix is None:
            pix = self._render_prog(self._static_p)
            if pix is not None:
                _STATIC[key] = pix
        return pix

    def _frame(self, idx: int, lst: list):
        pix = lst[idx]
        if pix is None:
            pix = self._render_prog(_prog_of_frame(idx))
            if pix is not None:
                _FRAMES.store(self._key(), idx, pix)
        return pix

    def _prefetch(self, lst: list, start_idx: int, t0: float) -> None:
        n = len(lst)
        for k in range(1, n):
            if time.perf_counter() - t0 >= TICK_RENDER_BUDGET_S:
                return
            j = (start_idx + k) % n
            if lst[j] is None:
                self._frame(j, lst)

    # ── playback ──

    def set_playing(self, on):
        on = bool(on)
        if on == self._playing and (self._timer.isActive() == on):
            return
        self._playing = on
        if on:
            if self.domain == "visual":
                self._ensure_sources()
                # continue from the static frame so hover does not jump
                self._pos = HOLD_START_TICKS + int(round(self._static_p * (ANIM_FRAMES - 1)))
                self._advance(step=0)
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
            self._prog = self._static_p
            self._cur = None
            self.update()

    def is_playing(self) -> bool:
        return self._playing

    def hideEvent(self, event):
        self._timer.stop()
        self._playing = False
        self._prog = self._static_p
        self._cur = None
        super().hideEvent(event)

    def _advance(self, step: int = 1) -> None:
        t0 = time.perf_counter()
        if self.domain == "audio":
            self._prog = (self._prog + 0.026 * step) % 1.0
        else:
            self._pos = (self._pos + step) % _loop_ticks()
            idx = _frame_of_tick(self._pos)
            lst = _FRAMES.frames(self._key(), ANIM_FRAMES)
            self._cur = self._frame(idx, lst)
            self._prog = _prog_of_frame(idx)
            self._prefetch(lst, idx, t0)
        _PERF["tick_ms"].append((time.perf_counter() - t0) * 1000.0)
        self.update()

    def _tick(self):
        self._advance(step=1)

    # ── paint ──

    def paintEvent(self, event):
        t0 = time.perf_counter()
        from cutora.ui.theme.tokens import current
        tok = current()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        w, h = float(self.width()), float(self.height())
        clip_path = QPainterPath()
        clip_path.addRoundedRect(QRectF(0.0, 0.0, w, h), 5.0, 5.0)
        p.save()
        p.setClipPath(clip_path)
        if self.domain == "audio":
            self._paint_audio(p, tok)
        else:
            self._paint_video(p)
        self._paint_badges(p, tok)
        p.restore()
        if self._playing or self.selected:
            border_col = QColor(tok.accent_hi)
        elif self.hovered:
            border_col = _qc(tok.accent, 200)
        else:
            border_col = QColor(255, 255, 255, 26)
        p.setPen(QPen(border_col, 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(0.5, 0.5, w - 1.0, h - 1.0), 5.0, 5.0)
        p.end()
        _PERF["paint_ms"].append((time.perf_counter() - t0) * 1000.0)

    def _paint_video(self, painter):
        self._ensure_sources()
        pix = None
        if self._playing and self._cur is not None:
            pix = self._cur
        elif abs(self._prog - self._static_p) < 1e-6:
            pix = self.static_pixmap()
        else:
            pix = self._render_prog(self._prog)
        if pix is not None and not pix.isNull():
            painter.drawPixmap(QRectF(0.0, 0.0, float(THUMB_W), float(THUMB_H)), pix,
                               QRectF(0.0, 0.0, float(pix.width()), float(pix.height())))
        else:
            painter.fillRect(self.rect(), QColor("#2f3b4a"))

    def _paint_audio(self, painter, tok):
        w, h = float(self.width()), float(self.height())
        g = QLinearGradient(0, 0, 0, h)
        g.setColorAt(0.0, QColor(tok.bg1))
        g.setColorAt(1.0, QColor(tok.bg0))
        painter.fillRect(self.rect(), QBrush(g))
        mid = h * 0.5
        painter.setPen(QPen(QColor(255, 255, 255, 18), 1.0))
        painter.drawLine(QPointF(0.0, mid), QPointF(w, mid))
        prog = self._prog
        amp_out = 1.0 - prog
        amp_in = prog
        if self.preset == "audio_xfade_equal":
            amp_out = math.cos(prog * math.pi / 2.0)
            amp_in = math.sin(prog * math.pi / 2.0)
        elif self.preset == "audio_fade_in":
            amp_out = 0.0
            amp_in = prog
        elif self.preset == "audio_fade_out":
            amp_out = 1.0 - prog
            amp_in = 0.0
        elif self.preset == "audio_seam_fade":
            edge = 0.12
            if prog < edge:
                amp_out, amp_in = 1.0 - prog / edge, 0.0
            elif prog > 1.0 - edge:
                amp_out, amp_in = 0.0, (prog - (1.0 - edge)) / edge
            else:
                amp_out, amp_in = 0.0, 1.0
        painter.setPen(QPen(QColor("#6ab0d6"), 1.4))
        for i in range(int(w) - 1):
            t = i / max(1.0, w - 1)
            y = mid - math.sin(t * math.pi * 4) * (h * 0.32) * amp_out
            painter.drawLine(int(i), int(mid), int(i), int(y))
        painter.setPen(QPen(QColor("#e0a85c"), 1.4))
        for i in range(int(w) - 1):
            t = i / max(1.0, w - 1)
            y = mid - math.sin(t * math.pi * 4 + 0.6) * (h * 0.32) * amp_in
            painter.drawLine(int(i), int(mid), int(i), int(y))

    def star_rect(self) -> QRectF:
        s = float(self.STAR_SIZE)
        return QRectF(float(self.width()) - s - 4.0, 4.0, s, s)

    def _paint_badges(self, p: QPainter, tok) -> None:
        w, h = float(self.width()), float(self.height())
        font = QFont(self.font())
        font.setPixelSize(11)
        font.setWeight(QFont.Weight.DemiBold)
        p.setFont(font)
        fm = p.fontMetrics()
        pill_h = 16.0
        if self.duration_text:
            tw = float(fm.horizontalAdvance(self.duration_text))
            r = QRectF(w - tw - 12.0 - 4.0, h - pill_h - 4.0, tw + 12.0, pill_h)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(12, 10, 8, 178))
            p.drawRoundedRect(r, 5.0, 5.0)
            p.setPen(QColor(tok.fg0))
            p.drawText(r, int(Qt.AlignmentFlag.AlignCenter), self.duration_text)
        if self.has_sfx:
            r = QRectF(4.0, h - pill_h - 4.0, 20.0, pill_h)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(12, 10, 8, 178))
            p.drawRoundedRect(r, 5.0, 5.0)
            _draw_speaker(p, QRectF(r.x() + 4.0, r.y() + 3.0, 12.0, 10.0), QColor(tok.accent_hi))
        if self.favorite or self.hovered:
            sr = self.star_rect()
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(12, 10, 8, 150))
            p.drawEllipse(sr)
            poly = _star_polygon(sr.center().x(), sr.center().y() + 0.4, 6.4, 2.7)
            if self.favorite:
                p.setBrush(QColor(tok.accent_hi))
                p.setPen(QPen(QColor(tok.accent_hi), 0.8))
            else:
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(QColor(255, 255, 255, 220), 1.1))
            p.drawPolygon(poly)


class TransitionCard(QFrame):
    apply_requested = Signal(str, str, float)
    favorite_toggled = Signal(str, bool)
    used = Signal(str, str)

    def __init__(self, label, preset, domain="visual", parent=None, pair=None,
                 flip=False, favorite=False):
        super().__init__(parent)
        self.label_text = str(label or "")
        self.preset = str(preset)
        self.domain = "audio" if domain == "audio" else "visual"
        self.category = _preset_category(self.preset, self.domain)
        if self.domain == "audio":
            self.duration = audio_transition_duration(self.preset)
            self.sfx = ""
            meta = {}
        else:
            self.duration = _recommended_duration(self.preset)
            self.sfx = _recommended_sfx(self.preset)
            meta = _meta_of(self.preset) if _catalog_has(self.preset) else {}
        self.hot = bool(meta.get("hot", False))
        self.keywords = str(meta.get("keywords") or "")
        if pair is None:
            pair, flip = _sample_pair_for(self.preset, self.category, 0)
        self.setObjectName("transitionCard")
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setFixedSize(CARD_W, CARD_H)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(CARD_PAD, CARD_PAD, CARD_PAD, CARD_PAD)
        lay.setSpacing(4)
        self.thumb = TransitionThumb(self.preset, self.domain, pair=pair, flip=flip)
        self.thumb.duration_text = _fmt_dur(self.duration)
        self.thumb.has_sfx = bool(self.sfx)
        self.thumb.favorite = bool(favorite)
        self._selected = False
        self._hovered = False
        lay.addWidget(self.thumb, 0, Qt.AlignmentFlag.AlignHCenter)
        self.caption = QLabel(tr(label))
        self.caption.setObjectName("transitionCaption")
        self.caption.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        self.caption.setWordWrap(False)
        self.caption.setFixedHeight(18)
        lay.addWidget(self.caption)
        self.search_blob = " ".join([
            self.label_text, tr(label), self.preset, self.preset.replace("_", " "),
            pinyin_initials(self.label_text), self.keywords,
        ]).lower()
        self.setToolTip(self._tooltip())

    def _tooltip(self) -> str:
        name = tr(self.label_text)
        line2 = tr("推荐时长") + " " + _fmt_dur(self.duration)
        if self.sfx:
            line2 += " · " + tr("推荐音效") + ": " + sfx_label(self.sfx)
        return "%s (%s)\n%s\n%s" % (
            name, self.preset, line2,
            tr("双击或拖拽到视频接缝、视频片段、图片图层或文字图层"))

    # ── state ──

    def set_favorite(self, on: bool) -> None:
        self.thumb.favorite = bool(on)
        self.thumb.update()

    def is_favorite(self) -> bool:
        return bool(self.thumb.favorite)

    def _update_card_style(self):
        self.thumb.hovered = self._hovered
        self.thumb.selected = self._selected
        self.thumb.update()
        self.update()

    def paintEvent(self, event):
        from cutora.ui.theme.tokens import current
        tok = current()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(0.5, 0.5, float(self.width()) - 1.0, float(self.height()) - 1.0)
        if self._selected:
            bg, pen = QColor(tok.bg3), QPen(QColor(tok.accent_hi), 1.0)
        elif self._hovered:
            bg, pen = QColor(tok.bg3), QPen(_qc(tok.accent, 170), 1.0)
        else:
            bg, pen = QColor(tok.bg2), QPen(_qc(tok.border, 110), 1.0)
        p.setPen(pen)
        p.setBrush(bg)
        p.drawRoundedRect(r, 8.0, 8.0)
        p.end()

    def enterEvent(self, event):
        self._hovered = True
        self.thumb.set_playing(True)
        self._update_card_style()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        if not self._selected:
            self.thumb.set_playing(False)
        self._update_card_style()
        if event is not None:
            super().leaveEvent(event)

    def set_selected(self, on):
        self._selected = bool(on)
        self.thumb.set_playing(self._selected or self.underMouse())
        self._update_card_style()

    # ── interaction ──

    def mime_payload(self) -> dict:
        return {
            "preset": self.preset,
            "domain": self.domain,
            "duration": float(self.duration),
        }

    def build_mime(self) -> QMimeData:
        mime = QMimeData()
        payload = json.dumps(self.mime_payload(), ensure_ascii=False)
        mime.setData(TRANSITION_MIME, payload.encode("utf-8"))
        mime.setText(self.preset)
        return mime

    def _on_star(self, pos) -> bool:
        try:
            local = QPointF(pos) - QPointF(self.thumb.pos())
        except Exception:
            return False
        return self.thumb.star_rect().adjusted(-3, -3, 3, 3).contains(local)

    def toggle_favorite(self) -> None:
        on = not self.is_favorite()
        self.set_favorite(on)
        self.favorite_toggled.emit(self.preset, on)

    def mouseDoubleClickEvent(self, event):
        if event is not None and self._on_star(event.position()):
            event.accept()
            return
        self.apply_requested.emit(self.preset, self.domain, float(self.duration))
        self.used.emit(self.preset, self.domain)
        if event is not None:
            event.accept()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        if self._on_star(event.position()):
            self.toggle_favorite()
            event.accept()
            return
        drag = QDrag(self)
        drag.setMimeData(self.build_mime())
        pix = QPixmap(self.thumb.size() * self.thumb._dpr if self.thumb._dpr > 1 else self.thumb.size())
        pix.setDevicePixelRatio(max(1.0, self.thumb._dpr))
        pix.fill(Qt.GlobalColor.transparent)
        self.thumb.render(pix)
        drag.setPixmap(pix)
        drag.setHotSpot(QPoint(THUMB_W // 2, THUMB_H // 2))
        if drag.exec(Qt.DropAction.CopyAction) == Qt.DropAction.CopyAction:
            self.used.emit(self.preset, self.domain)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        act_apply = menu.addAction(tr("应用"))
        act_fav = menu.addAction(tr("取消收藏") if self.is_favorite() else tr("收藏"))
        chosen = menu.exec(event.globalPos())
        if chosen is act_apply:
            self.apply_requested.emit(self.preset, self.domain, float(self.duration))
            self.used.emit(self.preset, self.domain)
        elif chosen is act_fav:
            self.toggle_favorite()


class _FlowLayout(QLayout):
    """Wrapping row layout for the category chips."""

    def __init__(self, parent=None, spacing=4):
        super().__init__(parent)
        self._items = []
        self._sp = int(spacing)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for it in self._items:
            size = size.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _do_layout(self, rect, test_only):
        m = self.contentsMargins()
        eff = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line_h = eff.x(), eff.y(), 0
        for it in self._items:
            w = it.widget()
            if w is not None and not w.isVisibleTo(w.parentWidget()):
                continue
            hint = it.sizeHint()
            nx = x + hint.width() + self._sp
            if nx - self._sp > eff.right() + 1 and line_h > 0:
                x = eff.x()
                y += line_h + self._sp
                nx = x + hint.width() + self._sp
                line_h = 0
            if not test_only:
                it.setGeometry(QRect(QPoint(x, y), hint))
            x = nx
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y() + m.bottom()


class TransitionPanel(QWidget):
    apply_requested = Signal(str, str, float)

    def __init__(self, parent=None):
        t_build = time.perf_counter()
        super().__init__(parent)
        self.setObjectName("transitionPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        settings = _load_settings()
        favs = settings.get(SETTING_FAVORITES)
        self._favorites = [str(x) for x in favs] if isinstance(favs, list) else []
        rec = settings.get(SETTING_RECENT)
        self._recent = [str(x) for x in rec][:RECENT_MAX] if isinstance(rec, list) else []
        self._style_sig = None
        self._apply_panel_style()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        # 顶部：搜索 + 附带音效
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(0, 0, 0, 0)
        top_bar.setSpacing(8)
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText(tr("搜索转场：名称 / 拼音首字母 / 英文"))
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.textChanged.connect(self._on_filter_changed)
        top_bar.addWidget(self._search_edit, 1)
        self._sfx_check = QCheckBox(tr("附带音效"))
        self._sfx_check.setToolTip(tr("拖入转场时同时放入推荐的转场音效"))
        self._sfx_check.setChecked(self._initial_sfx(settings))
        self._sfx_check.toggled.connect(self._on_sfx_toggled)
        top_bar.addWidget(self._sfx_check, 0)
        lay.addLayout(top_bar)

        # 构建卡片（先建卡，才知道哪些分类非空）
        self._video_cards = []
        self._audio_cards = []
        self._recent_cards = []
        fav_set = set(self._favorites)
        cat_counter: dict = {}
        for label, preset in VIDEO_ITEMS:
            cat = _preset_category(preset, "visual")
            idx = cat_counter.get(cat, 0)
            cat_counter[cat] = idx + 1
            pair, flip = _sample_pair_for(preset, cat, idx)
            card = TransitionCard(label, preset, "visual", pair=pair, flip=flip,
                                  favorite=preset in fav_set)
            self._wire_card(card)
            self._video_cards.append(card)
        for label, preset in AUDIO_ITEMS:
            card = TransitionCard(label, preset, "audio", favorite=preset in fav_set)
            self._wire_card(card)
            self._audio_cards.append(card)
        if not any(c.hot for c in self._video_cards):
            hot_fb = set(_FALLBACK_HOT)
            for c in self._video_cards:
                c.hot = c.preset in hot_fb

        # 分类标签（热门 / 全部 / 目录分类… / 收藏）
        self._cat_buttons = {}
        chips_host = QWidget()
        chips_host.setObjectName("transitionChips")
        self._chips = _FlowLayout(chips_host, spacing=4)
        self._chips.setContentsMargins(0, 0, 0, 0)
        present = {c.category for c in self._video_cards} | {"audio"}
        tabs = []
        for cid, name in _catalog_categories():
            if cid == "hot":
                tabs.append((cid, name))
                tabs.append((_ALL_TAB, "全部"))
            elif cid in present:
                tabs.append((cid, name))
        if _ALL_TAB not in [t[0] for t in tabs]:
            tabs.insert(0, (_ALL_TAB, "全部"))
        tabs.append((_FAV_TAB, "★ 收藏"))
        for cid, name in tabs:
            btn = QPushButton(tr(name))
            btn.setObjectName("transitionChip")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.clicked.connect(lambda _checked=False, c=cid: self._set_category(c))
            self._cat_buttons[cid] = btn
            self._chips.addWidget(btn)
        saved_tab = str(settings.get(SETTING_TAB) or "")
        self._active_category = saved_tab if saved_tab in self._cat_buttons else (
            "hot" if "hot" in self._cat_buttons else _ALL_TAB)
        self._style_cat_buttons()
        lay.addWidget(chips_host)

        hint_lbl = QLabel(tr("双击或拖到「视频接缝 / 片段 / 图片 / 文字图层」应用 · 悬停预览 · 点 ☆ 收藏"))
        hint_lbl.setObjectName("transitionHint")
        hint_lbl.setWordWrap(True)
        lay.addWidget(hint_lbl)

        self._video_grid = None
        self._audio_grid = None
        self._last_cols = 0
        self._recent_box, self._recent_grid, self._recent_head = self._section(tr("最近使用"))
        self._video_box, self._video_grid, self._video_head = self._section(
            "%s (%d)" % (tr("视频转场"), len(self._video_cards)))
        self._audio_box, self._audio_grid, self._audio_head = self._section(
            "%s (%d)" % (tr("音频转场"), len(self._audio_cards)))
        self._empty_lbl = QLabel(tr("没有匹配的转场"))
        self._empty_lbl.setObjectName("transitionHint")
        self._empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_lbl.setVisible(False)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setObjectName("transitionInner")
        inner_lay = QVBoxLayout(inner)
        inner_lay.setContentsMargins(0, 0, 0, 0)
        inner_lay.setSpacing(12)
        inner_lay.addWidget(self._recent_box)
        inner_lay.addWidget(self._video_box)
        inner_lay.addWidget(self._audio_box)
        inner_lay.addWidget(self._empty_lbl)
        inner_lay.addStretch(1)
        self._scroll.setWidget(inner)
        self._scroll.viewport().installEventFilter(self)
        lay.addWidget(self._scroll, 1)
        self._rebuild_recent_cards()
        self._reflow_grids(force=True)
        self.build_ms = (time.perf_counter() - t_build) * 1000.0
        logger.info(
            "[TransitionPanel] built %d video + %d audio cards in %.1f ms (samples=%s)",
            len(self._video_cards), len(self._audio_cards), self.build_ms,
            "ok" if samples_available() else "missing")

    # ── style ──

    def _apply_panel_style(self):
        from cutora.ui.theme.tokens import current
        tok = current()
        sig = (tok.bg1, tok.bg2, tok.bg3, tok.fg0, tok.fg1, tok.fg2, tok.border,
               tok.accent, tok.accent_hi)
        if sig == self._style_sig:
            return
        self._style_sig = sig
        acc = QColor(tok.accent)
        acc_bg = "rgba(%d, %d, %d, 46)" % (acc.red(), acc.green(), acc.blue())
        self.setStyleSheet(
            f"QWidget#transitionPanel, QWidget#transitionInner {{ background-color: {tok.bg1}; }}"
            f"QLabel#transitionCaption {{ color: {tok.fg0}; font-size: 12px;"
            f" background: transparent; border: none; }}"
            f"QLabel#transitionSection {{ color: {tok.fg1}; font-size: 12px;"
            f" font-weight: 600; background: transparent; }}"
            f"QLabel#transitionHint {{ color: {tok.fg2}; font-size: 11px;"
            f" background: transparent; }}"
            f"QPushButton#transitionChip {{ background-color: {tok.bg1}; color: {tok.fg1};"
            f" border: 1px solid {tok.border}; border-radius: 8px; padding: 2px 8px;"
            f" font-size: 12px; min-height: 18px; }}"
            f"QPushButton#transitionChip:hover {{ background-color: {tok.bg2}; color: {tok.fg0};"
            f" border: 1px solid {tok.accent_dim}; }}"
            f"QPushButton#transitionChip:pressed {{ background-color: {tok.bg3}; }}"
            f"QPushButton#transitionChip:checked {{ background-color: {acc_bg};"
            f" color: {tok.accent_hi}; border: 1px solid {tok.accent}; font-weight: 600; }}"
        )

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.StyleChange, QEvent.Type.PaletteChange) \
                and hasattr(self, "_style_sig"):
            self._apply_panel_style()

    def _style_cat_buttons(self):
        for cid, btn in self._cat_buttons.items():
            btn.blockSignals(True)
            btn.setChecked(cid == self._active_category)
            btn.blockSignals(False)

    # ── settings-backed state ──

    def _initial_sfx(self, settings: dict) -> bool:
        mod = _sfx_module()
        if mod is not None and callable(getattr(mod, "is_enabled", None)):
            return sfx_enabled()
        return bool(settings.get(SETTING_SFX, False))

    def _on_sfx_toggled(self, on: bool):
        set_sfx_enabled(bool(on))

    def sfx_enabled(self) -> bool:
        return bool(self._sfx_check.isChecked())

    def favorites(self) -> list:
        return list(self._favorites)

    def recent(self) -> list:
        return list(self._recent)

    def set_favorite(self, preset: str, on: bool) -> None:
        p = str(preset or "")
        if not p:
            return
        if on and p not in self._favorites:
            self._favorites.append(p)
        elif not on and p in self._favorites:
            self._favorites.remove(p)
        else:
            return
        _save_settings_patch({SETTING_FAVORITES: list(self._favorites)})
        for card in self._all_cards():
            if card.preset == p:
                card.set_favorite(on)
        if self._active_category == _FAV_TAB:
            self._reflow_grids(force=True)

    def _on_card_favorite(self, preset: str, on: bool):
        self.set_favorite(preset, on)

    def note_used(self, preset: str, domain: str = "visual") -> None:
        p = str(preset or "")
        if not p:
            return
        if self._recent and self._recent[0] == p:
            return
        self._recent = [p] + [x for x in self._recent if x != p]
        self._recent = self._recent[:RECENT_MAX]
        _save_settings_patch({SETTING_RECENT: list(self._recent)})
        # the emitting card may be a recent card: rebuild after its handler returns
        QTimer.singleShot(0, self._after_recent_change)

    def _after_recent_change(self):
        self._rebuild_recent_cards()
        self._reflow_grids(force=True)

    def _wire_card(self, card: TransitionCard) -> None:
        card.apply_requested.connect(self.apply_requested)
        card.favorite_toggled.connect(self._on_card_favorite)
        card.used.connect(self.note_used)

    def _all_cards(self):
        return self._video_cards + self._audio_cards + self._recent_cards

    def _rebuild_recent_cards(self):
        for card in self._recent_cards:
            card.setParent(None)
            card.deleteLater()
        self._recent_cards = []
        by_preset = {c.preset: c for c in self._video_cards + self._audio_cards}
        fav_set = set(self._favorites)
        for p in self._recent:
            src = by_preset.get(p)
            if src is None:
                continue
            card = TransitionCard(src.label_text, src.preset, src.domain,
                                  pair=src.thumb.pair, flip=src.thumb.flip,
                                  favorite=p in fav_set)
            card.hot = src.hot
            self._wire_card(card)
            self._recent_cards.append(card)

    # ── layout ──

    def _set_category(self, cat_id: str):
        self._active_category = str(cat_id or _ALL_TAB)
        self._style_cat_buttons()
        _save_settings_patch({SETTING_TAB: self._active_category})
        self._reflow_grids(force=True)
        try:
            self._scroll.verticalScrollBar().setValue(0)
        except Exception:
            pass

    def active_category(self) -> str:
        return self._active_category

    def _on_filter_changed(self, _text: str = ""):
        self._reflow_grids(force=True)

    def sizeHint(self):
        return QSize(340, 520)

    def minimumSizeHint(self):
        return QSize(220, 240)

    def video_count(self) -> int:
        return len(self._video_cards)

    def audio_count(self) -> int:
        return len(self._audio_cards)

    def set_active_preset(self, preset):
        want = str(preset or "")
        for card in self._all_cards():
            card.set_selected(card.preset == want)

    def _section(self, title):
        box = QWidget()
        box_lay = QVBoxLayout(box)
        box_lay.setContentsMargins(0, 0, 0, 0)
        box_lay.setSpacing(6)
        head = QLabel(title)
        head.setObjectName("transitionSection")
        box_lay.addWidget(head)
        grid_host = QWidget()
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(GRID_SPACING)
        grid.setVerticalSpacing(GRID_SPACING)
        grid.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        box_lay.addWidget(grid_host)
        return box, grid, head

    def _compute_cols(self) -> int:
        sc = getattr(self, "_scroll", None)
        avail_w = int(sc.width() or 0) - 14 if sc is not None else 0
        if avail_w <= 40:
            avail_w = max(int(self.width() or 0) - 30, 300)
        return max(1, min(8, (avail_w + GRID_SPACING) // (CARD_W + GRID_SPACING)))

    def _card_matches(self, card: TransitionCard, q: str, cat: str) -> bool:
        if q:
            # search is global: every whitespace token must hit the card's text
            return all(tok in card.search_blob for tok in q.split())
        if cat == _ALL_TAB:
            return True
        if cat == "hot":
            return bool(card.hot)
        if cat == _FAV_TAB:
            return card.preset in self._favorites
        return card.category == cat

    def _populate_grid(self, grid: QGridLayout, cards: list, cols: int, q: str, cat: str,
                       limit: int | None = None) -> int:
        while grid.count():
            grid.takeAt(0)
        vis_idx = 0
        for card in cards:
            ok = self._card_matches(card, q, cat) and (limit is None or vis_idx < limit)
            card.setVisible(ok)
            if ok:
                grid.addWidget(card, vis_idx // cols, vis_idx % cols)
                vis_idx += 1
        rows = (vis_idx + cols - 1) // cols if vis_idx > 0 else 0
        gh = grid.parentWidget()
        if gh is not None:
            gh.setFixedHeight(rows * CARD_H + max(0, rows - 1) * GRID_SPACING)
        grid.activate()
        return vis_idx

    def matching_presets(self, query: str = "", cat: str | None = None) -> list:
        q = (query or "").strip().lower()
        c = self._active_category if cat is None else cat
        return [card.preset for card in self._video_cards + self._audio_cards
                if self._card_matches(card, q, c)]

    def _reflow_grids(self, force: bool = False):
        cols = self._compute_cols()
        if not force and cols == self._last_cols:
            return
        self._last_cols = cols
        q = (self._search_edit.text() or "").strip().lower()
        cat = self._active_category
        show_recent = (not q) and cat in (_ALL_TAB, "hot") and bool(self._recent_cards)
        r_vis = self._populate_grid(
            self._recent_grid, self._recent_cards, cols, "", _ALL_TAB,
            limit=cols if show_recent else 0)
        self._recent_box.setVisible(show_recent and r_vis > 0)
        v_vis = self._populate_grid(self._video_grid, self._video_cards, cols, q, cat)
        a_vis = self._populate_grid(self._audio_grid, self._audio_cards, cols, q, cat)
        self._video_head.setText(f"{tr('视频转场')} ({v_vis}/{len(self._video_cards)})")
        self._audio_head.setText(f"{tr('音频转场')} ({a_vis}/{len(self._audio_cards)})")
        self._video_box.setVisible(v_vis > 0)
        self._audio_box.setVisible(a_vis > 0)
        self._empty_lbl.setText(
            tr("还没有收藏：悬停卡片点右上角 ☆") if (cat == _FAV_TAB and not q)
            else tr("没有匹配的转场"))
        self._empty_lbl.setVisible(v_vis == 0 and a_vis == 0)
        for box in (self._recent_box, self._video_box, self._audio_box):
            if box.layout() is not None:
                box.layout().activate()
        if getattr(self, "_scroll", None) is not None and self._scroll.widget() is not None:
            inner = self._scroll.widget()
            if inner.layout() is not None:
                inner.layout().activate()

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Resize and getattr(self, "_scroll", None) is not None \
                and obj is self._scroll.viewport():
            self._reflow_grids(force=False)
        return super().eventFilter(obj, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow_grids(force=False)

    def showEvent(self, event):
        super().showEvent(event)
        self._apply_panel_style()
        self._reflow_grids(force=True)

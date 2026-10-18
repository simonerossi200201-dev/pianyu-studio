# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Qt-backed draw() implementation. Imported lazily from textfx.draw."""
from __future__ import annotations

import time

from dataclasses import replace

from PySide6.QtCore import QPoint, QRectF
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget

from cutora.core import preview_perf
from cutora.core.log import get_logger
from cutora.render import export_probe as _export_probe
from cutora.render.textfx.anim import (
    _anim_id,
    is_static_hold,
    transform_at,
)
from cutora.render.textfx.cache import ROI_CACHE
from cutora.render.textfx.layout import layout_event, segment_id
from cutora.render.tls import ThreadLocalMapping
from cutora.render.textfx.raster import raster_bg, raster_text
from cutora.render.textfx.spec import from_style

logger = get_logger("cutora.render.textfx")

_slow_frames = 0
_fast_frames = 0
_degraded = False
_degrade_logged = False
# Point-light export is 20.385 ms/frame (docs/plan/reports/Q41_投影性能.md).
# 12 ms guaranteed a degrade within 10 frames of any point light.
_PREVIEW_DEGRADE_MS = 24.0
_PREVIEW_RESTORE_MS = 6.0
_LAYOUT_CACHE = ThreadLocalMapping("fx_layout", dict)
_text_rasters = 0
_bg_rasters = 0
_subtitle_draws = 0
_layout_hits = 0
_layout_misses = 0
_WORD_HITS = ThreadLocalMapping("fx_word_hits", dict)


def reset_degrade() -> None:
    global _slow_frames, _fast_frames, _degraded, _degrade_logged
    _slow_frames = 0
    _fast_frames = 0
    _degraded = False
    _degrade_logged = False
    _LAYOUT_CACHE.clear()


def reset_draw_counters() -> None:
    global _text_rasters, _bg_rasters, _subtitle_draws
    global _layout_hits, _layout_misses
    _text_rasters = 0
    _bg_rasters = 0
    _subtitle_draws = 0
    _layout_hits = 0
    _layout_misses = 0
    ROI_CACHE.reset_stats()


def draw_counters() -> dict:
    return {
        "roi_cache_hits": int(ROI_CACHE.hits),
        "roi_cache_misses": int(ROI_CACHE.misses),
        "text_raster_calls": int(_text_rasters),
        "bg_raster_calls": int(_bg_rasters),
        "subtitle_draw_calls": int(_subtitle_draws),
        "layout_cache_hits": int(_layout_hits),
        "layout_cache_misses": int(_layout_misses),
    }


def _is_preview_painter(painter: QPainter) -> bool:
    return isinstance(painter.device(), QWidget)


def _style_opacity_scale(style: dict) -> tuple[float, float]:
    """Host opacity (0–255 or 0–1) and scale baked by keyframe.bake_host.

    Missing fields stay identity so legacy styles (no keyframes) are
    byte-identical to the pre-keyframe path.
    """
    raw_op = style.get("opacity") if isinstance(style, dict) else None
    if raw_op is None:
        op = 1.0
    else:
        try:
            v = float(raw_op)
        except (TypeError, ValueError):
            v = 255.0
        if v <= 1.0 + 1e-9:
            op = max(0.0, min(1.0, v))
        else:
            op = max(0.0, min(1.0, v / 255.0))
    raw_sc = style.get("scale") if isinstance(style, dict) else None
    if raw_sc is None:
        sc = 1.0
    else:
        try:
            sc = float(raw_sc)
        except (TypeError, ValueError):
            sc = 1.0
    return op, sc


def _note_slow(ms: float, is_preview: bool) -> bool:
    global _slow_frames, _fast_frames, _degraded, _degrade_logged
    if not is_preview:
        return False
    if _degraded:
        if ms < _PREVIEW_RESTORE_MS:
            _fast_frames += 1
        else:
            _fast_frames = 0
        if _fast_frames >= 60:
            _degraded = False
            _slow_frames = 0
            _fast_frames = 0
            logger.info("preview fx restored")
        return _degraded
    if ms > _PREVIEW_DEGRADE_MS:
        _slow_frames += 1
    else:
        _slow_frames = 0
    if _slow_frames >= 10:
        _degraded = True
        _fast_frames = 0
        if not _degrade_logged:
            logger.info("preview fx degraded (cheap shadow blur)")
            _degrade_logged = True
    return _degraded


def _mark_degraded(painter: QPainter) -> None:
    from cutora.ui.i18n import tr
    painter.save()
    try:
        painter.setPen(QColor("#F5D77A"))
        painter.drawText(12, 28, tr("⚡预览简化"))
    finally:
        painter.restore()


def _layout_fingerprint(style: dict, video_w: int, video_h: int):
    """Fields that move glyphs. Keep this cheap — it runs every frame."""
    return (
        style.get("pos_x"), style.get("pos_y"), style.get("fontsize"),
        style.get("anchor"), style.get("offset_x_pct"),
        style.get("offset_y_pct"), style.get("pos_x_pct"),
        style.get("pos_y_pct"), style.get("fontsize_pct"),
        style.get("font_family"), style.get("bold"), style.get("italic"),
        style.get("wrap_width_percent"),
        style.get("wrap_lines"), style.get("line_spacing"),
        style.get("writing_mode"), style.get("text_orientation"),
        int(video_w), int(video_h),
    )


def _layout_cached(event, style, spec, video_w: int, video_h: int):
    global _layout_hits, _layout_misses
    t_hash = time.perf_counter()
    digest = spec.cache_hash()
    if preview_perf.enabled():
        preview_perf.add_span("fx_hash", time.perf_counter() - t_hash)
    key = (
        segment_id(event), digest, int(video_w), int(video_h),
        str(event.get("text")), str(event.get("lines")),
        _layout_fingerprint(style, video_w, video_h),
    )
    hit = _LAYOUT_CACHE.get(key)
    if hit is not None:
        _layout_hits += 1
        return hit
    _layout_misses += 1
    lay = layout_event(event, style, spec, video_w, video_h)
    if len(_LAYOUT_CACHE) >= 32:
        _LAYOUT_CACHE.clear()
    _LAYOUT_CACHE[key] = lay
    return lay


def _emph_line_token(spec, t: float, event: dict):
    """before / frame-q / after, so the finished underline is a stable hold key."""
    if spec.emphasis.anim != "line_draw" or not (
            spec.emphasis.words or spec.emphasis.auto):
        return None
    from cutora.render.textfx.anim import LINE_DRAW_DUR_S
    from cutora.render.textfx.emphasis import detect_emphasis
    raw = event.get("edited_text")
    if raw is None:
        raw = event.get("text") or ""
    hits = detect_emphasis(str(raw), spec, lines=event.get("lines") or None)
    if not hits:
        return None
    words = event.get("words") or []
    starts = []
    for hit in hits:
        if hit.index < len(words) and isinstance(words[hit.index], dict):
            starts.append(float(words[hit.index].get("start") or 0.0))
        else:
            starts.append(float(event.get("start") or 0.0))
    if t < min(starts):
        return "before"
    if t > max(s + LINE_DRAW_DUR_S for s in starts):
        return "after"
    return int(round(float(t) * 60.0))


def _cache_key(layout, spec, t: float, event: dict, video_w: int, video_h: int,
               skip_blur: bool):
    # Anim xf is warped after cache. Path-stroke / emphasis bake t into the raster.
    line_tok = _emph_line_token(spec, t, event)
    bake = False
    if spec.emphasis.anim == "pop" and (
            spec.emphasis.words or spec.emphasis.auto):
        bake = True
    if _anim_id(spec.anim.in_name) in ("line_draw", "outline_to_fill", "tracking_in"):
        bake = True
    if bake and not is_static_hold(t, event, spec):
        anim_q: object = int(round(float(t) * 60.0))
    elif line_tok is not None:
        anim_q = ("ld", line_tok)
    else:
        anim_q = "hold"
    # Q139：正文本身也进 key。以前只靠 seg_id + 框尺寸，两条同 id 的字幕
    # （单字蹦切出来的词继承母句 id）框一样大就会拿到对方的光栅 ——
    # 画面上「Bible」写成了「Does」。同一段文字 key 不变，golden 不受影响。
    raw_text = event.get("edited_text")
    if raw_text is None:
        raw_text = event.get("text") or ""
    lines = event.get("lines")
    return (
        layout.seg_id,
        str(raw_text),
        str(lines) if isinstance(lines, list) else "",
        spec.cache_hash(),
        anim_q,
        int(video_w),
        int(video_h),
        int(layout.roi_w),
        int(layout.roi_h),
        layout.origin,
        bool(skip_blur),
    )


def _backdrop_id(backdrop, t: float) -> tuple:
    """Frame identity: QImage.cacheKey (stable on preview hold) + t ms."""
    stamp = int(round(float(t) * 1000.0))
    if backdrop is None:
        return ("none", stamp)
    try:
        if backdrop.isNull():
            return ("none", stamp)
        return (int(backdrop.cacheKey()), stamp)
    except Exception:
        return ("unk", stamp)


def _cached_layer(tag: str, key):
    return ROI_CACHE.get((tag,) + key)


def _store_layer(tag: str, key, image, origin) -> None:
    if image is not None:
        ROI_CACHE.put((tag,) + key, image, origin)


def _raster_layers(layout, spec, style, t, event, backdrop, video_w, video_h,
                   skip_blur, xf=None):
    global _text_rasters, _bg_rasters
    probe = _export_probe.on()
    t_enter = time.perf_counter()
    key = _cache_key(layout, spec, t, event, video_w, video_h, skip_blur)
    origin = layout.origin
    t_get = time.perf_counter()
    if probe:
        _export_probe.add_ms("other", t_get - t_enter)
    text_hit = _cached_layer("text", key)
    if text_hit is None:
        t_m = time.perf_counter()
        text = raster_text(layout, spec, style, skip_blur, t=t, xf=xf)
        if probe:
            _export_probe.add_family("text", time.perf_counter() - t_m)
            _export_probe.add_ms("other", t_m - t_get)
        _text_rasters += 1
        _store_layer("text", key, text, origin)
    else:
        text, origin = text_hit
        if probe:
            _export_probe.add_ms("other", time.perf_counter() - t_get)
    bg = None
    if spec.bg.kind() == "glass":
        gkey = key + (_backdrop_id(backdrop, t),)
        t_get = time.perf_counter()
        glass_hit = _cached_layer("glass", gkey)
        if glass_hit is None:
            t_m = time.perf_counter()
            bg = raster_bg(layout, spec, backdrop, video_w, video_h, skip_blur)
            if probe:
                _export_probe.add_family("glass", time.perf_counter() - t_m)
                _export_probe.add_ms("other", t_m - t_get)
            _bg_rasters += 1
            _store_layer("glass", gkey, bg, origin)
        else:
            bg, origin = glass_hit
            if probe:
                _export_probe.add_ms("other", time.perf_counter() - t_get)
    elif spec.bg.kind() in ("box", "texture", "bubble"):
        tag = spec.bg.kind()
        t_get = time.perf_counter()
        box_hit = _cached_layer(tag, key)
        if box_hit is None:
            t_m = time.perf_counter()
            bg = raster_bg(layout, spec, backdrop, video_w, video_h, skip_blur)
            if probe:
                _export_probe.add_family(tag, time.perf_counter() - t_m)
                _export_probe.add_ms("other", t_m - t_get)
            _bg_rasters += 1
            _store_layer(tag, key, bg, origin)
        else:
            bg, origin = box_hit
            if probe:
                _export_probe.add_ms("other", time.perf_counter() - t_get)
    return bg, text, origin


def word_hits_for(seg_idx) -> list:
    """Runtime word-hit cache keyed by segment index. Not stored on the event."""
    try:
        return list(_WORD_HITS.get(int(seg_idx)) or [])
    except (TypeError, ValueError):
        return []


def _fill_word_hits(event: dict, layout, char_offset: int = 0, seg_idx=None) -> list:
    """v2 per-word boxes so shift-click / inline edit can see v2 glyphs.

    Hits go to a sidecar table keyed by seg index — never onto the shared
    subtitle dict (that used to put QRectF into the project and kill autosave).
    """
    raw = event.get("edited_text")
    if raw is None:
        raw = event.get("text") or ""
    text = str(raw)
    hits = []
    cursor = 0
    for line in layout.lines:
        for run in line.runs:
            word = str(run.text or "")
            token = word.strip() or word
            idx = text.find(token, cursor) if token else cursor
            if idx < 0:
                idx = cursor
            hits.append({
                "rect": QRectF(run.x, run.y, run.w, run.h),
                "word_idx": int(run.word_index),
                "char_start": int(idx + char_offset),
                "char_end": int(idx + len(token) + char_offset),
            })
            cursor = idx + max(1, len(token))
    if isinstance(seg_idx, int):
        _WORD_HITS[int(seg_idx)] = hits
    return hits


def _blit_layers(painter, bg, text, origin, xf, preview: bool) -> None:
    painter.save()
    try:
        # Geometry/alpha already baked by warp_layer_xf when xf is not identity.
        pt = QPoint(origin[0], origin[1])
        if bg is not None:
            painter.drawImage(pt, bg)
        painter.drawImage(pt, text)
        if preview and _degraded:
            _mark_degraded(painter)
    finally:
        painter.restore()


def draw(
    painter: QPainter,
    event: dict,
    style: dict,
    t: float,
    video_w: int,
    video_h: int,
    backdrop=None,
    hit_recorder=None,
    seg_idx=None,
    dynamic_layout_list=None,
    _hit_char_offset=0,
) -> QRectF:
    """Raster one subtitle event into an ROI and blit it through *painter*."""
    empty = QRectF()
    if painter is None or painter.paintEngine() is None:
        return empty
    if not isinstance(event, dict) or not isinstance(style, dict):
        return empty
    global _subtitle_draws
    t0 = time.perf_counter()
    spec = from_style(style)
    t_spec = time.perf_counter()
    layout = _layout_cached(event, style, spec, video_w, video_h)
    t_lay = time.perf_counter()
    _subtitle_draws += 1
    preview = _is_preview_painter(painter)
    skip_blur = preview and _degraded
    xf = transform_at(t, event, spec, layout)
    op, sc = _style_opacity_scale(style)
    if abs(op - 1.0) > 1e-6 or abs(sc - 1.0) > 1e-6:
        xf = replace(
            xf,
            alpha=float(xf.alpha) * op,
            scale_x=float(xf.scale_x) * sc,
            scale_y=float(xf.scale_y) * sc,
        )
    t_xf = time.perf_counter()
    bg, text, origin = _raster_layers(
        layout, spec, style, t, event, backdrop, video_w, video_h, skip_blur,
        xf=xf)
    t_ras = time.perf_counter()
    from cutora.render.textfx.word_fx import (
        apply_geom, apply_word_color, draw_box_highlight, draw_line_bar,
    )
    text = apply_word_color(text, layout, spec, t, event)
    bg = draw_box_highlight(bg, layout, spec, t, event, style=style)
    bg = draw_line_bar(bg, layout, spec, t, event, style=style)
    text = apply_geom(text, layout, spec, t, event, xf, video_w, video_h)
    if bg is not None:
        bg_xf = replace(xf, blur_sigma_em=0.0, extra_color=None)
        bg = apply_geom(bg, layout, spec, t, event, bg_xf, video_w, video_h)
    from cutora.render.textfx.raster import rotate_roi_around_text, style_rotation_deg
    rot = style_rotation_deg(style)
    text = rotate_roi_around_text(text, layout, rot)
    if bg is not None:
        bg = rotate_roi_around_text(bg, layout, rot)
    t_wfx = time.perf_counter()
    _blit_layers(painter, bg, text, origin, xf, preview)
    t_blit = time.perf_counter()
    _fill_word_hits(event, layout, _hit_char_offset, seg_idx=seg_idx)
    t_end = time.perf_counter()
    if _export_probe.on():
        _export_probe.add_ms("layout", t_lay - t_spec)
        _export_probe.add_ms("word_fx", t_wfx - t_ras)
        _export_probe.add_ms("layer_blit", t_blit - t_wfx)
        _export_probe.add_ms("other", (t_spec - t0) + (t_xf - t_lay) + (t_end - t_blit))
    dt = t_end - t0
    if preview_perf.enabled():
        preview_perf.add_span("fx_spec", t_spec - t0)
        preview_perf.add_span("fx_layout", t_lay - t_spec)
        preview_perf.add_span("fx_raster", t_ras - t_lay)
        preview_perf.add_span("draw_subtitle", dt)
    _note_slow(dt * 1000.0, preview)
    return QRectF(layout.hit_x, layout.hit_y, layout.hit_w, layout.hit_h)

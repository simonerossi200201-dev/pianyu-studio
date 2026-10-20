# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Track parent transform: clip transform first, then track.props.

Preview and export share compose_track_transform / paint_track_layer.
Identity props skip the offscreen layer so existing golden pixels stay put.
"""
from __future__ import annotations

import math

from cutora.core.log import get_logger

logger = get_logger("cutora.render.track_transform")

IDENTITY_TRACK_PROPS = {
    "x": 0.0,
    "y": 0.0,
    "scale": 1.0,
    "rotation": 0.0,
    "flip_x": False,
    "flip_y": False,
    "opacity": 1.0,
    "blend_mode": "normal",
}

_PATCH_KEYS = (
    "x", "y", "scale", "rotation", "flip_x", "flip_y", "opacity", "blend_mode",
)

_FLOAT_KEYS = ("x", "y", "scale", "rotation", "opacity")
_BOOL_KEYS = ("flip_x", "flip_y")


def default_track_props():
    return dict(IDENTITY_TRACK_PROPS)


def normalize_track_props(raw) -> dict:
    out = default_track_props()
    if not isinstance(raw, dict):
        return out
    for key in _FLOAT_KEYS:
        if key in raw and raw[key] is not None:
            try:
                out[key] = float(raw[key])
            except (TypeError, ValueError):
                logger.warning("track.props.%s 不是数字: %r", key, raw[key])
    for key in _BOOL_KEYS:
        if key in raw:
            out[key] = bool(raw[key])
    if "blend_mode" in raw and raw["blend_mode"]:
        out["blend_mode"] = str(raw["blend_mode"])
    if out["scale"] <= 0:
        logger.warning("track.props.scale=%s 非法，回退 1.0", out["scale"])
        out["scale"] = 1.0
    out["opacity"] = max(0.0, min(1.0, float(out["opacity"])))
    return out


def track_props_of(track) -> dict:
    return normalize_track_props(getattr(track, "props", None))


def track_needs_layer(track) -> bool:
    """Non-identity track props must composite to an offscreen layer first."""
    raw = getattr(track, "props", None)
    if not raw or raw == IDENTITY_TRACK_PROPS:
        return False
    props = normalize_track_props(raw)
    if abs(props["x"]) > 1e-9 or abs(props["y"]) > 1e-9:
        return True
    if abs(props["scale"] - 1.0) > 1e-9:
        return True
    if abs(props["rotation"]) > 1e-9:
        return True
    if props["flip_x"] or props["flip_y"]:
        return True
    if abs(props["opacity"] - 1.0) > 1e-9:
        return True
    if str(props["blend_mode"] or "normal") != "normal":
        return True
    return False


def _canvas_wh(canvas):
    if canvas is None:
        return 0.0, 0.0
    if isinstance(canvas, (tuple, list)) and len(canvas) >= 2:
        return float(canvas[0]), float(canvas[1])
    if isinstance(canvas, dict):
        return float(canvas.get("width") or 0), float(canvas.get("height") or 0)
    w = getattr(canvas, "width", 0)
    h = getattr(canvas, "height", 0)
    if callable(w):
        w = w()
    if callable(h):
        h = h()
    return float(w or 0), float(h or 0)


def compose_track_transform(clip_rect, track_props, canvas):
    """Apply track parent transform after the clip's own rect.

    clip_rect is already in canvas pixels (clip x/y/scale/rotation applied).
    Translation is additive: clip x=100 + track x=50 → 150.
    Scale / rotation / flip are around the canvas centre.
    """
    from PySide6.QtCore import QRectF

    props = normalize_track_props(track_props)
    x = float(props["x"])
    y = float(props["y"])
    scale = float(props["scale"])
    rotation = float(props["rotation"])
    flip_x = bool(props["flip_x"])
    flip_y = bool(props["flip_y"])
    rect = QRectF(clip_rect)
    if (
        abs(scale - 1.0) < 1e-12
        and abs(rotation) < 1e-12
        and not flip_x
        and not flip_y
    ):
        return QRectF(rect.x() + x, rect.y() + y, rect.width(), rect.height())

    cw, ch = _canvas_wh(canvas)
    cx, cy = cw / 2.0, ch / 2.0
    sx = -scale if flip_x else scale
    sy = -scale if flip_y else scale
    rad = math.radians(rotation)
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    corners = (
        (rect.left(), rect.top()),
        (rect.right(), rect.top()),
        (rect.right(), rect.bottom()),
        (rect.left(), rect.bottom()),
    )
    xs = []
    ys = []
    for px, py in corners:
        dx = (px - cx) * sx
        dy = (py - cy) * sy
        rx = dx * cos_a - dy * sin_a
        ry = dx * sin_a + dy * cos_a
        xs.append(rx + cx + x)
        ys.append(ry + cy + y)
    return QRectF(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


def blit_track_layer(painter, layer, track_props, canvas_w, canvas_h, luma_override=None):
    """Composite an already-drawn track layer with track opacity / blend / xform."""
    from PySide6.QtGui import QPainter
    from cutora.render.frame_renderer import BLEND_MODES, _BLEND_NORMAL

    if layer is None or layer.isNull() or painter is None:
        return
    from cutora.render.frame_renderer import is_luma_matte, luma_coverage_image, luma_deferred, luma_invert

    props = normalize_track_props(track_props)
    props_blend = str(props.get("blend_mode") or "normal")
    op = max(0.0, min(1.0, float(props["opacity"])))
    # 片段自己的亮度蒙版记在 luma_override 上。轨道合成的模式仍是 props。
    # 外层还要按模式叠这一张图时，这里只交出画面，不在透明图上挖空。
    use = props_blend
    if luma_override and is_luma_matte(luma_override) and not luma_deferred():
        use = str(luma_override)
    if is_luma_matte(use) and not luma_deferred():
        mask = luma_coverage_image(layer, invert=luma_invert(use), opacity=op)
        painter.save()
        try:
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
            cx = float(canvas_w) / 2.0
            cy = float(canvas_h) / 2.0
            painter.translate(cx + float(props["x"]), cy + float(props["y"]))
            rot = float(props["rotation"])
            if rot:
                painter.rotate(rot)
            sx = float(props["scale"]) * (-1.0 if props["flip_x"] else 1.0)
            sy = float(props["scale"]) * (-1.0 if props["flip_y"] else 1.0)
            if abs(sx - 1.0) > 1e-12 or abs(sy - 1.0) > 1e-12:
                painter.scale(sx, sy)
            painter.translate(-cx, -cy)
            painter.drawImage(0, 0, mask)
        finally:
            painter.restore()
        return
    # 亮度蒙版推迟到外层时，这张图按普通覆盖留下画面。其它模式与原来相同。
    blend_name = "normal" if is_luma_matte(props_blend) else props_blend
    painter.save()
    try:
        painter.setOpacity(painter.opacity() * op)
        painter.setCompositionMode(BLEND_MODES.get(blend_name, _BLEND_NORMAL))
        cx = float(canvas_w) / 2.0
        cy = float(canvas_h) / 2.0
        painter.translate(cx + float(props["x"]), cy + float(props["y"]))
        rot = float(props["rotation"])
        if rot:
            painter.rotate(rot)
        sx = float(props["scale"]) * (-1.0 if props["flip_x"] else 1.0)
        sy = float(props["scale"]) * (-1.0 if props["flip_y"] else 1.0)
        if abs(sx - 1.0) > 1e-12 or abs(sy - 1.0) > 1e-12:
            painter.scale(sx, sy)
        painter.translate(-cx, -cy)
        painter.drawImage(0, 0, layer)
    finally:
        painter.restore()


def paint_track_layer(painter, track, canvas_w, canvas_h, paint_clips):
    """Call paint_clips(painter). Non-identity tracks go through an offscreen layer."""
    from cutora.render.frame_renderer import take_luma_note

    if not track_needs_layer(track):
        paint_clips(painter)
        take_luma_note()
        return
    from PySide6.QtGui import QImage, QPainter
    w = int(canvas_w)
    h = int(canvas_h)
    if w <= 0 or h <= 0:
        logger.warning("track layer skipped: canvas %sx%s", w, h)
        paint_clips(painter)
        take_luma_note()
        return
    layer = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    layer.fill(0)
    lp = QPainter(layer)
    take_luma_note()
    try:
        lp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        paint_clips(lp)
        noted = take_luma_note()
    finally:
        lp.end()
    blit_track_layer(painter, layer, track_props_of(track), w, h, luma_override=noted)


def _coerce_patch_value(key, value):
    if key in _FLOAT_KEYS:
        return float(value)
    if key in _BOOL_KEYS:
        return bool(value)
    if key == "blend_mode":
        return str(value or "normal")
    if key == "name":
        return str(value or "")
    return value


def apply_track_props_patch(task, track_ids, patch):
    """Write patch onto every id. One call = one undoable command."""
    from cutora.core.commands import CommandError
    from cutora.model.track_guard import find_track

    ids = [str(t) for t in (track_ids or []) if str(t)]
    if not ids:
        raise CommandError("track.set_props 需要 track_ids")
    raw = dict(patch or {})
    unknown = [k for k in raw if k not in _PATCH_KEYS and k != "name"]
    if unknown:
        raise CommandError("未知轨道属性: %s" % ", ".join(unknown))
    applied = []
    for tid in ids:
        tr = find_track(task, track_id=tid)
        if tr is None:
            raise CommandError("找不到轨道 %s" % tid)
        props = track_props_of(tr)
        for key, value in raw.items():
            if key == "name":
                tr.name = _coerce_patch_value("name", value)
                continue
            try:
                props[key] = _coerce_patch_value(key, value)
            except (TypeError, ValueError) as exc:
                logger.exception("track.set_props 字段 %s 非法", key)
                raise CommandError("轨道属性 %s 非法: %s" % (key, exc)) from exc
        tr.props = normalize_track_props(props)
        tr.blend_mode = str(tr.props.get("blend_mode") or "normal")
        applied.append(tid)
    return {"track_ids": applied, "patch": dict(raw)}


def session_pinned_ids(timeline_widget) -> set:
    pins = getattr(timeline_widget, "_pinned_ids", None)
    if pins is None:
        return set()
    return set(str(x) for x in pins)


def toggle_session_pin(timeline_widget, track) -> bool:
    """Session thumbtack (not saved). Returns new pinned state."""
    tid = str(getattr(track, "id", "") or "")
    if not tid:
        return False
    pins = getattr(timeline_widget, "_pinned_ids", None)
    if pins is None:
        timeline_widget._pinned_ids = set()
        pins = timeline_widget._pinned_ids
    if tid in pins:
        pins.discard(tid)
        track.pinned = False
        return False
    pins.add(tid)
    track.pinned = True
    return True


def track_is_session_pinned(timeline_widget, track) -> bool:
    tid = str(getattr(track, "id", "") or "")
    if not tid:
        return False
    return tid in session_pinned_ids(timeline_widget)


def edit_track_ids(timeline_widget, current_track) -> list:
    """Pinned set if the current track is pinned; otherwise just current."""
    cur = str(getattr(current_track, "id", "") or "")
    if not cur:
        return []
    pins = session_pinned_ids(timeline_widget)
    if cur in pins and pins:
        return list(pins)
    return [cur]

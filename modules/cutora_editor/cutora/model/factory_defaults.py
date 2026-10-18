# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Factory defaults for new objects and inspector reset.

One source, in the model / render layer. The property panel reads these;
it does not invent a second set of numbers.

Image position and size are not constants. ``image_fit_rect`` fits the
source inside the canvas (never upscales) and centers it. That rect is the
picture element (``elements[0]``). The overlay box stays the full canvas
(x/y = 0, w/h = canvas) so element x/y are canvas pixels — the same frame
canvas drag and hit-testing already use.
"""
from __future__ import annotations

import copy
import inspect
import uuid


def image_fit_rect(src_w, src_h, canvas_w, canvas_h) -> tuple[float, float, int, int]:
    """Fit ``src`` inside ``canvas``, never upscale, then center.

    Returns ``(x, y, w, h)`` in canvas pixels. This is the image element's
    rect inside an overlay box that covers the canvas (overlay x/y = 0,
    w/h = canvas). ``w`` / ``h`` are truncated the same way the old
    ``build_image_overlay`` placed the element.
    """
    tw = max(1, int(canvas_w))
    th = max(1, int(canvas_h))
    iw = max(1, int(src_w))
    ih = max(1, int(src_h))
    scale = min(tw / iw, th / ih, 1.0)
    dw = int(iw * scale)
    dh = int(ih * scale)
    dx = (tw - dw) / 2.0
    dy = (th - dh) / 2.0
    return dx, dy, dw, dh


def overlay_defaults(
    kind,
    *,
    canvas_w: int = 1080,
    canvas_h: int = 1920,
    image_wh: tuple[int, int] | None = None,
    path: str = "",
    start: float = 0.0,
    end: float | None = None,
    duration: float = 5.0,
    track_id=None,
    ov_id: str | None = None,
) -> dict:
    """The dict ``add_text_ov`` / ``add_img_ov`` / ``build_image_overlay`` share.

    ``kind`` is ``"text"`` or ``"image"``. The image overlay box is the
    canvas. The picture element uses ``image_fit_rect`` (source fitted
    inside the canvas, never upscaled, then centered) — not a fixed 300×300.
    """
    name = str(kind or "text")
    if name not in ("text", "image"):
        raise ValueError("overlay_defaults kind 必须是 text 或 image，收到 %r" % kind)
    t0 = float(start)
    t1 = float(end) if end is not None else t0 + float(duration)
    cw = max(1, int(canvas_w))
    ch = max(1, int(canvas_h))
    if name == "text":
        ov = _text_overlay_defaults(cw, ch, t0, t1)
    else:
        ov = _image_overlay_defaults(cw, ch, image_wh, path, t0, t1, ov_id)
    if track_id is not None:
        ov["_track_id"] = track_id
    return ov


# Q124：新建文本的框。以前是整块画布（x=0,y=0,w=画布宽,h=画布高），
# 八个手柄挂在画面四角，框和文字没有任何关系 —— 这就是「无法正常排版」。
# 现在：宽 = 画布 78%，水平居中，框中心落在画面 70% 高处（下三分之一），
# 高先按一行字估，第一次绘制 auto_height 会把它改成文字的真实高度。
TEXT_BOX_WIDTH_FRAC = 0.78
TEXT_BOX_ANCHOR_Y = 0.70
TEXT_LINE_HEIGHT_EM = 1.35


def default_text_box(canvas_w: int, canvas_h: int, *, fontsize: float = 80.0,
                     lines: int = 1) -> tuple[int, int, int, int]:
    """新建文本该放哪：(x, y, w, h)，全在画布里。纯算术，不碰字体。"""
    cw = max(1, int(canvas_w))
    ch = max(1, int(canvas_h))
    w = int(round(cw * TEXT_BOX_WIDTH_FRAC))
    h = int(round(float(fontsize) * TEXT_LINE_HEIGHT_EM * max(1, int(lines))))
    h = max(8, min(h, ch))
    x = int(round((cw - w) / 2.0))
    y = int(round(ch * TEXT_BOX_ANCHOR_Y - h / 2.0))
    y = max(0, min(y, ch - h))
    return x, y, w, h


def _text_overlay_defaults(cw: int, ch: int, start: float, end: float) -> dict:
    """No-preset text layer. Matches the fallback ``add_text_ov`` used to inline."""
    x, y, w, h = default_text_box(cw, ch, fontsize=80, lines=1)
    return {
        "type": "text",
        "start": float(start),
        "end": float(end),
        "x": x,
        "y": y,
        "w": w,
        "h": h,
        "rotation": 0,
        "opacity": 255,
        "blend_mode": "normal",
        "content_format": "plain",
        # 文本框不锁比例：拖边只改折行宽，高度由 auto_height 自己管
        "lock_aspect": False,
        "aspect_ratio": (float(w) / float(h)) if h else 1.0,
        "fontsize": 80,
        "font_family": "Arimo",
        "bold": True,
        "italic": False,
        "color": "#FFFFFF",
        "bg_enabled": False,
        "use_stroke": True,
        "stroke_color": "#000000",
        "stroke_width": 4,
        "content": "Generato da IA",
        "writing_mode": "horizontal",
        # 宽度用户定、高度跟着文字走（双向）。老工程存的是什么还是什么。
        "fit_mode": "auto_height",
        "text_layout": {
            "left_pad": 0,
            "right_pad": 0,
            "top_pad": 0,
            "bottom_pad": 0,
            "line_spacing": 0,
            "letter_spacing": 0,
            "align": "center",
            "valign": "top",
            "first_indent": 0,
            "para_spacing": 0,
        },
    }


def _image_overlay_defaults(cw, ch, image_wh, path, start, end, ov_id) -> dict:
    if image_wh and len(image_wh) >= 2:
        iw, ih = int(image_wh[0]), int(image_wh[1])
    else:
        iw, ih = 200, 200
    iw = max(1, iw)
    ih = max(1, ih)
    # The overlay box is the canvas. The picture itself is the element,
    # placed by image_fit_rect. Canvas drag and hit-testing treat element
    # x/y as canvas pixels inside that box (the box stays at 0,0).
    dx, dy, dw, dh = image_fit_rect(iw, ih, cw, ch)
    element = None
    if path:
        element = {
            "kind": "image",
            "path": path,
            "x": dx,
            "y": dy,
            "w": dw,
            "h": dh,
            "rotation": 0.0,
            "opacity": 255.0,
            "blend_mode": "normal",
            "lock_aspect": True,
        }
    return {
        "type": "image",
        "start": float(start),
        "end": float(end),
        "x": 0,
        "y": 0,
        "w": int(cw),
        "h": int(ch),
        "rotation": 0,
        "opacity": 255,
        "elements": [element] if element is not None else [],
        "scale": 1.0,
        "flip_x": False,
        "flip_y": False,
        "lock_aspect": True,
        "keep_aspect": True,
        "aspect_ratio": iw / float(ih),
        "blend_mode": "normal",
        "feather": 0,
        "transition_preset": "none",
        "transition_duration": 0.35,
        "id": ov_id or ("ov_%s" % uuid.uuid4().hex[:12]),
    }


def clip_defaults() -> dict:
    """Inspector-facing defaults: a fresh ``Clip()`` plus the fallbacks the
    panel uses for fields the constructor does not set (opacity, crop,
    feather) and ``identity_grade()``.

    ``alpha_mode`` is also ``MediaSource``'s constructor default (``straight``).
    """
    from cutora.model.timeline_model import Clip
    from cutora.render.anim_engine import DEFAULT_TRANSITION_DURATION
    from cutora.render.grade import identity_grade

    def _default_bg_fill():
        from cutora.model.project_schema import BG_FILL_DEFAULTS
        return dict(BG_FILL_DEFAULTS)

    clip = Clip("", 0.0, 0.0, 0.0)
    return {
        "fit_mode": clip.fit_mode,
        "bg_fill": _default_bg_fill(),
        "blend_mode": clip.blend_mode,
        "x": float(clip.x),
        "y": float(clip.y),
        "scale": float(clip.scale),
        "scale_y": float(clip.scale_y),
        "lock_aspect": bool(clip.lock_aspect),
        "rotation": float(clip.rotation),
        "flip_x": bool(clip.flip_x),
        "flip_y": bool(clip.flip_y),
        "speed": float(clip.speed),
        "loop": bool(clip.loop),
        "alpha_mode": str(clip.alpha_mode),
        "match_duration": bool(clip.match_duration),
        "transition_preset": str(clip.transition_preset or "none"),
        "transition_duration": float(clip.transition_duration or DEFAULT_TRANSITION_DURATION),
        "fade_top": float(clip.fade_top),
        "fade_bottom": float(clip.fade_bottom),
        "fade_left": float(clip.fade_left),
        "fade_right": float(clip.fade_right),
        "fade_top_offset": 0.0,
        "fade_bottom_offset": 0.0,
        "fade_left_offset": 0.0,
        "fade_right_offset": 0.0,
        "opacity": 255,
        "crop_left": 0,
        "crop_right": 0,
        "crop_top": 0,
        "crop_bottom": 0,
        "feather": 0,
        "grade": identity_grade(),
    }


def _preset_callable(preset: str):
    """Render function for ``preset``, unwrapping a bound-direction closure.

    Returns ``(fn, bound_direction_or_None)``.
    """
    from cutora.render.transitions import TRANSITION_FUNCS

    fn = TRANSITION_FUNCS.get(str(preset or ""))
    if fn is None:
        return None, None
    bound = None
    for cell in (getattr(fn, "__closure__", None) or ()):
        try:
            val = cell.cell_contents
        except ValueError:
            continue
        if callable(val):
            fn = val
        elif isinstance(val, str):
            bound = val
    return fn, bound


def preset_param_defaults(preset: str) -> dict:
    """Keyword defaults of the preset's render function.

    A direction closed over by ``_bind_dir`` is included as ``direction``
    even though it is no longer a free parameter. Missing preset → ``{}``.
    """
    fn, bound = _preset_callable(preset)
    out = {}
    if bound is not None:
        out["direction"] = bound
    if fn is None:
        return out
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return out
    for name, param in params.items():
        if name in ("a", "b", "t", "w", "h"):
            continue
        if param.default is inspect.Parameter.empty:
            continue
        if name == "direction" and bound is not None:
            continue
        out[name] = copy.deepcopy(param.default)
    return out


def transition_defaults(preset: str | None = None) -> dict:
    """``TransitionItem`` constructor defaults, plus the preset's own parameters.

    Duration is the product default (``DEFAULT_TRANSITION_DURATION``, 0.35s):
    the constructor requires a duration, and new tapes are created with that
    value. ``direction`` is the preset signature default, or ``"left"`` when
    the constructor leaves it ``None`` — that is what the inspector shows and
    what a user edit writes back.
    """
    from cutora.model.timeline_model import TransitionItem
    from cutora.render.anim_engine import DEFAULT_TRANSITION_DURATION

    dur = float(DEFAULT_TRANSITION_DURATION)
    name = str(preset or "cross_dissolve")
    item = TransitionItem(0.0, dur, name)
    out = {
        "preset": name,
        "duration": dur,
        "zoom_scale_start": float(item.zoom_scale_start),
        "motion_blur": float(item.motion_blur),
        "domain": str(item.domain or "visual"),
        "intensity": float(item.intensity),
        "easing": str(item.easing or "ease_in_out"),
        "direction": item.direction,
        "angle": float(item.angle),
        "feather": float(item.feather),
    }
    sig = preset_param_defaults(name) if preset else {}
    if "intensity" in sig:
        out["intensity"] = float(sig["intensity"])
    if "easing" in sig and sig["easing"]:
        out["easing"] = str(sig["easing"])
    if "motion_blur" in sig:
        out["motion_blur"] = float(sig["motion_blur"])
    if "feather" in sig:
        out["feather"] = float(sig["feather"])
    if "angle" in sig:
        out["angle"] = float(sig["angle"])
    if "zoom_scale_start" in sig:
        out["zoom_scale_start"] = float(sig["zoom_scale_start"])
    if "zoom_start" in sig:
        out["zoom_scale_start"] = float(sig["zoom_start"])
    if "direction" in sig and sig["direction"] is not None:
        out["direction"] = sig["direction"]
    elif out["direction"] is None:
        out["direction"] = "left"
    return out


def track_defaults() -> dict:
    """``Track()`` constructor props, plus ``default_split()`` and identity grade.

    ``pinned`` is session state and is not a factory field. ``name`` is ``""``.
    """
    from cutora.model.timeline_model import Track
    from cutora.render.grade import identity_grade
    from cutora.render.split_layout import default_split
    from cutora.render.track_transform import track_props_of

    track = Track("video")
    return {
        "name": str(track.name or ""),
        "visible": bool(track.visible),
        "locked": bool(track.locked),
        "mute": bool(track.mute),
        "blend_mode": str(track.blend_mode or "normal"),
        "split_cell": track.split_cell,
        "props": track_props_of(track),
        "split": default_split(),
        "grade": identity_grade(),
    }


def text_overlay_fx_defaults() -> dict:
    """fx tree a brand-new text overlay loads into the inspector.

    Built from ``overlay_defaults("text")`` through the same conversion the
    panel uses (``overlay_to_style`` + ``ensure_section_enabled``), so a
    reset lands on the creation stroke instead of an empty style.
    """
    from cutora.render.textfx.spec import ensure_section_enabled
    from cutora.subtitle.text_layer import overlay_to_style

    ov = overlay_defaults("text")
    style = overlay_to_style(ov, int(ov["w"]), int(ov["h"]))
    fx = style.get("fx") if isinstance(style.get("fx"), dict) else {}
    fx = copy.deepcopy(fx)
    ensure_section_enabled(fx)
    # The inspector font combos show the creation family, not the first
    # installed face. Empty-style defaults would land on that face instead.
    fonts = fx.get("fonts") if isinstance(fx.get("fonts"), dict) else {}
    fonts = dict(fonts)
    fonts.setdefault("body", ov.get("font_family") or "Arimo")
    fonts.setdefault("emphasis", ov.get("font_family") or "Arimo")
    fx["fonts"] = fonts
    return fx

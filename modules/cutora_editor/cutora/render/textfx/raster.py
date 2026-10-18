# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""ROI off-screen raster: fill / stroke / glow / neon / shadow / extrude / bg.

Blur is cv2.GaussianBlur on an ARGB32_Premultiplied numpy view. No concentric
strokes, no offset redraws, no QGraphicsBlurEffect.
"""
from __future__ import annotations

import math
import threading
from collections import OrderedDict
from dataclasses import replace

import cv2
import numpy as np
from PySide6.QtCore import QLineF, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
    QTransform,
)

from cutora.core.log import get_logger
from cutora.render.textfx.layout import (
    LayoutResult, WordRun, _qfont, bg_panel_rect,
)
from cutora.render.textfx.paint_fill import (
    emphasis_brush, face_fill_hex, fill_brush, gradient_ends, mix_hex,
    neon_face_hex, paint_metal_highlight, qcolor_hex,
)
from cutora.render.textfx.spec import (
    DEFAULT_FILL, FxSpec, emphasis_is_gradient, rgb_tuple, rgba_tuple,
    SHADOW_CONTACT_EM, SHADOW_DIST_MIN_EM, point_light_scale,
)

logger = get_logger("cutora.render.textfx.raster")
_GLASS_FALLBACK_LOGGED = False


def bgr_numpy_to_qimage(frame: np.ndarray | None) -> QImage | None:
    """BGR uint8 HxWx3 → owned QImage Format_RGB888 (honours stride)."""
    if frame is None or getattr(frame, "size", 0) == 0:
        return None
    if frame.ndim != 3 or frame.shape[2] < 3:
        return None
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    rgb = np.ascontiguousarray(rgb)
    h, w = rgb.shape[:2]
    qimg = QImage(rgb.data, w, h, int(rgb.strides[0]), QImage.Format.Format_RGB888)
    return qimg.copy()


def as_bgra(img: QImage) -> tuple[QImage, np.ndarray]:
    """Return (owned image, HxWx4 BGRA view). Caller must keep image alive."""
    fmt = QImage.Format.Format_ARGB32_Premultiplied
    owned = img if img.format() == fmt else img.convertToFormat(fmt)
    h, w = owned.height(), owned.width()
    bpl = owned.bytesPerLine()
    ptr = owned.bits()  # PySide6: memoryview over the image bits
    nbytes = bpl * h
    raw = np.frombuffer(ptr, dtype=np.uint8, count=nbytes)
    view = raw.reshape(h, bpl)[:, : w * 4].reshape(h, w, 4)
    return owned, view


def mul_premul_alpha(img: QImage, alpha: float) -> QImage:
    """Integer premul scale. Replaces QPainter.setOpacity on a whole layer."""
    a = max(0.0, min(1.0, float(alpha)))
    if a >= 1.0 - 1e-4:
        return img
    out = img.copy()
    if a <= 1e-6:
        out.fill(0)
        return out
    owned, bgra = as_bgra(out)
    q = int(round(a * 255.0))
    bgra[:] = ((bgra.astype(np.uint16) * q + 127) // 255).astype(np.uint8)
    return owned


def gaussian_channel(src: np.ndarray, sigma: float) -> np.ndarray:
    """float32 Gaussian blur. sigma in pixels. (0,0) kernel lets OpenCV pick."""
    if sigma <= 0.05:
        return src.astype(np.float32, copy=False)
    return cv2.GaussianBlur(
        src.astype(np.float32, copy=False), (0, 0), sigmaX=float(sigma))


def dilate_u8(mask: np.ndarray, spread_px: float) -> np.ndarray:
    k = int(round(spread_px))
    if k <= 0:
        return mask
    size = k * 2 + 1
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))
    return cv2.dilate(mask, kernel)


def _qcolor(hex_color: str, alpha: float = 1.0) -> QColor:
    r, g, b = rgb_tuple(hex_color)
    c = QColor(r, g, b)
    c.setAlpha(max(0, min(255, int(round(alpha * 255)))))
    return c


def _pop_scale(run: WordRun, spec: FxSpec, t: float | None) -> float:
    from cutora.render.textfx.spec import POP_DUR_S, POP_PEAK
    if t is None or spec.emphasis.anim != "pop" or not run.emphasized:
        return 1.0
    u = (float(t) - float(run.t_start)) / POP_DUR_S
    if u < 0.0 or u > 1.0:
        return 1.0
    peak = POP_PEAK
    if u < 0.4:
        p = u / 0.4
        s = p * p * (3.0 - 2.0 * p)
        return 1.0 + (peak - 1.0) * s
    p = (u - 0.4) / 0.6
    s = p * p * (3.0 - 2.0 * p)
    return peak + (1.0 - peak) * s


def _run_path(run: WordRun, style: dict, spec: FxSpec | None = None,
              t: float | None = None, tracking_em: float | None = None) -> QPainterPath:
    track = run.tracking_em if tracking_em is None else tracking_em
    if run.qfont is not None:
        font = QFont(run.qfont)
        px = float(run.fontsize_px)
        if track:
            font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, track * px)
        else:
            font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 100.0)
    else:
        font = _qfont(
            run.font_family, run.fontsize_px,
            bool(style.get("bold", True)), bool(style.get("italic", False)),
            track)
    pop = _pop_scale(run, spec, t) if spec is not None else 1.0
    orient = float(getattr(run, "orient_deg", 0.0) or 0.0)
    if abs(orient) >= 0.01:
        fm = QFontMetricsF(font)
        orig_w = float(fm.horizontalAdvance(run.text))
        orig_h = float(fm.height())
        orig_asc = float(fm.ascent())
        draw_x = run.x + (run.w - orig_w) * 0.5 + float(run.pad_px)
        draw_baseline = run.y + (run.h - orig_h) * 0.5 + orig_asc
        path = QPainterPath()
        path.addText(QPointF(draw_x, draw_baseline), font, run.text)
        cx = run.x + max(1.0, run.w) * 0.5
        cy = run.y + max(1.0, run.h) * 0.5
        xf = QTransform()
        xf.translate(cx, cy)
        xf.rotate(orient + float(run.rot_deg or 0.0))
        if abs(pop - 1.0) >= 0.001:
            xf.scale(pop, pop)
        xf.translate(-cx, -cy)
        return xf.map(path)
    ox = float(getattr(run, "glyph_ox", 0.0) or 0.0)
    oy = float(getattr(run, "glyph_oy", 0.0) or 0.0)
    path = QPainterPath()
    path.addText(QPointF(run.x + float(run.pad_px) + ox, run.baseline + oy), font, run.text)
    if abs(run.rot_deg) < 0.01 and abs(pop - 1.0) < 0.001:
        return path
    cx = run.x + max(1.0, run.w) * 0.5
    cy = run.baseline
    xf = QTransform()
    xf.translate(cx, cy)
    if abs(run.rot_deg) >= 0.01:
        xf.rotate(run.rot_deg)
    if abs(pop - 1.0) >= 0.001:
        xf.scale(pop, pop)
    xf.translate(-cx, -cy)
    return xf.map(path)


def collect_run_paths(
    layout: LayoutResult, style: dict,
    spec: FxSpec | None = None, t: float | None = None,
    xf=None,
) -> list[tuple[QPainterPath, WordRun]]:
    pairs = []
    track = None
    if xf is not None and spec is not None:
        from cutora.render.textfx.anim import _anim_ids
        if "tracking_in" in _anim_ids(spec.anim.in_name):
            track = float(xf.tracking_em)
    for line in layout.lines:
        for run in line.runs:
            if run.text:
                pairs.append((_run_path(run, style, spec, t, tracking_em=track), run))
    return pairs


def collect_paths(layout: LayoutResult, style: dict) -> list[QPainterPath]:
    return [p for p, _ in collect_run_paths(layout, style)]


def _text_mask(
    layout: LayoutResult, paths: list[QPainterPath], spec: FxSpec,
) -> tuple[QImage, np.ndarray]:
    """Alpha of fill ∪ stroke, so glow/neon hug the outline."""
    img = QImage(layout.roi_w, layout.roi_h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    p = QPainter(img)
    try:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.translate(-layout.origin[0], -layout.origin[1])
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_qcolor("#FFFFFF", 1.0))
        for path in paths:
            p.fillPath(path, p.brush())
        if spec.stroke.live():
            width_px = spec.stroke.width_em * layout.em
            pen = QPen(_qcolor("#FFFFFF", 1.0), width_px)
            join = (Qt.PenJoinStyle.RoundJoin if spec.stroke.join == "round"
                    else Qt.PenJoinStyle.MiterJoin)
            pen.setJoinStyle(join)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            for path in paths:
                p.strokePath(path, pen)
    finally:
        p.end()
    owned, bgra = as_bgra(img)
    return owned, bgra[..., 3].copy()


def _colorize_alpha(
    alpha_f: np.ndarray, color: str, strength: float,
) -> tuple[QImage, np.ndarray]:
    h, w = alpha_f.shape
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    owned, bgra = as_bgra(img)
    a = np.clip(alpha_f * strength, 0.0, 1.0)
    r, g, b = rgb_tuple(color)
    bgra[..., 0] = (b * a).astype(np.uint8)
    bgra[..., 1] = (g * a).astype(np.uint8)
    bgra[..., 2] = (r * a).astype(np.uint8)
    bgra[..., 3] = (a * 255.0).astype(np.uint8)
    return owned, bgra


def _paint_neon(
    painter: QPainter, mask_u8: np.ndarray, spec: FxSpec, em: float,
) -> None:
    spread = spec.glow.spread_em * em
    work = dilate_u8(mask_u8, spread)
    sigma = spec.glow.sigma_em * em
    painter.save()
    try:
        painter.setCompositionMode(
            QPainter.CompositionMode.CompositionMode_Screen)
        halo = gaussian_channel(work, sigma)
        layer, _ = _colorize_alpha(halo / 255.0, spec.glow.color, 0.9)
        painter.drawImage(0, 0, layer)
        core = gaussian_channel(work, 0.35 * sigma)
        core_c = mix_hex(spec.glow.color, "#FFFFFF", 0.6)
        layer2, _ = _colorize_alpha(core / 255.0, core_c, 1.0)
        painter.drawImage(0, 0, layer2)
    finally:
        painter.restore()


def paint_glow(
    painter: QPainter,
    mask_u8: np.ndarray,
    spec: FxSpec,
    em: float,
    skip_blur: bool,
) -> None:
    if not spec.glow.live() or skip_blur:
        return
    if spec.glow.mode == "neon":
        _paint_neon(painter, mask_u8, spec, em)
        return
    sigma = spec.glow.sigma_em * em
    spread = spec.glow.spread_em * em
    work = dilate_u8(mask_u8, spread)
    blur = gaussian_channel(work, sigma)
    layer, _ = _colorize_alpha(blur / 255.0, spec.glow.color, spec.glow.strength)
    painter.drawImage(0, 0, layer)


def _shift2d(src: np.ndarray, dx: int, dy: int) -> np.ndarray:
    h, w = src.shape[:2]
    out = np.zeros_like(src)
    sy0, sx0 = max(0, -dy), max(0, -dx)
    dy0, dx0 = max(0, dy), max(0, dx)
    hh = min(h - sy0, h - dy0)
    ww = min(w - sx0, w - dx0)
    if hh > 0 and ww > 0:
        out[dy0:dy0 + hh, dx0:dx0 + ww] = src[sy0:sy0 + hh, sx0:sx0 + ww]
    return out


def _paint_shadow_legacy(
    painter: QPainter,
    mask_u8: np.ndarray,
    spec: FxSpec,
    em: float,
    skip_blur: bool,
) -> None:
    if spec.shadow.sigma_em <= 0 or skip_blur:
        return
    sigma = spec.shadow.sigma_em * em
    dx = int(round(spec.shadow.dx_em * em))
    dy = int(round(spec.shadow.dy_em * em))
    blur = gaussian_channel(mask_u8, sigma)
    shifted = _shift2d(blur, dx, dy)
    layer, _ = _colorize_alpha(shifted / 255.0, spec.shadow.color, spec.shadow.alpha)
    painter.drawImage(0, 0, layer)


def _mask_centroid(mask: np.ndarray) -> tuple[float, float] | None:
    ys, xs = np.where(mask > 8)
    if xs.size == 0:
        return None
    return float(xs.mean()), float(ys.mean())


def _radial_scale(mask: np.ndarray, lx: float, ly: float, scale: float) -> np.ndarray:
    h, w = mask.shape[:2]
    mat = np.array(
        [[scale, 0.0, (1.0 - scale) * lx],
         [0.0, scale, (1.0 - scale) * ly]],
        dtype=np.float32,
    )
    return cv2.warpAffine(
        mask.astype(np.float32, copy=False), mat, (w, h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0,
    )


def _mask_ray_extent(
    mask: np.ndarray, cos_a: float, sin_a: float,
) -> tuple[float, float] | None:
    ys, xs = np.where(mask > 8)
    if xs.size == 0:
        return None
    proj = xs.astype(np.float32) * float(cos_a) + ys.astype(np.float32) * float(sin_a)
    return float(proj.min()), float(proj.max())


def _shadow_falloff(
    shape: tuple[int, int], origin_proj: float,
    cos_a: float, sin_a: float, falloff: float, span: float,
) -> np.ndarray | None:
    if falloff <= 1e-6:
        return None
    h, w = shape
    yy, xx = np.ogrid[:h, :w]
    proj = xx.astype(np.float32) * float(cos_a) + yy.astype(np.float32) * float(sin_a)
    t = np.clip(
        (proj - float(origin_proj)) / max(1.0, float(span)), 0.0, 1.0)
    return (1.0 - float(falloff) * t).astype(np.float32)


# Layered penumbra. N and K are not user parameters.
_SHADOW_LAYERS = 4
_SHADOW_K = 0.5
_SHADOW_WEIGHTS = (0.38, 0.28, 0.20, 0.14)
_SHADOW_PREVIEW_DS = 4


def _shadow_layer_ds(
    sigma: float, w: int, h: int, layer_i: int, n_layers: int,
    *, point: bool = False,
) -> int:
    """Far layers are already soft: half res, farthest at 1/4.

    Point light's cost is warpAffine; every layer except a full-res root
    runs at ≤½ so four warps stay near the old single-warp budget.
    """
    ds = _gauss_ds(sigma, w, h)
    if n_layers <= 1:
        return ds
    if layer_i >= n_layers - 1:
        want = 4
    elif point or layer_i >= 1:
        want = 2
    else:
        want = 1
    ds = max(ds, want)
    return min(ds, max(1, int(w) // 32), max(1, int(h) // 32), 8)


def _blur_channel_ds(src: np.ndarray, sigma: float, ds: int = 0) -> np.ndarray:
    """Gaussian blur; far/soft layers run at 1/ds resolution then upsample."""
    if sigma <= 0.05:
        return src.astype(np.float32, copy=False)
    work = src.astype(np.float32, copy=False)
    h, w = work.shape[:2]
    if ds <= 0:
        ds = _gauss_ds(sigma, w, h)
    if ds <= 1:
        return cv2.GaussianBlur(work, (0, 0), sigmaX=float(sigma))
    sw, sh = max(1, w // ds), max(1, h // ds)
    small = cv2.resize(work, (sw, sh), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(
        small, (0, 0), sigmaX=max(0.3, float(sigma) / float(ds)))
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)


def _point_shadow_layer(
    base: np.ndarray,
    lx: float, ly: float, scale: float,
    dx: int, dy: int, sigma: float, skip_blur: bool, ds: int = 1,
) -> np.ndarray:
    """Radial scale + shift; blur the far copy at reduced resolution."""
    h, w = base.shape[:2]
    if skip_blur:
        ds = max(int(ds), _SHADOW_PREVIEW_DS)
    if ds > 1:
        sw, sh = max(1, w // ds), max(1, h // ds)
        small = cv2.resize(
            base.astype(np.float32, copy=False), (sw, sh),
            interpolation=cv2.INTER_AREA)
        scaled = _radial_scale(small, lx / float(ds), ly / float(ds), scale)
        shifted = _shift2d(
            scaled, int(round(dx / float(ds))), int(round(dy / float(ds))))
        if sigma > 0.05:
            shifted = cv2.GaussianBlur(
                shifted.astype(np.float32, copy=False),
                (0, 0), sigmaX=max(0.3, float(sigma) / float(ds)))
        return cv2.resize(shifted, (w, h), interpolation=cv2.INTER_LINEAR)
    scaled = _radial_scale(base, lx, ly, scale)
    shifted = _shift2d(scaled, dx, dy)
    if sigma > 0.05:
        return _blur_channel_ds(shifted, sigma, ds=1)
    return shifted.astype(np.float32, copy=False)


def paint_shadow(
    painter: QPainter,
    mask_u8: np.ndarray,
    spec: FxSpec,
    em: float,
    skip_blur: bool,
) -> None:
    sh = spec.shadow
    if sh._legacy:
        _paint_shadow_legacy(painter, mask_u8, spec, em, skip_blur)
        return
    if (not sh.enabled) or sh.strength <= 0:
        return
    cen = _mask_centroid(mask_u8)
    if cen is None:
        logger.warning("paint_shadow: empty glyph mask, skip")
        return
    cx, cy = cen
    rad = math.radians(sh.angle_deg)
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    _r, _g, _b, col_a = rgba_tuple(sh.color)
    strength = sh.strength * col_a
    n_layers = 1 if skip_blur else _SHADOW_LAYERS
    dist_em = max(0.0, float(sh.distance_em))
    dist_px = dist_em * em
    size_px = max(0.0, float(sh.size_em)) * em
    dist_term = (dist_em + SHADOW_CONTACT_EM) / (dist_em + _SHADOW_K)
    dist_scale = max(dist_em, SHADOW_DIST_MIN_EM)
    work = mask_u8
    if dist_em <= 1e-4:
        bleed_px = max(1.0, float(sh.size_em) * em * 0.20)
        work = dilate_u8(mask_u8, bleed_px)
    base = work.astype(np.float32, copy=False)
    acc = np.zeros(mask_u8.shape, dtype=np.float32)
    weights = (1.0,) if n_layers == 1 else _SHADOW_WEIGHTS
    is_point = sh.light == "point" and dist_em > 1e-4
    lx = ly = 0.0
    if is_point:
        ys, xs = np.where(mask_u8 > 8)
        if xs.size == 0:
            logger.warning("paint_shadow: point light, empty support, skip")
            return
        rad_max = float(np.sqrt(((xs - cx) ** 2 + (ys - cy) ** 2).max()))
        light_dist = dist_scale * em + rad_max + 4.0
        lx = cx - cos_a * light_dist
        ly = cy - sin_a * light_dist
    for i in range(n_layers):
        t = (i + 1) / float(n_layers)
        w_i = weights[i]
        sigma = size_px * t * dist_term
        dx = int(round(dist_px * t * cos_a))
        dy = int(round(dist_px * t * sin_a))
        if is_point:
            sigma *= 1.0 + 0.65 * t
            scale = point_light_scale(dist_em, t)
            i_t = 1.0 / (1.0 + (t * dist_em) * (t * dist_em))
            ds = _SHADOW_PREVIEW_DS if skip_blur else _shadow_layer_ds(
                sigma, base.shape[1], base.shape[0], i, n_layers,
                point=True)
            layer = _point_shadow_layer(
                base, lx, ly, scale, dx, dy, sigma, skip_blur, ds=ds)
            acc += (layer / 255.0) * (w_i * i_t)
        else:
            layer = _shift2d(base, dx, dy)
            if sigma > 0.05:
                if skip_blur:
                    layer = _blur_channel_ds(
                        layer, sigma, ds=_SHADOW_PREVIEW_DS)
                else:
                    ds = _shadow_layer_ds(
                        sigma, layer.shape[1], layer.shape[0], i, n_layers)
                    layer = _blur_channel_ds(layer, sigma, ds=ds)
            acc += (layer / 255.0) * w_i
    if is_point:
        # Closer light → brighter umbra. 1/(1+d²) with no *scale left
        # distance 2.0 below the visible floor; 1/(1+d) still gives the
        # 0.2 vs 0.6 peak rise without flattening the penumbra.
        acc *= 1.0 / (1.0 + dist_em)
        peak = float(np.max(acc)) if acc.size else 0.0
        if peak > 1.0:
            acc *= 1.0 / peak
    acc *= strength
    ray = _mask_ray_extent(mask_u8, cos_a, sin_a)
    if ray is not None:
        _, p_max = ray
        # Origin is the downwind glyph edge (not the centroid). Span is the
        # cast length: offset plus a penumbra tail. Glyph length is already
        # in the origin — adding it again made t never reach 1 at the tip.
        span = max(float(dist_px), 1.0) + 3.0 * size_px * max(dist_term, 0.05)
        fo = _shadow_falloff(
            acc.shape, p_max, cos_a, sin_a, sh.falloff, span)
    else:
        fo = None
    if fo is not None:
        acc *= fo
    acc = np.clip(acc, 0.0, 1.0)
    if float(acc.max()) <= 1e-4:
        return
    layer, _ = _colorize_alpha(acc, sh.color, 1.0)
    painter.drawImage(0, 0, layer)


def _round_rect_path(rect: QRectF, radius: float) -> QPainterPath:
    path = QPainterPath()
    rad = max(0.0, min(radius, min(rect.width(), rect.height()) / 2.0))
    path.addRoundedRect(rect, rad, rad)
    return path


def _glass_rect(layout: LayoutResult, spec: FxSpec) -> QRectF:
    return bg_panel_rect(
        layout.text_x, layout.text_y, layout.text_w, layout.text_h,
        spec, layout.em)


def _backdrop_crop_box(
    backdrop: QImage, rect: QRectF, video_w: int, video_h: int, pad: int,
) -> tuple[int, int, int, int, int, int, int, int] | None:
    """Map canvas *rect* + blur *pad* into backdrop pixels.

    *pad* is in canvas px; scaled by the backdrop/canvas ratio so a 800 px
    preview frame does not get a 152 px canvas pad (that was a near-full-frame
    crop + convertToFormat — the T04f-2-fix3 11 ms gap).
    """
    if backdrop is None or backdrop.isNull():
        return None
    bw, bh = backdrop.width(), backdrop.height()
    sx = bw / float(max(1, video_w))
    sy = bh / float(max(1, video_h))
    inner_x = int(math.floor(rect.x() * sx))
    inner_y = int(math.floor(rect.y() * sy))
    inner_w = max(1, int(math.ceil(rect.width() * sx)))
    inner_h = max(1, int(math.ceil(rect.height() * sy)))
    pad_x = int(round(float(pad) * sx))
    pad_y = int(round(float(pad) * sy))
    crop_x = max(0, inner_x - pad_x)
    crop_y = max(0, inner_y - pad_y)
    crop_x2 = min(bw, inner_x + inner_w + pad_x)
    crop_y2 = min(bh, inner_y + inner_h + pad_y)
    cw, ch = crop_x2 - crop_x, crop_y2 - crop_y
    if cw < 1 or ch < 1:
        return None
    pad_left = inner_x - crop_x
    pad_top = inner_y - crop_y
    iw = min(inner_w, cw - pad_left)
    ih = min(inner_h, ch - pad_top)
    if iw < 1 or ih < 1:
        return None
    return crop_x, crop_y, cw, ch, pad_left, pad_top, iw, ih


def _copy_backdrop_roi(
    backdrop: QImage, rect: QRectF, video_w: int, video_h: int, pad: int,
) -> tuple[QImage, int, int, int, int] | None:
    """Crop rect plus blur pad. Returns (image, pad_left, pad_top, inner_w, inner_h).

    pad_left/pad_top are the *actual* pixels of pad that survived edge clamp.
    """
    box = _backdrop_crop_box(backdrop, rect, video_w, video_h, pad)
    if box is None:
        return None
    crop_x, crop_y, cw, ch, pad_left, pad_top, iw, ih = box
    return backdrop.copy(crop_x, crop_y, cw, ch), pad_left, pad_top, iw, ih


def _qimage_bgr_roi(img: QImage, x: int, y: int, rw: int, rh: int) -> np.ndarray:
    """Copy a BGR uint8 ROI. No full-frame convertToFormat.

    Decoder frames are Format_BGR888; export tests use ARGB32_Premultiplied.
    """
    fmt = img.format()
    w, h = img.width(), img.height()
    bpl = img.bytesPerLine()
    # constBits: bits() detaches and changes QImage.cacheKey, breaking glass hold.
    ptr = img.constBits() if hasattr(img, "constBits") else img.bits()
    nbytes = bpl * h
    raw = np.frombuffer(ptr, dtype=np.uint8, count=nbytes)
    x0 = max(0, int(x))
    y0 = max(0, int(y))
    x1 = min(w, x0 + max(1, int(rw)))
    y1 = min(h, y0 + max(1, int(rh)))
    bgr888 = QImage.Format.Format_BGR888
    rgb888 = QImage.Format.Format_RGB888
    argb = QImage.Format.Format_ARGB32
    argbp = QImage.Format.Format_ARGB32_Premultiplied
    if fmt == bgr888:
        view = raw.reshape(h, bpl)[:, : w * 3].reshape(h, w, 3)
        return np.ascontiguousarray(view[y0:y1, x0:x1])
    if fmt == rgb888:
        view = raw.reshape(h, bpl)[:, : w * 3].reshape(h, w, 3)
        return np.ascontiguousarray(view[y0:y1, x0:x1, ::-1])
    if fmt in (argb, argbp):
        view = raw.reshape(h, bpl)[:, : w * 4].reshape(h, w, 4)
        return np.ascontiguousarray(view[y0:y1, x0:x1, :3])
    crop = img.copy(x0, y0, x1 - x0, y1 - y0)
    _o, bgra = as_bgra(crop)
    return np.ascontiguousarray(bgra[..., :3].copy())


def _gauss_ds(sigma: float, w: int, h: int) -> int:
    """Downsample so σ/ds stays in [4, 8]. Same rule as _blur_qimage."""
    sigma = max(0.0, float(sigma))
    ds = max(1, min(8, int(sigma // 4))) if sigma > 0 else 1
    return min(ds, max(1, int(w) // 32), max(1, int(h) // 32))


def _blur_bgr(
    bgr: np.ndarray, sigma: float, cheap: bool = False,
) -> np.ndarray:
    """BGR Gaussian blur with the same ds rule as _blur_qimage."""
    h, w = bgr.shape[:2]
    sigma = max(0.0, float(sigma))
    ds = _gauss_ds(sigma, w, h)
    if cheap:
        ds = min(16, max(ds * 2, 2))
        ds = min(ds, max(1, w // 32), max(1, h // 32))
    sx = max(0.3, sigma if ds <= 1 else sigma / float(ds))
    work = np.ascontiguousarray(bgr)
    if ds <= 1:
        return cv2.GaussianBlur(work, (0, 0), sigmaX=sx)
    small = cv2.resize(
        work, (max(1, w // ds), max(1, h // ds)),
        interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), sigmaX=sx)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)


def _blur_qimage(
    src: QImage, sigma: float, opaque: bool = False, cheap: bool = False,
) -> QImage:
    """BGR Gaussian blur. Downsample so σ/ds stays in [4, 8]; never box blur."""
    owned, bgra = as_bgra(src)
    out = owned.copy()
    dest, dview = as_bgra(out)
    bgr = np.ascontiguousarray(dview[..., :3])
    h, w = bgr.shape[:2]
    sigma = max(0.0, float(sigma))
    ds = _gauss_ds(sigma, w, h)
    if cheap:
        ds = min(16, max(ds * 2, 2))
        ds = min(ds, max(1, w // 32), max(1, h // 32))
    sx = max(0.3, sigma if ds <= 1 else sigma / float(ds))
    if ds <= 1:
        bgr = cv2.GaussianBlur(bgr, (0, 0), sigmaX=sx)
    else:
        small = cv2.resize(
            bgr, (max(1, w // ds), max(1, h // ds)),
            interpolation=cv2.INTER_AREA)
        small = cv2.GaussianBlur(small, (0, 0), sigmaX=sx)
        bgr = cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)
    dview[..., :3] = bgr
    if opaque:
        dview[..., 3] = 255
    else:
        alpha = np.ascontiguousarray(dview[..., 3])
        dview[..., 3] = cv2.GaussianBlur(alpha, (0, 0), sigmaX=sx)
    return dest


def _tint_qimage(src: QImage, color: str, alpha: float) -> QImage:
    """Mix opaque backdrop RGB with *color* at *alpha*, keep pixels opaque."""
    owned, bgra = as_bgra(src)
    out = QImage(owned.size(), QImage.Format.Format_ARGB32_Premultiplied)
    dest, dview = as_bgra(out)
    r, g, b = rgb_tuple(color)
    a = max(0.0, min(1.0, float(alpha)))
    src_f = bgra.astype(np.float32)
    dview[..., 0] = (src_f[..., 0] * (1.0 - a) + b * a).astype(np.uint8)
    dview[..., 1] = (src_f[..., 1] * (1.0 - a) + g * a).astype(np.uint8)
    dview[..., 2] = (src_f[..., 2] * (1.0 - a) + r * a).astype(np.uint8)
    dview[..., 3] = 255
    return dest


def _paint_glass_border(
    painter: QPainter, rect: QRectF, radius: float, strength: float = 1.0,
) -> None:
    s = max(0.0, min(1.0, float(strength)))
    grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    grad.setColorAt(0.0, QColor(255, 255, 255, int(0.65 * 255 * s)))
    grad.setColorAt(0.30, QColor(255, 255, 255, int(0.25 * 255 * s)))
    grad.setColorAt(1.0, QColor(255, 255, 255, int(0.12 * 255 * s)))
    pen = QPen(QBrush(grad), 1.0)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(pen)
    painter.drawPath(_round_rect_path(rect, radius))


def glass_sampled_image(
    backdrop: QImage,
    rect: QRectF,
    video_w: int,
    video_h: int,
    sigma: float,
    color: str,
    alpha: float,
    skip_blur: bool = False,
    tint: bool = True,
) -> QImage | None:
    """Blurred, tinted inner of *rect* (blur pad cropped off, not scaled in)."""
    pad = int(math.ceil(3.0 * max(0.0, sigma)))
    copied = _copy_backdrop_roi(backdrop, rect, video_w, video_h, pad)
    if copied is None:
        return None
    crop, pad_l, pad_t, iw, ih = copied
    blurred = _blur_qimage(crop, sigma, opaque=True, cheap=skip_blur)
    iw = min(iw, blurred.width() - pad_l)
    ih = min(ih, blurred.height() - pad_t)
    if iw < 1 or ih < 1:
        return None
    inner = blurred.copy(pad_l, pad_t, iw, ih)
    if not tint:
        return inner
    return _tint_qimage(inner, color, alpha)


def glass_blur_pair(
    backdrop: QImage,
    rect: QRectF,
    video_w: int,
    video_h: int,
    sigma: float,
    skip_blur: bool = False,
    sigma_rim: float = 0.0,
    band_px: int = 0,
) -> tuple[np.ndarray, np.ndarray] | None:
    """Strong full-ROI blur + optional weak rim. BGR cropped, format not converted."""
    from cutora.core import preview_perf
    from cutora.render.textfx.glass_thick import blur_edge_bands
    pad = int(math.ceil(3.0 * max(0.0, sigma)))
    t0 = preview_perf.now()
    box = _backdrop_crop_box(backdrop, rect, video_w, video_h, pad)
    if box is None:
        return None
    crop_x, crop_y, cw, ch, pad_l, pad_t, iw, ih = box
    bgr = _qimage_bgr_roi(backdrop, crop_x, crop_y, cw, ch)
    preview_perf.add_span("fx_bd_fmt", preview_perf.now() - t0)
    t1 = preview_perf.now()
    strong = _blur_bgr(bgr, sigma, cheap=skip_blur)
    ih = min(ih, strong.shape[0] - pad_t)
    iw = min(iw, strong.shape[1] - pad_l)
    if iw < 1 or ih < 1:
        return None
    inner = np.ascontiguousarray(bgr[pad_t:pad_t + ih, pad_l:pad_l + iw])
    strong_inner = np.ascontiguousarray(strong[pad_t:pad_t + ih, pad_l:pad_l + iw])
    rim = max(0.0, float(sigma_rim) if sigma_rim > 0 else max(0.3, sigma / 3.0))
    # σ_rim ≤ 1.5 px: unblurred crop is the refraction source (review §2.2).
    if rim > 1.5:
        band = int(band_px) if band_px > 0 else max(4, ih // 8, iw // 8)
        weak_inner = blur_edge_bands(inner, rim, band)
    else:
        weak_inner = inner
    preview_perf.add_span("fx_glass_blur", preview_perf.now() - t1)
    return strong_inner, weak_inner


def paint_glass(
    painter: QPainter,
    layout: LayoutResult,
    spec: FxSpec,
    backdrop: QImage | None,
    video_w: int,
    video_h: int,
    skip_blur: bool,
) -> None:
    global _GLASS_FALLBACK_LOGGED
    if spec.bg.kind() not in ("glass", "box"):
        return
    if spec.bg.kind() == "glass" and spec.bg.glass_thickness_em > 0:
        _paint_glass_thick(
            painter, layout, spec, backdrop, video_w, video_h, skip_blur)
        return
    _paint_glass_flat(
        painter, layout, spec, backdrop, video_w, video_h, skip_blur)


def _bg_blend_mode(name: str):
    table = {
        "multiply": QPainter.CompositionMode.CompositionMode_Multiply,
        "screen": QPainter.CompositionMode.CompositionMode_Screen,
        "overlay": QPainter.CompositionMode.CompositionMode_Overlay,
        "soft_light": QPainter.CompositionMode.CompositionMode_SoftLight,
    }
    return table.get(str(name or "normal"), QPainter.CompositionMode.CompositionMode_SourceOver)


def _paint_bg_grad(painter, local, local_path, spec, overlay: bool) -> bool:
    gtype = str(spec.bg.grad_type or "none")
    cols = [c for c in (spec.bg.grad_colors or []) if c]
    if gtype == "none" or len(cols) < 2:
        return False
    a = spec.bg.alpha
    angle_off = float(getattr(spec.bg, "grad_angle", 0.0) or 0.0)
    if gtype == "center":
        g = QRadialGradient(
            local.center(),
            max(local.width(), local.height()) / 2.0)
    elif gtype == "linear_angle":
        p0, p1 = gradient_ends(local, angle_off)
        g = QLinearGradient(p0, p1)
    elif gtype == "linear_v":
        if abs(angle_off) > 0.01:
            p0, p1 = gradient_ends(local, 90.0 + angle_off)
            g = QLinearGradient(p0, p1)
        else:
            g = QLinearGradient(local.topLeft(), local.bottomLeft())
    elif gtype == "fresnel":
        if abs(angle_off) > 0.01:
            p0, p1 = gradient_ends(local, angle_off)
            g = QLinearGradient(p0, p1)
        else:
            g = QLinearGradient(local.topLeft(), local.topRight())
    else:
        if abs(angle_off) > 0.01:
            p0, p1 = gradient_ends(local, angle_off)
            g = QLinearGradient(p0, p1)
        else:
            g = QLinearGradient(local.topLeft(), local.topRight())
    if gtype == "fresnel":
        edge = _qcolor(cols[0], a)
        mid = _qcolor(cols[-1], a * 0.2)
        g.setColorAt(0.0, edge)
        g.setColorAt(0.15, mid)
        g.setColorAt(0.85, mid)
        g.setColorAt(1.0, edge)
    else:
        n = min(3, len(cols))
        for i in range(n):
            g.setColorAt(i / float(max(1, n - 1)), _qcolor(cols[i], a))
    painter.save()
    try:
        painter.setPen(Qt.PenStyle.NoPen)
        if overlay:
            painter.setCompositionMode(_bg_blend_mode(getattr(spec.bg, "blend", "normal")))
        painter.setBrush(QBrush(g))
        painter.drawPath(local_path)
        if spec.bg.grad_highlight:
            hl = QLinearGradient(
                local.topLeft(),
                QPointF(local.left(), local.top() + local.height() * 0.3))
            hl.setColorAt(0.0, QColor(255, 255, 255, 60))
            hl.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.setBrush(QBrush(hl))
            painter.drawPath(local_path)
    finally:
        painter.restore()
    return True


def _paint_glass_flat(
    painter: QPainter,
    layout: LayoutResult,
    spec: FxSpec,
    backdrop: QImage | None,
    video_w: int,
    video_h: int,
    skip_blur: bool,
) -> None:
    global _GLASS_FALLBACK_LOGGED
    rect = _glass_rect(layout, spec)
    radius = spec.bg.radius_em * layout.em
    local = QRectF(rect)
    local.translate(-layout.origin[0], -layout.origin[1])
    local_path = _round_rect_path(local, radius)
    sampled = None
    if spec.bg.kind() == "glass" and backdrop is not None and not backdrop.isNull():
        sampled = glass_sampled_image(
            backdrop, rect, video_w, video_h,
            spec.bg.glass_sigma_em * layout.em,
            spec.bg.color, spec.bg.alpha, skip_blur)
    if sampled is not None and not sampled.isNull():
        target_w = max(1, int(round(local.width())))
        target_h = max(1, int(round(local.height())))
        if sampled.width() != target_w or sampled.height() != target_h:
            sampled = sampled.scaled(
                target_w, target_h,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
        painter.save()
        painter.setClipPath(local_path)
        painter.drawImage(local.topLeft(), sampled)
        _paint_bg_grad(painter, local, local_path, spec, overlay=True)
        painter.restore()
    else:
        if spec.bg.kind() == "glass" and not _GLASS_FALLBACK_LOGGED:
            logger.debug("glass backdrop missing; falling back to rounded box")
            _GLASS_FALLBACK_LOGGED = True
        painter.setPen(Qt.PenStyle.NoPen)
        if not _paint_bg_grad(painter, local, local_path, spec, overlay=False):
            painter.setBrush(_qcolor(spec.bg.color, spec.bg.alpha))
            painter.drawPath(local_path)
    if spec.bg.kind() == "glass" and spec.bg.glass_border:
        _paint_glass_border(painter, local, radius, 1.0)


_MASK_CACHE: dict = {}


def _rounded_mask_u8(w: int, h: int, radius: float) -> np.ndarray:
    from cutora.render.textfx.glass_thick import sdf_key
    key = sdf_key(w, h, radius)
    hit = _MASK_CACHE.get(key)
    if hit is not None:
        return hit
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(255, 255, 255))
    p.drawRoundedRect(0, 0, max(0, w - 1), max(0, h - 1), radius, radius)
    p.end()
    _o, bgra = as_bgra(img)
    mask = np.where(bgra[..., 3] > 127, 255, 0).astype(np.uint8)
    _MASK_CACHE[key] = mask
    if len(_MASK_CACHE) > 24:
        _MASK_CACHE.pop(next(iter(_MASK_CACHE)))
    return mask


def _qimage_to_bgr(img: QImage) -> np.ndarray:
    _o, bgra = as_bgra(img)
    return np.ascontiguousarray(bgra[..., :3].copy())


def _bgr_to_qimage(bgr: np.ndarray) -> QImage:
    h, w = bgr.shape[:2]
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    dest, dview = as_bgra(img)
    dview[..., :3] = bgr
    dview[..., 3] = 255
    return dest


def _paint_glass_thick(
    painter: QPainter,
    layout: LayoutResult,
    spec: FxSpec,
    backdrop: QImage | None,
    video_w: int,
    video_h: int,
    skip_blur: bool,
) -> None:
    from cutora.render.textfx.glass_thick import (
        compose_thick_base, shade_thick_bgr)
    rect = _glass_rect(layout, spec)
    radius = spec.bg.radius_em * layout.em
    local = QRectF(rect)
    local.translate(-layout.origin[0], -layout.origin[1])
    local_path = _round_rect_path(local, radius)
    target_w = max(1, int(round(local.width())))
    target_h = max(1, int(round(local.height())))
    sigma = spec.bg.glass_sigma_em * layout.em
    from cutora.render.textfx.glass_thick import rim_sigma_px
    sigma_rim = rim_sigma_px(layout.em)
    band_px = int(math.ceil(spec.bg.glass_thickness_em * layout.em * 1.5 + 2.0))
    pair = None
    if backdrop is not None and not backdrop.isNull():
        pair = glass_blur_pair(
            backdrop, rect, video_w, video_h, sigma, skip_blur,
            sigma_rim=sigma_rim, band_px=band_px)
    strong_bgr = None
    weak_bgr = None
    if pair is not None:
        strong_bgr, weak_bgr = pair
    if strong_bgr is None:
        r, g, b = rgb_tuple(spec.bg.color)
        strong_bgr = np.empty((target_h, target_w, 3), dtype=np.uint8)
        strong_bgr[..., 0] = b
        strong_bgr[..., 1] = g
        strong_bgr[..., 2] = r
    sh, sw = strong_bgr.shape[:2]
    from cutora.core import preview_perf
    t_comp = preview_perf.now()
    # 1:1 crop can be 1 px off the rounded glass rect. Snap so the pixel
    # lock stays on the target grid. Preview (800/1920) is hundreds of px
    # smaller and must not snap — that was the sx<0.5 quality switch.
    if abs(sw - target_w) <= 2 and abs(sh - target_h) <= 2 and (
            sw != target_w or sh != target_h):
        strong_bgr = cv2.resize(
            strong_bgr, (target_w, target_h), interpolation=cv2.INTER_LINEAR)
        if weak_bgr is not None:
            weak_bgr = cv2.resize(
                weak_bgr, (target_w, target_h), interpolation=cv2.INTER_LINEAR)
        sw, sh = target_w, target_h
    # Backdrop (refract + tint) stays at sample res. Geometry shade maps
    # run at target res — cached, so play frames only look them up.
    scale = sw / float(target_w) if target_w else 1.0
    em_s = layout.em * scale
    radius_s = radius * scale
    mask_s = _rounded_mask_u8(sw, sh, radius_s)
    base_s = compose_thick_base(
        strong_bgr, weak_bgr, mask_s, em_s,
        spec.bg.glass_thickness_em, spec.bg.glass_refraction,
        spec.bg.glass_bevel, spec.bg.glass_sheen, spec.bg.glass_light_deg,
        spec.bg.color, spec.bg.alpha, radius_s)
    if sw != target_w or sh != target_h:
        base = cv2.resize(
            base_s, (target_w, target_h), interpolation=cv2.INTER_LINEAR)
    else:
        base = base_s
    mask_t = _rounded_mask_u8(target_w, target_h, radius)
    composed = shade_thick_bgr(
        base, mask_t, layout.em,
        spec.bg.glass_thickness_em, spec.bg.glass_bevel,
        spec.bg.glass_sheen, spec.bg.glass_light_deg, radius)
    preview_perf.add_span("fx_glass_compose", preview_perf.now() - t_comp)
    layer = _bgr_to_qimage(composed)
    painter.save()
    painter.setClipPath(local_path)
    painter.drawImage(local.topLeft(), layer)
    _paint_bg_grad(painter, local, local_path, spec, overlay=True)
    painter.restore()
    if spec.bg.glass_border:
        _paint_glass_border(painter, local, radius, 0.5)


def _path_upto(path: QPainterPath, u: float) -> QPainterPath:
    """Prefix of *path* at arc-length fraction *u*, contour by contour.

    Uses ``pointAtPercent`` on each flattened subpath so glyphs stay
    disconnected (no lineTo across a moveTo).
    """
    u = max(0.0, min(1.0, float(u)))
    if u <= 0.0:
        return QPainterPath()
    if u >= 1.0:
        return QPainterPath(path)
    polys = path.toSubpathPolygons()
    lengths = []
    total = 0.0
    for poly in polys:
        n = len(poly)
        acc = 0.0
        for i in range(n - 1):
            acc += math.hypot(
                poly[i + 1].x() - poly[i].x(),
                poly[i + 1].y() - poly[i].y())
        lengths.append(acc)
        total += acc
    if total <= 1e-6:
        return QPainterPath()
    budget = total * u
    out = QPainterPath()
    for poly, length in zip(polys, lengths):
        if budget <= 0.0:
            break
        n = len(poly)
        if n < 2:
            continue
        if budget >= length - 1e-9:
            out.moveTo(poly[0])
            for i in range(1, n):
                out.lineTo(poly[i])
            budget -= length
            continue
        sub = QPainterPath()
        sub.moveTo(poly[0])
        for i in range(1, n):
            sub.lineTo(poly[i])
        frac = budget / length if length > 1e-9 else 1.0
        steps = max(4, int(n * frac) + 2)
        out.moveTo(sub.pointAtPercent(0.0))
        for i in range(1, steps + 1):
            out.lineTo(sub.pointAtPercent(frac * i / steps))
        budget = 0.0
    return out


def paint_stroke_fill(
    painter: QPainter,
    pairs: list[tuple[QPainterPath, WordRun]],
    layout: LayoutResult,
    spec: FxSpec,
    fill_color: str | None = None,
    path_progress: float = 1.0,
    fill_progress: float = 1.0,
) -> None:
    painter.save()
    try:
        painter.translate(-layout.origin[0], -layout.origin[1])
        progress = max(0.0, min(1.0, float(path_progress)))
        if spec.stroke.live():
            width_px = spec.stroke.width_em * layout.em
            pen = QPen(qcolor_hex(spec.stroke.color), width_px)
            join = (Qt.PenJoinStyle.RoundJoin if spec.stroke.join == "round"
                    else Qt.PenJoinStyle.MiterJoin)
            pen.setJoinStyle(join)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(pen)
            for path, _run in pairs:
                sp = path if progress >= 0.999 else _path_upto(path, progress)
                if not sp.isEmpty():
                    painter.strokePath(sp, pen)
        if progress < 0.999:
            return
        fill_a = max(0.0, min(1.0, float(fill_progress)))
        if fill_a <= 1e-6:
            return
        painter.setPen(Qt.PenStyle.NoPen)
        for path, run in pairs:
            if (run.fill_override and emphasis_is_gradient(spec)
                    and not spec.emphasis.palette):
                rect = path.boundingRect()
                if fill_a < 0.999:
                    br = rect.adjusted(-2.0, -2.0, 2.0, 2.0)
                    tw = max(1, int(math.ceil(br.width())))
                    th = max(1, int(math.ceil(br.height())))
                    layer = QImage(tw, th, QImage.Format.Format_ARGB32_Premultiplied)
                    layer.fill(0)
                    lp = QPainter(layer)
                    try:
                        lp.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                        lp.translate(-br.x(), -br.y())
                        lp.fillPath(path, emphasis_brush(spec, rect))
                    finally:
                        lp.end()
                    layer = mul_premul_alpha(layer, fill_a)
                    painter.drawImage(br.topLeft(), layer)
                else:
                    painter.fillPath(path, emphasis_brush(spec, rect))
                continue
            solid = run.fill_override or fill_color
            if solid and spec.fill.type == "solid":
                painter.fillPath(path, QBrush(qcolor_hex(solid, fill_a)))
                continue
            if run.fill_override:
                painter.fillPath(path, QBrush(qcolor_hex(run.fill_override, fill_a)))
                continue
            if spec.fill.type == "gradient":
                stops = [c for c in (spec.fill.colors or []) if c]
                if len(stops) < 2:
                    hex_c = stops[0] if stops else DEFAULT_FILL
                    painter.fillPath(path, QBrush(qcolor_hex(hex_c, fill_a)))
                    continue
            rect = path.boundingRect()
            if fill_a < 0.999:
                br = rect.adjusted(-2.0, -2.0, 2.0, 2.0)
                tw = max(1, int(math.ceil(br.width())))
                th = max(1, int(math.ceil(br.height())))
                layer = QImage(tw, th, QImage.Format.Format_ARGB32_Premultiplied)
                layer.fill(0)
                lp = QPainter(layer)
                try:
                    lp.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                    lp.translate(-br.x(), -br.y())
                    lp.fillPath(path, fill_brush(spec, rect, None))
                    if spec.fill.type == "metal":
                        paint_metal_highlight(lp, path, rect, spec)
                finally:
                    lp.end()
                layer = mul_premul_alpha(layer, fill_a)
                painter.drawImage(br.topLeft(), layer)
                continue
            painter.fillPath(path, fill_brush(spec, rect, None))
            if spec.fill.type == "metal":
                paint_metal_highlight(painter, path, rect, spec)
    finally:
        painter.restore()


def paint_run_underlines(
    painter: QPainter,
    layout: LayoutResult,
    spec: FxSpec,
) -> None:
    """Static underline for rich-text runs with underline=True."""
    has = False
    for line in layout.lines:
        for run in line.runs:
            if run.underline:
                has = True
                break
        if has:
            break
    if not has:
        return
    painter.save()
    try:
        painter.translate(-layout.origin[0], -layout.origin[1])
        for line in layout.lines:
            for run in line.runs:
                if not run.underline or not run.text:
                    continue
                em = float(run.fontsize_px or layout.em)
                width = max(1.5, 0.08 * em)
                y = run.baseline + 0.20 * em
                hex_c = run.fill_override
                if not hex_c:
                    cols = spec.fill.colors if spec.fill.colors else []
                    hex_c = cols[0] if cols else DEFAULT_FILL
                pen = QPen(qcolor_hex(hex_c), width)
                pen.setCapStyle(Qt.PenCapStyle.FlatCap)
                painter.setPen(pen)
                painter.drawLine(QLineF(run.x, y, run.x + max(1.0, run.w), y))
    finally:
        painter.restore()


def paint_emphasis_line(
    painter: QPainter,
    layout: LayoutResult,
    spec: FxSpec,
    t: float | None,
) -> None:
    """Underline under emphasized words, 180 ms LTR. Round caps."""
    if t is None or spec.emphasis.anim != "line_draw":
        return
    from cutora.render.textfx.anim import LINE_DRAW_DUR_S, LINE_DRAW_EM, LINE_DRAW_UNDER_EM
    em = layout.em
    width = LINE_DRAW_EM * em
    painter.save()
    try:
        painter.translate(-layout.origin[0], -layout.origin[1])
        pen = QPen(_qcolor(spec.emphasis.color, 1.0), width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        for line in layout.lines:
            for run in line.runs:
                if not run.emphasized:
                    continue
                u = (float(t) - float(run.t_start)) / LINE_DRAW_DUR_S
                if u <= 0.0:
                    continue
                u = min(1.0, u)
                y = run.baseline + LINE_DRAW_UNDER_EM * em
                painter.drawLine(QLineF(run.x, y, run.x + run.w * u, y))
    finally:
        painter.restore()


def _empty_roi(layout: LayoutResult) -> QImage:
    img = QImage(layout.roi_w, layout.roi_h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    return img


def _qrect_to_fade_box(rect: QRectF, pad_px: float = 1.0) -> tuple[int, int, int, int]:
    """Integer (x, y, w, h). *pad_px* covers the 1 px glass-border stroke."""
    x0 = int(math.floor(rect.x() - pad_px))
    y0 = int(math.floor(rect.y() - pad_px))
    x1 = int(math.ceil(rect.x() + rect.width() + pad_px))
    y1 = int(math.ceil(rect.y() + rect.height() + pad_px))
    return (x0, y0, max(1, x1 - x0), max(1, y1 - y0))


def _bg_fade_boxes(layout: LayoutResult, spec: FxSpec) -> list[tuple[int, int, int, int]]:
    """ROI-local boxes for bg.fade. One box per background panel, not the ROI."""
    ox, oy = layout.origin[0], layout.origin[1]
    if spec.bg.kind() in ("glass", "box"):
        local = QRectF(_glass_rect(layout, spec))
        local.translate(-ox, -oy)
        return [_qrect_to_fade_box(local)]
    if spec.bg.kind() == "texture":
        local = bg_panel_rect(
            layout.text_x, layout.text_y, layout.text_w, layout.text_h,
            spec, layout.em)
        local.translate(-ox, -oy)
        extra = 1.0
        if spec.bg.texture == "paper":
            extra += 0.10 * layout.em
        return [_qrect_to_fade_box(local, extra)]
    if spec.bg.kind() == "bubble":
        boxes = []
        for line in layout.lines:
            local = bg_panel_rect(line.x, line.y, line.w, line.h, spec, layout.em)
            local.translate(-ox, -oy)
            boxes.append(_qrect_to_fade_box(local))
        return boxes
    return []


# Glyph-hug mask. Independent of time, colour and backdrop. Same cap style
# as _MASK_CACHE: the distance field is the expensive part, and glass
# repaints every frame.
_HUG_LOCK = threading.Lock()
_HUG_CACHE: OrderedDict = OrderedDict()
_HUG_BUILDS = 0
_HUG_CACHE_MAX = 32
_HUG_FEATHER_SIGMA = 1.0
_HUG_GLYPH_MIN = 127


def reset_hug_mask_stats() -> None:
    global _HUG_BUILDS
    _HUG_BUILDS = 0


def clear_hug_mask_cache() -> None:
    reset_hug_mask_stats()
    with _HUG_LOCK:
        _HUG_CACHE.clear()


def hug_mask_builds() -> int:
    return int(_HUG_BUILDS)


def _hug_cache_key(layout: LayoutResult, spec: FxSpec):
    runs = []
    for line in layout.lines:
        for run in line.runs:
            qf = run.qfont
            runs.append((
                str(run.text or ""),
                str(run.font_family or ""),
                round(float(run.fontsize_px or 0.0), 2),
                round(float(run.x), 2),
                round(float(run.y), 2),
                round(float(run.w), 2),
                round(float(run.h), 2),
                round(float(run.baseline), 2),
                round(float(run.rot_deg or 0.0), 2),
                round(float(run.pad_px or 0.0), 2),
                round(float(run.tracking_em or 0.0), 4),
                round(float(run.orient_deg or 0.0), 2),
                round(float(run.glyph_ox or 0.0), 2),
                round(float(run.glyph_oy or 0.0), 2),
                bool(qf.bold()) if qf is not None else False,
                bool(qf.italic()) if qf is not None else False,
                int(qf.weight()) if qf is not None else 0,
            ))
    st = spec.stroke
    return (
        tuple(runs),
        round(float(layout.em), 3),
        int(layout.origin[0]), int(layout.origin[1]),
        int(layout.roi_w), int(layout.roi_h),
        bool(st.live()),
        round(float(st.width_em or 0.0), 4),
        str(st.join or "round"),
        round(float(spec.bg.hug_pad_em), 4),
        round(float(spec.bg.hug_join_em), 4),
        round(float(spec.bg.radius_em), 4),
        tuple(round(float(v), 4) for v in (spec.bg.grow_em or (0, 0, 0, 0))),
        str(spec.bg.size_mode or ""),
        tuple(
            (
                round(float(ln.x), 2),
                round(float(ln.w), 2),
                round(float(ln.baseline), 2),
            )
            for ln in layout.lines
        ),
    )


def _shift_mask(mask: np.ndarray, dx: float, dy: float) -> np.ndarray:
    ix = int(round(float(dx or 0.0)))
    iy = int(round(float(dy or 0.0)))
    if ix == 0 and iy == 0:
        return mask
    out = np.zeros_like(mask)
    h, w = mask.shape[:2]
    src_x0 = max(0, -ix)
    src_y0 = max(0, -iy)
    src_x1 = min(w, w - ix)
    src_y1 = min(h, h - iy)
    if src_x1 <= src_x0 or src_y1 <= src_y0:
        return out
    dst_x0 = max(0, ix)
    dst_y0 = max(0, iy)
    out[
        dst_y0:dst_y0 + (src_y1 - src_y0),
        dst_x0:dst_x0 + (src_x1 - src_x0),
    ] = mask[src_y0:src_y1, src_x0:src_x1]
    return out


def _hug_glyph_u8(layout: LayoutResult, spec: FxSpec) -> np.ndarray:
    """Binary glyph ∪ stroke. Glow is not included."""
    paths = [p for p, _run in collect_run_paths(layout, {}, spec, None)]
    _img, alpha_u8 = _text_mask(layout, paths, spec)
    return np.where(alpha_u8 > _HUG_GLYPH_MIN, np.uint8(255), np.uint8(0))


def _half_stroke_px(glyph: np.ndarray) -> float:
    """Median of the interior distance field.

    distanceTransform measures distance to the nearest zero. Inside the
    glyph that is the distance to the stroke edge. The median is a robust
    stand-in for half the stroke width, and the close radius may not
    exceed it — a larger ellipse fills counters such as the hole in H.
    """
    if glyph.size == 0 or int(glyph.max()) == 0:
        return 0.0
    dist = cv2.distanceTransform(glyph, cv2.DIST_L2, 5)
    inside = dist[glyph > 0]
    if inside.size == 0:
        return 0.0
    return float(np.median(inside))


def _close_radius_px(glyph: np.ndarray, em: float, hug_join_em: float) -> float:
    """Pixels for MORPH_CLOSE. 0 skips the close."""
    requested = max(0.0, float(hug_join_em)) * float(em)
    if requested <= 1e-8:
        return 0.0
    return float(min(requested, _half_stroke_px(glyph)))


def hug_close_radius_px(layout: LayoutResult, spec: FxSpec) -> float:
    """Actual close radius in px for this layout.

    ``min(hug_join_em * em, median interior distance)``. The kernel uses
    ``round`` of this value; 0 means the close is skipped. ``bg.radius_em``
    is not an input.
    """
    return _close_radius_px(
        _hug_glyph_u8(layout, spec), layout.em, spec.bg.hug_join_em)


def _line_em_metrics(line, em: float) -> tuple[float, float]:
    """Ascent, descent of this line's primary face measured at ``em``.

    A run whose size is not ``em`` (emphasis scale) does not set the band,
    so a lowercase line and a cap-height line stay the same height.
    """
    run = None
    for candidate in line.runs:
        if candidate.qfont is None:
            continue
        if abs(float(candidate.fontsize_px or 0.0) - float(em)) <= 0.51:
            run = candidate
            break
        if run is None:
            run = candidate
    if (
        run is not None and run.qfont is not None
        and abs(float(run.fontsize_px or 0.0) - float(em)) <= 0.51
    ):
        fm = QFontMetricsF(run.qfont)
        return float(fm.ascent()), float(fm.descent())
    family = run.font_family if run is not None else "Arimo"
    bold = bool(run.qfont.bold()) if run is not None and run.qfont is not None else False
    italic = bool(run.qfont.italic()) if run is not None and run.qfont is not None else False
    font = _qfont(family or "Arimo", em, bold, italic, 0.0)
    fm = QFontMetricsF(font)
    return float(fm.ascent()), float(fm.descent())


def line_hug_boxes(layout: LayoutResult, spec: FxSpec) -> list[tuple[float, float, float, float]]:
    """Canvas-space (x0, y0, x1, y1) slabs, one per non-empty line.

    Height is the primary face's em ascent/descent around the baseline.
    Width is that line's own ``x .. x+w``. ``hug_pad_em`` moves every edge
    (negative shrinks). Empty lines are skipped.
    """
    em = float(layout.em)
    pad = float(spec.bg.hug_pad_em) * em
    gl, gt, gr, gb = spec.bg.grow_px(em)   # Q129：四边扩展
    boxes: list[tuple[float, float, float, float]] = []
    for line in layout.lines:
        if not str(line.text or "").strip():
            continue
        if float(line.w) <= 0.5:
            continue
        asc, desc = _line_em_metrics(line, em)
        x0 = float(line.x) - pad - gl
        x1 = float(line.x) + float(line.w) + pad + gr
        y0 = float(line.baseline) - asc - pad - gt
        y1 = float(line.baseline) + desc + pad + gb
        if x1 - x0 < 1.0 or y1 - y0 < 1.0:
            continue
        boxes.append((x0, y0, x1, y1))
    return boxes


def line_hug_radius_px(layout: LayoutResult, spec: FxSpec) -> float:
    """Corner radius actually used for ``line_hug``.

    ``min(radius_em * em, half the narrowest slab's height, half its width)``.
    The cap is what keeps the opening from deleting a short or thin line.
    0 skips the round.
    """
    requested = max(0.0, float(spec.bg.radius_em)) * float(layout.em)
    boxes = line_hug_boxes(layout, spec)
    if not boxes or requested <= 1e-6:
        return 0.0
    min_w = min(x1 - x0 for x0, _y0, x1, _y1 in boxes)
    min_h = min(y1 - y0 for _x0, y0, _x1, y1 in boxes)
    return float(min(requested, min_w * 0.5, min_h * 0.5))


def _dt_erode(mask: np.ndarray, radius: float) -> np.ndarray:
    """Disk erosion via distance-to-background. ``radius`` is in pixels."""
    if radius <= 1e-6 or int(mask.max()) == 0:
        return mask
    dist = cv2.distanceTransform(mask, cv2.DIST_L2, 5)
    return np.where(dist + 1e-3 >= float(radius), np.uint8(255), np.uint8(0))


def _dt_dilate(mask: np.ndarray, radius: float) -> np.ndarray:
    """Disk dilation via distance-to-foreground. ``radius`` is in pixels."""
    if radius <= 1e-6 or int(mask.max()) == 0:
        return mask
    inv = np.where(mask > 0, np.uint8(0), np.uint8(255))
    dist = cv2.distanceTransform(inv, cv2.DIST_L2, 5)
    return np.where(dist <= float(radius) + 1e-3, np.uint8(255), np.uint8(0))


def _round_line_corners(mask: np.ndarray, radius: float) -> np.ndarray:
    """Open (convex corners) then close (concave corners). Disk, not a box."""
    if radius <= 1e-6 or int(mask.max()) == 0:
        return mask
    opened = _dt_dilate(_dt_erode(mask, radius), radius)
    return _dt_erode(_dt_dilate(opened, radius), radius)


def _raster_line_boxes(layout: LayoutResult, boxes) -> np.ndarray:
    h = max(1, int(layout.roi_h))
    w = max(1, int(layout.roi_w))
    mask = np.zeros((h, w), np.uint8)
    ox, oy = layout.origin
    for x0, y0, x1, y1 in boxes:
        ix0 = max(0, int(math.floor(x0 - ox)))
        iy0 = max(0, int(math.floor(y0 - oy)))
        ix1 = min(w, int(math.ceil(x1 - ox)))
        iy1 = min(h, int(math.ceil(y1 - oy)))
        if ix1 > ix0 and iy1 > iy0:
            mask[iy0:iy1, ix0:ix1] = 255
    return mask


def _build_line_hug_mask(layout: LayoutResult, spec: FxSpec) -> np.ndarray:
    """0–1 alpha. Union of per-line slabs, corners rounded by radius_em."""
    global _HUG_BUILDS
    hard = _raster_line_boxes(layout, line_hug_boxes(layout, spec))
    radius = line_hug_radius_px(layout, spec)
    hard = _round_line_corners(hard, radius)
    soft = cv2.GaussianBlur(
        hard.astype(np.float32), (0, 0), sigmaX=_HUG_FEATHER_SIGMA)
    _HUG_BUILDS += 1
    return np.clip(soft * (1.0 / 255.0), 0.0, 1.0)


def _grow_mask_asym(hard: np.ndarray, grow_px) -> np.ndarray:
    """Q129：四边各自向外长 (左, 上, 右, 下) 像素。全 0 时原样返回。

    矩形核 (l+r+1)×(t+b+1)、锚点 (r, b)：膨胀后左边多出 l、右边多出 r、
    上边多出 t、下边多出 b（实测过，别改锚点）。负值这里不收缩 —— 抠字
    模式收缩会咬到描边，跟 hug_pad_em 的处理一致。
    """
    l, t, r, b = (max(0, int(round(float(v)))) for v in grow_px)
    if not (l or t or r or b) or int(hard.max()) == 0:
        return hard
    kernel = np.ones((t + b + 1, l + r + 1), np.uint8)
    return cv2.dilate(hard, kernel, anchor=(r, b))


def _build_hug_mask(layout: LayoutResult, spec: FxSpec) -> np.ndarray:
    """0–1 alpha. Glyph ∪ stroke, dilated by hug_pad, closed by hug_join."""
    global _HUG_BUILDS
    glyph = _hug_glyph_u8(layout, spec)
    # Shrinking would bite the strokes. line_hug is the mode that allows it.
    r = max(0.0, float(spec.bg.hug_pad_em)) * float(layout.em)
    if int(glyph.max()) == 0:
        hard = glyph
    else:
        inv = np.where(glyph > 0, np.uint8(0), np.uint8(255))
        dist = cv2.distanceTransform(inv, cv2.DIST_L2, 5)
        hard = np.where(dist <= r + 1e-3, np.uint8(255), np.uint8(0))
    hard = _grow_mask_asym(hard, spec.bg.grow_px(layout.em))
    radius = _close_radius_px(glyph, layout.em, spec.bg.hug_join_em)
    k = int(round(radius))
    if k > 0:
        kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (k * 2 + 1, k * 2 + 1))
        hard = cv2.morphologyEx(hard, cv2.MORPH_CLOSE, kernel)
    soft = cv2.GaussianBlur(
        hard.astype(np.float32), (0, 0), sigmaX=_HUG_FEATHER_SIGMA)
    _HUG_BUILDS += 1
    return np.clip(soft * (1.0 / 255.0), 0.0, 1.0)


def hug_glyph_mask(layout: LayoutResult, spec: FxSpec) -> np.ndarray:
    """Cached hug alpha for this layout. Offset is applied by the caller."""
    key = _hug_cache_key(layout, spec)
    with _HUG_LOCK:
        hit = _HUG_CACHE.get(key)
        if hit is not None:
            _HUG_CACHE.move_to_end(key)
            return hit.copy()
    if spec.bg.size_mode == "line_hug":
        mask = _build_line_hug_mask(layout, spec)
    else:
        mask = _build_hug_mask(layout, spec)
    with _HUG_LOCK:
        _HUG_CACHE[key] = mask
        _HUG_CACHE.move_to_end(key)
        while len(_HUG_CACHE) > _HUG_CACHE_MAX:
            _HUG_CACHE.popitem(last=False)
    return mask.copy()


def _hug_cover_spec(layout: LayoutResult, spec: FxSpec) -> FxSpec:
    """Fixed, sharp slab the size of the ROI. The hug mask supplies the shape."""
    bg = spec.bg
    if bg.kind() == "bubble":
        bg = replace(bg, type="box")
    bw = float(max(1, int(layout.roi_w)))
    bh = float(max(1, int(layout.roi_h)))
    ox, oy = layout.origin
    off_x = (float(ox) + bw * 0.5) - (
        float(layout.text_x) + float(layout.text_w) * 0.5)
    off_y = (float(oy) + bh * 0.5) - (
        float(layout.text_y) + float(layout.text_h) * 0.5)
    bg = replace(
        bg,
        size_mode="fixed",
        radius_em=0.0,
        width_px=bw,
        height_px=bh,
        offset_x=off_x,
        offset_y=off_y,
    )
    return replace(spec, bg=bg)


def _multiply_mask(img: QImage, mask: np.ndarray) -> QImage:
    owned, bgra = as_bgra(img)
    h, w = bgra.shape[:2]
    if mask.shape[0] != h or mask.shape[1] != w:
        mask = cv2.resize(
            mask.astype(np.float32), (w, h), interpolation=cv2.INTER_LINEAR)
    a = np.clip(mask.astype(np.float32), 0.0, 1.0)
    scaled = bgra.astype(np.float32) * a[..., None]
    bgra[...] = np.clip(scaled, 0, 255).astype(np.uint8)
    return owned


def raster_bg(
    layout: LayoutResult,
    spec: FxSpec,
    backdrop: QImage | None,
    video_w: int,
    video_h: int,
    skip_blur: bool = False,
) -> QImage | None:
    """Background layer only (box / glass / texture / bubble)."""
    if spec.bg.kind() == "none":
        return None
    if spec.bg.size_mode in ("hug_glyphs", "line_hug"):
        return _raster_bg_hug(
            layout, spec, backdrop, video_w, video_h, skip_blur)
    return _raster_bg_rect(
        layout, spec, backdrop, video_w, video_h, skip_blur)


def _raster_bg_hug(
    layout: LayoutResult,
    spec: FxSpec,
    backdrop: QImage | None,
    video_w: int,
    video_h: int,
    skip_blur: bool,
) -> QImage | None:
    mask = hug_glyph_mask(layout, spec)
    mask = _shift_mask(mask, spec.bg.offset_x, spec.bg.offset_y)
    cover = _hug_cover_spec(layout, spec)
    img = _raster_bg_rect(
        layout, cover, backdrop, video_w, video_h, skip_blur)
    if img is None:
        return None
    return _multiply_mask(img, mask)


def _raster_bg_rect(
    layout: LayoutResult,
    spec: FxSpec,
    backdrop: QImage | None,
    video_w: int,
    video_h: int,
    skip_blur: bool = False,
) -> QImage | None:
    """Rectangular / rounded panel. fit_text and fixed stay on this path."""
    if spec.bg.kind() == "none":
        return None
    img = _empty_roi(layout)
    p = QPainter(img)
    try:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if spec.bg.kind() in ("glass", "box"):
            paint_glass(p, layout, spec, backdrop, video_w, video_h, skip_blur)
        elif spec.bg.kind() == "texture":
            from cutora.render.textfx.paint_bg import paint_texture_bg
            paint_texture_bg(p, layout, spec)
        elif spec.bg.kind() == "bubble":
            from cutora.render.textfx.paint_bg import paint_bubble_bg
            paint_bubble_bg(p, layout, spec)
    finally:
        p.end()
    if spec.bg.kind() == "texture" and spec.bg.texture == "paper":
        img = _feather_alpha(img, 0.6)
    fade = spec.bg.fade
    _fade_offs = (
        fade.top_offset, fade.bottom_offset,
        fade.left_offset, fade.right_offset,
    )
    from cutora.render.textfx.fade import offsets_active
    if max(fade.top, fade.bottom, fade.left, fade.right) > 0 or offsets_active(_fade_offs):
        from cutora.render.textfx.fade import apply_edge_fade
        img = apply_edge_fade(
            img, fade.top, fade.bottom, fade.left, fade.right,
            boxes=_bg_fade_boxes(layout, spec),
            offsets=_fade_offs)
    return img


def _feather_alpha(img: QImage, sigma: float) -> QImage:
    """Soft torn edge: blur alpha, keep premul RGB consistent.

    Must not scale premul RGB by ``blurred / max(a, 1)``. At a≈0 that
    ratio explodes and saturates leftover RGB (T04f-6b-1 cyan specks on
    paper texture). Un-premultiply, zero tiny-alpha RGB, blur alpha,
    re-premultiply.
    """
    if sigma <= 0.05:
        return img
    owned, bgra = as_bgra(img)
    a = bgra[..., 3].astype(np.float32)
    if float(a.max()) < 1.0:
        return owned
    rgb = bgra[..., :3].astype(np.float32)
    tiny = a <= 1.0
    straight = np.zeros_like(rgb)
    ok = ~tiny
    if np.any(ok):
        straight[ok] = np.clip(
            rgb[ok] * (255.0 / a[ok][..., None]), 0.0, 255.0)
    blurred = cv2.GaussianBlur(a, (0, 0), sigmaX=float(sigma))
    out_rgb = straight * (blurred[..., None] / 255.0)
    bgra[..., :3] = np.clip(out_rgb, 0, 255).astype(np.uint8)
    bgra[..., 3] = np.clip(blurred, 0, 255).astype(np.uint8)
    return owned


def raster_text(
    layout: LayoutResult,
    spec: FxSpec,
    style: dict,
    skip_blur: bool = False,
    t: float | None = None,
    xf=None,
) -> QImage:
    """Text layer: shadow → glow → extrude → stroke → fill. No background."""
    img = _empty_roi(layout)
    pairs = collect_run_paths(layout, style, spec, t, xf=xf)
    paths = [p for p, _ in pairs]
    mask_img, mask_u8 = _text_mask(layout, paths, spec)
    _ = mask_img
    progress = 1.0
    fill_p = 1.0
    if xf is not None and str(getattr(xf, "clip_dir", "")) == "path":
        progress = max(0.0, min(1.0, float(xf.clip)))
    if xf is not None:
        fill_p = max(0.0, min(1.0, float(xf.fill_progress)))
    p = QPainter(img)
    try:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if progress >= 0.999:
            face = neon_face_hex(spec) or face_fill_hex(spec)
            paint_shadow(p, mask_u8, spec, layout.em, skip_blur)
            paint_glow(p, mask_u8, spec, layout.em, skip_blur)
            from cutora.render.textfx.paint_extrude import paint_extrude
            paint_extrude(p, paths, layout, spec, face)
        paint_stroke_fill(
            p, pairs, layout, spec, fill_color=neon_face_hex(spec),
            path_progress=progress, fill_progress=fill_p)
        paint_run_underlines(p, layout, spec)
        paint_emphasis_line(p, layout, spec, t)
    finally:
        p.end()
    return img


def _text_box_roi(layout: LayoutResult) -> QRectF:
    ox, oy = layout.origin
    return QRectF(
        layout.text_x - ox,
        layout.text_y - oy,
        max(1.0, layout.text_w),
        max(1.0, layout.text_h),
    )


def _wipe_rect(layout: LayoutResult, xf) -> QRectF:
    box = _text_box_roi(layout)
    box.adjust(-1.0, -1.0, 1.0, 1.0)
    c = max(0.0, min(1.0, float(xf.clip)))
    d = str(xf.clip_dir or "ltr")
    if d == "rtl":
        return QRectF(
            box.x() + (1.0 - c) * box.width(), box.y(),
            c * box.width(), box.height())
    if d == "ttb":
        return QRectF(box.x(), box.y(), box.width(), c * box.height())
    if d == "btt":
        return QRectF(
            box.x(), box.y() + (1.0 - c) * box.height(),
            box.width(), c * box.height())
    return QRectF(box.x(), box.y(), c * box.width(), box.height())


def _blur_premul(img: QImage, sigma_px: float) -> QImage:
    """Premul BGRA Gaussian. Same ds as _blur_qimage; all 4 channels together.

    Crop to ink + 3σ(t) so a shrinking σ does not blur the max-pad ROI.
    Resize and blur are linear, so premul stays valid through the round-trip.
    """
    from cutora.render.textfx.anim import BLUR_SKIP_PX
    if sigma_px <= BLUR_SKIP_PX:
        return img
    owned, bgra = as_bgra(img)
    h, w = bgra.shape[:2]
    pad = int(math.ceil(3.0 * float(sigma_px)))
    alpha = bgra[..., 3]
    ys, xs = np.nonzero(alpha)
    if xs.size == 0:
        return owned
    x0 = max(0, int(xs.min()) - pad)
    y0 = max(0, int(ys.min()) - pad)
    x1 = min(w, int(xs.max()) + 1 + pad)
    y1 = min(h, int(ys.max()) + 1 + pad)
    crop = np.ascontiguousarray(bgra[y0:y1, x0:x1])
    ch, cw = crop.shape[:2]
    ds = _gauss_ds(sigma_px, cw, ch)
    if ds <= 1:
        blurred = cv2.GaussianBlur(crop, (0, 0), sigmaX=float(sigma_px))
    else:
        small = cv2.resize(
            crop, (max(1, cw // ds), max(1, ch // ds)),
            interpolation=cv2.INTER_AREA)
        small = cv2.GaussianBlur(
            small, (0, 0), sigmaX=float(sigma_px) / float(ds))
        blurred = cv2.resize(small, (cw, ch), interpolation=cv2.INTER_LINEAR)
    bgra[y0:y1, x0:x1] = blurred
    return owned


def _ink_xyxy(img: QImage | None) -> tuple[int, int, int, int] | None:
    """Inclusive ROI-pixel bbox of alpha>8, or None if empty."""
    if img is None or img.isNull():
        return None
    _o, bgra = as_bgra(img)
    a = bgra[..., 3]
    ys, xs = np.nonzero(a > 8)
    if xs.size == 0:
        return None
    return (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))


def _xf_canvas_bbox(layout, xf, ink=None) -> tuple[float, float, float, float]:
    """Axis-aligned bbox of the scaled layer in canvas coordinates.

    Prefer painted ink (ROI pixels mapped through origin); fall back to
    layout.text_w/h when the image is not available.
    """
    em = float(getattr(layout, "em", 1.0) or 1.0)
    px = float(getattr(layout, "cx", 0.0) or 0.0)
    py = float(getattr(layout, "cy", 0.0) or 0.0)
    dx = float(getattr(xf, "dx_em", 0.0) or 0.0) * em
    dy = float(getattr(xf, "dy_em", 0.0) or 0.0) * em
    sx = max(abs(float(getattr(xf, "scale_x", 1.0) or 1.0)), 1e-6)
    sy = max(abs(float(getattr(xf, "scale_y", 1.0) or 1.0)), 1e-6)
    rot = float(getattr(xf, "rot_deg", 0.0) or 0.0) * math.pi / 180.0
    if ink is not None:
        ox, oy = getattr(layout, "origin", (0, 0))
        left = float(ox) + float(ink[0])
        top = float(oy) + float(ink[1])
        right = float(ox) + float(ink[2])
        bot = float(oy) + float(ink[3])
    else:
        hw = max(0.5, float(getattr(layout, "text_w", 1.0) or 1.0) * 0.5)
        hh = max(0.5, float(getattr(layout, "text_h", 1.0) or 1.0) * 0.5)
        left, top = px - hw, py - hh
        right, bot = px + hw, py + hh
    cos_r, sin_r = math.cos(rot), math.sin(rot)
    xs, ys = [], []
    for x, y in ((left, top), (right, top), (right, bot), (left, bot)):
        qx = (x - px) * sx
        qy = (y - py) * sy
        xs.append(px + dx + qx * cos_r - qy * sin_r)
        ys.append(py + dy + qx * sin_r + qy * cos_r)
    x0, y0 = min(xs), min(ys)
    return (x0, y0, max(xs) - x0, max(ys) - y0)


def _bbox_inside_safe(x, y, w, h, video_w, video_h, margin, pad=0.0) -> bool:
    ix = float(video_w) * float(margin) + float(pad)
    iy = float(video_h) * float(margin) + float(pad)
    return (x >= ix and y >= iy
            and x + w <= float(video_w) - ix
            and y + h <= float(video_h) - iy)


def _fit_uniform_scale(x, y, bw, bh, cx, cy, s, vw, vh, ix, iy) -> float:
    """Largest uniform scale around (cx, cy) that keeps AABB inside insets."""
    s = max(abs(float(s)), 1e-6)
    k_l = (x - cx) / s
    k_r = (x + bw - cx) / s
    k_t = (y - cy) / s
    k_b = (y + bh - cy) / s
    caps = [s]
    if k_l < -1e-9:
        caps.append((ix - cx) / k_l)
    if k_r > 1e-9:
        caps.append(((vw - ix) - cx) / k_r)
    if k_t < -1e-9:
        caps.append((iy - cy) / k_t)
    if k_b > 1e-9:
        caps.append(((vh - iy) - cy) / k_b)
    return max(0.05, min(caps))


def _clamp_xf_to_safe(xf, layout, video_w: int, video_h: int, img=None):
    """Overflow-only: in-margin overshoot is a no-op; else shrink inside SAFE.

    Inside-check is strict against SAFE_MARGIN (g10 short-circuit). Clamp
    target is the inner ceil/floor of that margin plus 1 px so painted ink
    keeps ≥1 px of room after rounding / bilinear spill. Scale ≤ 1 is
    never touched — that is layout, not overshoot.
    """
    from dataclasses import replace
    from cutora.subtitle.stylepack import SAFE_MARGIN
    sx = abs(float(getattr(xf, "scale_x", 1.0) or 1.0))
    sy = abs(float(getattr(xf, "scale_y", 1.0) or 1.0))
    if sx <= 1.0 + 1e-6 and sy <= 1.0 + 1e-6:
        return xf
    ink = _ink_xyxy(img)
    x, y, bw, bh = _xf_canvas_bbox(layout, xf, ink=ink)
    if _bbox_inside_safe(x, y, bw, bh, video_w, video_h, SAFE_MARGIN):
        return xf
    em = float(getattr(layout, "em", 1.0) or 1.0)
    cx = float(getattr(layout, "cx", 0.0) or 0.0) + float(
        getattr(xf, "dx_em", 0.0) or 0.0) * em
    cy = float(getattr(layout, "cy", 0.0) or 0.0) + float(
        getattr(xf, "dy_em", 0.0) or 0.0) * em
    # Inner integer edge of SAFE, then +1 px of stable room.
    ix = float(math.ceil(float(video_w) * SAFE_MARGIN) + 1)
    iy = float(math.ceil(float(video_h) * SAFE_MARGIN) + 1)
    s_cur = min(sx, sy)
    ns = _fit_uniform_scale(
        x, y, bw, bh, cx, cy, s_cur,
        float(video_w), float(video_h), ix, iy)
    if ns >= s_cur - 1e-6:
        return xf
    return replace(xf, scale_x=ns, scale_y=ns)


def warp_layer_xf(
    img: QImage | None, layout: LayoutResult, xf,
    video_w: int = 0, video_h: int = 0,
) -> QImage | None:
    """Bake alpha/scale/dx/dy/rot/clip/blur into the ROI. Identity is a no-op."""
    if img is None:
        return None
    from cutora.render.textfx.anim import SCROLL_GAP_EM, xf_is_identity
    if video_w and video_h and xf is not None:
        xf = _clamp_xf_to_safe(xf, layout, int(video_w), int(video_h), img)
    if xf_is_identity(xf):
        return img
    clip_dir = str(xf.clip_dir or "ltr")
    rect_clip = xf.clip < 0.999 and clip_dir in ("ltr", "rtl", "ttb", "btt")
    box_clip = clip_dir == "box"
    if rect_clip and xf.clip <= 1e-6:
        out = QImage(img.size(), img.format())
        out.fill(0)
        return out
    geo = (
        abs(xf.scale_x - 1.0) > 1e-4 or abs(xf.scale_y - 1.0) > 1e-4
        or abs(xf.dx_em) > 1e-6 or abs(xf.dy_em) > 1e-6
        or abs(xf.rot_deg) > 1e-3
    )
    alpha_only = (
        float(xf.alpha) < 0.999
        and not geo and not rect_clip and not box_clip
        and not xf.extra_color
        and float(xf.blur_sigma_em) <= 1e-6
    )
    if alpha_only:
        return mul_premul_alpha(img, xf.alpha)
    out = QImage(img.size(), img.format())
    out.fill(0)
    p = QPainter(out)
    try:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        if box_clip:
            box = _text_box_roi(layout)
            box.adjust(-1.0, -1.0, 1.0, 1.0)
            p.setClipRect(box)
        elif rect_clip:
            p.setClipRect(_wipe_rect(layout, xf))
        if geo:
            em = layout.em
            cx = layout.cx - layout.origin[0]
            cy = layout.cy - layout.origin[1]
            p.translate(cx + xf.dx_em * em, cy + xf.dy_em * em)
            if abs(xf.rot_deg) > 1e-3:
                p.rotate(xf.rot_deg)
            p.scale(xf.scale_x, xf.scale_y)
            p.translate(-cx, -cy)
        p.drawImage(0, 0, img)
        if box_clip:
            span = layout.text_w + SCROLL_GAP_EM * layout.em
            p.drawImage(int(round(span)), 0, img)
    finally:
        p.end()
    if float(xf.alpha) < 0.999:
        out = mul_premul_alpha(out, xf.alpha)
    if xf.extra_color:
        from cutora.render.textfx.word_fx import recolor_premul
        out = recolor_premul(out, xf.extra_color, 1.0)
    sigma_px = float(xf.blur_sigma_em) * float(layout.em)
    if sigma_px > 0.0:
        out = _blur_premul(out, sigma_px)
    return out


def style_rotation_deg(style: dict | None) -> float:
    """style['rotation'] in degrees. Missing / junk → 0."""
    if not isinstance(style, dict):
        return 0.0
    try:
        return float(style.get("rotation") or 0)
    except (TypeError, ValueError):
        return 0.0


def rotate_roi_around_text(img: QImage | None, layout: LayoutResult, degrees) -> QImage | None:
    """Rotate an ROI around the laid-out text centre. Identity is a no-op."""
    if img is None:
        return None
    try:
        deg = float(degrees or 0)
    except (TypeError, ValueError):
        deg = 0.0
    if abs(deg) < 1e-3:
        return img
    from cutora.render.textfx.anim import Transform
    return warp_layer_xf(img, layout, Transform(rot_deg=deg))


def raster_roi(
    layout: LayoutResult,
    spec: FxSpec,
    style: dict,
    backdrop: QImage | None,
    video_w: int,
    video_h: int,
    skip_blur: bool = False,
    t: float | None = None,
    event: dict | None = None,
) -> QImage:
    """Composite bg + text into one ROI (tests / stills)."""
    xf = None
    if t is not None and event is not None:
        from cutora.render.textfx.anim import transform_at
        xf = transform_at(float(t), event, spec, layout)
    text = raster_text(layout, spec, style, skip_blur, t=t, xf=xf)
    bg = raster_bg(layout, spec, backdrop, video_w, video_h, skip_blur)
    if t is not None and event is not None:
        from cutora.render.textfx.word_fx import (
            apply_geom, apply_word_color, draw_box_highlight,
        )
        text = apply_word_color(text, layout, spec, float(t), event)
        bg = draw_box_highlight(bg, layout, spec, float(t), event, style=style)
        text = apply_geom(text, layout, spec, float(t), event, xf, video_w, video_h)
        if bg is not None:
            from dataclasses import replace
            bg_xf = replace(xf, blur_sigma_em=0.0, extra_color=None) if xf else None
            bg = apply_geom(bg, layout, spec, float(t), event, bg_xf, video_w, video_h)
    elif xf is not None:
        text = warp_layer_xf(text, layout, xf, video_w, video_h)
        if bg is not None:
            bg = warp_layer_xf(bg, layout, xf, video_w, video_h)
    if bg is None:
        out = text
    else:
        p = QPainter(bg)
        try:
            p.drawImage(0, 0, text)
        finally:
            p.end()
        out = bg
    return rotate_roi_around_text(out, layout, style_rotation_deg(style))

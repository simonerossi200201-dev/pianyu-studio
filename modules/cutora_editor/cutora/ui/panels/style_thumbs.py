# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Gallery thumbnails: subtitle-strip crop of the current-project frame."""
from __future__ import annotations

import os

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap

from cutora.ui.theme.tokens import RADIUS_CARD

from cutora.subtitle.stylepack import (
    GALLERY_ASPECT,
    GALLERY_FIT_PAD,
    SAMPLE_EN,
    gallery_card_size,
    gallery_content_band,
    gallery_hover_t,
    resolve_style_geometry,
    sample_event,
    subtitle_bbox,
)

CARD_W = 180
CARD_ASPECT = 1.0 / GALLERY_ASPECT
CARD_MAX_H = 240
SAVE_ROLE = "__save_current__"
CARD_RADIUS = RADIUS_CARD
CARD_BORDER = "#2A3242"


def card_size(
    canvas_w: int, canvas_h: int,
    bbox_cx=None, bbox_cy=None, col_w=None,
) -> tuple[int, int]:
    return gallery_card_size(
        canvas_w, canvas_h, bbox_cx=bbox_cx, bbox_cy=bbox_cy, col_w=col_w)


def cache_key(
    pack_id: str, t_ms: int, canvas_w: int, canvas_h: int,
    sample: str = "", crop: tuple = (), video: str = "",
) -> tuple:
    bucket = int(t_ms // 1000) * 1000
    return (
        str(pack_id), bucket, int(canvas_w), int(canvas_h),
        str(sample or ""), tuple(crop) if crop else (),
        str(video or ""),
    )


def far_playhead(prev_ms: int | None, now_ms: int) -> bool:
    if prev_ms is None:
        return True
    return abs(int(now_ms) - int(prev_ms)) >= 1000


def pack_sample(pack: dict | None, fallback: str = SAMPLE_EN) -> str:
    meta = (pack or {}).get("meta") or {}
    text = str(meta.get("sample") or "").strip()
    return text or (fallback or SAMPLE_EN).strip() or SAMPLE_EN


def pack_in_s(pack: dict | None) -> float:
    pack = pack or {}
    style = pack.get("style") or {}
    fx = style.get("fx") if isinstance(style.get("fx"), dict) else {}
    anim = fx.get("anim") if isinstance(fx.get("anim"), dict) else {}
    in_name = str(anim.get("in") or "none")
    if in_name != "none":
        try:
            return max(0.0, float(anim.get("in_s") or 0.35))
        except (TypeError, ValueError):
            return 0.35
    animation = pack.get("animation") or {}
    in_type = str(animation.get("anim_in_type") or style.get("anim_in_type") or "none")
    if in_type not in ("", "none"):
        try:
            return max(0.0, float(animation.get("anim_in_duration") or 0.3))
        except (TypeError, ValueError):
            return 0.3
    return 0.0


def scale_sample_style(style: dict) -> dict:
    """Identity. Gallery cards are a crop of the real render, no extra scale."""
    return dict(style)


def neutral_frame(w: int, h: int) -> QImage:
    img = QImage(max(2, w), max(2, h), QImage.Format.Format_ARGB32)
    img.fill(QColor("#1A1814"))
    p = QPainter(img)
    try:
        p.fillRect(0, int(h * 0.62), w, h, QColor("#2A2620"))
        p.setPen(QPen(QColor("#3A342C")))
        p.drawRect(0, 0, w - 1, h - 1)
    finally:
        p.end()
    return img


def grab_video_frame(path: str, t_sec: float, w: int, h: int) -> QImage | None:
    if not path or not os.path.isfile(path):
        return None
    try:
        import cv2
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            return None
        cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, float(t_sec) * 1000.0))
        ok, bgr = cap.read()
        cap.release()
        if not ok or bgr is None:
            return None
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        ih, iw = rgb.shape[:2]
        qimg = QImage(rgb.data, iw, ih, rgb.strides[0], QImage.Format.Format_RGB888).copy()
        scaled = qimg.scaled(
            w, h, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation)
        if scaled.width() == w and scaled.height() == h:
            return scaled
        x = max(0, (scaled.width() - w) // 2)
        y = max(0, (scaled.height() - h) // 2)
        return scaled.copy(x, y, w, h)
    except Exception:
        return None


def paint_pack_on_frame(
    base: QImage, pack: dict, sample: str, t: float = 0.4, style: dict | None = None,
) -> QImage:
    img = QImage(base)
    if style is None:
        style = dict(pack.get("style") or {})
        style.update(pack.get("animation") or {})
        style = resolve_style_geometry(style, img.width(), img.height())
    text = (sample or SAMPLE_EN).strip() or SAMPLE_EN
    event = sample_event(text, 0.0, 10.0)
    painter = QPainter(img)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        from cutora.render.frame_renderer import FrameRenderer
        FrameRenderer.draw_subtitle(
            painter, event, style, float(t), img.width(), img.height(),
            backdrop=base)
    finally:
        painter.end()
    return img


def crop_to_band(img: QImage, band: tuple[float, float, float, float]) -> QImage:
    x, y, w, h = band
    ix = max(0, int(round(x)))
    iy = max(0, int(round(y)))
    iw = max(1, int(round(w)))
    ih = max(1, int(round(h)))
    iw = min(iw, img.width() - ix)
    ih = min(ih, img.height() - iy)
    return img.copy(ix, iy, max(1, iw), max(1, ih))


def scale_card(img: QImage, cw: int, ch: int) -> QPixmap:
    pix = QPixmap.fromImage(img)
    scaled = pix.scaled(
        cw, ch, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation)
    if scaled.width() == cw and scaled.height() == ch:
        return scaled
    x = max(0, (scaled.width() - cw) // 2)
    y = max(0, (scaled.height() - ch) // 2)
    return scaled.copy(x, y, cw, ch)


def fit_card(img: QImage, cw: int, ch: int, pad: int = GALLERY_FIT_PAD) -> QPixmap:
    """KeepAspectRatio into the card; 2 px transparent margin. Not cover."""
    out = QPixmap(max(1, cw), max(1, ch))
    out.fill(QColor(0, 0, 0, 0))
    inner_w = max(1, int(cw) - 2 * int(pad))
    inner_h = max(1, int(ch) - 2 * int(pad))
    pix = QPixmap.fromImage(img)
    scaled = pix.scaled(
        inner_w, inner_h, Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation)
    p = QPainter(out)
    try:
        p.drawPixmap(
            (cw - scaled.width()) // 2, (ch - scaled.height()) // 2, scaled)
    finally:
        p.end()
    return out


def round_card(
    pix: QPixmap, radius: int = CARD_RADIUS, border: str = CARD_BORDER,
) -> QPixmap:
    """Clip the thumb to a rounded rect and stroke 1 px. No mask image."""
    w, h = pix.width(), pix.height()
    out = QPixmap(w, h)
    out.fill(QColor(0, 0, 0, 0))
    p = QPainter(out)
    try:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0.5, 0.5, w - 1.0, h - 1.0), radius, radius)
        p.setClipPath(path)
        p.drawPixmap(0, 0, pix)
        p.setClipping(False)
        pen = QPen(QColor(border))
        pen.setWidth(1)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
    finally:
        p.end()
    return out


def _cover_qimage(img: QImage, cw: int, ch: int) -> QImage:
    scaled = img.scaled(
        max(1, cw), max(1, ch), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation)
    if scaled.width() == cw and scaled.height() == ch:
        return scaled
    x = max(0, (scaled.width() - cw) // 2)
    y = max(0, (scaled.height() - ch) // 2)
    return scaled.copy(x, y, cw, ch)


def _fit_qimage(img: QImage, cw: int, ch: int, pad: int = GALLERY_FIT_PAD) -> QImage:
    out = QImage(max(1, cw), max(1, ch), QImage.Format.Format_ARGB32)
    out.fill(QColor(0, 0, 0, 0))
    inner_w = max(1, int(cw) - 2 * int(pad))
    inner_h = max(1, int(ch) - 2 * int(pad))
    scaled = img.scaled(
        inner_w, inner_h, Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation)
    p = QPainter(out)
    try:
        p.drawImage(
            (cw - scaled.width()) // 2, (ch - scaled.height()) // 2, scaled)
    finally:
        p.end()
    return out


def _round_qimage(
    img: QImage, radius: int = CARD_RADIUS, border: str = CARD_BORDER,
) -> QImage:
    w, h = img.width(), img.height()
    out = QImage(w, h, QImage.Format.Format_ARGB32)
    out.fill(QColor(0, 0, 0, 0))
    p = QPainter(out)
    try:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0.5, 0.5, w - 1.0, h - 1.0), radius, radius)
        p.setClipPath(path)
        p.drawImage(0, 0, img)
        p.setClipping(False)
        pen = QPen(QColor(border))
        pen.setWidth(1)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
    finally:
        p.end()
    return out


def render_pack_image(
    base: QImage, pack: dict, sample: str, tw: int, th: int, t: float = 0.4,
) -> QImage:
    """Same card as render_pack_card, as a QImage so a worker thread can paint it."""
    style = dict(pack.get("style") or {})
    style.update(pack.get("animation") or {})
    style = resolve_style_geometry(style, base.width(), base.height())
    x, y, bw, bh = subtitle_bbox(style, base.width(), base.height(), [sample])
    band, overflow = gallery_content_band(
        base.width(), base.height(),
        bbox_cy=y + bh / 2.0, bbox_h=bh)
    painted = paint_pack_on_frame(base, pack, sample, t=t, style=style)
    cropped = crop_to_band(painted, band)
    if overflow:
        placed = _fit_qimage(cropped, tw, th)
    else:
        placed = _cover_qimage(cropped, tw, th)
    return _round_qimage(placed)


def render_pack_card(
    base: QImage, pack: dict, sample: str, tw: int, th: int, t: float = 0.4,
    band_h: float | None = None,
) -> QPixmap:
    style = dict(pack.get("style") or {})
    style.update(pack.get("animation") or {})
    style = resolve_style_geometry(style, base.width(), base.height())
    x, y, bw, bh = subtitle_bbox(style, base.width(), base.height(), [sample])
    band, overflow = gallery_content_band(
        base.width(), base.height(),
        bbox_cy=y + bh / 2.0, bbox_h=bh)
    painted = paint_pack_on_frame(base, pack, sample, t=t, style=style)
    cropped = crop_to_band(painted, band)
    if overflow:
        placed = fit_card(cropped, tw, th)
    else:
        placed = scale_card(cropped, tw, th)
    return round_card(placed)


def badge_user(pix: QPixmap) -> QPixmap:
    out = QPixmap(pix)
    p = QPainter(out)
    try:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(200, 162, 74, 220))
        p.drawRect(0, 0, 36, 16)
        p.setPen(QColor("#141210"))
        p.drawText(QRectF(0, 0, 36, 16), Qt.AlignmentFlag.AlignCenter, "用户")
    finally:
        p.end()
    return out


def badge_pure_black(pix: QPixmap) -> QPixmap:
    out = QPixmap(pix)
    p = QPainter(out)
    try:
        w, h = out.width(), out.height()
        bw, bh = 72, 16
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(244, 231, 136, 230))
        p.drawRect(w - bw, h - bh, bw, bh)
        p.setPen(QColor("#141210"))
        p.drawText(
            QRectF(w - bw, h - bh, bw, bh),
            Qt.AlignmentFlag.AlignCenter,
            "⚠ 含纯黑",
        )
    finally:
        p.end()
    return out


def plus_card(cw: int, ch: int) -> QPixmap:
    img = QImage(cw, ch, QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    try:
        pen = QPen(QColor("#C8A24A"))
        pen.setStyle(Qt.PenStyle.DashLine)
        pen.setWidth(2)
        p.setPen(pen)
        p.setBrush(QColor(20, 18, 16, 80))
        from cutora.ui.theme.tokens import RADIUS_CARD
        p.drawRoundedRect(4, 4, cw - 8, ch - 8, RADIUS_CARD, RADIUS_CARD)
        p.setPen(QColor("#E8DCC8"))
        p.drawText(
            QRectF(8, 8, cw - 16, ch - 16),
            Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
            "＋",
        )
    finally:
        p.end()
    return QPixmap.fromImage(img)


__all__ = [
    "CARD_W", "CARD_ASPECT", "CARD_MAX_H", "SAVE_ROLE",
    "CARD_RADIUS", "CARD_BORDER",
    "card_size", "cache_key", "far_playhead", "pack_sample", "pack_in_s",
    "gallery_hover_t", "render_pack_card", "badge_user", "badge_pure_black",
    "plus_card", "grab_video_frame", "neutral_frame", "paint_pack_on_frame",
    "render_pack_image",
    "scale_card", "fit_card", "round_card",
]

# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""短视频转场引擎（90 款）：多段灵动运镜 / 节奏卡点 / 分屏图形遮罩 / 电影光效水墨氛围。

所有转场函数遵循统一契约：
* 签名：``fn(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw) -> QImage``
  同时兼容 ``transitions.apply_transition`` 的 ``fn(a, b, t, w, h, **params)`` 调用顺序。
* 端点连续：``t <= 0.0`` 返回 ``a``，``t >= 1.0`` 返回 ``b``；``t=0.02`` 与 ``t=0.98`` 无跳变。
* 全程不透明（ARGB32_Premultiplied，alpha 恒为 255），小缩略图（128×72）与 1080p 均高速稳定。
"""
from __future__ import annotations

import functools
import math
import os

_QCACHE = None


def _q():
    global _QCACHE
    if _QCACHE is None:
        from PySide6.QtCore import QPointF, QRectF, Qt
        from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QTransform
        _QCACHE = (QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt)
    return _QCACHE


def _ease(t, name="ease_in_out"):
    from cutora.render.anim_engine import EASING_MAP, ease_in_out_cubic
    t = max(0.0, min(1.0, float(t)))
    fn = EASING_MAP.get(str(name or "ease_in_out"), ease_in_out_cubic)
    return fn(t)


def _canvas(w, h, fill=0):
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    img = QImage(int(w), int(h), QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0 if fill is None else fill)
    return img


def _copy_fit(src, w, h):
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    if src is None or src.isNull():
        return _canvas(w, h)
    if src.width() == int(w) and src.height() == int(h):
        return src.copy()
    out = _canvas(w, h)
    p = QPainter(out)
    try:
        p.drawImage(QRectF(0, 0, w, h), src, QRectF(0, 0, src.width(), src.height()))
    finally:
        p.end()
    return out


def _draw(painter, img, x, y, w, h):
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    if img is None or img.isNull():
        return
    painter.drawImage(QRectF(x, y, w, h), img, QRectF(0, 0, img.width(), img.height()))


def _dir_vec(direction, w, h):
    d = str(direction or "left").lower()
    if d in ("right", "e"):
        return float(w), 0.0
    if d in ("up", "top", "n"):
        return 0.0, -float(h)
    if d in ("down", "bottom", "s"):
        return 0.0, float(h)
    return -float(w), 0.0

_ASSETS_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "resources", "transition_assets")
)


@functools.lru_cache(maxsize=32)
def _load_asset_image(name: str):
    """加载并缓存 resources/transition_assets 下的配套 RGBA 纹理资产。"""
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    path = os.path.join(_ASSETS_DIR, str(name))
    if os.path.isfile(path):
        img = QImage(path)
        if not img.isNull():
            return img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    fallback = QImage(64, 36, QImage.Format.Format_ARGB32_Premultiplied)
    fallback.fill(QColor(255, 255, 255, 0))
    return fallback


def _draw_asset_overlay(painter, name: str, w: int, h: int, *, opacity: float = 1.0,
                        tx: float = 0.0, ty: float = 0.0, sx: float = 1.0, sy: float | None = None,
                        rot: float = 0.0):
    """将配套纹理资产以指定变换与透明度绘制到画布。"""
    if opacity <= 1e-4:
        return
    asset = _load_asset_image(name)
    if asset is None or asset.isNull():
        return
    _draw_xform(painter, asset, w, h, tx=tx, ty=ty, sx=sx, sy=sy, rot=rot, opacity=opacity)


def _smoothstep(x: float) -> float:
    x = max(0.0, min(1.0, float(x)))
    return x * x * (3.0 - 2.0 * x)


def _choreo_4stage(p: float):
    """四段式灵动运镜曲线分解：
    Phase 1 (0.00 -> 0.28): 快速发力推进/甩入 (s1: 0->1)
    Phase 2 (0.28 -> 0.56): 悬浮微移「缓缓的动」+ A/B 丝滑交融 (s2: 0->1)
    Phase 3 (0.56 -> 0.82): 弹性拉远「再退回」过冲回撤 (s3: 0->1)
    Phase 4 (0.82 -> 1.00): 呼吸落定「再缓缓的动」平滑归位 (s4: 0->1)
    返回 (s1, s2, s3, s4, b_mix)。
    """
    s1 = _smoothstep(p / 0.28)
    s2 = _smoothstep((p - 0.28) / 0.28)
    s3 = _smoothstep((p - 0.56) / 0.26)
    s4 = _smoothstep((p - 0.82) / 0.18)
    b_mix = _smoothstep((p - 0.20) / 0.48)
    return s1, s2, s3, s4, b_mix


def _draw_mirrored_xform(painter, img, w, h, *, tx=0.0, ty=0.0, sx=1.0, sy=None, rot=0.0, opacity=1.0):
    """带边缘镜像/放大衬底保护的绘制，确保退回缩小(sx<1.0)或大幅平移旋转时绝不露黑边。"""
    if img is None or img.isNull() or opacity <= 1e-4:
        return
    if sy is None:
        sy = sx
    op = max(0.0, min(1.0, float(opacity)))
    # 当缩放小于 1.02 或存在旋转/位移时，先铺一层放大的同源衬底防露边
    if min(sx, sy) < 1.02 or abs(rot) > 0.2 or abs(tx) > 0.5 or abs(ty) > 0.5:
        bg_sc = max(1.28, 1.45 - 0.25 * min(sx, sy))
        _draw_xform(
            painter, img, w, h,
            tx=-tx * 0.25, ty=-ty * 0.25,
            sx=bg_sc, sy=bg_sc, rot=rot * 0.35, opacity=op,
        )
    _draw_xform(painter, img, w, h, tx=tx, ty=ty, sx=sx, sy=sy, rot=rot, opacity=op)


def _norm(t, a, b, w, h, intensity=1.0, easing="ease_in_out"):
    """兼容 (t, a, b, w, h) 与 (a, b, t, w, h) 两种参数顺序，并处理端点与缓动。"""
    if not isinstance(t, (int, float)) and isinstance(b, (int, float)):
        t, a, b = b, t, a
    tf = float(t)
    w, h = int(w), int(h)
    if tf <= 0.0:
        return tf, a, b, w, h, 0.0, 0.0, 1.0, _copy_fit(a, w, h)
    if tf >= 1.0:
        return tf, a, b, w, h, 1.0, 0.0, 1.0, _copy_fit(b, w, h)
    p = _ease(tf, easing)
    veil = math.sin(p * math.pi)
    try:
        k = float(intensity if intensity is not None else 1.0)
    except (TypeError, ValueError):
        k = 1.0
    if not math.isfinite(k):
        k = 1.0
    k = max(0.2, min(2.5, k))
    return tf, a, b, w, h, p, veil, k, None


def _draw_xform(painter, img, w, h, *, cx=None, cy=None, tx=0.0, ty=0.0,
                sx=1.0, sy=None, rot=0.0, opacity=1.0):
    if img is None or img.isNull() or opacity <= 1e-4:
        return
    if sy is None:
        sy = sx
    if cx is None:
        cx = w * 0.5
    if cy is None:
        cy = h * 0.5
    painter.save()
    painter.setOpacity(max(0.0, min(1.0, float(opacity))))
    painter.translate(cx + tx, cy + ty)
    if abs(rot) > 1e-4:
        painter.rotate(rot)
    if abs(sx - 1.0) > 1e-4 or abs(sy - 1.0) > 1e-4:
        painter.scale(sx, sy)
    painter.translate(-cx, -cy)
    _draw(painter, img, 0, 0, w, h)
    painter.restore()


# ═══════════════════════════════════════════════════════════════════════════
# A. 运镜类 (camera，12 款)
# ═══════════════════════════════════════════════════════════════════════════

def cam_crash_push(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """急推冲镜：A 极速中心冲镜放大并带径向残影，B 从远景蓄力冲入定格。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    from PySide6.QtGui import QRadialGradient
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        sc_a = 1.0 + 1.65 * k * (p ** 1.3)
        _draw_xform(painter, a, w, h, sx=sc_a, opacity=1.0)
        for i, op in ((1, 0.28), (2, 0.18), (3, 0.10)):
            _draw_xform(painter, a, w, h, sx=sc_a + i * 0.14 * k * veil, opacity=op * (1.0 - p))
        sc_b_base = max(0.32, 1.0 - 0.58 * k * ((1.0 - p) ** 1.5))
        # 底层先铺放大的 B 镜像防缩小时露底，再画冲入的 B
        _draw_xform(painter, b, w, h, sx=1.0 + 0.45 * (1.0 - p), opacity=p)
        for i, op in ((2, 0.18), (1, 0.28)):
            _draw_xform(painter, b, w, h, sx=sc_b_base * (1.0 - 0.08 * i * veil), opacity=p * op * veil)
        _draw_xform(painter, b, w, h, sx=sc_b_base, opacity=min(1.0, p * 1.15))
        if veil > 0.02:
            rg = QRadialGradient(w * 0.5, h * 0.5, max(w, h) * 0.65)
            rg.setColorAt(0.0, QColor(255, 250, 240, int(55 * veil * k)))
            rg.setColorAt(0.65, QColor(0, 0, 0, 0))
            rg.setColorAt(1.0, QColor(11, 11, 14, int(130 * veil)))
            painter.fillRect(QRectF(0, 0, w, h), rg)
    finally:
        painter.end()
    return out


def cam_pull_back(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """急拉远景：镜头从 A 画面中心急速后撤拉远，B 从超大焦距收束落位。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        sc_b = 1.0 + 1.35 * k * ((1.0 - p) ** 1.35)
        _draw_xform(painter, b, w, h, sx=sc_b, opacity=1.0)
        for i, op in ((1, 0.24), (2, 0.14)):
            _draw_xform(painter, b, w, h, sx=sc_b + i * 0.12 * veil, opacity=op * veil)
        sc_a = max(0.18, 1.0 - 0.78 * k * (p ** 1.15))
        _draw_xform(painter, a, w, h, sx=sc_a, opacity=max(0.0, 1.0 - p * 1.08))
        if veil > 0.02 and sc_a < 0.98:
            rw, rh = w * sc_a, h * sc_a
            rx, ry = (w - rw) * 0.5, (h - rh) * 0.5
            from PySide6.QtGui import QPen
            painter.setPen(QPen(QColor(255, 255, 255, int(140 * veil)), max(1.0, min(w, h) * 0.012)))
            painter.drawRect(QRectF(rx, ry, rw, rh))
    finally:
        painter.end()
    return out


def cam_whip_right(t, a, b, w, h, direction="right", intensity=1.0, *, easing="ease_in_out", **kw):
    """右甩镜：镜头高速向右横甩（A 向右飞出，B 从左侧带水平高速拖影甩入）。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    from PySide6.QtGui import QLinearGradient
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        dx_a = float(w) * p
        dx_b = -float(w) * (1.0 - p)
        zoom = 1.0 + 0.12 * veil * k
        _draw_xform(painter, a, w, h, tx=dx_a, sx=zoom, sy=1.0)
        _draw_xform(painter, b, w, h, tx=dx_b, sx=zoom, sy=1.0)
        stride = max(2.0, float(w) * 0.09 * k * veil)
        for idx, alpha in ((1, 0.30 * veil), (2, 0.18 * veil), (3, 0.10 * veil)):
            off = stride * idx
            _draw_xform(painter, a, w, h, tx=dx_a - off, sx=zoom, sy=1.0, opacity=alpha)
            _draw_xform(painter, b, w, h, tx=dx_b + off, sx=zoom, sy=1.0, opacity=alpha)
        if veil > 0.03:
            seam_x = dx_a
            g = QLinearGradient(seam_x - w * 0.18, 0, seam_x + w * 0.18, 0)
            g.setColorAt(0.0, QColor(80, 210, 255, 0))
            g.setColorAt(0.5, QColor(255, 245, 220, int(165 * veil * veil)))
            g.setColorAt(1.0, QColor(255, 120, 60, 0))
            painter.fillRect(QRectF(0, 0, w, h), g)
    finally:
        painter.end()
    return out


def cam_whip_down(t, a, b, w, h, direction="down", intensity=1.0, *, easing="ease_in_out", **kw):
    """下甩镜：镜头高速向下俯甩（A 向下滑出，B 从顶部带纵向拉丝残影甩入）。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    from PySide6.QtGui import QLinearGradient
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        dy_a = float(h) * p
        dy_b = -float(h) * (1.0 - p)
        zoom = 1.0 + 0.12 * veil * k
        _draw_xform(painter, a, w, h, ty=dy_a, sx=1.0, sy=zoom)
        _draw_xform(painter, b, w, h, ty=dy_b, sx=1.0, sy=zoom)
        stride = max(2.0, float(h) * 0.10 * k * veil)
        for idx, alpha in ((1, 0.30 * veil), (2, 0.18 * veil), (3, 0.10 * veil)):
            off = stride * idx
            _draw_xform(painter, a, w, h, ty=dy_a - off, sx=1.0, sy=zoom, opacity=alpha)
            _draw_xform(painter, b, w, h, ty=dy_b + off, sx=1.0, sy=zoom, opacity=alpha)
        if veil > 0.03:
            seam_y = dy_a
            g = QLinearGradient(0, seam_y - h * 0.18, 0, seam_y + h * 0.18)
            g.setColorAt(0.0, QColor(255, 160, 70, 0))
            g.setColorAt(0.5, QColor(255, 240, 200, int(160 * veil * veil)))
            g.setColorAt(1.0, QColor(90, 200, 255, 0))
            painter.fillRect(QRectF(0, 0, w, h), g)
    finally:
        painter.end()
    return out


def cam_whip_diag(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """对角甩镜：45° 对角线斜向高速甩镜 + 斜向流光拖尾。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        d = str(direction or "left").lower()
        sx_dir = -1.0 if d in ("left", "up") else 1.0
        sy_dir = -1.0 if d in ("left", "down") else 1.0
        sc = 1.0 + 0.32 * veil * k
        dx_a = sx_dir * float(w) * p
        dy_a = sy_dir * float(h) * p
        dx_b = -sx_dir * float(w) * (1.0 - p)
        dy_b = -sy_dir * float(h) * (1.0 - p)
        # 底层铺过渡底防对角空隙
        _draw_xform(painter, a if p < 0.5 else b, w, h, sx=1.25 + 0.25 * veil, rot=-8.0 * veil * sx_dir)
        _draw_xform(painter, a, w, h, tx=dx_a, ty=dy_a, sx=sc)
        _draw_xform(painter, b, w, h, tx=dx_b, ty=dy_b, sx=sc)
        for idx, alpha in ((1, 0.26 * veil), (2, 0.15 * veil)):
            ox = sx_dir * float(w) * 0.07 * idx * k * veil
            oy = sy_dir * float(h) * 0.07 * idx * k * veil
            _draw_xform(painter, a, w, h, tx=dx_a - ox, ty=dy_a - oy, sx=sc, opacity=alpha)
            _draw_xform(painter, b, w, h, tx=dx_b + ox, ty=dy_b + oy, sx=sc, opacity=alpha)
    finally:
        painter.end()
    return out


def cam_roll_cw(t, a, b, w, h, direction="cw", intensity=1.0, *, easing="ease_in_out", **kw):
    """顺时针旋转运镜：360° 顺时针高速滚筒旋转运镜 + 边缘镜像补偿。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        sc = 1.0 + 0.78 * veil * min(1.5, k)
        ang_a = 180.0 * p
        ang_b = -180.0 * (1.0 - p)
        _draw_xform(painter, a, w, h, sx=sc * 1.18, rot=ang_a * 0.6, opacity=1.0)
        for dg, op in ((-14.0, 0.24), (-7.0, 0.32)):
            _draw_xform(painter, a, w, h, sx=sc, rot=ang_a + dg * veil, opacity=(1.0 - p) * op * veil)
        _draw_xform(painter, a, w, h, sx=sc, rot=ang_a, opacity=max(0.0, 1.0 - p))
        for dg, op in ((-14.0, 0.24), (-7.0, 0.32)):
            _draw_xform(painter, b, w, h, sx=sc, rot=ang_b + dg * veil, opacity=p * op * veil)
        _draw_xform(painter, b, w, h, sx=sc, rot=ang_b, opacity=p)
    finally:
        painter.end()
    return out


def cam_roll_ccw(t, a, b, w, h, direction="ccw", intensity=1.0, *, easing="ease_in_out", **kw):
    """逆时针旋转运镜：逆时针高速滚筒旋转运镜 + 旋转残影。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        sc = 1.0 + 0.78 * veil * min(1.5, k)
        ang_a = -180.0 * p
        ang_b = 180.0 * (1.0 - p)
        _draw_xform(painter, a, w, h, sx=sc * 1.18, rot=ang_a * 0.6, opacity=1.0)
        for dg, op in ((14.0, 0.24), (7.0, 0.32)):
            _draw_xform(painter, a, w, h, sx=sc, rot=ang_a + dg * veil, opacity=(1.0 - p) * op * veil)
        _draw_xform(painter, a, w, h, sx=sc, rot=ang_a, opacity=max(0.0, 1.0 - p))
        for dg, op in ((14.0, 0.24), (7.0, 0.32)):
            _draw_xform(painter, b, w, h, sx=sc, rot=ang_b + dg * veil, opacity=p * op * veil)
        _draw_xform(painter, b, w, h, sx=sc, rot=ang_b, opacity=p)
    finally:
        painter.end()
    return out


def cam_zoom_spin(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """螺旋推进：镜头边猛烈推进边螺旋旋转切入下个画面。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        rot_a = 135.0 * k * (p ** 1.4)
        sc_a = 1.0 + 1.45 * k * (p ** 1.2)
        _draw_xform(painter, a, w, h, sx=sc_a, rot=rot_a, opacity=1.0)
        _draw_xform(painter, a, w, h, sx=sc_a * (1.0 + 0.12 * veil), rot=rot_a - 12.0 * veil, opacity=0.35 * veil)
        rot_b = -135.0 * k * ((1.0 - p) ** 1.4)
        sc_b = max(0.35, 1.0 - 0.55 * ((1.0 - p) ** 1.3))
        _draw_xform(painter, b, w, h, sx=1.0 + 0.75 * veil, rot=rot_b * 0.5, opacity=p)
        _draw_xform(painter, b, w, h, sx=sc_b, rot=rot_b, opacity=min(1.0, p * 1.15))
    finally:
        painter.end()
    return out


def cam_dolly_vertigo(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """希区柯克变焦：前景中心与背景边缘反向缩放（滑动变焦眩晕透视感）。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    try:
        # 外围背景反向拉远/推近，中心圆形区域反向缩放
        bg_sc_a = 1.0 + 0.72 * k * p
        bg_sc_b = 1.0 + 0.72 * k * (1.0 - p)
        _draw_xform(painter, a, w, h, sx=bg_sc_a, opacity=1.0)
        _draw_xform(painter, b, w, h, sx=bg_sc_b, opacity=p)
        for ring_frac, zoom_mul, op in ((0.72, -0.30, 0.75), (0.46, -0.48, 0.92)):
            painter.save()
            path = QPainterPath()
            rx = w * 0.5 * ring_frac * (1.0 + 0.15 * veil)
            ry = h * 0.5 * ring_frac * (1.0 + 0.15 * veil)
            path.addEllipse(QPointF(w * 0.5, h * 0.5), rx, ry)
            painter.setClipPath(path)
            fg_sc_a = max(0.45, 1.0 + zoom_mul * k * veil)
            fg_sc_b = max(0.45, 1.0 - zoom_mul * k * veil)
            _draw_xform(painter, a, w, h, sx=fg_sc_a, opacity=op * (1.0 - p))
            _draw_xform(painter, b, w, h, sx=fg_sc_b, opacity=op * p)
            painter.restore()
    finally:
        painter.end()
    return out


def cam_pendulum(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """钟摆摇镜：绕画面上方悬点做钟摆圆弧摇摆切换。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        px, py = w * 0.5, -h * 0.45
        sc = 1.0 + 0.42 * veil
        _draw_xform(painter, a if p < 0.5 else b, w, h, sx=sc * 1.2, opacity=1.0)
        ang_a = 52.0 * k * p
        ang_b = -52.0 * k * (1.0 - p)
        _draw_xform(painter, a, w, h, cx=px, cy=py, sx=sc, rot=ang_a, opacity=max(0.0, 1.0 - p * 0.85))
        _draw_xform(painter, b, w, h, cx=px, cy=py, sx=sc, rot=ang_b, opacity=min(1.0, p * 1.15))
    finally:
        painter.end()
    return out


def cam_perspective_tilt(t, a, b, w, h, direction="up", intensity=1.0, *, easing="ease_in_out", **kw):
    """3D 透视俯仰：3D 梯形透视俯仰翻转（A 向后仰倒远去，B 从上方俯冲立起）。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    from PySide6.QtGui import QPolygonF
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        # 底层深色环境虚化底
        _draw_xform(painter, a if p < 0.5 else b, w, h, sx=1.22, opacity=1.0)
        painter.fillRect(0, 0, w, h, QColor(11, 11, 14, int(150 * veil)))
        src_poly = QPolygonF([QPointF(0, 0), QPointF(w, 0), QPointF(w, h), QPointF(0, h)])
        pinch_a = w * 0.24 * k * p
        y_top_a = h * 0.68 * p
        dst_a = QPolygonF([
            QPointF(pinch_a, y_top_a),
            QPointF(w - pinch_a, y_top_a),
            QPointF(0, h),
            QPointF(w, h) if False else QPointF(w, h),
        ])
        dst_a = QPolygonF([
            QPointF(pinch_a, y_top_a),
            QPointF(w - pinch_a, y_top_a),
            QPointF(w, h),
            QPointF(0, h),
        ])
        xf_a = QTransform()
        if QTransform.quadToQuad(src_poly, dst_a, xf_a):
            painter.save()
            painter.setTransform(xf_a, True)
            painter.setOpacity(max(0.0, 1.0 - p * 0.9))
            _draw(painter, a, 0, 0, w, h)
            painter.restore()
        pinch_b = w * 0.24 * k * (1.0 - p)
        y_bot_b = h * (1.0 - 0.68 * (1.0 - p))
        dst_b = QPolygonF([
            QPointF(0, 0),
            QPointF(w, 0),
            QPointF(w - pinch_b, y_bot_b),
            QPointF(pinch_b, y_bot_b),
        ])
        xf_b = QTransform()
        if QTransform.quadToQuad(src_poly, dst_b, xf_b):
            painter.save()
            painter.setTransform(xf_b, True)
            painter.setOpacity(min(1.0, p * 1.15))
            _draw(painter, b, 0, 0, w, h)
            painter.restore()
    finally:
        painter.end()
    return out


def cam_flip_jib(t, a, b, w, h, direction="up", intensity=1.0, *, easing="ease_in_out", **kw):
    """摇臂翻转：垂直摇臂升起并带 3D 纵向翻转折页过渡。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        _draw_xform(painter, a if p < 0.5 else b, w, h, sx=1.18, opacity=1.0)
        sy_a = max(0.06, math.cos(p * math.pi * 0.5))
        ty_a = -h * 0.42 * p * k
        _draw_xform(painter, a, w, h, ty=ty_a, sx=1.0 + 0.12 * veil, sy=sy_a, opacity=1.0 - p * 0.85)
        sy_b = max(0.06, math.sin(p * math.pi * 0.5))
        ty_b = h * 0.42 * (1.0 - p) * k
        _draw_xform(painter, b, w, h, ty=ty_b, sx=1.0 + 0.12 * veil, sy=sy_b, opacity=min(1.0, p * 1.15))
        if veil > 0.03:
            seam_y = h * (1.0 - p)
            painter.fillRect(0, int(seam_y - 3), w, 6, QColor(255, 220, 150, int(170 * veil)))
    finally:
        painter.end()
    return out


# ═══════════════════════════════════════════════════════════════════════════
# B. 节奏卡点类 (rhythm，10 款)
# ═══════════════════════════════════════════════════════════════════════════

def beat_bass_pump(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """重低音震屏缩放：随重低音鼓点连环缩放脉冲 + 冲击波环扩散。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    from PySide6.QtGui import QPen
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    try:
        pulse = (math.sin(p * math.pi * 3.0) ** 2) * veil
        sc = 1.0 + 0.36 * k * pulse + 0.12 * veil
        jx = math.sin(p * math.pi * 10.0) * w * 0.025 * veil * k
        jy = math.cos(p * math.pi * 8.0) * h * 0.025 * veil * k
        _draw_xform(painter, a, w, h, tx=jx, ty=jy, sx=sc, opacity=1.0)
        _draw_xform(painter, b, w, h, tx=-jx, ty=-jy, sx=sc, opacity=p)
        if veil > 0.04:
            r = math.hypot(w, h) * 0.48 * p
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(110, 230, 255, int(185 * veil)), max(2.0, min(w, h) * 0.022 * veil)))
            painter.drawEllipse(QPointF(w * 0.5, h * 0.5), r, r)
    finally:
        painter.end()
    return out


def beat_RGB_split_shake(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """RGB 通道分离震动：高频震屏 + 强烈红蓝青三色通道错位撕裂。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    from cutora.render.transitions import _channel_shift
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    base = _copy_fit(a, w, h)
    painter = QPainter(base)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        shake_x = math.sin(p * math.pi * 9.0) * w * 0.045 * veil * k
        shake_y = math.cos(p * math.pi * 7.0) * h * 0.045 * veil * k
        sc = 1.0 + 0.16 * veil * k
        _draw_xform(painter, a, w, h, tx=shake_x, ty=shake_y, sx=sc, opacity=1.0)
        _draw_xform(painter, b, w, h, tx=-shake_x * 0.8, ty=-shake_y * 0.8, sx=sc, opacity=p)
    finally:
        painter.end()
    dx = int(round(max(2.0, w * 0.045) * k * veil * math.cos(p * math.pi * 4.0)))
    dy = int(round(max(1.0, h * 0.032) * k * veil * math.sin(p * math.pi * 3.0)))
    return _channel_shift(base, dx, dy)


def beat_flash_zoom(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """白闪急推卡点：急推放大伴随高能星芒白闪爆点切换。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    from PySide6.QtGui import QRadialGradient
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        sc_a = 1.0 + 0.85 * p * k
        sc_b = 1.65 - 0.65 * p
        _draw_xform(painter, a, w, h, sx=sc_a, opacity=1.0)
        _draw_xform(painter, b, w, h, sx=sc_b, opacity=p)
        flash = (veil ** 2.2) * min(1.5, k)
        if flash > 0.01:
            rg = QRadialGradient(w * 0.5, h * 0.5, max(w, h) * 0.6)
            rg.setColorAt(0.0, QColor(255, 255, 255, min(255, int(250 * flash))))
            rg.setColorAt(0.55, QColor(210, 240, 255, min(255, int(195 * flash))))
            rg.setColorAt(1.0, QColor(255, 255, 255, min(255, int(90 * flash))))
            painter.fillRect(QRectF(0, 0, w, h), rg)
    finally:
        painter.end()
    return out


def beat_strobe_cut(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """频闪快切：转场中段高频 A/B 交替闪烁 + 负片感电光频闪。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        # 频闪调制：中段快速在 A/B 间跳变，两端严格收敛到 0 和 1
        osc = 0.5 + 0.5 * math.sin(p * math.pi * 9.0)
        b_mix = p * (1.0 - veil) + (osc if p < 0.5 else (0.35 + 0.65 * osc)) * veil
        b_mix = max(0.0, min(1.0, b_mix))
        sc = 1.0 + 0.14 * veil * osc * k
        _draw_xform(painter, a, w, h, sx=sc, opacity=1.0)
        _draw_xform(painter, b, w, h, sx=sc, opacity=b_mix)
        strobe = max(0.0, math.sin(p * math.pi * 11.0)) * (veil ** 1.6)
        if strobe > 0.02:
            painter.fillRect(0, 0, w, h, QColor(245, 250, 255, min(230, int(185 * strobe * k))))
    finally:
        painter.end()
    return out


def beat_glitch_slice(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """故障错位切片：赛博风多频段水平错位切片 + 青品红故障色块卡点。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    try:
        _draw(painter, b, 0, 0, w, h)
        painter_op = 1.0 - p
        painter.setOpacity(painter_op)
        _draw(painter, a, 0, 0, w, h)
        painter.setOpacity(1.0)
        slices = 9
        sh = float(h) / slices
        for i in range(slices):
            y0 = i * sh
            phase = math.sin(i * 2.37 + p * 14.0)
            shift = phase * w * 0.22 * veil * k
            use_b = (p + 0.28 * phase * veil) >= 0.5
            src = b if use_b else a
            painter.save()
            painter.setClipRect(QRectF(0, y0, w, sh + 0.8))
            _draw(painter, src, shift, 0, w, h)
            if abs(phase) > 0.55 and veil > 0.1:
                col = QColor(0, 245, 255, int(95 * veil)) if i % 2 == 0 else QColor(255, 20, 190, int(95 * veil))
                painter.fillRect(QRectF(max(0.0, shift), y0, w * 0.35, sh * 0.5), col)
            painter.restore()
    finally:
        painter.end()
    return out


def beat_elastic_bounce(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """弹性回弹卡点：新画面带阻尼弹簧过冲回弹（Overshoot Bounce）砸入画布。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        # 阻尼弹簧曲线：p=0 时为 0，快速冲过 1.0 后振荡衰减收敛到 1.0
        decay = math.exp(-5.5 * p)
        spring = 1.0 - decay * math.cos(2.5 * math.pi * p * k)
        # 保证在 p->1 时精确归 1.0
        spring = spring * (1.0 - p) + 1.0 * p
        dx_a = -w * min(1.2, p * 1.4)
        dx_b = w * (1.0 - spring)
        _draw_xform(painter, a, w, h, tx=dx_a, sx=1.0 - 0.15 * veil, opacity=1.0)
        # 底层铺 B 的放大虚化底，防止回弹过冲时露边
        _draw_xform(painter, b, w, h, sx=1.18, opacity=p)
        _draw_xform(painter, b, w, h, tx=dx_b, sx=1.0 + 0.08 * veil * math.sin(p * math.pi * 3.0), opacity=min(1.0, p * 1.35))
    finally:
        painter.end()
    return out


def beat_zoom_bounce(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """两段式卡点推镜：砰-砰两段阶梯式鼓点缩放推进。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        bump1 = math.exp(-((p - 0.28) / 0.12) ** 2)
        bump2 = math.exp(-((p - 0.68) / 0.12) ** 2)
        sc_a = 1.0 + (0.32 * bump1 + 0.55 * p) * k
        sc_b = 1.0 + (0.45 * (1.0 - p) + 0.28 * bump2) * k
        rot = (6.0 * bump1 - 6.0 * bump2) * veil * k
        _draw_xform(painter, a, w, h, sx=sc_a, rot=rot, opacity=1.0)
        _draw_xform(painter, b, w, h, sx=sc_b, rot=rot, opacity=p)
    finally:
        painter.end()
    return out


def beat_shutter_frame(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """快门定格切：相机机械快门上下合拢闪白 + 取景框定格卡点。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    from PySide6.QtGui import QPen
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        sc = 1.0 - 0.12 * veil
        _draw_xform(painter, a if p < 0.5 else b, w, h, sx=1.08, opacity=1.0)
        _draw_xform(painter, a, w, h, sx=sc, opacity=1.0 - p)
        _draw_xform(painter, b, w, h, sx=sc, opacity=p)
        blade_h = h * 0.42 * (veil ** 1.4) * min(1.2, k)
        if blade_h > 1.0:
            painter.fillRect(QRectF(0, 0, w, blade_h), QColor(14, 15, 20, int(235 * veil)))
            painter.fillRect(QRectF(0, h - blade_h, w, blade_h), QColor(14, 15, 20, int(235 * veil)))
        if veil > 0.05:
            m = min(w, h) * 0.08 * veil
            painter.setPen(QPen(QColor(255, 255, 255, int(210 * veil)), max(1.5, min(w, h) * 0.015)))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(QRectF(m, m, w - 2 * m, h - 2 * m))
    finally:
        painter.end()
    return out


def beat_split_3x(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """三联分屏律动：左中右三栏按节拍依次纵向滑入拼合。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    try:
        cw = float(w) / 3.0
        for i in range(3):
            x0 = i * cw
            # 确保 p=0 时 local_p=0，p>=0.92 时三栏全部达到 1.0
            local_p = max(0.0, min(1.0, (p - i * 0.16) / 0.62))
            local_e = _ease(local_p, "ease_out")
            if local_e > 1e-4:
                painter.save()
                painter.setClipRect(QRectF(x0, 0, cw + 0.8, h))
                sign = 1.0 if i % 2 == 0 else -1.0
                dy = sign * h * (1.0 - local_e)
                _draw(painter, b, 0, dy, w, h)
                if 0.01 < local_e < 0.99:
                    painter.fillRect(QRectF(x0, 0, max(1.5, w * 0.008), h), QColor(255, 255, 255, int(180 * veil)))
                painter.restore()
    finally:
        painter.end()
    return out


def beat_grid_pop(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """九宫格节拍弹出：3×3 九宫格按中心向外节拍依次缩放弹出。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    try:
        cw, ch = float(w) / 3.0, float(h) / 3.0
        for r in range(3):
            for c in range(3):
                dist = abs(r - 1) + abs(c - 1)  # 0, 1, 2
                delay = dist * 0.14
                lp = max(0.0, min(1.0, (p - delay) / 0.66))
                if lp > 1e-4:
                    le = _ease(lp, "ease_out")
                    cell_w = cw * le
                    cell_h = ch * le
                    cx = (c + 0.5) * cw
                    cy = (r + 0.5) * ch
                    painter.save()
                    painter.setClipRect(QRectF(cx - cell_w * 0.5, cy - cell_h * 0.5, cell_w + 0.8, cell_h + 0.8))
                    _draw(painter, b, 0, 0, w, h)
                    painter.restore()
    finally:
        painter.end()
    return out


# ═══════════════════════════════════════════════════════════════════════════
# C. 图形与创意遮罩类 (graphic，10 款)
# ═══════════════════════════════════════════════════════════════════════════

def _heart_path(cx, cy, radius):
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    path = QPainterPath()
    steps = 48
    for i in range(steps + 1):
        th = (i / float(steps)) * 2.0 * math.pi
        hx = 16.0 * (math.sin(th) ** 3)
        hy = -(13.0 * math.cos(th) - 5.0 * math.cos(2.0 * th) - 2.0 * math.cos(3.0 * th) - math.cos(4.0 * th))
        px = cx + (hx / 16.0) * radius
        py = cy + (hy / 16.0) * radius
        if i == 0:
            path.moveTo(QPointF(px, py))
        else:
            path.lineTo(QPointF(px, py))
    path.closeSubpath()
    return path


def shape_heart_iris(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """爱心扩缩开合：中心甜美爱心遮罩向外绽放并伴随粉金描边。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    from PySide6.QtGui import QPen
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    try:
        max_r = math.hypot(w, h) * 1.15
        r = max_r * (p ** 1.1)
        path = _heart_path(w * 0.5, h * 0.5, r)
        painter.save()
        painter.setClipPath(path)
        _draw(painter, b, 0, 0, w, h)
        painter.restore()
        if p > 0.82:
            painter.setOpacity((p - 0.82) / 0.18)
            _draw(painter, b, 0, 0, w, h)
            painter.setOpacity(1.0)
        if veil > 0.03:
            painter.setPen(QPen(QColor(255, 105, 155, int(215 * veil)), max(2.0, min(w, h) * 0.025)))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
    finally:
        painter.end()
    return out


def shape_star_burst(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """五角星绽放：中心旋转五角星几何遮罩向外绽放揭示 B。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    from PySide6.QtGui import QPen, QPolygonF
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    try:
        cx, cy = w * 0.5, h * 0.5
        r_out = math.hypot(w, h) * 1.35 * (p ** 1.1)
        r_in = r_out * (0.42 + 0.35 * p)
        rot = -math.pi * 0.5 + (1.0 - p) * 0.65
        pts = []
        for i in range(10):
            r = r_out if i % 2 == 0 else r_in
            ang = rot + i * math.pi / 5.0
            pts.append(QPointF(cx + r * math.cos(ang), cy + r * math.sin(ang)))
        path = QPainterPath()
        path.addPolygon(QPolygonF(pts))
        path.closeSubpath()
        painter.save()
        painter.setClipPath(path)
        _draw(painter, b, 0, 0, w, h)
        painter.restore()
        if p > 0.82:
            painter.setOpacity((p - 0.82) / 0.18)
            _draw(painter, b, 0, 0, w, h)
            painter.setOpacity(1.0)
        if veil > 0.03:
            painter.setPen(QPen(QColor(255, 215, 80, int(210 * veil)), max(2.0, min(w, h) * 0.024)))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
    finally:
        painter.end()
    return out


def shape_diagonal_slash(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """斜切刀光分屏：一道锐利斜向刀光劈开原画面，上下两半斜向错开揭示新画面。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    from PySide6.QtGui import QPen, QPolygonF
    out = _copy_fit(b, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        shift = p * max(w, h) * 0.85
        if p < 0.999:
            # 上左三角
            p1 = QPainterPath()
            p1.addPolygon(QPolygonF([QPointF(-w, -h), QPointF(2 * w, -h), QPointF(-w, 2 * h)]))
            painter.save()
            painter.setClipPath(p1)
            _draw(painter, a, -shift, -shift * (float(h) / max(1.0, float(w))), w, h)
            painter.restore()
            # 下右三角
            p2 = QPainterPath()
            p2.addPolygon(QPolygonF([QPointF(2 * w, -h), QPointF(2 * w, 2 * h), QPointF(-w, 2 * h)]))
            painter.save()
            painter.setClipPath(p2)
            _draw(painter, a, shift, shift * (float(h) / max(1.0, float(w))), w, h)
            painter.restore()
        if veil > 0.02:
            painter.setPen(QPen(QColor(220, 245, 255, int(235 * veil)), max(2.5, min(w, h) * 0.03 * veil)))
            painter.drawLine(QPointF(0, h), QPointF(w, 0))
    finally:
        painter.end()
    return out


def shape_shutter_bands(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """动态斜百叶条带：多道平行斜向条带交错滑入合拢。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    from PySide6.QtGui import QPolygonF
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    try:
        bands = 8
        span = float(w + h)
        step = span / bands
        skew = float(h) * 0.45
        for i in range(-1, bands + 1):
            lp = max(0.0, min(1.0, (p * 1.25 - (i % 3) * 0.08)))
            bw = step * lp
            if bw <= 0.2:
                continue
            d0 = i * step
            d1 = d0 + bw + 0.8
            poly = QPolygonF([
                QPointF(d0, 0),
                QPointF(d1, 0),
                QPointF(d1 - skew, h),
                QPointF(d0 - skew, h),
            ])
            path = QPainterPath()
            path.addPolygon(poly)
            painter.save()
            painter.setClipPath(path)
            sign = 1.0 if i % 2 == 0 else -1.0
            _draw(painter, b, sign * w * 0.12 * (1.0 - lp), 0, w, h)
            painter.restore()
    finally:
        painter.end()
    return out


def shape_circle_wave(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """多重同心圆波纹扩散：多层同心圆环波浪依次向外扩散揭示 B。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        cx, cy = w * 0.5, h * 0.5
        max_r = math.hypot(cx, cy) * 1.15
        for idx, delay, sc_off in ((0, 0.0, 0.18), (1, 0.12, -0.12), (2, 0.24, 0.0)):
            lp = max(0.0, min(1.0, (p - delay) / (1.0 - delay)))
            if lp <= 1e-4:
                continue
            r = max_r * _ease(lp, "ease_out")
            path = QPainterPath()
            path.addEllipse(QPointF(cx, cy), r, r)
            painter.save()
            painter.setClipPath(path)
            sc = 1.0 + sc_off * math.sin(lp * math.pi) * k
            _draw_xform(painter, b, w, h, sx=sc, opacity=1.0)
            painter.restore()
    finally:
        painter.end()
    return out


def shape_split_slide_4(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """四象限交错滑入：画面分为四个象限从四个正交方向风车式滑入拼合。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    try:
        hw, hh = w * 0.5, h * 0.5
        rem = 1.0 - p
        quads = (
            (QRectF(0, 0, hw * p, hh + 0.5), -hw * rem, 0.0),
            (QRectF(hw, 0, hw + 0.5, hh * p), 0.0, -hh * rem),
            (QRectF(0, hh + hh * rem, hw + 0.5, hh * p + 0.5), 0.0, hh * rem),
            (QRectF(hw + hw * rem, hh, hw * p + 0.5, hh + 0.5), hw * rem, 0.0),
        )
        for rect, ox, oy in quads:
            if rect.width() <= 0.2 or rect.height() <= 0.2:
                continue
            painter.save()
            painter.setClipRect(rect)
            _draw(painter, b, ox, oy, w, h)
            painter.restore()
        if veil > 0.08:
            line_a = int(130 * (veil ** 2))
            if line_a > 2:
                painter.fillRect(QRectF(hw - 1.0, 0, 2.0, h), QColor(255, 255, 255, line_a))
                painter.fillRect(QRectF(0, hh - 1.0, w, 2.0), QColor(255, 255, 255, line_a))
    finally:
        painter.end()
    return out


def shape_clock_dual(t, a, b, w, h, direction="cw", intensity=1.0, *, easing="ease_in_out", **kw):
    """双翼扇形清扫：对向双扇叶（12 点与 6 点方向）同步顺时针旋转清扫。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    try:
        cx, cy = w * 0.5, h * 0.5
        rad = math.hypot(w, h)
        span = 180.0 * p
        box = QRectF(cx - rad, cy - rad, rad * 2.0, rad * 2.0)
        path = QPainterPath()
        for start_ang in (90.0, 270.0):
            path.moveTo(cx, cy)
            path.arcTo(box, start_ang, -span)
            path.closeSubpath()
        painter.setClipPath(path)
        _draw(painter, b, 0, 0, w, h)
    finally:
        painter.end()
    return out


def shape_polaroid_stack(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """拍立得相纸飞入堆叠：带白色相纸边框的照片倾斜飞入叠放后放大满屏。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    try:
        # 暗化背景 A
        painter.fillRect(0, 0, w, h, QColor(12, 12, 16, int(130 * veil)))
        # 相纸从 0.58 倍带倾角飞入，最后 35% 放大到 1.0 满幅且边框淡出
        fly_p = min(1.0, p / 0.65)
        zoom_p = max(0.0, (p - 0.55) / 0.45)
        sc = 0.56 + 0.44 * _ease(zoom_p, "ease_in_out")
        rot = -14.0 * (1.0 - _ease(p, "ease_out")) * k
        tx = (1.0 - _ease(fly_p, "ease_out")) * w * 0.55
        ty = (1.0 - _ease(fly_p, "ease_out")) * h * 0.25
        border = max(3.0, min(w, h) * 0.045) * (1.0 - zoom_p)
        painter.save()
        painter.translate(w * 0.5 + tx, h * 0.5 + ty)
        painter.rotate(rot)
        painter.scale(sc, sc)
        painter.translate(-w * 0.5, -h * 0.5)
        if border > 0.5:
            painter.fillRect(
                QRectF(-border, -border, w + 2 * border, h + 2.8 * border),
                QColor(250, 248, 242, int(245 * (1.0 - zoom_p))),
            )
        _draw(painter, b, 0, 0, w, h)
        painter.restore()
    finally:
        painter.end()
    return out


def shape_kaleidoscope(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """万花筒镜像折射：四象限对称翻转镜像折射旋转过渡。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        _draw_xform(painter, b, w, h, opacity=p)
        if veil > 0.01:
            src = a if p < 0.5 else b
            mh = src.flipped(Qt.Orientation.Horizontal)
            mv = src.flipped(Qt.Orientation.Vertical)
            mhv = src.flipped(Qt.Orientation.Horizontal | Qt.Orientation.Vertical)
            hw, hh = w * 0.5, h * 0.5
            sc = 1.0 + 0.35 * veil * k
            rot = 45.0 * veil * k
            tiles = (
                (QRectF(0, 0, hw, hh), src),
                (QRectF(hw, 0, hw, hh), mh),
                (QRectF(0, hh, hw, hh), mv),
                (QRectF(hw, hh, hw, hh), mhv),
            )
            for rect, tile_img in tiles:
                painter.save()
                painter.setClipRect(rect)
                _draw_xform(painter, tile_img, w, h, sx=sc, rot=rot, opacity=veil * 0.92)
                painter.restore()
    finally:
        painter.end()
    return out


def shape_origami_fold(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """折纸翻折：多面体几何三角折纸向四角翻折打开。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    from PySide6.QtGui import QPolygonF
    out = _copy_fit(b, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    try:
        cx, cy = w * 0.5, h * 0.5
        flaps = (
            (QPolygonF([QPointF(0, 0), QPointF(w, 0), QPointF(cx, cy)]), 0.0, -cy * 1.05 * p),
            (QPolygonF([QPointF(w, 0), QPointF(w, h), QPointF(cx, cy)]), cx * 1.05 * p, 0.0),
            (QPolygonF([QPointF(w, h), QPointF(0, h), QPointF(cx, cy)]), 0.0, cy * 1.05 * p),
            (QPolygonF([QPointF(0, h), QPointF(0, 0), QPointF(cx, cy)]), -cx * 1.05 * p, 0.0),
        )
        for idx, (poly, ox, oy) in enumerate(flaps):
            painter.save()
            path = QPainterPath()
            path.addPolygon(poly.translated(ox, oy))
            painter.setClipPath(path)
            _draw(painter, a, ox * 0.65, oy * 0.65, w, h)
            painter.fillPath(path, QColor(0, 0, 0, int(24 * (idx + 1) * veil)))
            painter.restore()
    finally:
        painter.end()
    return out


# ═══════════════════════════════════════════════════════════════════════════
# D. 光效与胶片氛围类 (light，8 款)
# ═══════════════════════════════════════════════════════════════════════════

def light_warm_flare(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """暖金逆光炫光扫过：电影变形宽银幕暖金太阳炫光 + 水平拉丝光斑横扫。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    from PySide6.QtGui import QLinearGradient, QRadialGradient
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    try:
        _draw(painter, b, 0, 0, w, h) if False else None
        painter.setOpacity(p)
        _draw(painter, b, 0, 0, w, h)
        painter.setOpacity(1.0)
        if veil > 0.01:
            fx = w * (0.1 + 0.8 * p)
            fy = h * (0.35 + 0.2 * math.sin(p * math.pi))
            rg = QRadialGradient(fx, fy, max(w, h) * 0.62)
            rg.setColorAt(0.0, QColor(255, 250, 220, min(255, int(240 * veil * k))))
            rg.setColorAt(0.35, QColor(255, 185, 65, min(255, int(195 * veil * k))))
            rg.setColorAt(0.75, QColor(235, 95, 35, min(255, int(95 * veil * k))))
            rg.setColorAt(1.0, QColor(255, 120, 40, 0))
            painter.fillRect(QRectF(0, 0, w, h), rg)
            # 宽银幕水平炫光条
            streak_h = max(4.0, h * 0.09 * veil)
            lg = QLinearGradient(0, fy, w, fy)
            lg.setColorAt(0.0, QColor(255, 210, 120, 0))
            lg.setColorAt(max(0.05, min(0.95, p)), QColor(255, 245, 210, min(255, int(220 * veil))))
            lg.setColorAt(1.0, QColor(255, 180, 90, 0))
            painter.fillRect(QRectF(0, fy - streak_h * 0.5, w, streak_h), lg)
    finally:
        painter.end()
    return out


def light_cyber_neon(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """赛博青紫霓虹光晕：电光青与霓虹品红双色激光斜扫光晕过渡。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    from PySide6.QtGui import QLinearGradient
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    try:
        _draw_xform(painter, a, w, h, tx=-w * 0.06 * p * veil, opacity=1.0)
        _draw_xform(painter, b, w, h, tx=w * 0.06 * (1.0 - p) * veil, opacity=p)
        if veil > 0.01:
            g = QLinearGradient(w * (p - 0.3), 0, w * (p + 0.3), h)
            g.setColorAt(0.0, QColor(0, 245, 255, 0))
            g.setColorAt(0.35, QColor(0, 245, 255, min(255, int(205 * veil * k))))
            g.setColorAt(0.65, QColor(255, 20, 220, min(255, int(215 * veil * k))))
            g.setColorAt(1.0, QColor(120, 0, 255, 0))
            painter.fillRect(QRectF(0, 0, w, h), g)
    finally:
        painter.end()
    return out


def light_prism_rainbow(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """棱镜彩虹色散过渡：水晶棱镜多重折光残影 + 七彩光谱散射带。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    from PySide6.QtGui import QLinearGradient
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        painter.setOpacity(p)
        _draw(painter, b, 0, 0, w, h)
        src = a if p < 0.5 else b
        for off_x, off_y, sc, op in ((-0.06, -0.03, 1.08, 0.26), (0.06, 0.03, 1.12, 0.22)):
            _draw_xform(painter, src, w, h, tx=w * off_x * veil * k, ty=h * off_y * veil * k, sx=sc, opacity=op * veil)
        if veil > 0.01:
            painter.setOpacity(1.0)
            g = QLinearGradient(0, h * (1.0 - p), w, h * p)
            a_val = min(230, int(175 * veil * k))
            g.setColorAt(0.00, QColor(255, 60, 80, 0))
            g.setColorAt(0.22, QColor(255, 75, 95, a_val))
            g.setColorAt(0.40, QColor(255, 210, 60, a_val))
            g.setColorAt(0.58, QColor(65, 235, 150, a_val))
            g.setColorAt(0.78, QColor(70, 155, 255, a_val))
            g.setColorAt(1.00, QColor(185, 80, 255, 0))
            painter.fillRect(QRectF(0, 0, w, h), g)
    finally:
        painter.end()
    return out


def light_film_sprocket(t, a, b, w, h, direction="down", intensity=1.0, *, easing="ease_in_out", **kw):
    """复古胶片齿孔走片：35mm 胶片纵向走片格线 + 两侧齿孔与复古暖褐闪烁。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    try:
        dy_a = h * p
        dy_b = -h * (1.0 - p)
        _draw(painter, a, 0, dy_a, w, h)
        _draw(painter, b, 0, dy_b, w, h)
        if veil > 0.02:
            # 胶片分格黑框线
            bar_h = max(3.0, h * 0.055 * veil)
            painter.fillRect(QRectF(0, dy_a - bar_h * 0.5, w, bar_h), QColor(18, 14, 10, int(235 * veil)))
            # 左右胶片黑边与白色齿孔
            rail_w = max(6.0, w * 0.075 * veil)
            rail_col = QColor(15, 12, 10, int(225 * veil))
            painter.fillRect(QRectF(0, 0, rail_w, h), rail_col)
            painter.fillRect(QRectF(w - rail_w, 0, rail_w, h), rail_col)
            hole_col = QColor(245, 235, 215, int(215 * veil))
            holes = 6
            step_y = float(h) / holes
            hw, hh = rail_w * 0.48, step_y * 0.42
            for i in range(-1, holes + 1):
                hy = ((i * step_y + dy_a) % (h + step_y)) - step_y * 0.5
                painter.fillRect(QRectF(rail_w * 0.26, hy, hw, hh), hole_col)
                painter.fillRect(QRectF(w - rail_w * 0.74, hy, hw, hh), hole_col)
            painter.fillRect(QRectF(0, 0, w, h), QColor(255, 190, 110, int(48 * veil)))
    finally:
        painter.end()
    return out


def light_vhs_rewind(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """VHS 磁带雪花倒带：磁带倒带横向雪花噪波条带 + 扫描线抖动。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    try:
        jx = math.sin(p * 32.0) * w * 0.03 * veil * k
        _draw(painter, a, jx, 0, w, h)
        painter.setOpacity(p)
        _draw(painter, b, -jx, 0, w, h)
        painter.setOpacity(1.0)
        if veil > 0.02:
            for band_i, rel_y in ((0, (p * 1.4) % 1.0), (1, (p * 1.4 + 0.48) % 1.0)):
                by = rel_y * h
                bh = max(3.0, h * 0.09 * veil)
                painter.fillRect(QRectF(0, by - bh * 0.5, w, bh), QColor(235, 242, 255, int(175 * veil)))
                for s in range(5):
                    sx = ((band_i * 73 + s * 41 + int(p * 100)) % 100) / 100.0 * w
                    painter.fillRect(QRectF(sx, by - bh * 0.4, w * 0.18, max(1.0, bh * 0.22)), QColor(30, 40, 55, int(180 * veil)))
    finally:
        painter.end()
    return out


def light_dream_glow(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """柔焦梦境弥散：高光柔焦弥散 + 梦幻粉紫柔光雾化过渡。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    from PySide6.QtGui import QRadialGradient
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        painter.setOpacity(p)
        _draw(painter, b, 0, 0, w, h)
        # 快速降采样再平滑放大生成柔焦梦境层（1080p 下仅 0.5ms）
        src = a if p < 0.5 else b
        sw = max(4, w // 8)
        sh = max(4, h // 8)
        small = _copy_fit(src, w, h).scaled(sw, sh, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.FastTransformation)
        soft = small.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        painter.setOpacity(min(0.88, veil * 0.85 * k))
        painter.drawImage(0, 0, soft)
        painter.setOpacity(1.0)
        if veil > 0.01:
            rg = QRadialGradient(w * 0.5, h * 0.5, max(w, h) * 0.65)
            rg.setColorAt(0.0, QColor(255, 235, 250, min(255, int(155 * veil * k))))
            rg.setColorAt(0.6, QColor(235, 190, 255, min(255, int(115 * veil * k))))
            rg.setColorAt(1.0, QColor(255, 220, 240, int(45 * veil)))
            painter.fillRect(QRectF(0, 0, w, h), rg)
    finally:
        painter.end()
    return out


def light_gold_dust(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """流金光斑消散：漫天流金散景光斑漂浮闪烁过渡。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    try:
        painter.setOpacity(p)
        _draw(painter, b, 0, 0, w, h)
        painter.setOpacity(1.0)
        if veil > 0.01:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.fillRect(0, 0, w, h, QColor(255, 210, 110, int(55 * veil)))
            for i in range(18):
                fx = (((i * 37 + 13) % 97) / 97.0 + 0.22 * p * (1 if i % 2 == 0 else -0.6)) % 1.0
                fy = (((i * 53 + 29) % 89) / 89.0 - 0.28 * p) % 1.0
                rad = min(w, h) * (0.028 + (i % 5) * 0.018) * veil * k
                alpha = min(240, int((130 + (i % 4) * 30) * veil))
                col = QColor(255, 230 - (i % 3) * 25, 120 + (i % 2) * 60, alpha)
                painter.setBrush(col)
                painter.drawEllipse(QPointF(fx * w, fy * h), rad, rad)
    finally:
        painter.end()
    return out


def light_ink_wash(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """东方水墨晕染遮罩：多团有机水墨墨滴晕染扩散揭示新画面。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    from PySide6.QtGui import QPen
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    try:
        diag = math.hypot(w, h)
        blobs = (
            (0.50, 0.50, 0.72),
            (0.28, 0.36, 0.54),
            (0.72, 0.64, 0.56),
            (0.68, 0.30, 0.48),
            (0.32, 0.70, 0.50),
        )
        path = QPainterPath()
        path.setFillRule(Qt.FillRule.WindingFill)
        for bx, by, scale_r in blobs:
            r = diag * scale_r * (p ** 1.05)
            path.addEllipse(QPointF(w * bx, h * by), r, r * 0.88)
        painter.save()
        painter.setClipPath(path)
        _draw(painter, b, 0, 0, w, h)
        painter.restore()
        if p > 0.82:
            painter.setOpacity((p - 0.82) / 0.18)
            _draw(painter, b, 0, 0, w, h)
            painter.setOpacity(1.0)
        if veil > 0.02:
            painter.setPen(QPen(QColor(22, 24, 28, int(185 * veil)), max(2.5, min(w, h) * 0.032 * veil)))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
    finally:
        painter.end()
    return out


# ═══════════════════════════════════════════════════════════════════════════
# E. 多段灵动运镜类 (camera，新增 16 款：推进-缓动-退回-缓动等自媒体高级运镜)
# ═══════════════════════════════════════════════════════════════════════════

def cam_push_drift_pull_drift(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """推近缓移退回缓动：一阶段急推放大 → 二阶段悬浮缓缓横移并叠化 B → 三阶段弹性退回广角 → 四阶段缓缓呼吸落定。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    s1, s2, s3, s4, b_mix = _choreo_4stage(p)
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        # 缩放轨迹：1.0 -> 1.62(推近) -> 1.68(缓移微推) -> 0.85(退回过冲) -> 1.00(缓动落定)
        sc = 1.0 + (0.62 * s1 + 0.06 * s2 - 0.83 * s3 + 0.15 * s4) * k
        sc = max(0.55, sc)
        # 位移轨迹：急推微抬 -> 缓缓右漂 -> 退回左偏 -> 缓缓归零
        tx = w * (0.02 * s1 + 0.08 * s2 - 0.14 * s3 + 0.04 * s4) * k
        ty = h * (-0.04 * s1 + 0.07 * s2 - 0.05 * s3 + 0.02 * s4) * k
        rot = (1.5 * s1 + 2.5 * s2 - 6.0 * s3 + 2.0 * s4) * k
        if b_mix < 0.999:
            _draw_mirrored_xform(painter, a, w, h, tx=tx, ty=ty, sx=sc, rot=rot, opacity=1.0)
            # 第一阶段急推径向残影
            if s1 > 0.05 and s2 < 0.8:
                trail_env = s1 * (1.0 - s2 * 0.8)
                for idx, op in ((1, 0.24), (2, 0.14)):
                    _draw_xform(painter, a, w, h, tx=tx, ty=ty, sx=sc + idx * 0.09 * trail_env, rot=rot, opacity=op * trail_env)
        if b_mix > 0.001:
            _draw_mirrored_xform(painter, b, w, h, tx=tx, ty=ty, sx=sc, rot=rot, opacity=b_mix)
            if s3 > 0.05 and s4 < 0.9:
                rebound_env = s3 * (1.0 - s4)
                _draw_xform(painter, b, w, h, tx=tx * 0.6, ty=ty * 0.6, sx=sc * (1.0 + 0.08 * rebound_env), rot=rot * 0.5, opacity=0.22 * rebound_env * b_mix)
    finally:
        painter.end()
    return out


def cam_pull_float_push_settle(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """拉远悬浮推进落定：先急速后拉成悬浮小画幅 → 空中缓缓漂移换景 → 猛然推进过冲 → 缓缓回落定格。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    s1, s2, s3, s4, b_mix = _choreo_4stage(p)
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        # 1.0 -> 0.66(拉远) -> 0.62(悬浮缓漂) -> 1.24(推进过冲) -> 1.00(缓缓落定)
        sc = 1.0 + (-0.34 * s1 - 0.04 * s2 + 0.62 * s3 - 0.24 * s4) * min(1.3, k)
        sc = max(0.45, sc)
        tx = w * (-0.03 * s1 + 0.09 * s2 - 0.09 * s3 + 0.03 * s4) * k
        ty = h * (0.03 * s1 - 0.07 * s2 + 0.06 * s3 - 0.02 * s4) * k
        rot = (-2.5 * s1 + 5.0 * s2 - 4.0 * s3 + 1.5 * s4) * k
        if b_mix < 0.999:
            _draw_mirrored_xform(painter, a, w, h, tx=tx, ty=ty, sx=sc, rot=rot, opacity=1.0)
        if b_mix > 0.001:
            _draw_mirrored_xform(painter, b, w, h, tx=tx, ty=ty, sx=sc, rot=rot, opacity=b_mix)
    finally:
        painter.end()
    return out


def cam_push_pan_right_pull(t, a, b, w, h, direction="right", intensity=1.0, *, easing="ease_in_out", **kw):
    """推进慢摇退回：高速推近特写 → 镜头向右缓缓平移摇镜切入 B → 弹性后撤拉远 → 缓动归位。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    s1, s2, s3, s4, b_mix = _choreo_4stage(p)
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        sc = max(0.60, 1.0 + (0.54 * s1 + 0.04 * s2 - 0.72 * s3 + 0.14 * s4) * k)
        pan_a = -w * (0.06 * s1 + 0.26 * s2 + 0.08 * s3) * k
        pan_b = w * (0.28 * (1.0 - s2) - 0.06 * s3 * (1.0 - s4)) * k
        ty = h * (-0.03 * s1 + 0.05 * s2 - 0.02 * s3) * k
        rot = (1.8 * s1 - 3.2 * s2 + 1.4 * s3) * (1.0 - s4) * k
        _draw_mirrored_xform(painter, a, w, h, tx=pan_a, ty=ty, sx=sc, rot=rot, opacity=1.0)
        if b_mix > 0.001:
            _draw_mirrored_xform(painter, b, w, h, tx=pan_b, ty=ty, sx=sc, rot=rot, opacity=b_mix)
    finally:
        painter.end()
    return out


def cam_push_pan_left_pull(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """推进左移回落：高速推近 → 镜头向左缓缓横摇切入 B → 弹性拉远退回 → 缓慢呼吸落定。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    s1, s2, s3, s4, b_mix = _choreo_4stage(p)
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        sc = max(0.60, 1.0 + (0.54 * s1 + 0.04 * s2 - 0.72 * s3 + 0.14 * s4) * k)
        pan_a = w * (0.06 * s1 + 0.26 * s2 + 0.08 * s3) * k
        pan_b = -w * (0.28 * (1.0 - s2) - 0.06 * s3 * (1.0 - s4)) * k
        ty = h * (0.03 * s1 - 0.05 * s2 + 0.02 * s3) * k
        rot = (-1.8 * s1 + 3.2 * s2 - 1.4 * s3) * (1.0 - s4) * k
        _draw_mirrored_xform(painter, a, w, h, tx=pan_a, ty=ty, sx=sc, rot=rot, opacity=1.0)
        if b_mix > 0.001:
            _draw_mirrored_xform(painter, b, w, h, tx=pan_b, ty=ty, sx=sc, rot=rot, opacity=b_mix)
    finally:
        painter.end()
    return out


def cam_push_tilt_up_settle(t, a, b, w, h, direction="up", intensity=1.0, *, easing="ease_in_out", **kw):
    """推近仰漂回落：镜头急推并微俯 → 缓缓向上仰漂升起并融合 B → 弹性后退回撤 → 缓缓呼吸落定。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    s1, s2, s3, s4, b_mix = _choreo_4stage(p)
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        sc = max(0.60, 1.0 + (0.52 * s1 + 0.06 * s2 - 0.72 * s3 + 0.14 * s4) * k)
        ty_a = h * (-0.05 * s1 + 0.24 * s2 + 0.06 * s3) * k
        ty_b = h * (-0.24 * (1.0 - s2) + 0.05 * s3 * (1.0 - s4)) * k
        tx = w * (0.03 * math.sin(p * math.pi * 2.0) * veil) * k
        _draw_mirrored_xform(painter, a, w, h, tx=tx, ty=ty_a, sx=sc, opacity=1.0)
        if b_mix > 0.001:
            _draw_mirrored_xform(painter, b, w, h, tx=tx, ty=ty_b, sx=sc, opacity=b_mix)
    finally:
        painter.end()
    return out


def cam_spiral_glide_rebound(t, a, b, w, h, direction="cw", intensity=1.0, *, easing="ease_in_out", **kw):
    """旋推缓漂回旋：螺旋急推放大 → 悬浮慢速旋转漂移切入 B → 反向回旋拉远 → 缓缓归正落定。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    s1, s2, s3, s4, b_mix = _choreo_4stage(p)
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        sc = max(0.62, 1.0 + (0.58 * s1 + 0.07 * s2 - 0.79 * s3 + 0.14 * s4) * k)
        rot = (26.0 * s1 + 12.0 * s2 - 46.0 * s3 + 8.0 * s4) * k
        tx = w * (0.05 * s2 - 0.05 * s3) * k
        ty = h * (-0.04 * s2 + 0.04 * s3) * k
        _draw_mirrored_xform(painter, a, w, h, tx=tx, ty=ty, sx=sc, rot=rot, opacity=1.0)
        if b_mix > 0.001:
            _draw_mirrored_xform(painter, b, w, h, tx=tx, ty=ty, sx=sc, rot=rot, opacity=b_mix)
    finally:
        painter.end()
    return out


def cam_s_curve_glide(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """S型丝滑运镜：镜头沿优雅 S 型流线轨迹推拉摇移，中段丝滑横漂叠化，尾段缓动收束。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        tx = w * 0.22 * math.sin(p * math.pi * 2.0) * veil * k
        ty = h * 0.12 * math.sin(p * math.pi * 4.0) * veil * k
        sc = 1.0 + (0.38 * veil + 0.14 * math.sin(p * math.pi * 3.0) * veil) * k
        rot = 8.5 * math.cos(p * math.pi * 2.0) * veil * k
        b_mix = _smoothstep((p - 0.18) / 0.64)
        _draw_mirrored_xform(painter, a, w, h, tx=tx, ty=ty, sx=sc, rot=rot, opacity=1.0)
        for i, op in ((1, 0.22), (2, 0.12)):
            _draw_xform(painter, a if p < 0.5 else b, w, h, tx=tx * (1.0 - 0.18 * i), ty=ty * (1.0 - 0.18 * i), sx=sc, rot=rot, opacity=op * veil)
        if b_mix > 0.001:
            _draw_mirrored_xform(painter, b, w, h, tx=tx, ty=ty, sx=sc, rot=rot, opacity=b_mix)
    finally:
        painter.end()
    return out


def cam_orbit_float_3d(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """环绕悬浮运镜：3D 空间弧线环绕推进 → 悬浮微移侧倾切换 → 弹性退回正面落定。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    from PySide6.QtGui import QPolygonF
    s1, s2, s3, s4, b_mix = _choreo_4stage(p)
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        sc_bg = 1.25 + 0.25 * veil
        _draw_xform(painter, a if b_mix < 0.5 else b, w, h, sx=sc_bg, opacity=1.0)
        yaw = (0.14 * s1 + 0.04 * s2 - 0.24 * s3 + 0.06 * s4) * min(1.4, k)
        zoom_in = (0.28 * s1 + 0.05 * s2 - 0.42 * s3 + 0.09 * s4) * min(1.3, k)
        src_poly = QPolygonF([QPointF(0, 0), QPointF(w, 0), QPointF(w, h), QPointF(0, h)])
        dx_orbit = w * 0.08 * math.sin(p * math.pi * 2.0) * veil * k
        dy_l = h * yaw
        dy_r = -h * yaw
        pad_x = -w * zoom_in * 0.5
        pad_y = -h * zoom_in * 0.5
        dst_poly = QPolygonF([
            QPointF(pad_x + dx_orbit, pad_y + dy_l),
            QPointF(w - pad_x + dx_orbit, pad_y + dy_r),
            QPointF(w - pad_x + dx_orbit, h - pad_y - dy_r),
            QPointF(pad_x + dx_orbit, h - pad_y - dy_l),
        ])
        xf = QTransform()
        if QTransform.quadToQuad(src_poly, dst_poly, xf):
            painter.save()
            painter.setTransform(xf, True)
            _draw(painter, a, 0, 0, w, h)
            if b_mix > 0.001:
                painter.setOpacity(b_mix)
                _draw(painter, b, 0, 0, w, h)
            painter.restore()
    finally:
        painter.end()
    return out


def cam_handheld_breathe_push(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """手持呼吸推拉：纪实感手持微晃呼吸 + 推进寻焦缓移 + 退回呼吸落位。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    from PySide6.QtGui import QPen
    s1, s2, s3, s4, b_mix = _choreo_4stage(p)
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        sc = max(0.68, 1.0 + (0.44 * s1 + 0.05 * s2 - 0.58 * s3 + 0.09 * s4) * k)
        breathe_x = w * (0.032 * math.sin(p * math.pi * 3.0) + 0.018 * math.cos(p * math.pi * 7.0)) * veil * k
        breathe_y = h * (0.028 * math.cos(p * math.pi * 2.5) + 0.015 * math.sin(p * math.pi * 6.0)) * veil * k
        rot = (2.8 * math.sin(p * math.pi * 2.5) + 1.4 * math.cos(p * math.pi * 5.5)) * veil * k
        _draw_mirrored_xform(painter, a, w, h, tx=breathe_x, ty=breathe_y, sx=sc, rot=rot, opacity=1.0)
        if b_mix > 0.001:
            _draw_mirrored_xform(painter, b, w, h, tx=breathe_x, ty=breathe_y, sx=sc, rot=rot, opacity=b_mix)
        # 寻焦呼吸框微光
        if veil > 0.15:
            m = min(w, h) * (0.18 + 0.03 * math.sin(p * math.pi * 4.0))
            painter.setPen(QPen(QColor(255, 255, 255, int(75 * veil)), max(1.0, min(w, h) * 0.006)))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(QRectF(w * 0.5 - m, h * 0.5 - m * 0.6, m * 2.0, m * 1.2))
    finally:
        painter.end()
    return out


def cam_whip_brake_drift(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """急甩急刹慢漂：极速横甩入场 → 磁吸急刹切换为超慢速悬浮横漂 → 弹性回拉 → 缓停落定。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    s1, s2, s3, s4, b_mix = _choreo_4stage(p)
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        # 位移进度：Stage 1 急甩到 0.76 -> Stage 2 慢漂到 0.90 -> Stage 3 过冲到 1.07 -> Stage 4 回退到 1.00
        prog_x = 0.76 * s1 + 0.14 * s2 + 0.17 * s3 - 0.07 * s4
        sc = max(0.75, 1.0 + (0.28 * s1 + 0.04 * s2 - 0.40 * s3 + 0.08 * s4) * k)
        dx_a = -w * prog_x
        dx_b = w * (1.0 - prog_x)
        _draw_mirrored_xform(painter, b if prog_x > 0.5 else a, w, h, sx=1.26, opacity=1.0)
        _draw_xform(painter, a, w, h, tx=dx_a, sx=sc, opacity=max(0.0, 1.0 - b_mix * 0.85))
        _draw_xform(painter, b, w, h, tx=dx_b, sx=sc, opacity=min(1.0, 0.25 + 0.75 * s1))
        if s1 > 0.05 and s2 < 0.5:
            whip_env = s1 * (1.0 - s2 * 2.0)
            for idx, op in ((1, 0.26), (2, 0.15)):
                _draw_xform(painter, b, w, h, tx=dx_b + idx * w * 0.06 * whip_env, sx=sc, opacity=op * whip_env)
    finally:
        painter.end()
    return out


def cam_drop_bounce_float(t, a, b, w, h, direction="down", intensity=1.0, *, easing="ease_in_out", **kw):
    """俯冲回弹慢升：镜头急速下潜俯冲 → 失重悬浮慢升并切入 B → 退回微沉 → 缓缓升起落定。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    s1, s2, s3, s4, b_mix = _choreo_4stage(p)
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        sc = max(0.65, 1.0 + (0.46 * s1 + 0.05 * s2 - 0.63 * s3 + 0.12 * s4) * k)
        ty = h * (0.22 * s1 - 0.30 * s2 + 0.13 * s3 - 0.05 * s4) * k
        tx = w * (0.03 * s2 - 0.03 * s3) * k
        rot = (-2.2 * s1 + 3.8 * s2 - 1.6 * s3) * (1.0 - s4) * k
        _draw_mirrored_xform(painter, a, w, h, tx=tx, ty=ty, sx=sc, rot=rot, opacity=1.0)
        if b_mix > 0.001:
            _draw_mirrored_xform(painter, b, w, h, tx=tx, ty=ty, sx=sc, rot=rot, opacity=b_mix)
    finally:
        painter.end()
    return out


def cam_zoom_step_triple(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """三段阶梯推拉：一段推近 → 悬停微动 → 二段深推换画 → 悬停微动 → 三段拉远退回并缓动归位。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        st1 = _smoothstep(p / 0.22)
        dr1 = _smoothstep((p - 0.22) / 0.18)
        st2 = _smoothstep((p - 0.40) / 0.20)
        dr2 = _smoothstep((p - 0.60) / 0.16)
        st3 = _smoothstep((p - 0.76) / 0.24)
        sc = 1.0 + (0.30 * st1 + 0.03 * dr1 + 0.34 * st2 - 0.04 * dr2 - 0.63 * st3) * k
        sc = max(0.65, sc)
        tx = w * (0.04 * dr1 - 0.07 * dr2 + 0.03 * st3) * (1.0 - st3) * k
        ty = h * (-0.03 * dr1 + 0.05 * dr2 - 0.02 * st3) * (1.0 - st3) * k
        b_mix = _smoothstep((p - 0.34) / 0.28)
        _draw_mirrored_xform(painter, a, w, h, tx=tx, ty=ty, sx=sc, opacity=1.0)
        if b_mix > 0.001:
            _draw_mirrored_xform(painter, b, w, h, tx=tx, ty=ty, sx=sc, opacity=b_mix)
    finally:
        painter.end()
    return out


def cam_dolly_pan_parallax(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """视差推摇慢移：远景/中景/近景三层景深以不同视差速率做推近-慢摇-退回-缓动运镜。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    s1, s2, s3, s4, b_mix = _choreo_4stage(p)
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    try:
        base_sc = 1.0 + (0.48 * s1 + 0.06 * s2 - 0.64 * s3 + 0.10 * s4) * k
        base_tx = w * (0.04 * s1 + 0.12 * s2 - 0.20 * s3 + 0.04 * s4) * k
        # 1. 远景层
        _draw_mirrored_xform(painter, a, w, h, tx=base_tx * 0.5, sx=max(0.7, base_sc * 0.92), opacity=1.0)
        if b_mix > 0.001:
            _draw_mirrored_xform(painter, b, w, h, tx=base_tx * 0.5, sx=max(0.7, base_sc * 0.92), opacity=b_mix)
        # 2. 中景与近景视差环
        for ring_frac, mul_sc, mul_tx, op in ((0.76, 1.08, 1.0, 0.85), (0.48, 1.18, 1.45, 0.95)):
            painter.save()
            path = QPainterPath()
            path.addEllipse(QPointF(w * 0.5 + base_tx * 0.25, h * 0.5), w * 0.5 * ring_frac, h * 0.5 * ring_frac)
            painter.setClipPath(path)
            sc_layer = max(0.65, 1.0 + (base_sc - 1.0) * mul_sc)
            _draw_mirrored_xform(painter, a, w, h, tx=base_tx * mul_tx, sx=sc_layer, opacity=op * (1.0 - b_mix))
            if b_mix > 0.001:
                _draw_mirrored_xform(painter, b, w, h, tx=base_tx * mul_tx, sx=sc_layer, opacity=op * b_mix)
            painter.restore()
    finally:
        painter.end()
    return out


def cam_crane_swoop_settle(t, a, b, w, h, direction="up", intensity=1.0, *, easing="ease_in_out", **kw):
    """摇臂俯掠缓停：大摇臂从高空弧线俯掠推近 → 贴地缓缓滑行换景 → 抬升拉远 → 缓缓悬停。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    s1, s2, s3, s4, b_mix = _choreo_4stage(p)
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        sc = max(0.65, 1.0 + (0.50 * s1 + 0.08 * s2 - 0.70 * s3 + 0.12 * s4) * k)
        tx = w * (-0.12 * s1 + 0.20 * s2 - 0.12 * s3 + 0.04 * s4) * k
        ty = h * (-0.18 * s1 + 0.06 * s2 + 0.16 * s3 - 0.04 * s4) * k
        rot = (-8.0 * s1 + 11.0 * s2 - 5.0 * s3 + 2.0 * s4) * k
        _draw_mirrored_xform(painter, a, w, h, tx=tx, ty=ty, sx=sc, rot=rot, opacity=1.0)
        if b_mix > 0.001:
            _draw_mirrored_xform(painter, b, w, h, tx=tx, ty=ty, sx=sc, rot=rot, opacity=b_mix)
    finally:
        painter.end()
    return out


def cam_pendulum_double_sway(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """双摆微醺摇镜：像钟摆与手持结合的微醺双段摇摆，推近右摆缓漂 → 退回左摆缓停。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    s1, s2, s3, s4, b_mix = _choreo_4stage(p)
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        sc = max(0.68, 1.0 + (0.42 * s1 + 0.05 * s2 - 0.56 * s3 + 0.09 * s4) * k)
        sway_ang = (14.0 * s1 + 4.0 * s2 - 26.0 * s3 + 8.0 * s4) * k
        tx = w * (0.10 * s1 + 0.05 * s2 - 0.22 * s3 + 0.07 * s4) * k
        ty = h * (0.04 * abs(math.sin(p * math.pi * 2.0)) * veil) * k
        _draw_mirrored_xform(painter, a, w, h, tx=tx, ty=ty, sx=sc, rot=sway_ang, opacity=1.0)
        if b_mix > 0.001:
            _draw_mirrored_xform(painter, b, w, h, tx=tx, ty=ty, sx=sc, rot=sway_ang, opacity=b_mix)
    finally:
        painter.end()
    return out


def cam_elastic_slingshot(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """弹弓蓄力冲镜：先缓慢向后拉远蓄力 → 猛然弹射极速冲镜切入 B → 高位悬浮缓动 → 柔和回落。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        draw_back = _smoothstep(p / 0.30)
        launch = _smoothstep((p - 0.30) / 0.24)
        float_glide = _smoothstep((p - 0.54) / 0.24)
        settle = _smoothstep((p - 0.78) / 0.22)
        sc = 1.0 + (-0.32 * draw_back + 0.98 * launch - 0.12 * float_glide - 0.54 * settle) * k
        sc = max(0.55, sc)
        rot = (-4.0 * draw_back + 9.0 * launch - 2.0 * float_glide - 3.0 * settle) * k
        tx = w * (0.05 * float_glide * (1.0 - settle)) * k
        b_mix = _smoothstep((p - 0.32) / 0.26)
        _draw_mirrored_xform(painter, a, w, h, tx=tx, sx=sc, rot=rot, opacity=1.0)
        if b_mix > 0.001:
            _draw_mirrored_xform(painter, b, w, h, tx=tx, sx=sc, rot=rot, opacity=b_mix)
        if 0.28 < p < 0.62:
            burst = math.sin((p - 0.28) / 0.34 * math.pi)
            _draw_xform(painter, b if b_mix > 0.5 else a, w, h, sx=sc * (1.0 + 0.14 * burst), rot=rot, opacity=0.28 * burst)
    finally:
        painter.end()
    return out


# ═══════════════════════════════════════════════════════════════════════════
# F. 卡点变速与弹性律动类 (rhythm，新增 12 款)
# ═══════════════════════════════════════════════════════════════════════════

def beat_speed_ramp_flow(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """曲线变速卡点：慢起蓄力 → 中段指数级极速甩切冲镜 → 尾段丝滑阻尼慢停。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        # S 型高斜率变速曲线（两端极缓，中段极快）
        if p < 0.5:
            ramp = 0.5 * ((p * 2.0) ** 3.8)
        else:
            ramp = 1.0 - 0.5 * (((1.0 - p) * 2.0) ** 3.8)
        vel = math.exp(-((p - 0.5) / 0.16) ** 2)
        sc = 1.0 + 0.58 * vel * k
        dx_a = -w * ramp
        dx_b = w * (1.0 - ramp)
        _draw_mirrored_xform(painter, b if ramp > 0.5 else a, w, h, sx=1.25, opacity=1.0)
        _draw_xform(painter, a, w, h, tx=dx_a, sx=sc, opacity=1.0 - ramp)
        _draw_xform(painter, b, w, h, tx=dx_b, sx=sc, opacity=ramp)
        if vel > 0.05:
            for idx, op in ((1, 0.28), (2, 0.16)):
                off = idx * w * 0.07 * vel * k
                _draw_xform(painter, b if ramp > 0.5 else a, w, h, tx=(dx_b if ramp > 0.5 else dx_a) + off, sx=sc, opacity=op * vel)
    finally:
        painter.end()
    return out


def beat_heartbeat_double_zoom(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """双跳心跳推拉：模拟咚-咚双跳心脏搏动缩放脉冲 + 绯红暗角呼吸晕染。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    from PySide6.QtGui import QRadialGradient
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        lub = math.exp(-((p - 0.26) / 0.085) ** 2)
        dub = 1.25 * math.exp(-((p - 0.50) / 0.095) ** 2)
        pulse = (lub + dub) * veil
        sc = 1.0 + 0.36 * pulse * k
        b_mix = _smoothstep((p - 0.24) / 0.42)
        _draw_xform(painter, a, w, h, sx=sc, opacity=1.0)
        if b_mix > 0.001:
            _draw_xform(painter, b, w, h, sx=sc, opacity=b_mix)
        if pulse > 0.04:
            rg = QRadialGradient(w * 0.5, h * 0.5, max(w, h) * 0.62)
            rg.setColorAt(0.0, QColor(255, 70, 110, 0))
            rg.setColorAt(0.65, QColor(255, 50, 95, int(65 * min(1.0, pulse))))
            rg.setColorAt(1.0, QColor(180, 15, 55, int(155 * min(1.0, pulse))))
            painter.fillRect(QRectF(0, 0, w, h), rg)
    finally:
        painter.end()
    return out


def beat_shake_flash_combo(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """震屏闪白连击：三连击重拍震屏移位 + 高能爆闪卡点。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        hit1 = math.exp(-((p - 0.25) / 0.07) ** 2)
        hit2 = math.exp(-((p - 0.50) / 0.07) ** 2)
        hit3 = math.exp(-((p - 0.74) / 0.07) ** 2)
        combo = (hit1 + 1.2 * hit2 + 0.85 * hit3) * veil
        sc = 1.0 + 0.30 * combo * k
        jx = math.sin(p * math.pi * 16.0) * w * 0.045 * combo * k
        jy = math.cos(p * math.pi * 12.0) * h * 0.045 * combo * k
        rot = math.sin(p * math.pi * 8.0) * 5.0 * combo * k
        _draw_xform(painter, a, w, h, tx=jx, ty=jy, sx=sc, rot=rot, opacity=1.0)
        _draw_xform(painter, b, w, h, tx=-jx * 0.7, ty=-jy * 0.7, sx=sc, rot=-rot * 0.7, opacity=_smoothstep((p - 0.22) / 0.52))
        flash_al = min(225, int(195 * combo * min(1.4, k)))
        if flash_al > 4:
            painter.fillRect(0, 0, w, h, QColor(255, 252, 245, flash_al))
    finally:
        painter.end()
    return out


def beat_rgb_wave_pulse(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """幻彩波浪脉冲：正弦流体波浪切片 + RGB 通道脉冲色散卡点。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    from cutora.render.transitions import _channel_shift
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    base = _copy_fit(a, w, h)
    painter = QPainter(base)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        sc = 1.0 + 0.22 * veil * k
        _draw_xform(painter, a, w, h, sx=sc, opacity=1.0)
        _draw_xform(painter, b, w, h, sx=sc, opacity=p)
        bands = 10
        bh = float(h) / bands
        for i in range(bands):
            y0 = i * bh
            dx = math.sin(i * 0.9 + p * math.pi * 5.0) * w * 0.08 * veil * k
            painter.save()
            painter.setClipRect(QRectF(0, y0, w, bh + 0.8))
            _draw_xform(painter, b if p > 0.45 else a, w, h, tx=dx, sx=sc, opacity=0.75 * veil)
            painter.restore()
    finally:
        painter.end()
    shift_x = int(round(max(2.0, w * 0.038) * veil * k * math.sin(p * math.pi * 3.0)))
    shift_y = int(round(max(1.0, h * 0.022) * veil * k * math.cos(p * math.pi * 2.0)))
    return _channel_shift(base, shift_x, shift_y)


def beat_freeze_pop_card(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """定格抠像弹卡：B 画面以斜向白框潮酷定格卡弹出悬停，再平滑放大占满全屏。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    from PySide6.QtGui import QPen
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    try:
        _draw_xform(painter, a, w, h, sx=1.0 + 0.18 * veil, opacity=1.0)
        painter.fillRect(0, 0, w, h, QColor(12, 14, 22, int(145 * veil)))
        pop_in = _smoothstep(p / 0.24)
        hold_drift = _smoothstep((p - 0.24) / 0.38)
        expand_out = _smoothstep((p - 0.62) / 0.38)
        card_sc = (0.58 * pop_in + 0.06 * hold_drift) * (1.0 - expand_out) + 1.0 * expand_out
        card_rot = (-9.0 * pop_in + 3.5 * hold_drift) * (1.0 - expand_out) * k
        painter.save()
        painter.setOpacity(min(1.0, pop_in * 1.2))
        painter.translate(w * 0.5, h * 0.5)
        painter.rotate(card_rot)
        painter.scale(max(0.1, card_sc), max(0.1, card_sc))
        painter.translate(-w * 0.5, -h * 0.5)
        _draw(painter, b, 0, 0, w, h)
        if expand_out < 0.98:
            bw = max(2.0, min(w, h) * 0.032 * (1.0 - expand_out))
            painter.setPen(QPen(QColor(255, 255, 255, int(245 * (1.0 - expand_out))), bw))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(QRectF(0, 0, w, h))
        painter.restore()
    finally:
        painter.end()
    return out


def beat_jelly_squash_stretch(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """果冻挤压回弹：非均匀宽高比挤压拉伸（Squash & Stretch）Q 弹果冻形变卡点。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        osc = math.sin(p * math.pi * 4.0) * math.exp(-2.2 * p) * veil * min(1.4, k)
        base_sc = 1.0 + 0.24 * veil
        sx = max(0.65, base_sc * (1.0 + 0.26 * osc))
        sy = max(0.65, base_sc * (1.0 - 0.22 * osc))
        _draw_mirrored_xform(painter, a, w, h, sx=sx, sy=sy, opacity=1.0)
        _draw_mirrored_xform(painter, b, w, h, sx=sx, sy=sy, opacity=_smoothstep((p - 0.15) / 0.70))
    finally:
        painter.end()
    return out


def beat_vinyl_scratch_rewind(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """搓盘倒带卡点：DJ 黑胶唱片正反搓盘旋转摆动 + 扫描纹理倒带切换。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    from PySide6.QtGui import QPen
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    try:
        scratch_rot = (32.0 * math.sin(p * math.pi * 3.0) - 14.0 * math.sin(p * math.pi * 6.0)) * veil * k
        sc = 1.0 + 0.42 * veil
        _draw_mirrored_xform(painter, a, w, h, sx=sc, rot=scratch_rot, opacity=1.0)
        _draw_mirrored_xform(painter, b, w, h, sx=sc, rot=scratch_rot, opacity=p)
        if veil > 0.08:
            cx, cy = w * 0.5, h * 0.5
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for r_fac, al in ((0.22, 110), (0.34, 85), (0.46, 65)):
                r = min(w, h) * r_fac
                painter.setPen(QPen(QColor(255, 255, 255, int(al * veil)), max(1.0, min(w, h) * 0.008)))
                painter.drawEllipse(QPointF(cx, cy), r, r)
    finally:
        painter.end()
    return out


def beat_neon_border_bounce(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """霓虹框律动弹：画面缩入赛博双色霓虹发光边框内随节拍弹跳，随后满幅展开。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    from PySide6.QtGui import QPen
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    try:
        _draw_xform(painter, b if p > 0.5 else a, w, h, sx=1.22, opacity=1.0)
        painter.fillRect(0, 0, w, h, QColor(11, 11, 16, int(155 * veil)))
        in_sc = 1.0 - (0.24 - 0.06 * math.sin(p * math.pi * 4.0)) * veil
        rot = 4.5 * math.sin(p * math.pi * 2.0) * veil * k
        painter.save()
        painter.translate(w * 0.5, h * 0.5)
        painter.rotate(rot)
        painter.scale(in_sc, in_sc)
        painter.translate(-w * 0.5, -h * 0.5)
        _draw(painter, a, 0, 0, w, h)
        painter.setOpacity(p)
        _draw(painter, b, 0, 0, w, h)
        painter.setOpacity(1.0)
        if veil > 0.03:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(0, 245, 255, int(230 * veil)), max(2.5, min(w, h) * 0.026)))
            painter.drawRect(QRectF(0, 0, w, h))
            painter.setPen(QPen(QColor(255, 35, 210, int(190 * veil)), max(1.5, min(w, h) * 0.012)))
            painter.drawRect(QRectF(-4, -4, w + 8, h + 8))
        painter.restore()
    finally:
        painter.end()
    return out


def beat_comic_speed_impact(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """漫威速度线冲击：结合配套资产 speed_lines_mask.png 的高燃动漫放射速度线冲镜卡点。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        sc_a = 1.0 + 0.95 * p * k
        sc_b = max(0.55, 1.45 - 0.45 * p)
        _draw_xform(painter, a, w, h, sx=sc_a, opacity=1.0)
        _draw_xform(painter, b, w, h, sx=sc_b, opacity=p)
        if veil > 0.02:
            _draw_asset_overlay(
                painter, "speed_lines_mask.png", w, h,
                sx=1.0 + 0.18 * (1.0 - veil),
                rot=8.0 * math.sin(p * math.pi * 3.0),
                opacity=min(0.95, veil * 0.90 * k),
            )
    finally:
        painter.end()
    return out


def beat_glitch_datamosh_slide(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """像素错位滑切：数字宏块（Macroblock）矩阵交错拖尾滑切与青品红故障色块。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    try:
        painter.setOpacity(p)
        _draw(painter, b, 0, 0, w, h)
        painter.setOpacity(1.0)
        cols, rows = 6, 4
        cw, ch = float(w) / cols, float(h) / rows
        for r in range(rows):
            for c in range(cols):
                seed = (r * 7 + c * 13) % 11
                lp = max(0.0, min(1.0, (p - seed * 0.03) / 0.68))
                shift_x = math.sin(seed + p * 10.0) * cw * 0.85 * veil * k
                shift_y = math.cos(seed * 1.7 + p * 8.0) * ch * 0.35 * veil * k
                src = b if lp > 0.45 else a
                painter.save()
                painter.setClipRect(QRectF(c * cw, r * ch, cw + 0.8, ch + 0.8))
                _draw(painter, src, shift_x, shift_y, w, h)
                painter.restore()
    finally:
        painter.end()
    return out


def beat_zoom_blur_whip(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """径向残影甩切：强烈多层中心径向拉焦残影叠加高速横甩切入。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        dx_a = -w * 0.65 * p * veil
        dx_b = w * 0.65 * (1.0 - p) * veil
        base_sc = 1.0 + 0.45 * veil * k
        _draw_mirrored_xform(painter, a, w, h, tx=dx_a, sx=base_sc, opacity=1.0)
        _draw_mirrored_xform(painter, b, w, h, tx=dx_b, sx=base_sc, opacity=p)
        for i, op in ((1, 0.28), (2, 0.18), (3, 0.10)):
            sc_blur = base_sc + i * 0.12 * veil * k
            _draw_xform(painter, b if p > 0.5 else a, w, h, tx=(dx_b if p > 0.5 else dx_a) * 0.5, sx=sc_blur, opacity=op * veil)
    finally:
        painter.end()
    return out


def beat_drum_roll_slice(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """鼓点四连切：随密集四连鼓点依次从四个斜向条带高速劈入拼合。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    try:
        cols = 4
        cw = float(w) / cols
        for i in range(cols):
            delay = i * 0.16
            lp = _smoothstep((p - delay) / 0.46)
            if lp <= 1e-4:
                continue
            x0 = i * cw
            painter.save()
            painter.setClipRect(QRectF(x0, 0, cw + 0.8, h))
            sign = 1.0 if i % 2 == 0 else -1.0
            dy = sign * h * (1.0 - lp)
            _draw(painter, b, 0, dy, w, h)
            if 0.02 < lp < 0.98:
                painter.fillRect(QRectF(x0, 0, cw, h), QColor(255, 250, 235, int(110 * (1.0 - lp))))
            painter.restore()
    finally:
        painter.end()
    return out


# ═══════════════════════════════════════════════════════════════════════════
# G. 分屏画廊与创意图形类 (graphic，新增 11 款)
# ═══════════════════════════════════════════════════════════════════════════

def shape_split_diagonal_twin(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """斜切双分屏交错：B 画面沿对角线一分为二，从左上与右下双轨交错滑入咬合。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    from PySide6.QtGui import QPen, QPolygonF
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        _draw_xform(painter, a, w, h, sx=1.0 + 0.15 * p, opacity=1.0)
        rem = 1.0 - p
        dx = w * 0.75 * rem
        dy = h * 0.75 * rem
        # 上左三角滑入 B
        p1 = QPainterPath()
        p1.addPolygon(QPolygonF([QPointF(0, 0), QPointF(w, 0), QPointF(0, h)]))
        painter.save()
        painter.setClipPath(p1)
        _draw(painter, b, -dx, -dy, w, h)
        painter.restore()
        # 下右三角滑入 B
        p2 = QPainterPath()
        p2.addPolygon(QPolygonF([QPointF(w, 0), QPointF(w, h), QPointF(0, h)]))
        painter.save()
        painter.setClipPath(p2)
        _draw(painter, b, dx, dy, w, h)
        painter.restore()
        if veil > 0.02:
            painter.setPen(QPen(QColor(255, 240, 195, int(225 * veil)), max(2.0, min(w, h) * 0.024 * veil)))
            painter.drawLine(QPointF(0, h), QPointF(w, 0))
    finally:
        painter.end()
    return out


def shape_split_accordion_5(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """五联手风琴分屏：五道垂直风琴折页交替明暗折展揭示新画面。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    try:
        cols = 5
        cw = float(w) / cols
        for i in range(cols):
            delay = abs(i - 2) * 0.10
            lp = _smoothstep((p - delay) / (1.0 - 2 * 0.10))
            if lp <= 1e-4:
                continue
            cur_w = cw * lp
            cx = (i + 0.5) * cw
            rect = QRectF(cx - cur_w * 0.5, 0, cur_w + 0.8, h)
            painter.save()
            painter.setClipRect(rect)
            _draw(painter, b, (1.0 - lp) * cw * (0.4 if i % 2 == 0 else -0.4), 0, w, h)
            if lp < 0.98:
                shade = int(75 * (1.0 - lp) * (1.0 if i % 2 == 0 else 0.5))
                painter.fillRect(rect, QColor(0, 0, 0, shade))
            painter.restore()
    finally:
        painter.end()
    return out


def shape_magazine_collage(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """时尚杂志拼贴画：三张错落有致的画报相框卡片飞入拼贴，主卡随后平滑放大满屏。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    from PySide6.QtGui import QPen
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    try:
        painter.fillRect(0, 0, w, h, QColor(16, 15, 20, int(140 * veil)))
        s_in = _smoothstep(p / 0.45)
        s_exp = _smoothstep((p - 0.55) / 0.45)
        # 左右两张辅卡
        for side, ang in ((-1.0, -11.0), (1.0, 9.5)):
            if s_exp >= 0.99:
                continue
            painter.save()
            painter.setOpacity(s_in * (1.0 - s_exp))
            painter.translate(w * (0.5 + side * 0.26 * s_in), h * (0.5 + side * 0.06))
            painter.rotate(ang)
            painter.scale(0.46, 0.46)
            painter.translate(-w * 0.5, -h * 0.5)
            _draw(painter, b, 0, 0, w, h)
            painter.setPen(QPen(QColor(250, 246, 238, 230), max(2.0, min(w, h) * 0.03)))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(QRectF(0, 0, w, h))
            painter.restore()
        # 中心主卡：从 0.54 放大到 1.0
        main_sc = 0.54 * s_in * (1.0 - s_exp) + 1.0 * s_exp
        main_rot = 4.5 * (1.0 - s_exp) * s_in
        if main_sc > 0.02:
            painter.save()
            painter.setOpacity(min(1.0, s_in * 1.25))
            painter.translate(w * 0.5, h * 0.5)
            painter.rotate(main_rot)
            painter.scale(main_sc, main_sc)
            painter.translate(-w * 0.5, -h * 0.5)
            _draw(painter, b, 0, 0, w, h)
            if s_exp < 0.98:
                painter.setPen(QPen(QColor(255, 252, 245, int(245 * (1.0 - s_exp))), max(2.0, min(w, h) * 0.032)))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(QRectF(0, 0, w, h))
            painter.restore()
    finally:
        painter.end()
    return out


def shape_filmstrip_scroll(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """电影胶片长廊：横向胶片画廊滚过三格联排画面后推进定格到 B。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        scroll_p = _smoothstep(p / 0.65)
        zoom_p = _smoothstep((p - 0.55) / 0.45)
        strip_sc = 0.68 + 0.32 * zoom_p
        painter.fillRect(0, 0, w, h, QColor(12, 11, 14, int(210 * veil)))
        painter.save()
        painter.translate(w * 0.5, h * 0.5)
        painter.rotate(-5.0 * (1.0 - zoom_p) * veil)
        painter.scale(strip_sc, strip_sc)
        painter.translate(-w * 0.5, -h * 0.5)
        offset_x = -w * 1.06 * scroll_p * (1.0 - zoom_p)
        _draw(painter, a, offset_x, 0, w, h)
        _draw(painter, b, offset_x + w * 1.06 * (1.0 - zoom_p), 0, w, h)
        painter.restore()
        if veil > 0.04 and zoom_p < 0.98:
            bar_h = max(4.0, h * 0.08 * (1.0 - zoom_p))
            painter.fillRect(QRectF(0, 0, w, bar_h), QColor(14, 12, 10, int(230 * veil)))
            painter.fillRect(QRectF(0, h - bar_h, w, bar_h), QColor(14, 12, 10, int(230 * veil)))
    finally:
        painter.end()
    return out


def shape_paper_tear_reveal(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """手撕纸张拼贴：配合 paper_tear_edge.png 毛边纹理，将原画面沿手撕纸裂口向两侧撕开露出新画面。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    from PySide6.QtGui import QPolygonF
    out = _copy_fit(b, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        if p < 0.999:
            steps = 20
            seam_pts = []
            for i in range(steps + 1):
                u = i / float(steps)
                cx = w * (0.5 + 0.06 * math.sin(u * math.pi * 5.0 + 0.3) + 0.03 * math.cos(u * math.pi * 13.0 + 1.2))
                seam_pts.append(QPointF(cx, u * h))
            spread = w * 0.62 * p
            # 左半页纸向左撕开
            left_poly = QPolygonF([QPointF(-w, 0)] + seam_pts + [QPointF(-w, h)])
            painter.save()
            lp_path = QPainterPath()
            lp_path.addPolygon(left_poly.translated(-spread, 0))
            painter.setClipPath(lp_path)
            _draw(painter, a, -spread * 0.85, 0, w, h)
            painter.restore()
            # 右半页纸向右撕开
            right_poly = QPolygonF([QPointF(2 * w, 0)] + seam_pts + [QPointF(2 * w, h)])
            painter.save()
            rp_path = QPainterPath()
            rp_path.addPolygon(right_poly.translated(spread, 0))
            painter.setClipPath(rp_path)
            _draw(painter, a, spread * 0.85, 0, w, h)
            painter.restore()
            # 绘制真实手撕毛边纸纤维纹理
            if veil > 0.02:
                _draw_asset_overlay(painter, "paper_tear_edge.png", w, h, tx=-spread, opacity=min(1.0, veil * 1.15))
                _draw_asset_overlay(painter, "paper_tear_edge.png", w, h, tx=spread, opacity=min(1.0, veil * 1.15))
    finally:
        painter.end()
    return out


def shape_shatter_glass_prism(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """碎裂水晶折射：配合 glass_shards_map.png 水晶裂纹图，画面碎裂为多面体折射晶片后崩解揭示 B。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    from PySide6.QtGui import QPolygonF
    out = _copy_fit(b, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        cx, cy = w * 0.5, h * 0.5
        sectors = 8
        max_r = math.hypot(w, h) * 0.75
        for i in range(sectors):
            a0 = (i / float(sectors)) * 2.0 * math.pi
            a1 = ((i + 1) / float(sectors)) * 2.0 * math.pi
            mid_a = 0.5 * (a0 + a1)
            fly = (p ** 1.35) * max_r * 0.95
            ox = math.cos(mid_a) * fly
            oy = math.sin(mid_a) * fly
            poly = QPolygonF([
                QPointF(cx + ox, cy + oy),
                QPointF(cx + ox + max_r * math.cos(a0), cy + oy + max_r * math.sin(a0)),
                QPointF(cx + ox + max_r * math.cos(a1), cy + oy + max_r * math.sin(a1)),
            ])
            path = QPainterPath()
            path.addPolygon(poly)
            painter.save()
            painter.setClipPath(path)
            painter.setOpacity(max(0.0, 1.0 - p * 0.95))
            _draw_xform(painter, a, w, h, tx=ox * 0.45, ty=oy * 0.45, rot=(i % 2 * 2 - 1) * 18.0 * p, opacity=1.0)
            painter.restore()
        if veil > 0.02:
            _draw_asset_overlay(
                painter, "glass_shards_map.png", w, h,
                sx=1.0 + 0.35 * p,
                opacity=min(0.85, veil * 0.75 * k),
            )
    finally:
        painter.end()
    return out


def shape_puzzle_interlock(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """四块拼图咬合：带圆弧凸榫的四象限拼图块从四方飞入严丝合缝咬合。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    from PySide6.QtGui import QPen
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    try:
        hw, hh = w * 0.5, h * 0.5
        tab_r = min(w, h) * 0.08
        rem = 1.0 - p
        pieces = (
            (QRectF(0, 0, hw, hh), -hw * rem, -hh * rem, QPointF(hw, hh * 0.5)),
            (QRectF(hw, 0, hw, hh), hw * rem, -hh * rem, QPointF(hw * 1.5, hh)),
            (QRectF(0, hh, hw, hh), -hw * rem, hh * rem, QPointF(hw * 0.5, hh)),
            (QRectF(hw, hh, hw, hh), hw * rem, hh * rem, QPointF(hw, hh * 1.5)),
        )
        for rect, ox, oy, tab_c in pieces:
            path = QPainterPath()
            path.setFillRule(Qt.FillRule.WindingFill)
            path.addRect(rect)
            path.addEllipse(tab_c, tab_r, tab_r)
            path_moved = path.translated(ox, oy)
            painter.save()
            painter.setClipPath(path_moved)
            _draw(painter, b, ox, oy, w, h)
            painter.restore()
            if veil > 0.04:
                painter.setPen(QPen(QColor(255, 255, 255, int(175 * veil)), max(1.5, min(w, h) * 0.014)))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPath(path_moved)
    finally:
        painter.end()
    return out


def shape_liquid_morph_wave(t, a, b, w, h, direction="up", intensity=1.0, *, easing="ease_in_out", **kw):
    """流体波浪吞没：多谐波液态曲线浪潮自下而上翻涌吞没全屏。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    from PySide6.QtGui import QPen
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    try:
        base_y = h * (1.12 - 1.24 * p)
        amp = h * 0.11 * veil * k
        steps = 28
        path = QPainterPath()
        path.moveTo(0, h + 10)
        wave_pts = []
        for i in range(steps + 1):
            u = i / float(steps)
            x = u * w
            y = base_y + amp * math.sin(u * math.pi * 3.0 + p * math.pi * 5.0) + amp * 0.45 * math.cos(u * math.pi * 6.0 - p * math.pi * 3.0)
            wave_pts.append(QPointF(x, y))
            path.lineTo(x, y)
        path.lineTo(w, h + 10)
        path.closeSubpath()
        painter.save()
        painter.setClipPath(path)
        _draw(painter, b, 0, 0, w, h)
        painter.restore()
        if veil > 0.03:
            crest = QPainterPath()
            crest.moveTo(wave_pts[0])
            for pt in wave_pts[1:]:
                crest.lineTo(pt)
            painter.setPen(QPen(QColor(135, 240, 255, int(215 * veil)), max(2.5, min(w, h) * 0.025 * veil)))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(crest)
    finally:
        painter.end()
    return out


def shape_portal_tunnel_hex(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """六边形时空门：三层同心旋转六边形几何时空门自中心逐级扩张。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    from PySide6.QtGui import QPen, QPolygonF
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        cx, cy = w * 0.5, h * 0.5
        max_r = math.hypot(w, h) * 1.15
        for idx, delay, col in ((0, 0.0, QColor(0, 240, 255)), (1, 0.12, QColor(255, 70, 210)), (2, 0.24, QColor(255, 225, 100))):
            lp = _smoothstep((p - delay) / (1.0 - delay))
            if lp <= 1e-4:
                continue
            r = max_r * lp
            rot = (1.0 - lp) * 0.55 * (1 if idx % 2 == 0 else -1)
            pts = [QPointF(cx + r * math.cos(rot + k_i * math.pi / 3.0), cy + r * math.sin(rot + k_i * math.pi / 3.0)) for k_i in range(6)]
            path = QPainterPath()
            path.addPolygon(QPolygonF(pts))
            path.closeSubpath()
            painter.save()
            painter.setClipPath(path)
            _draw_xform(painter, b, w, h, sx=1.0 + (2 - idx) * 0.08 * (1.0 - lp), opacity=1.0)
            painter.restore()
            if 0.02 < lp < 0.96:
                c_pen = QColor(col.red(), col.green(), col.blue(), int(200 * (1.0 - lp)))
                painter.setPen(QPen(c_pen, max(2.0, min(w, h) * 0.02)))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPath(path)
    finally:
        painter.end()
    return out


def shape_card_carousel_3d(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """3D卡片旋转木马：A 与 B 作为 3D 透视画廊卡片左右弧线交错轮播切换。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    from PySide6.QtGui import QPolygonF
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    try:
        _draw_xform(painter, b if p > 0.5 else a, w, h, sx=1.25, opacity=1.0)
        painter.fillRect(0, 0, w, h, QColor(11, 12, 18, int(170 * veil)))
        src_poly = QPolygonF([QPointF(0, 0), QPointF(w, 0), QPointF(w, h), QPointF(0, h)])
        # A 卡向左后方透视滑出
        if p < 0.98:
            x_l = -w * 0.65 * p
            x_r = w * (1.0 - 0.72 * p)
            pinch = h * 0.18 * p
            dst_a = QPolygonF([QPointF(x_l, pinch * 0.5), QPointF(x_r, pinch), QPointF(x_r, h - pinch), QPointF(x_l, h - pinch * 0.5)])
            xf_a = QTransform()
            if QTransform.quadToQuad(src_poly, dst_a, xf_a):
                painter.save()
                painter.setTransform(xf_a, True)
                painter.setOpacity(1.0 - p * 0.85)
                _draw(painter, a, 0, 0, w, h)
                painter.restore()
        # B 卡从右后方透视滑入正中
        rem = 1.0 - p
        x_l_b = w * 0.72 * rem
        x_r_b = w * (1.0 + 0.65 * rem)
        pinch_b = h * 0.18 * rem
        dst_b = QPolygonF([QPointF(x_l_b, pinch_b), QPointF(x_r_b, pinch_b * 0.5), QPointF(x_r_b, h - pinch_b * 0.5), QPointF(x_l_b, h - pinch_b)])
        xf_b = QTransform()
        if QTransform.quadToQuad(src_poly, dst_b, xf_b):
            painter.save()
            painter.setTransform(xf_b, True)
            painter.setOpacity(min(1.0, p * 1.25))
            _draw(painter, b, 0, 0, w, h)
            painter.restore()
    finally:
        painter.end()
    return out


def shape_pip_expand_drift(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """画中画缓移展开：B 以精致画中画小窗浮现 → 缓缓漂移至画面中心 → 丝滑展开铺满全屏。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    from PySide6.QtGui import QPen
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    try:
        _draw_xform(painter, a, w, h, sx=1.0 + 0.16 * p, tx=-w * 0.06 * p, opacity=1.0)
        s_appear = _smoothstep(p / 0.25)
        s_drift = _smoothstep((p - 0.20) / 0.38)
        s_expand = _smoothstep((p - 0.56) / 0.44)
        pip_sc = (0.44 * s_appear + 0.08 * s_drift) * (1.0 - s_expand) + 1.0 * s_expand
        pip_tx = w * 0.22 * (1.0 - s_drift) * (1.0 - s_expand)
        pip_ty = h * 0.16 * (1.0 - s_drift) * (1.0 - s_expand)
        if pip_sc > 0.02:
            rw, rh = w * pip_sc, h * pip_sc
            rx, ry = (w - rw) * 0.5 + pip_tx, (h - rh) * 0.5 + pip_ty
            rect = QRectF(rx, ry, rw, rh)
            painter.save()
            painter.setOpacity(min(1.0, s_appear * 1.25))
            painter.setClipRect(rect)
            _draw_xform(painter, b, w, h, tx=pip_tx, ty=pip_ty, sx=pip_sc, opacity=1.0)
            painter.restore()
            if s_expand < 0.98:
                painter.setPen(QPen(QColor(255, 255, 255, int(230 * (1.0 - s_expand))), max(2.0, min(w, h) * 0.022)))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(rect)
    finally:
        painter.end()
    return out


# ═══════════════════════════════════════════════════════════════════════════
# H. 电影光效与水墨氛围类 (light，新增 11 款)
# ═══════════════════════════════════════════════════════════════════════════

def light_anamorphic_streak(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """变形宽银幕蓝光：使用配套纹理 anamorphic_flare.png 呈现好莱坞科幻电影级水平蓝光炫光横扫。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        _draw_xform(painter, a, w, h, sx=1.0 + 0.12 * p * veil, opacity=1.0)
        _draw_xform(painter, b, w, h, sx=1.0 + 0.12 * (1.0 - p) * veil, opacity=p)
        if veil > 0.01:
            _draw_asset_overlay(
                painter, "anamorphic_flare.png", w, h,
                tx=w * (0.45 - 0.90 * p),
                sx=1.35, sy=1.0 + 0.25 * veil,
                opacity=min(1.0, veil * 0.95 * k),
            )
    finally:
        painter.end()
    return out


def light_golden_hour_leak(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """落日流金漏光：使用配套纹理 warm_light_leak.png 呈现落日琥珀金胶片漏光呼吸过渡。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        painter.setOpacity(p)
        _draw(painter, b, 0, 0, w, h)
        painter.setOpacity(1.0)
        if veil > 0.01:
            _draw_asset_overlay(
                painter, "warm_light_leak.png", w, h,
                tx=w * 0.15 * math.sin(p * math.pi),
                sx=1.18 + 0.15 * veil,
                opacity=min(1.0, veil * 0.92 * k),
            )
    finally:
        painter.end()
    return out


def light_halation_film_burn(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """胶片灼烧红晕：使用配套纹理 film_burn_overlay.png 呈现复古 35mm 胶片红晕灼烧穿孔曝光。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        jy = h * 0.04 * math.sin(p * math.pi * 6.0) * veil
        _draw_mirrored_xform(painter, a, w, h, ty=jy, sx=1.06, opacity=1.0)
        _draw_mirrored_xform(painter, b, w, h, ty=-jy, sx=1.06, opacity=p)
        if veil > 0.01:
            _draw_asset_overlay(
                painter, "film_burn_overlay.png", w, h,
                sx=1.12 + 0.20 * veil,
                rot=6.0 * math.sin(p * math.pi),
                opacity=min(1.0, veil * 0.94 * k),
            )
    finally:
        painter.end()
    return out


def light_crystal_bokeh_rain(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """水晶散景光雨：使用配套纹理 bokeh_crystal_overlay.png 呈现双层视差水晶六角光斑飘落过渡。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        painter.setOpacity(p)
        _draw(painter, b, 0, 0, w, h)
        painter.setOpacity(1.0)
        if veil > 0.01:
            _draw_asset_overlay(
                painter, "bokeh_crystal_overlay.png", w, h,
                tx=w * 0.12 * (p - 0.5), ty=h * 0.18 * (p - 0.5),
                sx=1.22, opacity=min(1.0, veil * 0.90 * k),
            )
            _draw_asset_overlay(
                painter, "bokeh_crystal_overlay.png", w, h,
                tx=-w * 0.16 * (p - 0.5), ty=h * 0.25 * (0.5 - p),
                sx=1.45, rot=15.0, opacity=min(1.0, veil * 0.65 * k),
            )
    finally:
        painter.end()
    return out


def light_ink_landscape_bleed(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """国风水墨山河晕染：结合配套资产 ink_wash_mask.png 的东方宣纸水墨浓淡笔触晕染转场。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        diag = math.hypot(w, h)
        path = QPainterPath()
        path.setFillRule(Qt.FillRule.WindingFill)
        for bx, by, r_mul in ((0.5, 0.5, 0.78), (0.22, 0.38, 0.58), (0.78, 0.62, 0.60), (0.35, 0.75, 0.52), (0.70, 0.28, 0.52)):
            r = diag * r_mul * (p ** 1.02)
            path.addEllipse(QPointF(w * bx, h * by), r * 1.15, r * 0.85)
        painter.save()
        painter.setClipPath(path)
        _draw(painter, b, 0, 0, w, h)
        painter.restore()
        if p > 0.80:
            painter.setOpacity((p - 0.80) / 0.20)
            _draw(painter, b, 0, 0, w, h)
            painter.setOpacity(1.0)
        if veil > 0.01:
            _draw_asset_overlay(
                painter, "ink_wash_mask.png", w, h,
                sx=1.05 + 0.45 * p,
                opacity=min(0.72, veil * 0.58 * k),
            )
    finally:
        painter.end()
    return out


def light_cyber_hud_scan(t, a, b, w, h, direction="down", intensity=1.0, *, easing="ease_in_out", **kw):
    """全息科技扫描：结合配套资产 cyber_hud_grid.png 全息网格准星与激光扫描线逐行重构画面。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        scan_y = h * p
        painter.save()
        painter.setClipRect(QRectF(0, 0, w, scan_y))
        _draw(painter, b, 0, 0, w, h)
        painter.restore()
        if veil > 0.01:
            _draw_asset_overlay(
                painter, "cyber_hud_grid.png", w, h,
                sx=1.0 + 0.08 * veil,
                opacity=min(0.92, veil * 0.82 * k),
            )
            bar_h = max(3.0, h * 0.035 * veil)
            painter.fillRect(QRectF(0, scan_y - bar_h * 0.5, w, bar_h), QColor(0, 250, 255, int(235 * veil)))
    finally:
        painter.end()
    return out


def light_aurora_silk_veil(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """极光丝绸流转：北欧极光翠绿与紫罗兰丝绸光带斜向流转柔化过渡。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    from PySide6.QtGui import QLinearGradient
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    try:
        painter.setOpacity(p)
        _draw(painter, b, 0, 0, w, h)
        painter.setOpacity(1.0)
        if veil > 0.01:
            g = QLinearGradient(w * (0.1 + 0.6 * p), 0, w * (0.9 - 0.6 * p), h)
            al = min(220, int(175 * veil * k))
            g.setColorAt(0.0, QColor(20, 255, 180, 0))
            g.setColorAt(0.32, QColor(40, 245, 190, al))
            g.setColorAt(0.65, QColor(155, 85, 255, al))
            g.setColorAt(1.0, QColor(255, 65, 195, 0))
            painter.fillRect(QRectF(0, 0, w, h), g)
    finally:
        painter.end()
    return out


def light_starlight_twinkle(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """星芒璀璨闪耀：多颗八角十字衍射星芒闪烁绽放完成唯美换景。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    from PySide6.QtGui import QPen
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    try:
        painter.setOpacity(p)
        _draw(painter, b, 0, 0, w, h)
        painter.setOpacity(1.0)
        if veil > 0.02:
            stars = ((0.28, 0.32, 1.0), (0.72, 0.36, 0.85), (0.50, 0.52, 1.35), (0.35, 0.72, 0.75), (0.68, 0.68, 0.95))
            for idx, (sx_f, sy_f, sz) in enumerate(stars):
                tw = math.sin(max(0.0, min(1.0, (p - idx * 0.06) / 0.75)) * math.pi)
                if tw <= 0.02:
                    continue
                cx, cy = w * sx_f, h * sy_f
                arm = min(w, h) * 0.22 * sz * tw * k
                painter.setPen(QPen(QColor(255, 250, 225, int(235 * tw * veil)), max(1.8, min(w, h) * 0.014)))
                painter.drawLine(QPointF(cx - arm, cy), QPointF(cx + arm, cy))
                painter.drawLine(QPointF(cx, cy - arm), QPointF(cx, cy + arm))
                d_arm = arm * 0.55
                painter.drawLine(QPointF(cx - d_arm, cy - d_arm), QPointF(cx + d_arm, cy + d_arm))
                painter.drawLine(QPointF(cx - d_arm, cy + d_arm), QPointF(cx + d_arm, cy - d_arm))
    finally:
        painter.end()
    return out


def light_smoke_mist_reveal(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """云雾缭绕穿云：镜头推进穿越多层流动云海迷雾切换到新画面。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    from PySide6.QtGui import QRadialGradient
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        _draw_xform(painter, a, w, h, sx=1.0 + 0.45 * p, opacity=1.0)
        _draw_xform(painter, b, w, h, sx=1.35 - 0.35 * p, opacity=p)
        if veil > 0.01:
            rg = QRadialGradient(w * (0.5 + 0.15 * math.sin(p * math.pi * 2.0)), h * 0.5, max(w, h) * 0.72)
            rg.setColorAt(0.0, QColor(242, 246, 255, min(245, int(215 * veil * k))))
            rg.setColorAt(0.6, QColor(215, 228, 245, min(220, int(155 * veil * k))))
            rg.setColorAt(1.0, QColor(200, 215, 235, 0))
            painter.fillRect(QRectF(0, 0, w, h), rg)
    finally:
        painter.end()
    return out


def light_neon_tunnel_rush(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """霓虹光廊穿梭：多层同心发光霓虹矩形光门迎面穿梭掠过。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    from PySide6.QtGui import QPen
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    try:
        _draw_xform(painter, a, w, h, sx=1.0 + 0.65 * p, opacity=1.0)
        _draw_xform(painter, b, w, h, sx=1.0 + 0.45 * (1.0 - p), opacity=p)
        if veil > 0.02:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for i in range(4):
                gate_p = ((p * 1.6 + i * 0.25) % 1.0)
                rw, rh = w * gate_p, h * gate_p
                rx, ry = (w - rw) * 0.5, (h - rh) * 0.5
                col = QColor(0, 245, 255, int(210 * veil * (1.0 - gate_p))) if i % 2 == 0 else QColor(255, 45, 210, int(210 * veil * (1.0 - gate_p)))
                painter.setPen(QPen(col, max(2.0, min(w, h) * 0.02 * gate_p)))
                painter.drawRect(QRectF(rx, ry, rw, rh))
    finally:
        painter.end()
    return out


def light_polaroid_flash_develop(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw):
    """拍立得闪光显影：氙气闪光灯瞬间爆闪后，新画面从暖米色化学相纸中缓缓显影浮现。"""
    tf, a, b, w, h, p, veil, k, hit = _norm(t, a, b, w, h, intensity, easing)
    if hit is not None:
        return hit
    QColor, QImage, QPainter, QPainterPath, QTransform, QRectF, QPointF, Qt = _q()
    out = _copy_fit(a, w, h)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    try:
        if p >= 0.22:
            dev_p = _smoothstep((p - 0.22) / 0.78)
            _draw_xform(painter, b, w, h, sx=1.08 - 0.08 * dev_p, opacity=min(1.0, dev_p * 1.25))
        flash_env = math.exp(-((p - 0.22) / 0.14) ** 2) * veil
        chem_env = max(0.0, (1.0 - p) * _smoothstep((p - 0.15) / 0.35))
        if flash_env > 0.01:
            painter.fillRect(0, 0, w, h, QColor(255, 253, 246, min(250, int(240 * flash_env * k))))
        if chem_env > 0.01:
            painter.fillRect(0, 0, w, h, QColor(245, 225, 185, min(180, int(135 * chem_env * k))))
    finally:
        painter.end()
    return out


# ═══════════════════════════════════════════════════════════════════════════
# 导出注册表（90 款自媒体转场 = 原 40 款 + 新增 50 款）
# ═══════════════════════════════════════════════════════════════════════════

SOCIAL_TRANSITION_LIBRARY: list[tuple[str, str]] = [
    # A. 运镜类 (原 12 款)
    ("急推冲镜", "cam_crash_push"),
    ("急拉远景", "cam_pull_back"),
    ("右甩镜", "cam_whip_right"),
    ("下甩镜", "cam_whip_down"),
    ("对角甩镜", "cam_whip_diag"),
    ("顺时针旋转", "cam_roll_cw"),
    ("逆时针旋转", "cam_roll_ccw"),
    ("螺旋推进", "cam_zoom_spin"),
    ("希区柯克变焦", "cam_dolly_vertigo"),
    ("钟摆摇镜", "cam_pendulum"),
    ("3D透视俯仰", "cam_perspective_tilt"),
    ("摇臂翻转", "cam_flip_jib"),
    # E. 多段灵动运镜类 (新增 16 款)
    ("推近缓移退回缓动", "cam_push_drift_pull_drift"),
    ("拉远悬浮推进落定", "cam_pull_float_push_settle"),
    ("推进慢摇退回", "cam_push_pan_right_pull"),
    ("推进左移回落", "cam_push_pan_left_pull"),
    ("推近仰漂回落", "cam_push_tilt_up_settle"),
    ("旋推缓漂回旋", "cam_spiral_glide_rebound"),
    ("S型丝滑运镜", "cam_s_curve_glide"),
    ("环绕悬浮运镜", "cam_orbit_float_3d"),
    ("手持呼吸推拉", "cam_handheld_breathe_push"),
    ("急甩急刹慢漂", "cam_whip_brake_drift"),
    ("俯冲回弹慢升", "cam_drop_bounce_float"),
    ("三段阶梯推拉", "cam_zoom_step_triple"),
    ("视差推摇慢移", "cam_dolly_pan_parallax"),
    ("摇臂俯掠缓停", "cam_crane_swoop_settle"),
    ("双摆微醺摇镜", "cam_pendulum_double_sway"),
    ("弹弓蓄力冲镜", "cam_elastic_slingshot"),
    # B. 节奏卡点类 (原 10 款)
    ("重低音震屏", "beat_bass_pump"),
    ("RGB分离震动", "beat_RGB_split_shake"),
    ("白闪急推", "beat_flash_zoom"),
    ("频闪快切", "beat_strobe_cut"),
    ("故障切片", "beat_glitch_slice"),
    ("弹性回弹", "beat_elastic_bounce"),
    ("两段卡点推镜", "beat_zoom_bounce"),
    ("快门定格切", "beat_shutter_frame"),
    ("三联分屏律动", "beat_split_3x"),
    ("九宫格弹出", "beat_grid_pop"),
    # F. 卡点变速与弹性律动类 (新增 12 款)
    ("曲线变速卡点", "beat_speed_ramp_flow"),
    ("双跳心跳推拉", "beat_heartbeat_double_zoom"),
    ("震屏闪白连击", "beat_shake_flash_combo"),
    ("幻彩波浪脉冲", "beat_rgb_wave_pulse"),
    ("定格抠像弹卡", "beat_freeze_pop_card"),
    ("果冻挤压回弹", "beat_jelly_squash_stretch"),
    ("搓盘倒带卡点", "beat_vinyl_scratch_rewind"),
    ("霓虹框律动弹", "beat_neon_border_bounce"),
    ("漫威速度线冲击", "beat_comic_speed_impact"),
    ("像素错位滑切", "beat_glitch_datamosh_slide"),
    ("径向残影甩切", "beat_zoom_blur_whip"),
    ("鼓点四连切", "beat_drum_roll_slice"),
    # C. 图形与创意遮罩类 (原 10 款)
    ("爱心开合", "shape_heart_iris"),
    ("五角星绽放", "shape_star_burst"),
    ("斜切刀光", "shape_diagonal_slash"),
    ("动态斜百叶", "shape_shutter_bands"),
    ("同心圆波纹", "shape_circle_wave"),
    ("四象限滑入", "shape_split_slide_4"),
    ("双翼扇形", "shape_clock_dual"),
    ("拍立得堆叠", "shape_polaroid_stack"),
    ("万花筒折射", "shape_kaleidoscope"),
    ("折纸翻折", "shape_origami_fold"),
    # G. 分屏画廊与创意图形类 (新增 11 款)
    ("斜切双分屏交错", "shape_split_diagonal_twin"),
    ("五联手风琴分屏", "shape_split_accordion_5"),
    ("时尚杂志拼贴画", "shape_magazine_collage"),
    ("电影胶片长廊", "shape_filmstrip_scroll"),
    ("手撕纸张拼贴", "shape_paper_tear_reveal"),
    ("碎裂水晶折射", "shape_shatter_glass_prism"),
    ("四块拼图咬合", "shape_puzzle_interlock"),
    ("流体波浪吞没", "shape_liquid_morph_wave"),
    ("六边形时空门", "shape_portal_tunnel_hex"),
    ("3D卡片旋转木马", "shape_card_carousel_3d"),
    ("画中画缓移展开", "shape_pip_expand_drift"),
    # D. 光效与胶片氛围类 (原 8 款)
    ("暖金逆光炫光", "light_warm_flare"),
    ("赛博青紫霓虹", "light_cyber_neon"),
    ("棱镜彩虹色散", "light_prism_rainbow"),
    ("复古胶片齿孔", "light_film_sprocket"),
    ("VHS磁带倒带", "light_vhs_rewind"),
    ("柔焦梦境弥散", "light_dream_glow"),
    ("流金光斑", "light_gold_dust"),
    ("东方水墨晕染", "light_ink_wash"),
    # H. 电影光效与水墨氛围类 (新增 11 款)
    ("变形宽银幕蓝光", "light_anamorphic_streak"),
    ("落日流金漏光", "light_golden_hour_leak"),
    ("胶片灼烧红晕", "light_halation_film_burn"),
    ("水晶散景光雨", "light_crystal_bokeh_rain"),
    ("国风水墨山河晕染", "light_ink_landscape_bleed"),
    ("全息科技扫描", "light_cyber_hud_scan"),
    ("极光丝绸流转", "light_aurora_silk_veil"),
    ("星芒璀璨闪耀", "light_starlight_twinkle"),
    ("云雾缭绕穿云", "light_smoke_mist_reveal"),
    ("霓虹光廊穿梭", "light_neon_tunnel_rush"),
    ("拍立得闪光显影", "light_polaroid_flash_develop"),
]

SOCIAL_TRANSITION_FUNCS: dict = {
    "cam_crash_push": cam_crash_push,
    "cam_pull_back": cam_pull_back,
    "cam_whip_right": cam_whip_right,
    "cam_whip_down": cam_whip_down,
    "cam_whip_diag": cam_whip_diag,
    "cam_roll_cw": cam_roll_cw,
    "cam_roll_ccw": cam_roll_ccw,
    "cam_zoom_spin": cam_zoom_spin,
    "cam_dolly_vertigo": cam_dolly_vertigo,
    "cam_pendulum": cam_pendulum,
    "cam_perspective_tilt": cam_perspective_tilt,
    "cam_flip_jib": cam_flip_jib,
    "cam_push_drift_pull_drift": cam_push_drift_pull_drift,
    "cam_pull_float_push_settle": cam_pull_float_push_settle,
    "cam_push_pan_right_pull": cam_push_pan_right_pull,
    "cam_push_pan_left_pull": cam_push_pan_left_pull,
    "cam_push_tilt_up_settle": cam_push_tilt_up_settle,
    "cam_spiral_glide_rebound": cam_spiral_glide_rebound,
    "cam_s_curve_glide": cam_s_curve_glide,
    "cam_orbit_float_3d": cam_orbit_float_3d,
    "cam_handheld_breathe_push": cam_handheld_breathe_push,
    "cam_whip_brake_drift": cam_whip_brake_drift,
    "cam_drop_bounce_float": cam_drop_bounce_float,
    "cam_zoom_step_triple": cam_zoom_step_triple,
    "cam_dolly_pan_parallax": cam_dolly_pan_parallax,
    "cam_crane_swoop_settle": cam_crane_swoop_settle,
    "cam_pendulum_double_sway": cam_pendulum_double_sway,
    "cam_elastic_slingshot": cam_elastic_slingshot,
    "beat_bass_pump": beat_bass_pump,
    "beat_RGB_split_shake": beat_RGB_split_shake,
    "beat_flash_zoom": beat_flash_zoom,
    "beat_strobe_cut": beat_strobe_cut,
    "beat_glitch_slice": beat_glitch_slice,
    "beat_elastic_bounce": beat_elastic_bounce,
    "beat_zoom_bounce": beat_zoom_bounce,
    "beat_shutter_frame": beat_shutter_frame,
    "beat_split_3x": beat_split_3x,
    "beat_grid_pop": beat_grid_pop,
    "beat_speed_ramp_flow": beat_speed_ramp_flow,
    "beat_heartbeat_double_zoom": beat_heartbeat_double_zoom,
    "beat_shake_flash_combo": beat_shake_flash_combo,
    "beat_rgb_wave_pulse": beat_rgb_wave_pulse,
    "beat_freeze_pop_card": beat_freeze_pop_card,
    "beat_jelly_squash_stretch": beat_jelly_squash_stretch,
    "beat_vinyl_scratch_rewind": beat_vinyl_scratch_rewind,
    "beat_neon_border_bounce": beat_neon_border_bounce,
    "beat_comic_speed_impact": beat_comic_speed_impact,
    "beat_glitch_datamosh_slide": beat_glitch_datamosh_slide,
    "beat_zoom_blur_whip": beat_zoom_blur_whip,
    "beat_drum_roll_slice": beat_drum_roll_slice,
    "shape_heart_iris": shape_heart_iris,
    "shape_star_burst": shape_star_burst,
    "shape_diagonal_slash": shape_diagonal_slash,
    "shape_shutter_bands": shape_shutter_bands,
    "shape_circle_wave": shape_circle_wave,
    "shape_split_slide_4": shape_split_slide_4,
    "shape_clock_dual": shape_clock_dual,
    "shape_polaroid_stack": shape_polaroid_stack,
    "shape_kaleidoscope": shape_kaleidoscope,
    "shape_origami_fold": shape_origami_fold,
    "shape_split_diagonal_twin": shape_split_diagonal_twin,
    "shape_split_accordion_5": shape_split_accordion_5,
    "shape_magazine_collage": shape_magazine_collage,
    "shape_filmstrip_scroll": shape_filmstrip_scroll,
    "shape_paper_tear_reveal": shape_paper_tear_reveal,
    "shape_shatter_glass_prism": shape_shatter_glass_prism,
    "shape_puzzle_interlock": shape_puzzle_interlock,
    "shape_liquid_morph_wave": shape_liquid_morph_wave,
    "shape_portal_tunnel_hex": shape_portal_tunnel_hex,
    "shape_card_carousel_3d": shape_card_carousel_3d,
    "shape_pip_expand_drift": shape_pip_expand_drift,
    "light_warm_flare": light_warm_flare,
    "light_cyber_neon": light_cyber_neon,
    "light_prism_rainbow": light_prism_rainbow,
    "light_film_sprocket": light_film_sprocket,
    "light_vhs_rewind": light_vhs_rewind,
    "light_dream_glow": light_dream_glow,
    "light_gold_dust": light_gold_dust,
    "light_ink_wash": light_ink_wash,
    "light_anamorphic_streak": light_anamorphic_streak,
    "light_golden_hour_leak": light_golden_hour_leak,
    "light_halation_film_burn": light_halation_film_burn,
    "light_crystal_bokeh_rain": light_crystal_bokeh_rain,
    "light_ink_landscape_bleed": light_ink_landscape_bleed,
    "light_cyber_hud_scan": light_cyber_hud_scan,
    "light_aurora_silk_veil": light_aurora_silk_veil,
    "light_starlight_twinkle": light_starlight_twinkle,
    "light_smoke_mist_reveal": light_smoke_mist_reveal,
    "light_neon_tunnel_rush": light_neon_tunnel_rush,
    "light_polaroid_flash_develop": light_polaroid_flash_develop,
}

# 用于图层叠加时防止全局发光/暗角染亮透明底的列表
SOCIAL_OVERLAY_LIGHT_PRESETS = frozenset({
    "cam_crash_push",
    "cam_perspective_tilt",
    "cam_handheld_breathe_push",
    "beat_flash_zoom",
    "beat_strobe_cut",
    "beat_shutter_frame",
    "beat_heartbeat_double_zoom",
    "beat_shake_flash_combo",
    "beat_freeze_pop_card",
    "beat_vinyl_scratch_rewind",
    "beat_neon_border_bounce",
    "beat_comic_speed_impact",
    "beat_drum_roll_slice",
    "shape_polaroid_stack",
    "shape_split_diagonal_twin",
    "shape_magazine_collage",
    "shape_filmstrip_scroll",
    "shape_paper_tear_reveal",
    "shape_shatter_glass_prism",
    "shape_puzzle_interlock",
    "shape_liquid_morph_wave",
    "shape_portal_tunnel_hex",
    "shape_card_carousel_3d",
    "shape_pip_expand_drift",
    "light_warm_flare",
    "light_cyber_neon",
    "light_prism_rainbow",
    "light_film_sprocket",
    "light_vhs_rewind",
    "light_dream_glow",
    "light_gold_dust",
    "light_ink_wash",
    "light_anamorphic_streak",
    "light_golden_hour_leak",
    "light_halation_film_burn",
    "light_crystal_bokeh_rain",
    "light_ink_landscape_bleed",
    "light_cyber_hud_scan",
    "light_aurora_silk_veil",
    "light_starlight_twinkle",
    "light_smoke_mist_reveal",
    "light_neon_tunnel_rush",
    "light_polaroid_flash_develop",
})

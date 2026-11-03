# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""短视频转场引擎（40 款）：运镜 / 节奏卡点 / 图形遮罩 / 光效胶片。

所有转场函数遵循统一契约：
* 签名：``fn(t, a, b, w, h, direction="left", intensity=1.0, *, easing="ease_in_out", **kw) -> QImage``
  同时兼容 ``transitions.apply_transition`` 的 ``fn(a, b, t, w, h, **params)`` 调用顺序。
* 端点连续：``t <= 0.0`` 返回 ``a``，``t >= 1.0`` 返回 ``b``；``t=0.02`` 与 ``t=0.98`` 无跳变。
* 全程不透明（ARGB32_Premultiplied，alpha 恒为 255），小缩略图（128×72）与 1080p 均高速稳定。
"""
from __future__ import annotations

import math

from cutora.render.transitions import _canvas, _copy_fit, _dir_vec, _draw, _ease, _q


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
# 导出注册表
# ═══════════════════════════════════════════════════════════════════════════

SOCIAL_TRANSITION_LIBRARY: list[tuple[str, str]] = [
    # A. 运镜类 (12)
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
    # B. 节奏卡点类 (10)
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
    # C. 图形与创意遮罩类 (10)
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
    # D. 光效与胶片氛围类 (8)
    ("暖金逆光炫光", "light_warm_flare"),
    ("赛博青紫霓虹", "light_cyber_neon"),
    ("棱镜彩虹色散", "light_prism_rainbow"),
    ("复古胶片齿孔", "light_film_sprocket"),
    ("VHS磁带倒带", "light_vhs_rewind"),
    ("柔焦梦境弥散", "light_dream_glow"),
    ("流金光斑", "light_gold_dust"),
    ("东方水墨晕染", "light_ink_wash"),
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
    "light_warm_flare": light_warm_flare,
    "light_cyber_neon": light_cyber_neon,
    "light_prism_rainbow": light_prism_rainbow,
    "light_film_sprocket": light_film_sprocket,
    "light_vhs_rewind": light_vhs_rewind,
    "light_dream_glow": light_dream_glow,
    "light_gold_dust": light_gold_dust,
    "light_ink_wash": light_ink_wash,
}

# 用于图层叠加时防止全局发光/暗角染亮透明底的列表
SOCIAL_OVERLAY_LIGHT_PRESETS = frozenset({
    "cam_crash_push",
    "cam_perspective_tilt",
    "beat_flash_zoom",
    "beat_strobe_cut",
    "beat_shutter_frame",
    "shape_polaroid_stack",
    "light_warm_flare",
    "light_cyber_neon",
    "light_prism_rainbow",
    "light_film_sprocket",
    "light_vhs_rewind",
    "light_dream_glow",
    "light_gold_dust",
})

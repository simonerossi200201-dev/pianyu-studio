# SPDX-License-Identifier: GPL-3.0-or-later
# Extracted from the T01 monolith by tools/split_monolith.py.
# Original lines: 3857-6279

import os
import copy
import json
import math
import time
from collections import OrderedDict
from PySide6.QtWidgets import (
    QWidget,
    QToolTip,
    QMenu,
    QSizePolicy,
)
from PySide6.QtCore import (
    Qt,
    Signal,
    QRectF,
    QPoint,
    QSize,
    QRect,
    QPointF,
)
from PySide6.QtGui import (
    QColor,
    QPainter,
    QPen,
    QBrush,
    QFont,
    QFontMetrics,
    QPainterPath,
    QCursor,
    QLinearGradient,
    QPolygonF,
    QImage,
    QPixmap,
)
from cutora.ui.panels.fonts import (
    DEFAULT_FONT_FAMILY,
    get_font_manager,
)
from PySide6.QtWidgets import QMenu
from PySide6.QtCore import Qt
from cutora.ui.panels.fonts import DEFAULT_FONT_FAMILY, get_font_manager
from cutora.core.counters import bump as _bump
from cutora.core.log import get_logger
from cutora.subtitle.edit import apply_move, apply_retime, apply_trim
from cutora.ui.timeline.selection import TimelineSelection
from cutora.ui.timeline import snap as snap_mod
from cutora.model.timeline_model import (
    TRACK_HEIGHT_COMPACT,
    TRACK_HEIGHT_CYCLE,
    TRACK_HEIGHT_HIGH,
    TRACK_HEIGHT_STANDARD,
    clamp_track_height,
)

_log = get_logger("cutora.ui.timeline")
_paint_err_counts: dict[tuple[str, str], int] = {}

# Inner glow for clip chrome. 9-slice tile keyed by (h, color, alpha, dpr)
# so hundreds of different-width subtitle clips share one pixmap.
# Q169 起片段是实色色块：轨道色自己就是身份，内发光只剩一圈很淡的同色浅边（像斜面），
# 预合成组 / 字幕角色 / 超速提示那种「另有含义」的颜色才用老的亮度。
CLIP_GLOW_PX = 5
CLIP_GLOW_ALPHA = 110
CLIP_GLOW_ALPHA_HOVER = 150
CLIP_GLOW_ALPHA_SEL = 200
CLIP_RIM_ALPHA = 58
CLIP_RIM_ALPHA_HOVER = 80
CLIP_RIM_ALPHA_SEL = 104
# 实色块的几档亮度（相对轨道色）
CLIP_LIFT_HOVER = 0.08       # 悬停：整体提亮
CLIP_LIFT_SEL = 0.16         # 选中：再亮一档 + 主题色描边
CLIP_TOP_LIGHT = 0.07        # 顶部到底部的轻微渐变（有体积感，不抢眼）
CLIP_BOTTOM_DARK = 0.05
CLIP_EDGE_DARK = 0.38        # 1px 深色描边，把色块从轨道底上切出来
CLIP_THUMB_W = 64            # 视频条头尾缩略图的最大宽度
_GLOW_CACHE: OrderedDict = OrderedDict()
_GLOW_BUILD_COUNT = 0
_GLOW_CACHE_CAP = 64


def reset_clip_glow_cache():
    """Test hook: drop cached glow tiles and the build counter."""
    global _GLOW_BUILD_COUNT
    _GLOW_CACHE.clear()
    _GLOW_BUILD_COUNT = 0


def clip_glow_build_count():
    return _GLOW_BUILD_COUNT


def _glow_smoothstep(t: float) -> float:
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    return t * t * (3.0 - 2.0 * t)


def _glow_tile_dist(x, y, w, h, depth, radius):
    """Inward distance from the rounded-rect rim. Outside the shape → inf."""
    r = max(0.0, float(radius))
    inf = depth + 1.0

    def _corner(cx, cy):
        d = r - math.hypot(x - cx, y - cy)
        return inf if d < 0.0 else d

    dists = []
    if x < depth:
        if y < r:
            dists.append(_corner(r, r))
        elif y > h - r:
            dists.append(_corner(r, h - r))
        else:
            dists.append(x)
    if x > w - depth:
        if y < r:
            dists.append(_corner(w - r, r))
        elif y > h - r:
            dists.append(_corner(w - r, h - r))
        else:
            dists.append(w - x)
    if y < depth and r <= x <= w - r:
        dists.append(y)
    if y > h - depth and r <= x <= w - r:
        dists.append(h - y)
    if not dists:
        return inf
    return min(dists)


def _file_label(path) -> str:
    """片段条上的文件名：两种斜杠都切（工程里存的是 Windows 路径，在别的系统上 basename 切不开）。"""
    p = str(path or "")
    return p.replace("\\", "/").rsplit("/", 1)[-1]


def metal_gradient(rect, base_hex: str):
    """Q174 调整轨的金属质感：竖向渐变，顶 18% 处一道柔和高光、正中一条细暗缝（拉丝金属的「地平线」）、
    底部压暗成阴影。亮度整体落在中灰，放在深色界面里不扎眼。"""
    from cutora.ui.theme.tokens import darken_hex, lighten_hex
    g = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    g.setColorAt(0.00, QColor(lighten_hex(base_hex, 0.10)))
    g.setColorAt(0.18, QColor(lighten_hex(base_hex, 0.26)))
    g.setColorAt(0.42, QColor(base_hex))
    g.setColorAt(0.50, QColor(darken_hex(base_hex, 0.10)))
    g.setColorAt(0.56, QColor(darken_hex(base_hex, 0.02)))
    g.setColorAt(1.00, QColor(darken_hex(base_hex, 0.30)))
    return g


def _clip_glow_tile(h, color: QColor, alpha: int, radius: float, dpr: float = 1.0):
    """Width-independent inner-glow 9-slice. Cached LRU. HiDPI via dpr."""
    global _GLOW_BUILD_COUNT
    h = max(1, int(h))
    radius = float(radius)
    alpha = int(max(0, min(255, alpha)))
    dpr = float(dpr) if dpr and dpr > 0 else 1.0
    dpr = max(1.0, min(8.0, dpr))
    key = (h, int(color.rgba()), alpha, round(dpr, 2), round(radius, 2))
    hit = _GLOW_CACHE.get(key)
    if hit is not None:
        _GLOW_CACHE.move_to_end(key)
        return hit
    _GLOW_BUILD_COUNT += 1

    slice_px = float(CLIP_GLOW_PX)
    log_w = 2 * CLIP_GLOW_PX + 1
    log_h = float(h)
    phys_w = max(1, int(round(log_w * dpr)))
    phys_h = max(1, int(round(log_h * dpr)))
    img = QImage(phys_w, phys_h, QImage.Format.Format_ARGB32)
    img.fill(0)

    cr, cg, cb = int(color.red()), int(color.green()), int(color.blue())
    inv = 1.0 / dpr
    depth = slice_px
    for py in range(phys_h):
        ly = (py + 0.5) * inv
        for px in range(phys_w):
            lx = (px + 0.5) * inv
            dist = _glow_tile_dist(lx, ly, log_w, log_h, depth, radius)
            a = int(round(alpha * (1.0 - _glow_smoothstep(dist / depth))))
            if a <= 0:
                continue
            img.setPixel(px, py, (a << 24) | (cr << 16) | (cg << 8) | cb)

    img.setDevicePixelRatio(dpr)
    pix = QPixmap.fromImage(img)
    pix.setDevicePixelRatio(dpr)
    while len(_GLOW_CACHE) >= _GLOW_CACHE_CAP:
        _GLOW_CACHE.popitem(last=False)
    _GLOW_CACHE[key] = pix
    return pix


def _clip_glow_pixmap(w, h, color: QColor, alpha: int, radius: float, dpr: float = 1.0):
    """Back-compat: width is ignored; glow is a 9-slice tile."""
    return _clip_glow_tile(h, color, alpha, radius, dpr=dpr)


def _draw_clip_glow(painter, rect, pix):
    """Stretch a 9-slice glow tile across ``rect``. Independent of clip width."""
    slice_px = float(CLIP_GLOW_PX)
    dpr = float(pix.devicePixelRatio() or 1.0)
    src_w = float(pix.width()) / dpr
    src_h = float(pix.height()) / dpr
    if src_w <= 0 or src_h <= 0:
        return
    x = float(rect.x())
    y = float(rect.y())
    w = float(rect.width())
    h = float(rect.height())
    if w < 1 or h < 1:
        return
    if w <= 2.0 * slice_px + 1.0:
        painter.drawPixmap(QRectF(x, y, w, h).toRect(), pix)
        return
    # Source rects are pixmap pixels (physical). Stretch with drawPixmap
    # (PySide6's drawPixmapFragments wants a C array, not a Python list).
    ps = slice_px * dpr
    pw = float(pix.width())
    ph = float(pix.height())
    mid_w = w - 2.0 * slice_px
    painter.drawPixmap(QRectF(x, y, slice_px, h), pix, QRectF(0.0, 0.0, ps, ph))
    painter.drawPixmap(
        QRectF(x + w - slice_px, y, slice_px, h), pix,
        QRectF(pw - ps, 0.0, ps, ph))
    if mid_w > 0:
        src_mid = max(1.0, pw - 2.0 * ps)
        painter.drawPixmap(
            QRectF(x + slice_px, y, mid_w, slice_px), pix,
            QRectF(ps, 0.0, src_mid, ps))
        painter.drawPixmap(
            QRectF(x + slice_px, y + h - slice_px, mid_w, slice_px), pix,
            QRectF(ps, ph - ps, src_mid, ps))

def _tl_of(*a, **k):
    from cutora.ui.main_window import _tl_of as _impl
    tl = _impl(*a, **k)
    if tl is not None and a and isinstance(a[0], dict):
        tl._task = a[0]
    return tl

def apply_intro_video_to_task(*a, **k):
    from cutora.ui.main_window import apply_intro_video_to_task as _impl
    return _impl(*a, **k)

def get_batch_tracks(*a, **k):
    from cutora.ui.main_window import get_batch_tracks as _impl
    return _impl(*a, **k)

def remove_intro_video_from_task(*a, **k):
    from cutora.ui.main_window import remove_intro_video_from_task as _impl
    return _impl(*a, **k)

def track_supports_batch(*a, **k):
    from cutora.ui.main_window import track_supports_batch as _impl
    return _impl(*a, **k)

from cutora.ui.timeline.ops import TimelineOpsMixin  # noqa: E402  Q145 第二期
from cutora.model import clip_groups as _CG  # noqa: E402  Q154 预合成


# --- Timeline (Fixed: 修复单词高亮跟随缩放，恢复单选 Handles) ---
from cutora.ui.theme.tokens import TRACK_ADJUST as _TRACK_ADJUST

_LABEL_FONT = {}


def timeline_label_font() -> QFont:
    """时间线上素材条 / 字幕条 / 标记的文字字体。

    Q189h：以前直接拿字幕用的 Arimo（``get_font`` 为了预览和导出一致，关掉了 Qt 的缺字回退），
    中文文件名全画成小方块。这里西文仍用 Arimo（观感不变），缺的字依次回退到包内 Noto Sans SC、
    系统微软雅黑。只影响时间线上的标注，不碰字幕渲染。
    """
    f = _LABEL_FONT.get("f")
    if f is None:
        from cutora.ui.theme.tokens import FONT_UI_CJK
        base = get_font_manager().get_font(DEFAULT_FONT_FAMILY, 8)
        f = QFont(base)
        f.setFamilies([base.family(), FONT_UI_CJK, "Microsoft YaHei UI", "Microsoft YaHei"])
        f.setStyleStrategy(QFont.StyleStrategy.PreferDefault)
        _LABEL_FONT["f"] = f
    return QFont(f)


class TimelineWidget(TimelineOpsMixin, QWidget):
    seek_request = Signal(int)
    tool_changed = Signal(str)       # Q145：V / C / R 切工具 → 工具栏按钮跟着变
    position_changed = Signal(int)
    item_selected = Signal(str, object) 
    segment_changed = Signal(list)
    text_edit_request = Signal(int)
    item_dragged = Signal()
    file_dropped_on_timeline = Signal(str, float, str)  # (path, time_sec, domain)
    # Q115：一次拖一批文件走这个（老信号留着兼容，主窗口只接这个，别两个都接）
    # payload: {"paths": [...], "time": float, "domain": str,
    #           "track": Track|None, "new_track": bool}
    files_dropped_on_timeline = Signal(object)
    import_to_track = Signal(object)  # Track 对象 — 轨道头导入按钮
    HANDLE_PX = 14
    FADE_HANDLE_PX = 8
    ALT_COPY_PX = 5
    SEAM_PX = 4
    MIN_DUR = 0.08
    RULER_H = 24
    TRACK_H = TRACK_HEIGHT_STANDARD
    WORD_HANDLE_PX = 6
    TRACK_GAP = 10
    TOP_PAD = 6
    BOTTOM_PAD = 6
    DOMAIN_SEP = 8  # px gap between visual / audio domains

    _TRACK_LABELS = {
        "video": "Video",
        "image": "Images",
        "text": "Text",
        "subs": "Subs",
        "audio": "Audio",
        "adjust": "Adjust",
    }

    @property
    def _TRACK_DISPLAY(self):
        from cutora.ui.theme.tokens import current, lighten_hex
        t = current()
        out = {}
        mapping = {
            "video": t.track_video,
            "image": t.track_image,
            "text": t.track_text,
            "subs": t.track_subtitle,
            "audio": t.track_audio,
            "adjust": getattr(t, "track_adjust", None) or _TRACK_ADJUST,
        }
        for key, hex_color in mapping.items():
            out[key] = {
                "label": self._TRACK_LABELS[key],
                "color": QColor(hex_color),
                "sel_color": QColor(lighten_hex(hex_color, 0.12)),
            }
        return out

    def _theme(self):
        from cutora.ui.theme.tokens import current
        return current()

    def _clip_base_hex(self, kind: str) -> str:
        """这一类片段的实色（Q169）：视频蓝 / 图片紫 / 文字赭黄 / 音频绿 / 字幕青绿。"""
        from cutora.ui.theme.tokens import current
        t = current()
        mapping = {
            "video": t.track_video,
            "audio": t.track_audio,
            "subtitle": t.track_subtitle,
            "subs": t.track_subtitle,
            "text": t.track_text,
            "image": t.track_image,
            "thumb": t.track_text,        # 缩略图静帧：跟文字一族（它就是封面字）
            "intro": t.track_video,
            "adjust": getattr(t, "track_adjust", None) or _TRACK_ADJUST,
        }
        return mapping.get(kind, t.track_video)

    def _clip_fill(self, kind: str, selected: bool, hovered: bool = False):
        """实色块：轨道色本身；悬停 / 选中各提亮一档。"""
        from cutora.ui.theme.tokens import lighten_hex
        base = self._clip_base_hex(kind)
        if selected:
            return QColor(lighten_hex(base, CLIP_LIFT_SEL))
        if hovered:
            return QColor(lighten_hex(base, CLIP_LIFT_HOVER))
        return QColor(base)

    def _clip_edge(self, kind: str, selected: bool):
        from cutora.ui.theme.tokens import current, darken_hex
        t = current()
        if selected:
            return QColor(t.accent_hi)
        return QColor(darken_hex(self._clip_base_hex(kind), CLIP_EDGE_DARK))

    def _clip_label_color(self) -> QColor:
        """片段上的字：fg0（暖白，不是纯白）—— 五种实色上对比度都 ≥ 4.5。"""
        return QColor(self._theme().fg0)

    def _clip_glow_color(self, kind: str):
        from cutora.ui.theme.tokens import current
        t = current()
        mapping = {
            "video": t.track_video,
            "audio": t.track_audio,
            "subtitle": t.track_subtitle,
            "text": t.track_text,
            "image": t.track_image,
            "thumb": t.track_text,
            "intro": t.track_audio,
            "adjust": getattr(t, "track_adjust", None) or _TRACK_ADJUST,
        }
        return QColor(mapping.get(kind, t.track_video))

    def _clip_is_hovered(self, clip_obj) -> bool:
        hover = getattr(self, "_hover_edge", None)
        return bool(hover and hover[0] == "clip" and hover[1] is clip_obj)

    def _paint_clip_chrome(self, painter, rect, kind, selected, hovered=False, glow_color=None):
        """Q169 实色色块：轨道色填充（顶亮底暗的轻渐变）+ 1px 深色描边 + 一圈很淡的同色浅边；
        选中 = 提亮 + 主题色 2px 描边。glow_color 给了（预合成组 / 字幕角色 / 超速）就按那个颜色发光。"""
        from cutora.ui.theme.tokens import RADIUS_SM, darken_hex, lighten_hex
        if rect.width() < 1 or rect.height() < 1:
            return
        radius = RADIUS_SM
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        fill = self._clip_fill(kind, selected, hovered)
        if kind == "adjust":
            # Q174：拉丝金属的银灰 —— 顶上一道反光、腰线一条暗缝、底下压暗，整体不刺眼
            painter.fillPath(path, QBrush(metal_gradient(rect, fill.name())))
        else:
            top = QColor(lighten_hex(fill.name(), CLIP_TOP_LIGHT))
            bottom = QColor(darken_hex(fill.name(), CLIP_BOTTOM_DARK))
            grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
            grad.setColorAt(0.0, top)
            grad.setColorAt(1.0, bottom)
            painter.fillPath(path, QBrush(grad))
        if glow_color is None:
            glow_color = QColor(lighten_hex(self._clip_base_hex(kind), 0.55))
            if selected:
                alpha = CLIP_RIM_ALPHA_SEL
            elif hovered:
                alpha = CLIP_RIM_ALPHA_HOVER
            else:
                alpha = CLIP_RIM_ALPHA
        elif selected:
            alpha = CLIP_GLOW_ALPHA_SEL
        elif hovered:
            alpha = CLIP_GLOW_ALPHA_HOVER
        else:
            alpha = CLIP_GLOW_ALPHA
        ih = max(1, int(round(float(rect.height()))))
        dpr = 1.0
        dev = painter.device()
        if dev is not None:
            getter = getattr(dev, "devicePixelRatioF", None) or getattr(
                dev, "devicePixelRatio", None)
            if callable(getter):
                try:
                    dpr = float(getter() or 1.0)
                except Exception:
                    dpr = 1.0
        pix = _clip_glow_tile(ih, QColor(glow_color), alpha, radius, dpr=dpr)
        painter.save()
        painter.setClipPath(path)
        _draw_clip_glow(painter, rect, pix)
        painter.restore()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(self._clip_edge(kind, selected), 2.0 if selected else 1.0))
        painter.drawPath(path)

    KF_BAND_H = 10
    KF_DIAMOND = 7

    MATTE_BAND_H = 3

    def _paint_matte_band(self, painter, clip, rect, start_t):
        """Q152-D：AI 抠像的覆盖条（片段条顶部）：绿 = 这段源帧有结果，黄 = 置信度低；没算的地方空着。
        点黄段 → 播放头跳到那段开头（_hit_matte_band）。"""
        try:
            from cutora.fx.ai_matte import coverage_runs
            runs = coverage_runs(clip)
        except Exception:
            return
        if not runs:
            return
        pps = float(self.pixels_per_second)
        y = rect.top() + 1.0
        band_h = float(self.MATTE_BAND_H)
        ok_col = QColor(102, 187, 106, 210)
        low_col = QColor(255, 193, 7, 230)
        painter.save()
        try:
            painter.setPen(Qt.PenStyle.NoPen)
            for t0, t1, low in runs:
                x0 = max(rect.left(), t0 * pps)
                x1 = min(rect.right(), t1 * pps)
                if x1 <= x0:
                    continue
                w = max(2.0, x1 - x0) if low else (x1 - x0)      # 单帧的黄段也要看得见
                painter.fillRect(QRectF(x0, y, w, band_h + (1.0 if low else 0.0)), low_col if low else ok_col)
        finally:
            painter.restore()

    TRACK_BAND_Y = 5        # 跟踪条在抠像条下面（片段顶部往下 5 px）

    def _paint_track_band(self, painter, clip, rect, start_t):
        """Q152-E：AI 跟踪覆盖条：蓝 = 全部点都看得见，红 = 有点被挡 / 跟丢（点红段跳过去）。"""
        if not getattr(clip, "tracking", None):
            return
        try:
            from cutora.fx.tracking import coverage_runs
            runs = coverage_runs(clip)
        except Exception:
            return
        if not runs:
            return
        pps = float(self.pixels_per_second)
        y = rect.top() + float(self.TRACK_BAND_Y)
        ok_col = QColor(79, 195, 247, 210)
        bad_col = QColor(229, 72, 72, 235)
        painter.save()
        try:
            painter.setPen(Qt.PenStyle.NoPen)
            for t0, t1, bad in runs:
                x0 = max(rect.left(), t0 * pps)
                x1 = min(rect.right(), t1 * pps)
                if x1 <= x0:
                    continue
                w = max(2.0, x1 - x0) if bad else (x1 - x0)
                painter.fillRect(QRectF(x0, y, w, 2.0 + (1.0 if bad else 0.0)), bad_col if bad else ok_col)
        finally:
            painter.restore()

    MATTE_HIT_PX = 7

    def _hit_matte_band(self, real_x, y):
        """点在某条视频片段顶部的黄段上 → 那段开头的时间线秒；否则 None。"""
        try:
            from cutora.fx.ai_matte import coverage_runs
        except Exception:
            return None
        tracks = self._track_positions()
        pps = float(self.pixels_per_second)
        for (t_obj, row_y, row_h) in tracks.get("rows", []):
            if getattr(t_obj, "type", "") != "video":
                continue
            if y < row_y or y > row_y + float(self.MATTE_HIT_PX) + 3.0:
                continue
            on_track_band = y >= row_y + float(self.TRACK_BAND_Y) - 1.0
            for clip_obj in getattr(t_obj, "clips", []) or []:
                runs = []
                if getattr(clip_obj, "tracking", None) and on_track_band:
                    from cutora.fx.tracking import coverage_runs as track_runs
                    runs = track_runs(clip_obj)          # Q152-E：红段（跟丢）
                elif (getattr(clip_obj, "ai_matte", None) or getattr(clip_obj, "fx_graph", None)) and (not on_track_band or not getattr(clip_obj, "tracking", None)):
                    runs = coverage_runs(clip_obj)
                for t0, t1, low in runs:
                    if not low:
                        continue
                    x0, x1 = t0 * pps, max(t1 * pps, t0 * pps + 4.0)
                    if x0 - 2.0 <= real_x <= x1 + 2.0:
                        return float(t0)
        return None

    def _paint_keyframe_band(self, painter, host, rect, start_t):
        """Diamonds on the bottom of a clip/overlay bar. No-op without keyframes."""
        from cutora.render.keyframe import all_times, has_keyframes, local_time
        if not has_keyframes(host):
            return
        band_h = float(self.KF_BAND_H)
        cy = rect.bottom() - band_h / 2.0
        playhead_s = float(self.position or 0) / 1000.0
        t_local = local_time(host, playhead_s)
        from cutora.ui.theme.tokens import current
        gold = QColor(current().accent)
        painter.save()
        try:
            band = QRectF(rect.left(), rect.bottom() - band_h, rect.width(), band_h)
            shade = QColor(gold)
            shade.setAlpha(40)
            painter.fillRect(band, shade)
            half = float(self.KF_DIAMOND) / 2.0
            for t in all_times(host):
                x = (float(start_t) + float(t)) * self.pixels_per_second
                if x < rect.left() - half or x > rect.right() + half:
                    continue
                dia = QPolygonF([
                    QPointF(x, cy - half),
                    QPointF(x + half, cy),
                    QPointF(x, cy + half),
                    QPointF(x - half, cy),
                ])
                on = abs(float(t) - t_local) <= (1.0 / 60.0)
                if on:
                    painter.setBrush(QBrush(gold))
                    painter.setPen(Qt.PenStyle.NoPen)
                else:
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.setPen(QPen(gold, 1.5))
                painter.drawPolygon(dia)
        finally:
            painter.restore()

    def _hit_keyframe(self, real_x, y):
        """Return (host, path, t, start_t, target_params) or None."""
        from cutora.render.keyframe import all_times, has_keyframes, keyframes_of
        tracks = self._track_positions()
        half = float(self.KF_DIAMOND) / 2.0 + 2.0
        for (t_obj, row_y, row_h) in tracks.get("rows", []):
            if y < row_y or y > row_y + row_h:
                continue
            t_type = getattr(t_obj, "type", "")
            tid = str(getattr(t_obj, "id", "") or "")
            if t_type in ("video", "audio"):
                for i, clip_obj in enumerate(getattr(t_obj, "clips", []) or []):
                    if not has_keyframes(clip_obj):
                        continue
                    start_t = float(getattr(clip_obj, "start_t", 0.0))
                    for t in all_times(clip_obj):
                        x = (start_t + float(t)) * self.pixels_per_second
                        cy = row_y + row_h - float(self.KF_BAND_H) / 2.0
                        if abs(real_x - x) <= half and abs(y - cy) <= half:
                            path = None
                            for p, pts in keyframes_of(clip_obj).items():
                                for rec in pts or []:
                                    if abs(float(rec.get("t") or 0.0) - float(t)) <= 1e-6:
                                        path = p
                                        break
                                if path:
                                    break
                            return {
                                "host": clip_obj,
                                "path": path or "x",
                                "t": float(t),
                                "target": "clip",
                                "track_id": tid,
                                "clip_index": i,
                            }
            elif t_type == "text":
                for i, ov in self._get_overlays_for_track(t_obj, "text_overlays"):
                    hit = self._hit_ov_keyframe(
                        ov, i, "text_overlays", real_x, y, row_y, row_h, half)
                    if hit:
                        return hit
            elif t_type in ("image", "adjust"):
                for i, ov in self._get_overlays_for_track(t_obj, "image_overlays"):
                    hit = self._hit_ov_keyframe(
                        ov, i, "image_overlays", real_x, y, row_y, row_h, half)
                    if hit:
                        return hit
        return None

    def _hit_ov_keyframe(self, ov, idx, list_name, real_x, y, row_y, row_h, half):
        from cutora.render.keyframe import all_times, has_keyframes, keyframes_of
        if not has_keyframes(ov):
            return None
        start_t = float(ov.get("start") or 0.0)
        cy = row_y + row_h - float(self.KF_BAND_H) / 2.0
        for t in all_times(ov):
            x = (start_t + float(t)) * self.pixels_per_second
            if abs(real_x - x) <= half and abs(y - cy) <= half:
                path = None
                for p, pts in keyframes_of(ov).items():
                    for rec in pts or []:
                        if abs(float(rec.get("t") or 0.0) - float(t)) <= 1e-6:
                            path = p
                            break
                    if path:
                        break
                return {
                    "host": ov,
                    "path": path or "opacity",
                    "t": float(t),
                    "target": "overlay",
                    "list": list_name,
                    "overlay_index": idx,
                }
        return None

    def _keyframe_context_menu(self, hit, global_pos):
        menu = QMenu(self)
        act_del = menu.addAction("删除关键帧")
        ease_menu = menu.addMenu("缓动")
        ease_acts = []
        for lab, name in (
            ("线性", "linear"),
            ("渐入", "ease_in"),
            ("渐出", "ease_out"),
            ("渐入渐出", "ease_in_out"),
            ("保持", "hold"),
            ("贝塞尔", "bezier"),
        ):
            a = ease_menu.addAction(lab)
            a.setData(name)
            ease_acts.append(a)
        act_copy = menu.addAction("复制")
        act_paste = menu.addAction("粘贴")
        chosen = menu.exec(global_pos)
        params = {
            "target": hit["target"],
            "path": hit["path"],
            "t": hit["t"],
        }
        if hit["target"] == "clip":
            params["track_id"] = hit["track_id"]
            params["clip_index"] = hit["clip_index"]
        else:
            params["list"] = hit["list"]
            params["overlay_index"] = hit["overlay_index"]
        if chosen == act_del:
            self._bus_execute("keyframe.delete", params)
        elif chosen in ease_acts:
            p = dict(params)
            p["ease"] = chosen.data()
            self._bus_execute("keyframe.set_interp", p)
        elif chosen == act_copy:
            self._bus_execute("keyframe.copy", params)
        elif chosen == act_paste:
            self._keyframe_paste_at_playhead(hit)
        self.update()

    def _kf_cmd_params(self, hit):
        params = {
            "target": hit["target"],
            "path": hit["path"],
        }
        if hit["target"] == "clip":
            params["track_id"] = hit["track_id"]
            params["clip_index"] = hit["clip_index"]
        else:
            params["list"] = hit["list"]
            params["overlay_index"] = hit["overlay_index"]
        return params

    def _keyframe_paste_at_playhead(self, hit):
        """Paste the copied key onto the playhead, not the diamond that was clicked."""
        from cutora.render.keyframe import local_time
        params = self._kf_cmd_params(hit)
        playhead_s = float(self.position or 0) / 1000.0
        params["t"] = local_time(hit["host"], playhead_s)
        self._bus_execute("keyframe.paste", params)

    def _host_start_t(self, host):
        if isinstance(host, dict):
            try:
                return float(host.get("start") or 0.0)
            except (TypeError, ValueError):
                return 0.0
        try:
            return float(getattr(host, "start_t", 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(80)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setObjectName("TimelineWidget")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAcceptDrops(True)  # Phase C: 接收拖拽
        self.duration = 10000 
        self.position = 0 
        self.task_data = None
        self.pixels_per_second = 20 
        self.scroll_x = 0
        self.scroll_y = 0
        
        self.mode = 'none' # none, move_single, move_multi, retime_left, retime_right, rubberband
        self.active_type = None 
        self._ops_init()
        
        # Selection
        self.selected_indices = set() # Stores indices of selected segments
        self.selection = TimelineSelection(self)
        self._solo_ids = set()
        self._pinned_ids = set()
        self._clip_sel = []  # extra multi-selected clips [(track_id, clip_index)]
        
        # Interaction State
        self.drag_start_x = 0
        self.drag_start_time = 0
        self.rubber_band_rect = QRect()
        
        # Retime State
        self.retime_pivot_index = -1
        self.original_segments_state = {} # Backup for retime calculation
        self._last_norm_stats = None
        self._active_resize_idx = None
        self._last_hover_sec = None
        self.selected_overlay = None  # (list_name, idx)
        self.selected_audio_idx = None
        self.selected_video_clip = None
        self.selected_transition = None  # (Track, Clip) 元组 — 选中的视频 clip
        self.selected_seam = None  # (Track, left_index, right_index, seam_t)
        self._hover_seam = None
        self.selected_track = None  # Track 对象 — 轨道本体选中
        self._clipboard_overlay = None
        self._clipboard_video_clip = None  # Clip 对象 — 复制的视频 clip
        self._clipboard_audio_clip = None  # Clip 对象 — 复制的音频 clip
        self._painting = False  # paintEvent 重入保护
        self._ov_drag_start_x = 0
        self._ov_drag_orig_start = 0
        self._ov_drag_orig_end = 0
        self._audio_drag_start_x = 0
        self._audio_drag_orig_start = 0
        self._video_drag_start_x = 0
        self._video_drag_orig_start = 0
        self._audio_drag_orig_end = 0
        self._drag_source_track = None
        self._drag_target_track = None

        # === 渲染起点裁剪手柄（Trim-Start Handle）===
        self.trim_start_sec = 0.0          # 渲染起点（0 = 不裁剪）
        self._dragging_trim = False         # 正在拖动手柄
        self._trim_drag_start_x = 0        # 拖动开始时的鼠标 X
        self._trim_drag_start_sec = 0.0    # 拖动开始时的 trim_start_sec

        # === 渲染终点裁剪手柄（Trim-End Handle）===
        self.trim_end_sec = 0.0            # 渲染终点（0 = 使用项目自然时长，即不裁剪）
        self._dragging_trim_end = False    # 正在拖动裁剪尾手柄
        self._trim_end_drag_start_x = 0
        self._trim_end_drag_start_sec = 0.0
        self.snap_enabled = True
        self._snap_hold_off = False
        self._alt_copy_pending = False
        self._alt_copy_did = False
        self._alt_copy_src = None
        self._av_link_index = {}
        self._av_trim_partners = None
        self._av_trim_side = None
        self._av_trim_primary_span = None
        self._av_trim_primary_audio = False
        self._fade_drag_clip = None
        self._fade_drag_orig = None
        self._fade_drag_sx = 0.0
        self._fade_drag_sw = 0.0
        self._fade_drag_dur = 0.0
        self.snap_kinds = {
            "clip": True, "playhead": True, "marker": True, "silence": True,
        }
        self.tool = "select"  # select | razor | speed
        self._hover_edge = None  # (kind, obj, side)
        self._snap_guide_t = None
        self._snap_edge_cache = None
        self._dragging_marker = None
        self._marker_drag_orig_t = None
        self._dragging_keyframe = None
        self._kf_drag_orig_t = None
        try:
            from cutora.core.history import _load_app_settings
            stored = (_load_app_settings() or {}).get("timeline_snap")
            if stored is not None:
                self.snap_enabled = bool(stored)
            kinds = (_load_app_settings() or {}).get("timeline_snap_kinds")
            if isinstance(kinds, dict):
                for key in ("clip", "playhead", "marker", "silence"):
                    if key in kinds:
                        self.snap_kinds[key] = bool(kinds[key])
        except Exception:
            pass

    # =========================================================
    # 编辑保护层（第 5/8 部分：防退化守卫）
    # =========================================================
    # 映射：UI 轨道 key → Track.type
    _TRACK_KEY_TO_TYPE = {
        "video": "video",
        "images": "image",
        "text": "text",
        "subs": "subs",
        "audio": "audio",
    }

    def _resolve_track_obj(self, track_key):
        """根据 UI 轨道 key（如 'subs', 'audio'）或 Track 对象找到 Track。
        NLE Gen-2: 如果传入的已经是 Track 对象则直接返回。"""
        from cutora.model.timeline_model import Track as _Track
        if isinstance(track_key, _Track):
            return track_key
        tl = _tl_of(self.task_data)
        if tl is None:
            return None
        target_type = self._TRACK_KEY_TO_TYPE.get(track_key)
        if not target_type:
            return None
        for t in tl.tracks:
            if hasattr(t, "type") and t.type == target_type:
                return t
        return None

    def can_edit_track(self, track_key):
        """检查指定轨道是否允许编辑。
        track_key: UI 轨道 key（如 'subs', 'audio', 'images', 'text', 'video'）
                   或 Track 对象实例（NLE Gen-2）。
        返回 False：轨道被锁定、轨道被隐藏。"""
        track = self._resolve_track_obj(track_key)
        if track is None:
            return True  # 旧项目/无 timeline 时不阻拦
        if getattr(track, "locked", False):
            return False
        if not getattr(track, "visible", True):
            return False
        return True

    def _is_subs_track(self, track_key):
        """字幕轨道判定——字幕操作只走原有字幕系统，不接受通用编辑逻辑。"""
        return track_key in ("subs",)

    # =========================================================
    # 批量复制保护层（第 8/8 部分）
    # =========================================================
    def can_batch_track(self, track_key):
        """判断轨道是否参与批量复制。
        必须同时满足：⭕ enable_batch 开启，且轨道支持共享。"""
        track = self._resolve_track_obj(track_key)
        if track is None:
            return False
        if not getattr(track, "enable_batch", False):
            return False
        return track_supports_batch(track)

    def _collect_batch_tracks(self, source_type):
        """找到所有开启 enable_batch 的同类型 Track 对象（排除不可编辑的）。
        source_type: Track.type 如 'text' / 'image'。
        返回 list[Track]。"""
        tl = _tl_of(self.task_data)
        if tl is None:
            return []
        result = []
        for t in get_batch_tracks(tl):
            if getattr(t, "type", None) != source_type:
                continue
            result.append(t)
        return result

    def _batch_paste_overlay(self, source_list_name, new_ov):
        """批量粘贴：将 overlay 副本插入到所有开启 batch 的同类型轨道。
        当前单轨架构下，每种类型只有 1 条轨道，source 已在主粘贴中处理。
        此方法处理"额外"的同类型轨道（未来多轨道时自动生效）。
        source_list_name: 'text_overlays' / 'image_overlays'。"""
        _LIST_TO_TYPE = {"text_overlays": "text", "image_overlays": "image"}
        source_type = _LIST_TO_TYPE.get(source_list_name)
        if isinstance(new_ov, dict) and new_ov.get("type") == "adjust":
            source_type = "adjust"          # Q175：调整条只往开了共享的调整轨上复制
        if not source_type:
            return 0
        batch_tracks = self._collect_batch_tracks(source_type)
        # 减去 source 自身（主粘贴已处理）
        extra_count = max(0, len(batch_tracks) - 1)
        if extra_count <= 0:
            return 0
        # overlay 存储在 task_data[list_name] 全局列表中，通过 _track_id 归属轨道
        ov_list = self.task_data.get(source_list_name, [])
        inserted = 0
        for t in batch_tracks[1:]:
            cloned = copy.deepcopy(new_ov)
            cloned['id'] = int(time.time() * 1000) + inserted + 1
            cloned['_track_id'] = getattr(t, "id", None)
            ov_list.append(cloned)
            inserted += 1
        return inserted

    def set_scroll_x(self, x):
        self.scroll_x = x
        opening = bool(getattr(self.window(), "_opening_project", False))
        if self.task_data is not None and not opening:
            self._queue_visible_thumbs()
        self.update()

    def set_scroll_y(self, y):
        self.scroll_y = y
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        parent = self.parent()
        if parent is not None and hasattr(parent, "update_scroll_range"):
            parent.update_scroll_range()
        opening = bool(getattr(self.window(), "_opening_project", False))
        if self.task_data is not None and not opening:
            self._queue_visible_thumbs()

    def set_data(self, duration_ms, task_data):
        _bump("timeline.set_data")
        if isinstance(task_data, dict) and (task_data.get("segments") or []):
            from cutora.ui.main_window import ensure_subs_track_for_segments
            ensure_subs_track_for_segments(task_data)
        is_same_task = (self.task_data is task_data)
        self.task_data = task_data
        self.base_duration = duration_ms
        self.set_trim_from_task(task_data)

        # 动态计算 NLE 时间线的真实长度
        tl = _tl_of(task_data)
        max_end_ms = 0
        if tl:
            for t in tl.tracks:
                for c in getattr(t, "clips", []) or []:
                    end_ms = int((getattr(c, "start_t", 0.0) + getattr(c, "effective_duration", 0.0)) * 1000)
                    if end_ms > max_end_ms:
                        max_end_ms = end_ms
                        
        # 兜底：取播放器时长和所有片段最远端的最大值。并追加 2000ms 冗余空间供拖拽。
        self.duration = max(duration_ms, max_end_ms) + 2000
        
        if not is_same_task:
            self.selected_indices.clear()
            self.selected_audio_idx = None
            self.selected_video_clip = None
            self.selected_transition = None
            self.selected_seam = None
            self._hover_seam = None
            self.selected_overlay = None
            self.selected_track = None
        opening = bool(getattr(self.window(), "_opening_project", False))
        if not opening:
            self._queue_visible_thumbs()
            self._queue_visible_waveforms()
        self._rebuild_av_link_index()
        self.update()

    def visible_time_range(self):
        pps = max(1e-6, float(self.pixels_per_second or 1.0))
        w = max(0, int(self.width() or 0))
        start = float(self.scroll_x or 0.0) / pps
        end = start + (w / pps)
        return start, end

    def _queue_visible_thumbs(self) -> None:
        from cutora.ui.timeline.thumbs import THUMB_PREFETCH_CLIPS, request_clip_thumb
        _bump("timeline.queue_thumbs")
        tl = _tl_of(self.task_data)
        if tl is None:
            return
        sources = getattr(tl, "sources", None) or {}
        clips = []
        for t_obj in getattr(tl, "tracks", []) or []:
            if getattr(t_obj, "type", None) != "video":
                continue
            # Q169：以前这里写死 row_h = 28，而绘制按真实轨道高（默认 40）去查缓存 —— 键永远对不上，
            # 视频条上从来不显示缩略图。现在排队和绘制用同一个 video_thumb_size(真实行高)。
            row_h = self._track_row_h(t_obj)
            for clip_obj in getattr(t_obj, "clips", []) or []:
                sid = getattr(clip_obj, "source_id", None)
                src = sources.get(sid) if sid else None
                src_path = getattr(src, "path", None) or (src.get("path") if isinstance(src, dict) else None) if src else None
                if not src_path:
                    continue
                start_t = float(getattr(clip_obj, "start_t", 0.0) or 0.0)
                dur = float(getattr(clip_obj, "effective_duration", 0.0) or 0.0)
                in_t = float(getattr(clip_obj, "in_t", 0.0) or 0.0)
                out_t = float(getattr(clip_obj, "out_t", in_t) or in_t)
                clips.append((start_t, start_t + max(dur, 0.0), src_path, in_t, out_t, row_h))
        if not clips:
            return
        clips.sort(key=lambda c: c[0])
        vis_start, vis_end = self.visible_time_range()
        visible_idx = [
            i for i, c in enumerate(clips)
            if c[1] >= vis_start and c[0] <= vis_end
        ]
        if not visible_idx:
            return
        prefetch = max(0, int(THUMB_PREFETCH_CLIPS))
        lo = max(0, visible_idx[0] - prefetch)
        hi = min(len(clips), visible_idx[-1] + 1 + prefetch)
        pps = max(1e-6, float(self.pixels_per_second or 1.0))
        for s_t, e_t, src_path, in_t, out_t, row_h in clips[lo:hi]:
            tw, th = self.video_thumb_size(row_h)
            request_clip_thumb(src_path, in_t + 0.1, tw, th, on_done=lambda _k: self.update())
            # 够宽的片段还要片尾那张（和 _paint_video_track 的门槛一致）
            if (e_t - s_t) * pps >= 3 * CLIP_THUMB_W + 40 and out_t - in_t > 0.3:
                request_clip_thumb(src_path, max(in_t + 0.1, out_t - 0.1), tw, th,
                                   on_done=lambda _k: self.update())

    def _queue_visible_waveforms(self) -> None:
        """Start waveform workers from set_data, never from paint."""
        win = self.window()
        trigger = getattr(win, "_trigger_waveform_generation", None)
        if not callable(trigger):
            return
        tl = _tl_of(self.task_data)
        if tl is None:
            return
        sources = getattr(tl, "sources", None) or {}
        seen = set()
        for t_obj in getattr(tl, "tracks", []) or []:
            if getattr(t_obj, "type", None) != "audio":
                continue
            for clip_obj in getattr(t_obj, "clips", []) or []:
                sid = getattr(clip_obj, "source_id", None)
                if not sid or sid in seen:
                    continue
                src = sources.get(sid) if sid else None
                src_path = getattr(src, "path", None) or (
                    src.get("path") if isinstance(src, dict) else None) if src else None
                if not src_path:
                    continue
                seen.add(sid)
                trigger(sid, src_path)

    def set_position(self, pos_ms):
        pos_ms = int(pos_ms or 0)
        if int(getattr(self, "position", -1) or -1) == pos_ms:
            return
        self.position = pos_ms
        self.position_changed.emit(pos_ms)
        self.update()

    def _get_timeline_tracks(self):
        """从当前 task_data 获取 timeline.tracks 列表。"""
        tl = _tl_of(self.task_data)
        return list(tl.tracks) if tl else []

    def _track_positions(self):
        """NLE Gen-2: 从 timeline.tracks 动态计算轨道行位置。
        返回 dict:
          'rows':  [(track_obj, y, h), ...]  — 按 timeline.tracks 顺序
          'domain_sep_y': int  — 分域线 y
          'content_h': int  — 总内容高度
        以及向后兼容的 type-key 快查：
          'video': (y, h), 'image': (y, h), ... 等
        """
        tracks = self._get_timeline_tracks()
        y = self.RULER_H + self.TOP_PAD
        rows = []
        by_type = {}  # type → first (y, h) 用于兼容
        domain_sep_y = 0
        sep_inserted = False

        for t in tracks:
            t_type = getattr(t, "type", None)
            t_domain = getattr(t, "domain", "visual")
            # 在 visual → audio 转换点插入分域间隔
            if t_domain == "audio" and not sep_inserted:
                domain_sep_y = y - self.TRACK_GAP + self.scroll_y
                y += self.DOMAIN_SEP
                sep_inserted = True
            row_h = self._track_row_h(t)
            row_y = y - self.scroll_y
            rows.append((t, row_y, row_h))
            # 兼容旧代码：第一个同类型轨道注册到 by_type
            compat_key = t_type
            if t_type == "image":
                compat_key = "images"  # 旧 UI key 为 images
            if compat_key and compat_key not in by_type:
                by_type[compat_key] = (row_y, row_h)
            y += row_h + self.TRACK_GAP

        if not sep_inserted:
            domain_sep_y = y - self.TRACK_GAP + self.scroll_y

        content_h = y + self.BOTTOM_PAD - self.TRACK_GAP
        result = dict(by_type)
        result["rows"] = rows
        result["domain_sep_y"] = domain_sep_y - self.scroll_y
        result["content_h"] = content_h
        return result

    def get_content_height(self):
        return self._track_positions()["content_h"]

    def _track_row_h(self, track) -> float:
        return clamp_track_height(getattr(track, "height", None) or self.TRACK_H)

    _IMG_PIX_CACHE: "OrderedDict" = OrderedDict()
    _IMG_PIX_CACHE_CAP = 96
    _IMG_PIX_MAX_H = 160

    @classmethod
    def _image_clip_pixmap(cls, path):
        """图片条的缩略图：按 (路径, 改动时间) 缓存一张缩到 160px 高的小图。
        以前每次重绘都 QPixmap(path) 整张解码，几张 4K 图就能把时间线拖卡。"""
        try:
            st = os.stat(path)
            key = (path, int(st.st_mtime_ns), int(st.st_size))
        except OSError:
            return None
        hit = cls._IMG_PIX_CACHE.get(key)
        if hit is not None:
            cls._IMG_PIX_CACHE.move_to_end(key)
            return hit
        pix = QPixmap(path)
        if pix.isNull():
            return None
        if pix.height() > cls._IMG_PIX_MAX_H:
            pix = pix.scaledToHeight(cls._IMG_PIX_MAX_H, Qt.TransformationMode.SmoothTransformation)
        while len(cls._IMG_PIX_CACHE) >= cls._IMG_PIX_CACHE_CAP:
            cls._IMG_PIX_CACHE.popitem(last=False)
        cls._IMG_PIX_CACHE[key] = pix
        return pix

    def _refresh_track_height_view(self) -> None:
        parent = self.parent()
        if parent is not None and hasattr(parent, "update_scroll_range"):
            parent.update_scroll_range()
        # 行高变了，视频条缩略图的尺寸键也变了：重新排队生成这一档的
        try:
            self._queue_visible_thumbs()
        except Exception:
            pass
        self.update()
        header = getattr(parent, "track_header", None) if parent is not None else None
        if header is not None:
            header.update()

    def set_track_height(self, track, height) -> None:
        if track is None:
            return
        track.height = clamp_track_height(height)
        self._refresh_track_height_view()

    def apply_live_track_height(self, track, height, all_tracks=False) -> None:
        h = clamp_track_height(height)
        if all_tracks:
            for t in self._get_timeline_tracks():
                t.height = h
        elif track is not None:
            track.height = h
        self._refresh_track_height_view()

    def commit_track_height(self, track, height, all_tracks=False) -> bool:
        if track is None:
            return False
        h = clamp_track_height(height)
        params = {
            "track_id": str(getattr(track, "id", "") or ""),
            "height": h,
        }
        if all_tracks:
            params["all"] = True
        if self._bus_execute("timeline.set_track_height", params):
            return True
        self.apply_live_track_height(track, h, all_tracks=all_tracks)
        return False

    def cycle_track_height(self, track) -> None:
        if track is None:
            return
        cur = int(round(self._track_row_h(track)))
        nxt = TRACK_HEIGHT_CYCLE[0]
        for i, h in enumerate(TRACK_HEIGHT_CYCLE):
            if abs(cur - int(h)) <= 1:
                nxt = TRACK_HEIGHT_CYCLE[(i + 1) % len(TRACK_HEIGHT_CYCLE)]
                break
        self.commit_track_height(track, nxt, all_tracks=False)

    def playhead_screen_x(self) -> float:
        return (self.position / 1000.0) * self.pixels_per_second - self.scroll_x

    def zoom_at_playhead(self, factor) -> None:
        """Zoom around the playhead so its widget x does not move."""
        self.zoom_at_x(factor, self.playhead_screen_x())

    def zoom_fit_all(self) -> None:
        dur = max(0.1, (self.duration or 0) / 1000.0)
        w = max(1.0, float(self.width()))
        self.pixels_per_second = max(1.0, min(300.0, w / dur))
        self.scroll_x = 0.0
        parent = self.parent()
        bar = getattr(parent, "scrollbar", None) if parent is not None else None
        if bar is not None:
            bar.blockSignals(True)
            try:
                if hasattr(parent, "update_scroll_range"):
                    parent.update_scroll_range()
                bar.setValue(0)
            finally:
                bar.blockSignals(False)
        self.update()

    def zoom_to_selection(self) -> None:
        t0, t1 = self._selection_time_span()
        if t1 - t0 < 1e-3:
            self.zoom_fit_all()
            return
        w = max(1.0, float(self.width()))
        pad = 0.05 * (t1 - t0)
        span = (t1 - t0) + 2.0 * pad
        self.pixels_per_second = max(1.0, min(300.0, w / span))
        self.scroll_x = max(0.0, (t0 - pad) * self.pixels_per_second)
        parent = self.parent()
        bar = getattr(parent, "scrollbar", None) if parent is not None else None
        if bar is not None:
            bar.blockSignals(True)
            try:
                if hasattr(parent, "update_scroll_range"):
                    parent.update_scroll_range()
                bar.setValue(int(max(0, round(self.scroll_x))))
            finally:
                bar.blockSignals(False)
        self.update()

    def _selection_time_span(self) -> tuple[float, float]:
        times = []
        segs = list((self.task_data or {}).get("segments") or [])
        for i in self.selected_indices or []:
            if 0 <= int(i) < len(segs) and isinstance(segs[int(i)], dict):
                times.append(float(segs[int(i)].get("start") or 0.0))
                times.append(float(segs[int(i)].get("end") or 0.0))
        if self.selected_video_clip is not None:
            _tr, clip = self.selected_video_clip
            a, b = snap_mod.clip_span(clip)
            times.extend([a, b])
        for tid, idx in getattr(self, "_clip_sel", []) or []:
            tl = _tl_of(self.task_data)
            if tl is None:
                continue
            for tr in getattr(tl, "tracks", []) or []:
                if str(getattr(tr, "id", "")) != str(tid):
                    continue
                clips = getattr(tr, "clips", []) or []
                if 0 <= int(idx) < len(clips):
                    a, b = snap_mod.clip_span(clips[int(idx)])
                    times.extend([a, b])
        ov = self.selected_overlay
        if ov is not None:
            name, i = ov
            rows = list((self.task_data or {}).get(name) or [])
            if 0 <= int(i) < len(rows) and isinstance(rows[int(i)], dict):
                times.append(float(rows[int(i)].get("start") or 0.0))
                times.append(float(rows[int(i)].get("end") or 0.0))
        if not times:
            return 0.0, max(0.1, (self.duration or 0) / 1000.0)
        return min(times), max(times)

    def _nudge_selection_frames(self, frames: int) -> None:
        fps = self._timeline_fps()
        delta = float(frames) / fps
        if abs(delta) < 1e-12:
            return
        items = []
        seen = set()
        tl = _tl_of(self.task_data)

        def _add_clip(track, clip):
            if track is None or clip is None or id(clip) in seen:
                return
            bag = list(getattr(track, "clips", []) or [])
            if clip not in bag:
                return
            seen.add(id(clip))
            items.append({
                "track_id": str(getattr(track, "id", "")),
                "clip_index": bag.index(clip),
                "start": max(0.0, float(getattr(clip, "start_t", 0.0)) + delta),
            })

        by_id = {}
        if tl is not None:
            by_id = {str(getattr(tr, "id", "")): tr
                     for tr in getattr(tl, "tracks", []) or []}
        for tid, idx in getattr(self, "_clip_sel", []) or []:
            tr = by_id.get(str(tid))
            clips = getattr(tr, "clips", []) or [] if tr is not None else []
            if 0 <= int(idx) < len(clips):
                _add_clip(tr, clips[int(idx)])
        if self.selected_video_clip is not None:
            _add_clip(self.selected_video_clip[0], self.selected_video_clip[1])
        if self.selected_audio_idx is not None:
            ac = self._get_audio_clip_list()
            if 0 <= self.selected_audio_idx < len(ac):
                clip = ac[self.selected_audio_idx][0]
                _add_clip(self._track_of_clip(clip), clip)
        if items:
            self._bus_execute("timeline.move_clips", {"clips": items})
            self.update()
            return
        if self.selected_indices:
            segs = list((self.task_data or {}).get("segments") or [])
            seg_items = []
            for i in sorted(self.selected_indices):
                if 0 <= int(i) < len(segs) and isinstance(segs[int(i)], dict):
                    seg_items.append({
                        "seg_id": int(i),
                        "start": max(0.0, float(segs[int(i)].get("start") or 0.0) + delta),
                    })
            if seg_items:
                self._bus_execute("subtitle.move_segments", {"segments": seg_items})
                self.update()

    def _row_for_type(self, tracks_dict, track_type):
        """从 _track_positions() 结果中查找指定类型的 (y, h)。"""
        for (t_obj, row_y, row_h) in tracks_dict.get("rows", []):
            if getattr(t_obj, "type", "") == track_type:
                return (row_y, row_h)
        return None

    def set_trim_from_task(self, task_data):
        """从 task 字典中恢复 trim_start_sec 和 trim_end_sec。"""
        if isinstance(task_data, dict):
            self.trim_start_sec = float(task_data.get("trim_start_sec") or 0.0)
            self.trim_end_sec = float(task_data.get("trim_end_sec") or 0.0)
        else:
            self.trim_start_sec = 0.0
            self.trim_end_sec = 0.0

    @staticmethod
    def _format_time_label(seconds):
        if seconds < 0:
            seconds = 0
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = seconds % 60
        if h > 0:
            return f"{h}:{m:02d}:{s:05.2f}"
        return f"{m:02d}:{s:05.2f}"

    @staticmethod
    def _pick_major_interval(pixels_per_second):
        candidates = [0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60]
        for sec in candidates:
            px = sec * pixels_per_second
            if 80 <= px <= 140:
                return sec
        if pixels_per_second * candidates[0] > 140:
            return candidates[0]
        return candidates[-1]

    def _get_audio_clip_list(self):
        """从 Track.clips 获取音频 clip 列表。
        返回 list[(Clip, str|None)]，其中 str 是文件路径。"""
        tl = _tl_of(self.task_data)
        if tl is None:
            return []
        tracks = tl.tracks
        sources = tl.sources
        result = []
        for t in tracks:
            if not hasattr(t, "type") or t.type != "audio":
                continue
            for clip in (t.clips if hasattr(t, "clips") else []):
                sid = getattr(clip, "source_id", None)
                src = sources.get(sid) if sid else None
                path = getattr(src, "path", None) or (src.get("path") if isinstance(src, dict) else None) if src else None
                result.append((clip, path))
        return result

    def selected_audio_clips(self):
        """Audio clips in the current selection (primary + Ctrl/rubberband extras)."""
        out = []
        seen = set()
        tl = _tl_of(self.task_data)
        if tl is None:
            return out
        by_id = {str(getattr(tr, "id", "")): tr for tr in getattr(tl, "tracks", []) or []}
        ac_list = self._get_audio_clip_list()
        if self.selected_audio_idx is not None and 0 <= self.selected_audio_idx < len(ac_list):
            clip = ac_list[self.selected_audio_idx][0]
            seen.add(id(clip))
            out.append(clip)
        for tid, idx in getattr(self, "_clip_sel", []) or []:
            tr = by_id.get(str(tid))
            if tr is None or getattr(tr, "type", None) != "audio":
                continue
            clips = getattr(tr, "clips", []) or []
            if 0 <= int(idx) < len(clips):
                clip = clips[int(idx)]
                if id(clip) in seen:
                    continue
                seen.add(id(clip))
                out.append(clip)
        return out

    def _draw_audio_clip_waveform(self, painter, clip_obj, rect, is_selected):
        """在给定矩形内绘制音频波形。"""
        from cutora.audio.waveform import get_waveform_cache
        cache = get_waveform_cache()
        source_id = getattr(clip_obj, "source_id", None)
        if not source_id:
            return
        peaks = cache.get_lod(source_id, self.pixels_per_second)
        if peaks is None or len(peaks) == 0:
            from cutora.ui.i18n import tr
            tok = self._theme()
            dim = QColor(tok.bg3)
            dim.setAlpha(80)
            painter.fillRect(rect, dim)
            if cache.is_pending(source_id):
                prog = cache.progress(source_id)
                bar_w = max(2.0, float(rect.width()) * max(0.05, prog))
                bar = QColor(tok.info)
                bar.setAlpha(160)
                painter.fillRect(
                    QRectF(rect.x(), rect.y() + rect.height() * 0.4,
                           bar_w, rect.height() * 0.2),
                    bar)
                painter.setPen(QColor(tok.fg1))
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, tr("生成中"))
            elif cache.is_failed(source_id):
                painter.setPen(QColor(tok.err_text))
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, tr("生成失败"))
            else:
                painter.setPen(QColor(tok.fg2))
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, tr("无波形"))
            return
        pps = 200
        if self.pixels_per_second < 8:
            pps = 2
        elif self.pixels_per_second < 80:
            pps = 20

        in_t = float(getattr(clip_obj, "in_t", 0.0))
        out_t = float(getattr(clip_obj, "out_t", in_t))
        # 在 peaks 数组中的范围
        start_idx = max(0, int(in_t * pps))
        end_idx = min(len(peaks), int(out_t * pps))
        if end_idx <= start_idx:
            return
        clip_peaks = peaks[start_idx:end_idx]

        # 绘制区域
        x0 = rect.x()
        y_mid = rect.y() + rect.height() / 2
        half_h = rect.height() / 2 - 2  # 留 2px 边距
        w = rect.width()
        n = len(clip_peaks)
        if n == 0 or w <= 0:
            return

        # 构建波形路径
        path = QPainterPath()
        # 上半部分（maxs）
        step = w / n
        path.moveTo(x0, y_mid)
        for i in range(n):
            px = x0 + i * step
            val = min(1.0, max(-1.0, float(clip_peaks[i, 1])))  # max
            py = y_mid - val * half_h
            path.lineTo(px, py)
        # 下半部分（mins，反向）
        for i in range(n - 1, -1, -1):
            px = x0 + i * step
            val = min(1.0, max(-1.0, float(clip_peaks[i, 0])))  # min
            py = y_mid - val * half_h
            path.lineTo(px, py)
        path.closeSubpath()

        # 填充
        from cutora.ui.theme.tokens import current, lighten_hex
        # Q169：音频条是绿色实色块，波形用同色的浅调（像专业软件），不再用 ok 色
        wc = QColor(lighten_hex(current().track_audio, 0.55))
        wc.setAlpha(210 if is_selected else 165)
        waveform_color = wc
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(waveform_color)
        painter.drawPath(path)

    # === NLE Gen-2: 分类型轨道绘制方法 ===

    def _paint_subs_track(self, painter, row_y, row_h, visible_left, visible_right, track_obj=None):
        """绘制字幕轨道内容。"""
        from cutora.subtitle.layout import is_over_cps, is_overlong, layout_caps
        from cutora.ui.theme.tokens import current
        segs = self.task_data.get('segments', [])
        layout = self._normalize_segments(track_obj=track_obj)
        sel_min_x, sel_max_x = 99999999, -1
        has_selection = len(self.selected_indices) > 0
        tok = current()
        max_c, max_l = layout_caps(self.task_data)

        for item in layout:
            i = item['idx']
            seg = segs[i]
            rect = QRectF(item['sx'], row_y, item['sw'], row_h)
            if rect.right() < visible_left or rect.left() > visible_right:
                continue
            is_sel = i in self.selected_indices
            if is_sel:
                sel_min_x = min(sel_min_x, rect.x())
                sel_max_x = max(sel_max_x, rect.x() + rect.width())
            glow = None
            role_id = seg.get("role", "default")
            roles = (self.task_data.get("subtitle_roles", {}) or {})
            if isinstance(roles, dict):
                info = roles.get(role_id, {})
                if isinstance(info, dict):
                    role_color = info.get("color")
                    rc = QColor(role_color) if role_color else QColor()
                    if rc.isValid():
                        glow = rc
            if is_over_cps(seg):
                warn = QColor(tok.warn)
                if warn.isValid():
                    glow = warn
            self._paint_clip_chrome(
                painter, rect, "subtitle", is_sel,
                hovered=self._clip_is_hovered(seg), glow_color=glow)
            path = QPainterPath()
            from cutora.ui.theme.tokens import RADIUS_SM
            path.addRoundedRect(rect, RADIUS_SM, RADIUS_SM)
            # 超长：左端 2px warn 竖条（同一轮绘制，不另遍历）
            if is_overlong(seg, max_c, max_l):
                painter.save()
                painter.setClipPath(path)
                painter.fillRect(
                    QRectF(rect.x(), rect.y(), 2.0, rect.height()),
                    QColor(tok.warn),
                )
                painter.restore()
            painter.setPen(QColor(self._theme().fg0))
            # Q139：以前硬砍前 5 个字（"Really" → "Reall"），看着像字幕本身错了。
            # 按格子宽度省略，放得下就整个显示。
            _label = str(seg.get('text', '') or '').replace("\n", " ")
            _fm = painter.fontMetrics()
            _label = _fm.elidedText(_label, Qt.TextElideMode.ElideRight,
                                    max(0, int(rect.width()) - 6))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, _label)
            if is_sel:
                self._paint_word_blocks(painter, seg, rect)
            if item['sw'] < 12:
                painter.setPen(QPen(QColor(self._theme().fg1), 1))
                x_hint = rect.right() - 1
                painter.drawLine(int(x_hint), int(rect.top() + 6), int(x_hint), int(rect.bottom() - 6))
        # Retime Handles
        if has_selection and sel_max_x > sel_min_x - 1:
            from cutora.ui.theme.tokens import RADIUS_SM
            handle_w = 10
            handle_h = 24
            handle_y = row_y + (row_h - handle_h) / 2
            handle_c = QColor(self._theme().fg1)
            hb = QColor(self._theme().fg0)
            hb.setAlpha(40)
            handle_border = hb
            left_rect = QRectF(sel_min_x - handle_w, handle_y, handle_w, handle_h)
            path_l = QPainterPath()
            path_l.addRoundedRect(left_rect, RADIUS_SM, RADIUS_SM)
            painter.fillPath(path_l, QBrush(handle_c))
            painter.setPen(QPen(handle_border, 1))
            painter.drawPath(path_l)
            right_rect = QRectF(sel_max_x, handle_y, handle_w, handle_h)
            path_r = QPainterPath()
            path_r.addRoundedRect(right_rect, RADIUS_SM, RADIUS_SM)
            painter.fillPath(path_r, QBrush(handle_c))
            painter.setPen(QPen(handle_border, 1))
            painter.drawPath(path_r)

    def _word_layout(self, seg) -> list:
        words = list((seg or {}).get("words") or [])
        out = []
        for i, w in enumerate(words):
            if not isinstance(w, dict):
                continue
            start = float(w.get("start") or 0.0)
            end = float(w.get("end") or start)
            sx = start * self.pixels_per_second
            sw = max(1.0, (end - start) * self.pixels_per_second)
            out.append({"i": i, "start": start, "end": end, "sx": sx, "sw": sw, "word": w})
        return out

    def _paint_word_blocks(self, painter, seg, clip_rect):
        from cutora.ui.theme.tokens import RADIUS_SM
        tok = self._theme()
        blocks = self._word_layout(seg)
        if not blocks:
            return
        painter.save()
        try:
            painter.setClipRect(clip_rect)
            for i, item in enumerate(blocks):
                wr = QRectF(item["sx"], clip_rect.y() + 2, item["sw"],
                            max(8.0, clip_rect.height() - 4))
                fill = QColor(tok.accent)
                fill.setAlpha(40 if i % 2 == 0 else 70)
                path = QPainterPath()
                path.addRoundedRect(wr, RADIUS_SM, RADIUS_SM)
                painter.fillPath(path, fill)
                painter.setPen(QPen(QColor(tok.accent), 1))
                painter.drawLine(int(round(wr.left())), int(wr.top()),
                                 int(round(wr.left())), int(wr.bottom()))
                painter.drawLine(int(round(wr.right())), int(wr.top()),
                                 int(round(wr.right())), int(wr.bottom()))
                label = str(item["word"].get("word") or item["word"].get("text") or "")
                if wr.width() >= 10 and label:
                    painter.setPen(QColor(tok.fg0))
                    painter.drawText(wr, Qt.AlignmentFlag.AlignCenter, label[:4])
        finally:
            painter.restore()

    def _hit_word_edge(self, real_x, y):
        """Return (seg_idx, word_index, 'left'|'right') or None."""
        tracks = self._track_positions()
        subs_row = self._row_for_type(tracks, "subs")
        if not subs_row:
            return None
        subs_top, subs_h = subs_row
        if not (subs_top <= y <= subs_top + subs_h):
            return None
        segs = list((self.task_data or {}).get("segments") or [])
        expanded = set(self.selected_indices or [])
        if not expanded:
            return None
        handle = float(getattr(self, "WORD_HANDLE_PX", 6))
        best = None
        best_d = None
        for seg_i in expanded:
            if not (0 <= int(seg_i) < len(segs)) or not isinstance(segs[int(seg_i)], dict):
                continue
            for item in self._word_layout(segs[int(seg_i)]):
                left = item["sx"]
                right = item["sx"] + item["sw"]
                d_left = abs(real_x - left)
                d_right = abs(real_x - right)
                if d_left <= handle and (best_d is None or d_left < best_d):
                    best = (int(seg_i), int(item["i"]), "left")
                    best_d = d_left
                if d_right <= handle and (best_d is None or d_right < best_d):
                    best = (int(seg_i), int(item["i"]), "right")
                    best_d = d_right
        return best

    def _word_snap_t(self, proposed, seg, word_index, extra=None):
        from cutora.subtitle.word_trim import snap_time_px, word_snap_times
        extras = list(extra or [])
        extras.append(self.position / 1000.0)
        kinds = getattr(self, "snap_kinds", None) or {}
        if kinds.get("silence", True):
            words = list((seg or {}).get("words") or [])
            prev_end = None
            for w in words:
                if not isinstance(w, dict):
                    continue
                start = float(w.get("start") or 0.0)
                end = float(w.get("end") or start)
                if prev_end is not None and start - prev_end > 1e-6:
                    extras.append(prev_end)
                    extras.append(start)
                prev_end = end
        targets = word_snap_times(
            (seg or {}).get("words") or [], skip_index=word_index, extra=extras)
        px = snap_mod.SNAP_PX if self._snap_active(None) else 0.0
        if px <= 0:
            self._snap_guide_t = None
            return float(proposed)
        snapped = snap_time_px(proposed, targets, self.pixels_per_second, px)
        if abs(float(snapped) - float(proposed)) > 1e-9:
            self._snap_guide_t = float(snapped)
        else:
            self._snap_guide_t = None
        return snapped

    def _commit_word_drag(self):
        orig = getattr(self, "_word_drag_orig", None)
        if not orig:
            return
        segs = list((self.task_data or {}).get("segments") or [])
        idx = int(orig.get("seg_id", -1))
        wi = int(orig.get("word_index", -1))
        edge = orig.get("edge")
        if not (0 <= idx < len(segs)) or not isinstance(segs[idx], dict):
            return
        words = list(segs[idx].get("words") or [])
        if not (0 <= wi < len(words)) or not isinstance(words[wi], dict):
            return
        final_t = float(words[wi].get("end") if edge == "right"
                        else words[wi].get("start") or 0.0)
        # Restore snapshot so the command is the only mutation (undo = mouse-up).
        segs[idx]["words"] = copy.deepcopy(orig.get("words") or [])
        if abs(final_t - float(orig.get("t") or 0.0)) < 1e-6:
            return
        if not self._bus_execute("subtitle.trim_word", {
            "seg_id": idx,
            "word_index": wi,
            "edge": edge,
            "t": final_t,
        }):
            segs[idx]["words"] = copy.deepcopy(orig.get("words") or [])

    def _paint_speed_curve(self, painter, clip_obj, rect):
        pts = getattr(clip_obj, "speed_curve", None) or []
        if len(pts) < 2 or rect.width() < 4:
            return
        span = max(0.001, float(getattr(clip_obj, "out_t", 0) or 0) - float(getattr(clip_obj, "in_t", 0) or 0))
        painter.save()
        painter.setPen(QPen(QColor(255, 196, 80, 180), 1.5))
        prev = None
        for row in pts:
            if not isinstance(row, dict):
                continue
            try:
                t = float(row.get("t") or 0.0)
                speed = float(row.get("speed") or 1.0)
            except (TypeError, ValueError):
                continue
            x = rect.left() + rect.width() * max(0.0, min(1.0, t / span))
            y = rect.bottom() - rect.height() * max(0.0, min(1.0, (speed - 0.1) / 2.0))
            if prev is not None:
                painter.drawLine(prev[0], prev[1], x, y)
            prev = (x, y)
        painter.restore()

    def _paint_silence_shades(self, painter, row_y, row_h, visible_left, visible_right):
        task = self.task_data if isinstance(self.task_data, dict) else {}
        analysis = task.get("analysis") if isinstance(task.get("analysis"), dict) else {}
        rows = analysis.get("silences") or []
        if not rows:
            return
        shade = QColor(255, 214, 120, 72)
        for rec in rows:
            if not isinstance(rec, dict):
                continue
            try:
                start = float(rec.get("start"))
                end = float(rec.get("end"))
            except (TypeError, ValueError):
                continue
            if end <= start:
                continue
            rect = QRectF(start * self.pixels_per_second, row_y,
                          (end - start) * self.pixels_per_second, row_h)
            if rect.right() < visible_left or rect.left() > visible_right:
                continue
            painter.fillRect(rect, shade)

    def _silence_at_x(self, real_x):
        task = self.task_data if isinstance(self.task_data, dict) else {}
        analysis = task.get("analysis") if isinstance(task.get("analysis"), dict) else {}
        try:
            t = float(real_x) / float(self.pixels_per_second or 1.0)
        except (TypeError, ValueError):
            return None
        for rec in analysis.get("silences") or []:
            if not isinstance(rec, dict):
                continue
            try:
                start = float(rec.get("start"))
                end = float(rec.get("end"))
            except (TypeError, ValueError):
                continue
            if start - 1e-3 <= t <= end + 1e-3:
                return rec
        return None

    def _paint_audio_track(self, painter, track_obj, row_y, row_h, visible_left, visible_right):
        """绘制单条音频轨道的内容（仅该 Track 自己的 Clips + 波形）。"""
        if str(getattr(track_obj, "audio_role", "") or "") == "main":
            self._paint_silence_shades(painter, row_y, row_h, visible_left, visible_right)
        tl = _tl_of(self.task_data)
        sources = tl.sources if tl else {}
        clips = getattr(track_obj, "clips", []) or []
        # 计算该 Track 之前所有 audio clips 的总数（用于全局 selected_audio_idx 匹配）
        global_offset = 0
        if tl:
            for t in tl.tracks:
                if t is track_obj:
                    break
                if getattr(t, "type", None) == "audio":
                    global_offset += len(getattr(t, "clips", []))
        for local_i, clip_obj in enumerate(clips):
            start_t = float(getattr(clip_obj, "start_t", 0.0))
            try:
                clip_dur = max(0.0, float(clip_obj.effective_duration))
            except Exception:
                in_t = float(getattr(clip_obj, "in_t", 0.0))
                out_t = float(getattr(clip_obj, "out_t", in_t))
                clip_dur = max(0.0, out_t - in_t)
            sx = start_t * self.pixels_per_second
            sw = clip_dur * self.pixels_per_second
            rect = QRectF(sx, row_y, sw, row_h)
            if rect.right() < visible_left or rect.left() > visible_right:
                continue
            global_i = global_offset + local_i
            tid = str(getattr(track_obj, "id", ""))
            is_sel = (self.selected_audio_idx == global_i
                      or (tid, local_i) in (getattr(self, "_clip_sel", None) or []))
            self._paint_clip_chrome(
                painter, rect, "audio", is_sel,
                hovered=self._clip_is_hovered(clip_obj))
            self._paint_keyframe_band(painter, clip_obj, rect, start_t)
            if self._clip_is_offline(clip_obj, sources):
                hatch = QBrush(QColor(220, 70, 70, 110), Qt.BrushStyle.BDiagPattern)
                painter.fillRect(rect, hatch)
            self._draw_audio_clip_waveform(painter, clip_obj, rect, is_sel)
            self._paint_speed_curve(painter, clip_obj, rect)
            self._paint_audio_fade_handles(painter, clip_obj, rect, is_sel)
            # 标签
            sid = getattr(clip_obj, "source_id", None)
            src = sources.get(sid) if sid else None
            src_path = getattr(src, "path", None) or (src.get("path") if isinstance(src, dict) else None) if src else None
            label = _file_label(src_path) if src_path else "Audio"
            self._draw_clip_label(painter, label, rect.left() + 6, rect.right() - 4, rect)
        self._paint_seams(painter, track_obj, row_y, row_h)
        self._paint_transition_tapes(painter, track_obj, row_y, row_h, visible_left, visible_right)

    def _timeline_fps(self) -> float:
        tl = _tl_of(self.task_data)
        try:
            fps = float(getattr(tl, "fps", 30.0) or 30.0) if tl is not None else 30.0
        except (TypeError, ValueError):
            fps = 30.0
        return fps if fps > 0 else 30.0

    def _status(self, msg, ms=4000):
        win = self.window()
        bar = getattr(win, "statusBar", None) if win is not None else None
        if callable(bar):
            bar().showMessage(str(msg), int(ms))
        _log.info("%s", msg)

    def _paint_seams(self, painter, track_obj, row_y, row_h):
        from cutora.model.seams import find_seams, transition_at_seam
        fps = self._timeline_fps()
        hover = getattr(self, "_hover_seam", None)
        selected = getattr(self, "selected_seam", None)
        gold = QColor(self._theme().accent)
        gold_sel = QColor(self._theme().accent_hi)
        for left_i, right_i, seam_t in find_seams(track_obj, fps=fps):
            if transition_at_seam(track_obj, seam_t, fps=fps) is not None:
                continue
            sx = float(seam_t) * self.pixels_per_second
            is_sel = (
                selected is not None
                and selected[0] is track_obj
                and int(selected[1]) == int(left_i)
                and int(selected[2]) == int(right_i)
            )
            is_hov = (
                hover is not None
                and hover[0] is track_obj
                and int(hover[1]) == int(left_i)
                and int(hover[2]) == int(right_i)
            )
            if not is_sel and not is_hov:
                continue
            if is_sel:
                glow = QColor(255, 224, 138, 90)
                painter.fillRect(QRectF(sx - 10, row_y, 10, row_h), glow)
                painter.fillRect(QRectF(sx, row_y, 10, row_h), glow)
                painter.setPen(QPen(gold_sel, 3))
            else:
                painter.setPen(QPen(gold, 2))
            painter.drawLine(int(sx), int(row_y), int(sx), int(row_y + row_h))

    def _paint_transition_tapes(self, painter, track_obj, row_y, row_h, visible_left, visible_right):
        transitions = getattr(track_obj, "transitions", []) or []
        tl = _tl_of(self.task_data)
        sources = getattr(tl, "sources", {}) if tl is not None else {}
        fps = self._timeline_fps()
        for trans_obj in transitions:
            t_dur = float(getattr(trans_obj, "duration", 0.0) or 0.0)
            if t_dur <= 0:
                continue
            trans_x = float(getattr(trans_obj, "start_t", 0.0)) * self.pixels_per_second
            trans_w = t_dur * self.pixels_per_second
            trans_rect = QRectF(trans_x, row_y, trans_w, row_h)
            if trans_rect.right() < visible_left or trans_rect.left() > visible_right:
                continue
            trans_is_sel = (getattr(self, "selected_transition", None) == (track_obj, trans_obj))
            is_audio = str(getattr(trans_obj, "domain", "visual") or "visual") == "audio"
            tok = self._theme()
            if trans_is_sel:
                fill = QColor(tok.accent if not is_audio else tok.track_audio)
                fill.setAlpha(120)
                painter.setBrush(fill)
                painter.setPen(QPen(QColor(tok.accent_hi), 2))
            else:
                fill = QColor(tok.fg0 if not is_audio else tok.track_audio)
                fill.setAlpha(80 if not is_audio else 70)
                painter.setBrush(fill)
                painter.setPen(Qt.PenStyle.NoPen)
            from cutora.ui.theme.tokens import RADIUS_SM
            painter.drawRoundedRect(trans_rect, RADIUS_SM, RADIUS_SM)
            try:
                from cutora.render.transition_runtime import freeze_extents
                left_s, right_s = freeze_extents(track_obj, trans_obj, sources=sources, fps=fps)
            except Exception:
                left_s, right_s = 0.0, 0.0
            hatch_c = QColor(tok.warn)
            hatch_c.setAlpha(110)
            hatch = QBrush(hatch_c, Qt.BrushStyle.BDiagPattern)
            if left_s > 0:
                lw = min(trans_w, left_s * self.pixels_per_second)
                painter.fillRect(QRectF(trans_x, row_y, lw, row_h), hatch)
            if right_s > 0:
                rw = min(trans_w, right_s * self.pixels_per_second)
                painter.fillRect(QRectF(trans_x + trans_w - rw, row_y, rw, row_h), hatch)
            if trans_is_sel:
                handle_c = QColor(tok.accent_hi)
            else:
                handle_c = QColor(tok.fg0)
                handle_c.setAlpha(220)
            painter.setPen(QPen(handle_c, 2))
            painter.drawLine(int(trans_x), int(row_y + 3), int(trans_x), int(row_y + row_h - 3))
            painter.drawLine(int(trans_x + trans_w), int(row_y + 3),
                             int(trans_x + trans_w), int(row_y + row_h - 3))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(handle_c)
            painter.drawPolygon(QPolygonF([
                QPointF(trans_x + 1, row_y + row_h / 2),
                QPointF(trans_x + 6, row_y + row_h / 2 - 4),
                QPointF(trans_x + 6, row_y + row_h / 2 + 4),
            ]))
            painter.drawPolygon(QPolygonF([
                QPointF(trans_x + trans_w - 1, row_y + row_h / 2),
                QPointF(trans_x + trans_w - 6, row_y + row_h / 2 - 4),
                QPointF(trans_x + trans_w - 6, row_y + row_h / 2 + 4),
            ]))

    def _hit_transition_tape(self, real_x, y):
        tracks = self._track_positions()
        for (t_obj, row_y, row_h) in tracks.get("rows", []):
            if not (row_y <= y <= row_y + row_h):
                continue
            if getattr(t_obj, "type", None) not in ("video", "audio"):
                continue
            for trans_obj in reversed(getattr(t_obj, "transitions", []) or []):
                t_dur = float(getattr(trans_obj, "duration", 0.0) or 0.0)
                if t_dur <= 0:
                    continue
                trans_x = float(getattr(trans_obj, "start_t", 0.0)) * self.pixels_per_second
                trans_w = t_dur * self.pixels_per_second
                if QRectF(trans_x, row_y, trans_w, row_h).contains(real_x, y):
                    return t_obj, trans_obj
        return None

    def _hit_transition_handle(self, real_x, y):
        """Return (track, trans, 'left'|'right') or None. Hit zone ±SEAM_PX (4)."""
        tracks = self._track_positions()
        slop = float(self.SEAM_PX)
        for (t_obj, row_y, row_h) in tracks.get("rows", []):
            if not (row_y <= y <= row_y + row_h):
                continue
            if getattr(t_obj, "type", None) not in ("video", "audio"):
                continue
            if getattr(t_obj, "locked", False):
                continue
            for trans_obj in reversed(getattr(t_obj, "transitions", []) or []):
                t_dur = float(getattr(trans_obj, "duration", 0.0) or 0.0)
                if t_dur <= 0:
                    continue
                trans_x = float(getattr(trans_obj, "start_t", 0.0)) * self.pixels_per_second
                trans_w = t_dur * self.pixels_per_second
                if abs(real_x - trans_x) <= slop:
                    return t_obj, trans_obj, "left"
                if abs(real_x - (trans_x + trans_w)) <= slop:
                    return t_obj, trans_obj, "right"
        return None

    def _min_transition_dur(self) -> float:
        fps = self._timeline_fps()
        return 1.0 / max(1.0, float(fps))

    def _transition_seam_bounds(self, track, trans):
        """(cover_t, prev_bound, next_bound) so the tape cannot leave its seam."""
        from cutora.model.seams import clip_span, find_seams, transition_at_seam
        fps = self._timeline_fps()
        seams = [float(st) for _l, _r, st in find_seams(track, fps=fps)]
        cover = None
        for st in seams:
            if transition_at_seam(track, st, fps=fps) is trans:
                cover = float(st)
                break
        if cover is None:
            from cutora.render.transition_runtime import clips_for_transition
            _out, inn = clips_for_transition(track, trans)
            if inn is not None:
                cover = clip_span(inn)[0]
            else:
                cover = (float(getattr(trans, "start_t", 0.0) or 0.0)
                         + float(getattr(trans, "duration", 0.0) or 0.0) * 0.5)
        prevs = [s for s in seams if s < cover - 1e-6]
        nexts = [s for s in seams if s > cover + 1e-6]
        frame = 1.0 / max(1.0, fps)
        lo = (max(prevs) + 2.0 * frame) if prevs else 0.0
        if nexts:
            hi = min(nexts) - 2.0 * frame
        else:
            hi = cover + 3600.0
        if hi <= cover + frame:
            hi = cover + frame
        return cover, lo, hi

    def _refresh_preview_canvas(self):
        win = self.window()
        vc = getattr(win, "video_canvas", None) if win is not None else None
        if vc is not None and hasattr(vc, "update"):
            vc.update()

    def _apply_transition_handle_live(self, real_x):
        """Live-preview duration while dragging. Opposite edge stays put."""
        trans_obj = getattr(self, "_trans_drag_obj", None)
        side = getattr(self, "_trans_drag_side", None)
        if trans_obj is None or side not in ("left", "right"):
            return
        orig_start = float(getattr(self, "_trans_orig_start", 0.0) or 0.0)
        orig_dur = float(getattr(self, "_trans_orig_dur", 0.0) or 0.0)
        orig_end = orig_start + orig_dur
        min_dur = self._min_transition_dur()
        t = real_x / max(1e-6, self.pixels_per_second)
        cover = getattr(self, "_trans_drag_seam", None)
        lo = float(getattr(self, "_trans_drag_lo", 0.0) or 0.0)
        hi = float(getattr(self, "_trans_drag_hi", orig_end + 3600.0) or (orig_end + 3600.0))
        if cover is None:
            cover = orig_start + orig_dur * 0.5
        cover = float(cover)
        if side == "left":
            hi_start = min(orig_end - min_dur, cover)
            new_start = min(max(t, lo), hi_start)
            trans_obj.start_t = new_start
            trans_obj.duration = max(min_dur, orig_end - new_start)
        else:
            lo_end = max(orig_start + min_dur, cover)
            new_end = min(max(t, lo_end), hi)
            trans_obj.start_t = orig_start
            trans_obj.duration = max(min_dur, new_end - orig_start)
        self._refresh_preview_canvas()

    def _commit_transition_handle_drag(self):
        trans_obj = getattr(self, "_trans_drag_obj", None)
        track = getattr(self, "_trans_drag_track", None)
        if trans_obj is None or track is None:
            return
        orig_start = float(getattr(self, "_trans_orig_start", 0.0) or 0.0)
        orig_dur = float(getattr(self, "_trans_orig_dur", 0.0) or 0.0)
        final_start = float(getattr(trans_obj, "start_t", orig_start) or orig_start)
        final_dur = float(getattr(trans_obj, "duration", orig_dur) or orig_dur)
        trans_obj.start_t = orig_start
        trans_obj.duration = orig_dur
        self._trans_drag_obj = None
        self._trans_drag_track = None
        self._trans_drag_side = None
        if (abs(final_start - orig_start) < 1e-6
                and abs(final_dur - orig_dur) < 1e-6):
            return
        ok = self._bus_execute("timeline.set_transition", {
            "track_id": str(getattr(track, "id", "") or ""),
            "transition_id": str(getattr(trans_obj, "id", "") or ""),
            "start_t": final_start,
            "duration": final_dur,
        })
        if not ok:
            trans_obj.start_t = orig_start
            trans_obj.duration = orig_dur

    def _hit_seam_at(self, real_x, y):
        from cutora.model.seams import find_seams, transition_at_seam
        fps = self._timeline_fps()
        tracks = self._track_positions()
        for (t_obj, row_y, row_h) in tracks.get("rows", []):
            if not (row_y <= y <= row_y + row_h):
                continue
            t_type = getattr(t_obj, "type", None)
            if t_type not in ("video", "audio"):
                continue
            if not getattr(t_obj, "visible", True) or getattr(t_obj, "locked", False):
                continue
            for left_i, right_i, seam_t in find_seams(t_obj, fps=fps):
                sx = float(seam_t) * self.pixels_per_second
                if abs(real_x - sx) > self.SEAM_PX:
                    continue
                trans = transition_at_seam(t_obj, seam_t, fps=fps)
                if trans is not None:
                    return ("transition", t_obj, trans, seam_t)
                return ("seam", t_obj, int(left_i), int(right_i), float(seam_t))
        return None

    def _select_seam(self, track, left_i, right_i, seam_t):
        self._reset_selection()
        self.selected_seam = (track, int(left_i), int(right_i), float(seam_t))
        self.selected_track = track
        self.mode = "none"
        self._publish_selection()
        self.update()
        self.item_selected.emit("seam", self.selected_seam)
        self.setFocus()

    def _join_selected_seam(self):
        seam = getattr(self, "selected_seam", None)
        if not seam:
            return False
        track, left_i, right_i, _t = seam
        if getattr(track, "locked", False):
            self._status("不能合并：轨道已锁定")
            return False
        ok = self._bus_execute("timeline.join_clips", {
            "track_id": str(getattr(track, "id", "") or ""),
            "left_index": int(left_i),
            "right_index": int(right_i),
        })
        if ok:
            self.selected_seam = None
            self.update()
        return bool(ok)

    def _add_transition_to_seam(self, track, left_i, right_i, preset, domain, duration):
        from cutora.model.seams import clip_span
        clips = getattr(track, "clips", []) or []
        if int(right_i) < 0 or int(right_i) >= len(clips):
            self._status("不能添加转场：接缝已失效")
            return False
        seam_t = float(getattr(clips[int(right_i)], "start_t", 0.0) or 0.0)
        if 0 <= int(left_i) < len(clips):
            _s, seam_t = clip_span(clips[int(left_i)])
        dur = float(duration or 0.35)
        start_t = seam_t - dur / 2.0
        want = "audio" if str(domain or "visual") == "audio" else "visual"
        track_domain = getattr(track, "domain", "visual")
        if (track_domain == "audio") != (want == "audio"):
            self._status("不能添加转场：视频转场请放到视频接缝，音频转场请放到音频接缝")
            return False
        return bool(self._bus_execute("timeline.add_transition", {
            "track_id": str(getattr(track, "id", "") or ""),
            "start_t": start_t,
            "duration": dur,
            "preset": str(preset or ""),
            "domain": want,
        }))

    def _delete_selected_transition(self):
        pair = getattr(self, "selected_transition", None)
        if not pair:
            return False
        track, trans = pair
        if getattr(track, "locked", False):
            return False
        ok = self._bus_execute("timeline.delete_transition", {
            "track_id": str(getattr(track, "id", "") or ""),
            "transition_id": str(getattr(trans, "id", "") or ""),
            "start_t": float(getattr(trans, "start_t", 0.0) or 0.0),
            "domain": str(getattr(trans, "domain", "visual") or "visual"),
        })
        if not ok:
            return False
        self.selected_transition = None
        self._publish_selection()
        self.item_selected.emit("", None)
        self.update()
        return True

    def _clip_is_offline(self, clip_obj, sources=None):
        from cutora.model.offline import source_is_offline
        if sources is None:
            tl = _tl_of(self.task_data)
            sources = tl.sources if tl else {}
        sid = getattr(clip_obj, "source_id", None)
        src = sources.get(sid) if sid and sources else None
        if source_is_offline(src):
            return True
        return bool((self.task_data or {}).get("offline"))

    def _is_first_track_of_type(self, track_obj, track_type):
        """判断 track_obj 是否是该类型的第一条轨道。"""
        tl = _tl_of(self.task_data)
        if tl is None:
            return True
        for t in tl.tracks:
            if getattr(t, "type", None) == track_type:
                return t is track_obj
        return True

    def _get_overlays_for_track(self, track_obj, list_name):
        """返回属于 track_obj 的 overlay 列表（带原始索引）。
        list_name: 'text_overlays' 或 'image_overlays'。
        返回 [(原始索引, overlay_dict), ...]。
        没有 _track_id 的旧 overlay 归到该类型的第一条轨道。"""
        all_ovs = self.task_data.get(list_name, [])
        track_id = getattr(track_obj, "id", None)
        # 找到同类型的第一条轨道 id（用于分配旧 overlay）
        tl = _tl_of(self.task_data)
        t_type = getattr(track_obj, "type", "")
        first_track_id = None
        if tl:
            for t in tl.tracks:
                if getattr(t, "type", None) == t_type:
                    first_track_id = getattr(t, "id", None)
                    break
        result = []
        want_adjust = t_type == "adjust"          # Q174：调整轨只认调整图层，图片轨不认它
        for i, ov in enumerate(all_ovs):
            if list_name == "image_overlays" and (ov.get("type") == "adjust") != want_adjust:
                continue
            ov_tid = ov.get('_track_id')
            if ov_tid == track_id:
                result.append((i, ov))
            elif ov_tid is None and track_id == first_track_id:
                # 旧 overlay 没有 _track_id → 归到第一条轨道
                result.append((i, ov))
        return result

    def _paint_text_track(self, painter, track_obj, row_y, row_h, visible_left, visible_right):
        """绘制文字覆层轨道内容（每条 text 轨道只绘制属于自己的 overlay）。"""
        tl = _tl_of(self.task_data)
        my_ovs = self._get_overlays_for_track(track_obj, 'text_overlays')
        for i, ov in my_ovs:
            sx = ov['start'] * self.pixels_per_second
            sw = (ov['end'] - ov['start']) * self.pixels_per_second
            rect = QRectF(sx, row_y, sw, row_h)
            if rect.right() < visible_left or rect.left() > visible_right:
                continue
            is_sel = self.ov_is_selected('text_overlays', i, ov)
            grp = _CG.group_of(ov)
            self._paint_clip_chrome(
                painter, rect, "text", is_sel,
                hovered=self._clip_is_hovered(ov),
                glow_color=QColor(grp["color"]) if grp and grp.get("color") else None)
            if grp:
                self._paint_group_badge(painter, ov, grp, rect, tl)
            self._paint_keyframe_band(painter, ov, rect, float(ov.get("start") or 0.0))
            painter.setPen(QColor(self._theme().fg0))
            label = str(ov.get("content") or ov.get("text") or "text")
            painter.drawText(
                rect.adjusted(4, 0, -4, 0),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                label)

    def _paint_image_track(self, painter, track_obj, row_y, row_h, visible_left, visible_right):
        """绘制图片覆层轨道内容（每条 image 轨道只绘制属于自己的 overlay）。"""
        tl = _tl_of(self.task_data)
        my_ovs = self._get_overlays_for_track(track_obj, 'image_overlays')
        for i, ov in my_ovs:
            sx = ov['start'] * self.pixels_per_second
            sw = (ov['end'] - ov['start']) * self.pixels_per_second
            rect = QRectF(sx, row_y, sw, row_h)
            if rect.right() < visible_left or rect.left() > visible_right:
                continue
            is_sel = self.ov_is_selected('image_overlays', i, ov)
            grp = _CG.group_of(ov)
            self._paint_clip_chrome(
                painter, rect, "image", is_sel,
                hovered=self._clip_is_hovered(ov),
                glow_color=QColor(grp["color"]) if grp and grp.get("color") else None)
            if grp:
                self._paint_group_badge(painter, ov, grp, rect, tl)
            self._paint_keyframe_band(painter, ov, rect, float(ov.get("start") or 0.0))
            img_path = ""
            els = ov.get("elements") or []
            if els and isinstance(els[0], dict):
                img_path = els[0].get("path") or ""
            img_path = img_path or ov.get("path") or ""
            drawn_w = 0
            if img_path and os.path.isfile(img_path) and sw >= 24:
                try:
                    pix = self._image_clip_pixmap(img_path)
                    if pix is not None and not pix.isNull():
                        th = max(8, int(row_h) - 4)
                        tw = min(int(sw) - 4, max(16, int(th * pix.width() / max(1, pix.height()))))
                        painter.drawPixmap(int(sx) + 2, int(row_y) + 2, tw, th, pix)
                        drawn_w = tw
                except Exception:
                    drawn_w = 0
            name = _file_label(img_path) or ov.get("type", "image")
            if drawn_w:
                self._draw_clip_label(painter, name, rect.left() + 2 + drawn_w + 6, rect.right() - 4, rect)
            else:
                self._draw_clip_label(painter, name, rect.left() + 6, rect.right() - 4, rect, centered=True)

    def _paint_adjust_track(self, painter, track_obj, row_y, row_h, visible_left, visible_right):
        """Q174：调整轨上的段：调整轨色块 + 一个小图标（纯色 = 实心方块 / 效果 = 虚线框）+ 名字。"""
        from cutora.render import adjust_layer as AL
        tl = _tl_of(self.task_data)
        for i, ov in self._get_overlays_for_track(track_obj, 'image_overlays'):
            if not AL.is_adjust(ov):
                continue
            sx = ov['start'] * self.pixels_per_second
            sw = (ov['end'] - ov['start']) * self.pixels_per_second
            rect = QRectF(sx, row_y, sw, row_h)
            if rect.right() < visible_left or rect.left() > visible_right:
                continue
            is_sel = self.ov_is_selected('image_overlays', i, ov)
            grp = _CG.group_of(ov)
            self._paint_clip_chrome(painter, rect, "adjust", is_sel, hovered=self._clip_is_hovered(ov),
                                    glow_color=QColor(grp["color"]) if grp and grp.get("color") else None)
            if grp:
                self._paint_group_badge(painter, ov, grp, rect, tl)
            self._paint_keyframe_band(painter, ov, rect, float(ov.get("start") or 0.0))
            sol, eff = AL.solid_of(ov), AL.effect_of(ov)
            ink = self._adjust_ink(is_sel, self._clip_is_hovered(ov))
            icon_w = 0
            if sw >= 30:
                s = max(8.0, min(14.0, row_h - 12))
                ir = QRectF(sx + 6, row_y + (row_h - s) / 2.0, s, s)
                painter.save()
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                if sol["on"]:
                    c = QColor(sol["color"])
                    c.setAlphaF(max(0.35, sol["opacity"]))
                    painter.setBrush(c)
                else:
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                pen = QPen(ink, 1.2)
                if AL.effect_active(eff):
                    pen.setStyle(Qt.PenStyle.DashLine)
                painter.setPen(pen)
                from cutora.ui.theme.tokens import RADIUS_SM as _R_SM
                painter.drawRoundedRect(ir, _R_SM, _R_SM)
                painter.restore()
                icon_w = int(s) + 6
            label = str(ov.get("name") or "调整")
            if AL.range_of(ov) == "full":
                label += " · 全画面"
            if AL.xform_of(ov)["on"]:
                label += " · 画面变换"
            if AL.effect_active(eff):
                label += " · " + AL.effect_label(eff)
            self._draw_clip_label(painter, label, rect.left() + 6 + icon_w, rect.right() - 4, rect,
                                  centered=not icon_w, color=ink)

    def _adjust_ink(self, selected=False, hovered=False) -> QColor:
        """调整轨（银灰）上的字 / 图标：主题色，压暗到在当前底色上看得清。"""
        from cutora.ui.theme.tokens import track_ink
        return QColor(track_ink(self._clip_fill("adjust", selected, hovered).name(), self._theme().accent))

    GROUP_BAR_H = 3

    def _paint_group_badge(self, painter, clip_obj, grp, rect, tl):
        """Q154：预合成组的标识 —— 顶上一条组色横杠（同一组一个颜色）；组里第一条写组名；
        重复份盖一层组色斜纹 + ↻，一眼看出是循环铺满复制出来的。"""
        col = QColor(grp.get("color") or "#F2B33D")
        painter.save()
        try:
            bar = QRectF(rect.left() + 1, rect.top() + 1, max(0.0, rect.width() - 2), float(self.GROUP_BAR_H))
            painter.fillRect(bar, col)
            if not grp.get("locked", True):
                # 解锁了：横杠改成虚线，提醒现在改的是单条
                painter.fillRect(bar, QColor(self._theme().bg0))
                painter.setPen(QPen(col, float(self.GROUP_BAR_H), Qt.PenStyle.DotLine))
                painter.drawLine(QPointF(bar.left(), bar.center().y()), QPointF(bar.right(), bar.center().y()))
            if grp.get("repeat"):
                hatch = QBrush(QColor(col.red(), col.green(), col.blue(), 46), Qt.BrushStyle.BDiagPattern)
                painter.fillRect(rect.adjusted(1, self.GROUP_BAR_H + 1, -1, -1), hatch)
                if rect.width() >= 18 and rect.height() >= 14:
                    painter.setPen(QColor(col.red(), col.green(), col.blue(), 200))
                    f = QFont(self.font())      # 界面字体（带中文回退），时间线自己的 Arimo 不合并字体
                    f.setPixelSize(max(9, min(12, int(rect.height() * 0.45))))
                    painter.setFont(f)
                    painter.drawText(rect.adjusted(0, self.GROUP_BAR_H, -3, 0),
                                     Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop, "↻")
                return
            # 组名：只写在时间上最靠前那条真身上
            first = None
            if tl is not None:
                mem = _CG.members(tl, grp["id"], repeats=False)
                first = mem[0][1] if mem else None
            if first is clip_obj and rect.width() >= 40 and rect.height() >= 16:
                idx = list(_CG.groups(tl).keys()).index(grp["id"]) if tl is not None and grp["id"] in _CG.groups(tl) else None
                name = _CG.display_name(grp, idx)
                f = QFont(self.font())      # 界面字体（带中文回退），时间线自己的 Arimo 不合并字体
                f.setPixelSize(max(9, min(11, int(rect.height() * 0.4))))
                f.setBold(True)
                painter.setFont(f)
                fm = painter.fontMetrics()
                tw = min(rect.width() - 8, fm.horizontalAdvance(name) + 8)
                pill = QRectF(rect.left() + 4, rect.top() + self.GROUP_BAR_H + 2, tw, fm.height() + 2)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(col.red(), col.green(), col.blue(), 200))
                painter.drawRoundedRect(pill, 3, 3)
                painter.setPen(QColor(self._theme().bg0))
                painter.drawText(pill, Qt.AlignmentFlag.AlignCenter,
                                 fm.elidedText(name, Qt.TextElideMode.ElideRight, int(tw - 6)))
        finally:
            painter.restore()

    @staticmethod
    def video_thumb_size(row_h) -> tuple:
        """视频条缩略图的 (w, h)。排队生成和绘制都从这里拿，键才对得上（Q169 修的就是两边不一致）。"""
        return CLIP_THUMB_W, max(16, int(row_h) - 6)

    def _paint_video_thumb(self, painter, clip_obj, src_path, rect, row_h, at_end=False) -> int:
        """画片头 / 片尾那张缩略图（只用缓存，绘制路径不开 ffmpeg）。返回画了多宽，没画返回 0。"""
        try:
            from cutora.ui.timeline.thumbs import peek_clip_thumb
            tw_max, th_max = self.video_thumb_size(row_h)
            in_t = float(getattr(clip_obj, "in_t", 0.0))
            if at_end:
                out_t = float(getattr(clip_obj, "out_t", in_t))
                src_t = max(in_t + 0.1, out_t - 0.1)
            else:
                src_t = in_t + 0.1
            jp = peek_clip_thumb(src_path, src_t, tw_max, th_max)
            if not jp:
                return 0
            pix = QPixmap(jp)
            if pix.isNull():
                return 0
            th = min(int(row_h) - 4, pix.height())
            tw = min(tw_max, pix.width())
            x = int(rect.right()) - 4 - tw if at_end else int(rect.left()) + 4
            y = int(rect.top()) + 2
            from cutora.ui.theme.tokens import RADIUS_SM
            clip_path = QPainterPath()
            clip_path.addRoundedRect(QRectF(x, y, tw, th), RADIUS_SM - 1, RADIUS_SM - 1)
            painter.save()
            painter.setClipPath(clip_path)
            painter.drawPixmap(x, y, tw, th, pix)
            painter.restore()
            # 缩略图外面一圈 1px 暗边，贴在色块上才像嵌进去的
            shade = QColor(0, 0, 0, 90)
            painter.setPen(QPen(shade, 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(x + 0.5, y + 0.5, tw - 1, th - 1), RADIUS_SM - 1, RADIUS_SM - 1)
            return tw
        except Exception:
            return 0

    def _draw_clip_label(self, painter, label, left, right, rect, centered=False, color=None):
        """片段上的文件名 / 内容：fg0（亮色片段给 color），按剩余宽度省略；放不下 20px 就不画。"""
        avail = int(right - left)
        if avail < 20:
            return
        fm = painter.fontMetrics()
        text = fm.elidedText(str(label or ""), Qt.TextElideMode.ElideMiddle, avail)
        painter.setPen(color if color is not None else self._clip_label_color())
        area = QRectF(left, rect.top(), avail, rect.height())
        flag = Qt.AlignmentFlag.AlignVCenter | (
            Qt.AlignmentFlag.AlignHCenter if centered else Qt.AlignmentFlag.AlignLeft)
        painter.drawText(area, flag, text)

    def _paint_video_track(self, painter, track_obj, row_y, row_h, visible_left, visible_right):
        """绘制视频轨道的 Clip 条（支持选中高亮 + 变速宽度）。"""
        tl = _tl_of(self.task_data)
        sources = tl.sources if tl else {}
        clips = getattr(track_obj, "clips", []) or []
        sel_vc = self.selected_video_clip  # (Track, Clip) or None
        tid = str(getattr(track_obj, "id", ""))
        for cidx, clip_obj in enumerate(clips):
            start_t = float(getattr(clip_obj, "start_t", 0.0))
            eff_dur = float(getattr(clip_obj, "effective_duration", 0.0))
            if eff_dur <= 0:
                in_t = float(getattr(clip_obj, "in_t", 0.0))
                out_t = float(getattr(clip_obj, "out_t", in_t))
                eff_dur = max(0.0, out_t - in_t)
            sx = start_t * self.pixels_per_second
            sw = eff_dur * self.pixels_per_second
            rect = QRectF(sx, row_y, sw, row_h)
            if rect.right() < visible_left or rect.left() > visible_right:
                continue
            is_thumb = bool(getattr(clip_obj, "is_thumbnail_leadin", False))
            is_intro_vid = bool(getattr(clip_obj, "is_intro_video_leadin", False))
            is_sel = (
                (sel_vc is not None and sel_vc[1] is clip_obj)
                or (tid, cidx) in self._clip_sel
                or self.selection.has_clip(tid, cidx)
            )
            if is_thumb:
                kind = "thumb"
            elif is_intro_vid:
                kind = "intro"
            else:
                kind = "video"
            hovered = self._clip_is_hovered(clip_obj)
            grp = _CG.group_of(clip_obj)
            self._paint_clip_chrome(painter, rect, kind, is_sel, hovered=hovered,
                                    glow_color=QColor(grp["color"]) if grp and grp.get("color") else None)
            if grp:
                self._paint_group_badge(painter, clip_obj, grp, rect, tl)
            self._paint_keyframe_band(painter, clip_obj, rect, start_t)
            self._paint_matte_band(painter, clip_obj, rect, start_t)
            self._paint_track_band(painter, clip_obj, rect, start_t)
            hover = getattr(self, "_hover_edge", None)
            if hover and hover[0] == "clip" and hover[1] is clip_obj:
                painter.setPen(QPen(QColor(self._theme().accent), 2))
                if hover[2] == "left":
                    painter.drawLine(int(sx), int(row_y), int(sx), int(row_y + row_h))
                else:
                    painter.drawLine(int(sx + sw), int(row_y), int(sx + sw), int(row_y + row_h))
            if self._clip_is_offline(clip_obj, sources):
                hatch = QBrush(QColor(220, 70, 70, 110), Qt.BrushStyle.BDiagPattern)
                painter.fillRect(rect, hatch)
                painter.setPen(QColor(self._theme().err_text))
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "离线")
            # 选中时绘制左右拉伸 handle 指示
            if is_sel and sw > self.HANDLE_PX * 3:
                from cutora.ui.theme.tokens import RADIUS_SM
                handle_color = QColor(self._theme().fg0)
                handle_color.setAlpha(60)
                painter.setBrush(handle_color)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(QRectF(sx, row_y, self.HANDLE_PX, row_h), RADIUS_SM, RADIUS_SM)
                painter.drawRoundedRect(QRectF(sx + sw - self.HANDLE_PX, row_y, self.HANDLE_PX, row_h), RADIUS_SM, RADIUS_SM)
            sid = getattr(clip_obj, "source_id", None)
            src = sources.get(sid) if sid else None
            src_path = getattr(src, "path", None) or (src.get("path") if isinstance(src, dict) else None) if src else None
            text_left = rect.left() + 6
            text_right = rect.right() - 4
            if sw >= 48 and src_path and not is_thumb:
                # Q169：片头缩略图（宽度够再加片尾那张），和 _queue_visible_thumbs 用同一个尺寸键
                head_w = self._paint_video_thumb(painter, clip_obj, src_path, rect, row_h, at_end=False)
                if head_w:
                    text_left = rect.left() + 4 + head_w + 6
                if sw >= 3 * CLIP_THUMB_W + 40:
                    tail_w = self._paint_video_thumb(painter, clip_obj, src_path, rect, row_h, at_end=True)
                    if tail_w:
                        text_right = rect.right() - 4 - tail_w - 6
            if is_thumb:
                label = "缩略图静帧"
            elif is_intro_vid:
                label = "开场视频"
            else:
                label = _file_label(src_path) if src_path else "Video"
            speed = float(getattr(clip_obj, "speed", 1.0))
            if (not is_thumb and not is_intro_vid) and abs(speed - 1.0) > 0.01:
                label = f"{label} ({speed:.1f}x)"
            self._draw_clip_label(painter, label, text_left, text_right, rect,
                                  centered=(is_thumb or is_intro_vid))

        self._paint_seams(painter, track_obj, row_y, row_h)
        self._paint_transition_tapes(painter, track_obj, row_y, row_h, visible_left, visible_right)

    def _draw_ruler(self, painter):
        major = self._pick_major_interval(self.pixels_per_second)
        minor = major / 5 if major >= 1 else major / 2
        if minor <= 0:
            minor = major
        start_x = self.scroll_x
        end_x = self.scroll_x + self.width()
        start_sec = start_x / self.pixels_per_second
        end_sec = end_x / self.pixels_per_second
        first_major = int(start_sec // major) * major
        painter.save()
        painter.setPen(QPen(QColor(self._theme().line), 1))
        painter.drawLine(int(start_x), self.RULER_H, int(end_x), self.RULER_H)
        painter.setPen(QPen(QColor(self._theme().border), 1))
        text_limit_x = self.width() + self.scroll_x - 8
        t = first_major
        while t <= end_sec + major:
            x = t * self.pixels_per_second
            painter.drawLine(int(x), 0, int(x), self.RULER_H)
            if x <= text_limit_x:
                label = self._format_time_label(t)
                painter.drawText(int(x + 4), self.RULER_H - 6, label)
            t += major
        if minor > 0:
            painter.setPen(QPen(QColor(self._theme().line), 1))
            t = int(start_sec // minor) * minor
            while t <= end_sec + minor:
                x = t * self.pixels_per_second
                painter.drawLine(int(x), self.RULER_H - 6, int(x), self.RULER_H)
                t += minor
        self._draw_io_points(painter, start_x, end_x)
        self._draw_markers(painter)
        painter.restore()

    def _draw_io_points(self, painter, start_x, end_x):
        task = self.task_data if isinstance(self.task_data, dict) else {}
        inp = task.get("in_point")
        outp = task.get("out_point")
        if inp is None and outp is None:
            return
        h = self.RULER_H
        dim = QColor(0, 0, 0, 90)
        if inp is not None:
            ix = float(inp) * self.pixels_per_second
            if ix > start_x:
                painter.fillRect(int(start_x), 0, int(ix - start_x), h, dim)
            painter.setPen(QPen(QColor(self._theme().ok), 2))
            painter.drawLine(int(ix), 0, int(ix), h)
        if outp is not None:
            ox = float(outp) * self.pixels_per_second
            if ox < end_x:
                painter.fillRect(int(ox), 0, int(end_x - ox), h, dim)
            painter.setPen(QPen(QColor(self._theme().err), 2))
            painter.drawLine(int(ox), 0, int(ox), h)

    def _draw_markers(self, painter):
        from cutora.model.markers import sorted_markers
        from PySide6.QtGui import QPolygonF
        rows = sorted_markers(self.task_data)
        if not rows:
            return
        show_name = self.pixels_per_second >= 36
        for rec in rows:
            t = float(rec.get("t") or 0.0)
            x = t * self.pixels_per_second
            color = QColor(str(rec.get("color") or self._theme().accent))
            if not color.isValid():
                color = QColor(self._theme().accent)
            poly = QPolygonF([
                QPointF(x, 0.0),
                QPointF(x - 6.0, 10.0),
                QPointF(x + 6.0, 10.0),
            ])
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawPolygon(poly)
            name = str(rec.get("name") or "")
            if show_name and name:
                painter.setPen(QColor(self._theme().fg0))
                painter.drawText(int(x + 8), 10, name)

    def _hit_marker(self, widget_x, widget_y):
        if widget_y > self.RULER_H + 2:
            return None
        from cutora.model.markers import sorted_markers
        real_x = widget_x + self.scroll_x
        best = None
        for rec in sorted_markers(self.task_data):
            mx = float(rec.get("t") or 0.0) * self.pixels_per_second
            dist = abs(real_x - mx)
            if dist <= 8 and (best is None or dist < best[0]):
                best = (dist, rec)
        return None if best is None else best[1]

    def _trim_handle_rect(self):
        """返回当前裁剪手柄（可拖动的标签）在 widget 坐标系（未 scroll）中的 QRectF。"""
        tw = 14   # 手柄宽度（px）
        th = 20   # 手柄高度（px）
        tx = self.trim_start_sec * self.pixels_per_second - self.scroll_x
        ty = 2
        return QRectF(tx - 1, ty, tw, th)

    def _trim_end_handle_rect(self):
        """返回裁剪尾手柄在 widget 坐标系中的 QRectF。"""
        if self.trim_end_sec <= 0:
            return QRectF()
        tw = 14
        th = 20
        tx = self.trim_end_sec * self.pixels_per_second - self.scroll_x
        ty = 2
        return QRectF(tx - tw + 1, ty, tw, th)

    def _draw_trim_overlay(self, painter):
        """绘制裁剪区域阴影 + 拖动手柄（在 painter 已 translate(-scroll_x,0) 的坐标系中）。"""
        trim_x = self.trim_start_sec * self.pixels_per_second
        h = self.height()
        w_vis = self.width() + self.scroll_x

        painter.save()
        tok = self._theme()
        fg_hi = QColor(tok.fg0)
        fg_hi.setAlpha(180)
        fg_lo = QColor(tok.fg0)
        fg_lo.setAlpha(160)

        # 1. 阴影区域（0 ~ trim_x）：整个 widget 高度
        if trim_x > 0:
            shadow = QColor(tok.bg0)
            shadow.setAlpha(120)
            painter.fillRect(QRectF(0, 0, trim_x, h), shadow)

            dash = QColor(tok.warn)
            dash.setAlpha(180)
            painter.setPen(QPen(dash, 1.5, Qt.PenStyle.DashLine))
            painter.drawLine(int(trim_x), 0, int(trim_x), h)

        # 2. 手柄标签（置于标尺顶部，形如 ▶ 形状的竖条 + 小三角耳朵）
        hx = trim_x
        hw = 14
        hh = 20
        hy = 2
        handle_color = QColor(tok.warn)
        handle_path = QPainterPath()
        # 主体矩形
        from cutora.ui.theme.tokens import RADIUS_SM
        handle_path.addRoundedRect(QRectF(hx - 1, hy, hw, hh), RADIUS_SM, RADIUS_SM)
        # 右侧小三角箭头
        handle_path.moveTo(hx + hw - 1, hy + hh * 0.35)
        handle_path.lineTo(hx + hw + 6, hy + hh * 0.5)
        handle_path.lineTo(hx + hw - 1, hy + hh * 0.65)
        handle_path.closeSubpath()

        painter.setBrush(QBrush(handle_color))
        painter.setPen(QPen(fg_hi, 1))
        painter.drawPath(handle_path)

        # 手柄内竖线装饰
        painter.setPen(QPen(fg_lo, 1))
        for i in range(3):
            lx = int(hx + 3 + i * 3)
            painter.drawLine(lx, int(hy + 4), lx, int(hy + hh - 4))

        # 当有裁剪时在手柄下方显示裁剪时间标注
        if trim_x > 0:
            label = f"▶ {self._format_time_label(self.trim_start_sec)}"
            painter.setPen(QColor(tok.warn))
            fm = QFontMetrics(painter.font())
            label_x = int(trim_x + 12)
            label_y = int(hy + hh - 4)
            if label_x + fm.horizontalAdvance(label) + 4 < w_vis:
                painter.drawText(label_x, label_y, label)

        painter.restore()

    def _draw_trim_end_overlay(self, painter):
        """绘制裁剪尾区域阴影 + 拖动手柄（在 painter 已 translate(-scroll_x,0) 的坐标系中）。"""
        if self.trim_end_sec <= 0:
            return
        trim_x = self.trim_end_sec * self.pixels_per_second
        h = self.height()
        w_vis = self.width() + self.scroll_x

        painter.save()
        tok = self._theme()
        fg_hi = QColor(tok.fg0)
        fg_hi.setAlpha(180)
        fg_lo = QColor(tok.fg0)
        fg_lo.setAlpha(160)

        # 1. 阴影区域（trim_x ~ 时间线末尾）
        if trim_x < w_vis:
            shadow = QColor(tok.bg0)
            shadow.setAlpha(120)
            painter.fillRect(QRectF(trim_x, 0, w_vis - trim_x, h), shadow)

            dash = QColor(tok.info)
            dash.setAlpha(180)
            painter.setPen(QPen(dash, 1.5, Qt.PenStyle.DashLine))
            painter.drawLine(int(trim_x), 0, int(trim_x), h)

        # 2. 手柄标签（◀ 形，在 trim_x 左侧）
        hx = trim_x
        hw = 14
        hh = 20
        hy = 2
        handle_color = QColor(tok.info)
        handle_path = QPainterPath()
        # 主体矩形（在 trim_x 左侧）
        from cutora.ui.theme.tokens import RADIUS_SM
        handle_path.addRoundedRect(QRectF(hx - hw + 1, hy, hw, hh), RADIUS_SM, RADIUS_SM)
        # 左侧小三角箭头（◀）
        handle_path.moveTo(hx - hw + 1, hy + hh * 0.35)
        handle_path.lineTo(hx - hw - 6, hy + hh * 0.5)
        handle_path.lineTo(hx - hw + 1, hy + hh * 0.65)
        handle_path.closeSubpath()

        painter.setBrush(QBrush(handle_color))
        painter.setPen(QPen(fg_hi, 1))
        painter.drawPath(handle_path)

        # 手柄内竖线装饰
        painter.setPen(QPen(fg_lo, 1))
        for i in range(3):
            lx = int(hx - hw + 4 + i * 3)
            painter.drawLine(lx, int(hy + 4), lx, int(hy + hh - 4))

        # 时间标注（显示在手柄左侧）
        label = f"◀ {self._format_time_label(self.trim_end_sec)}"
        painter.setPen(QColor(tok.info))
        fm = QFontMetrics(painter.font())
        label_w = fm.horizontalAdvance(label)
        label_x = int(hx - hw - label_w - 6)
        label_y = int(hy + hh - 4)
        if label_x > 0:
            painter.drawText(label_x, label_y, label)

        painter.restore()

    def _draw_track(self, painter, rect, base_color, label, label_color, label_bg):
        from cutora.ui.theme.tokens import RADIUS_CARD, RADIUS_DLG
        radius = RADIUS_DLG
        grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        grad.setColorAt(0.0, base_color.lighter(108))
        grad.setColorAt(1.0, base_color.darker(110))
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        painter.fillPath(path, QBrush(grad))
        painter.setPen(QPen(QColor(255, 255, 255, 30), 1))
        painter.drawPath(path)
        label_rect = QRectF(rect.x() + 6, rect.y() + 6, 70, rect.height() - 12)
        label_path = QPainterPath()
        label_path.addRoundedRect(label_rect, RADIUS_CARD, RADIUS_CARD)
        label_grad = QLinearGradient(label_rect.topLeft(), label_rect.bottomLeft())
        label_grad.setColorAt(0.0, label_bg.lighter(110))
        label_grad.setColorAt(1.0, label_bg.darker(115))
        painter.fillPath(label_path, QBrush(label_grad))
        painter.setPen(QPen(QColor(0, 0, 0, 80), 1))
        painter.drawPath(label_path)
        painter.setPen(label_color)
        painter.drawText(label_rect.adjusted(6, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, label)

    def _draw_time_bar(self, painter, rect, base_color):
        if rect.width() <= 0:
            return
        from cutora.ui.theme.tokens import RADIUS_CARD
        radius = min(RADIUS_CARD, rect.width() / 2)
        grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        grad.setColorAt(0.0, base_color.lighter(110))
        grad.setColorAt(1.0, base_color.darker(110))
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        painter.fillPath(path, QBrush(grad))
        painter.setPen(QPen(QColor(255, 255, 255, 30), 1))
        painter.drawPath(path)

    def _draw_empty_track(self, painter, rect, label):
        from cutora.ui.theme.tokens import RADIUS_CARD, RADIUS_DLG
        t = self._theme()
        radius = RADIUS_DLG
        bg = QColor(t.bg0)
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        painter.fillPath(path, QBrush(bg))
        edge = QColor(t.fg0)
        edge.setAlpha(15)
        painter.setPen(QPen(edge, 1))
        painter.drawPath(path)
        label_rect = QRectF(rect.x() + 6, rect.y() + 6, 70, rect.height() - 12)
        label_path = QPainterPath()
        label_path.addRoundedRect(label_rect, RADIUS_CARD, RADIUS_CARD)
        painter.fillPath(label_path, QBrush(QColor(t.bg3)))
        painter.setPen(QColor(t.fg1))
        painter.drawText(label_rect.adjusted(6, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, label)

    def _paint_drop_hint(self, painter):
        """Empty-project cue, drawn in widget coords (not scrolled)."""
        from cutora.ui.i18n import tr
        from cutora.ui.theme.tokens import current
        painter.save()
        try:
            painter.resetTransform()
            painter.setPen(QColor(current().fg2))
            area = QRect(0, self.RULER_H, self.width(), max(0, self.height() - self.RULER_H))
            painter.drawText(
                area, Qt.AlignmentFlag.AlignCenter, tr("把视频拖到这里开始"))
        finally:
            painter.restore()

    def _draw_playhead_marker(self, painter, x):
        w = 12
        h = 9
        top = 2
        path = QPainterPath()
        path.moveTo(x, top + h)
        path.lineTo(x - w / 2, top)
        path.lineTo(x + w / 2, top)
        path.closeSubpath()
        grad = QLinearGradient(QPointF(x, top), QPointF(x, top + h))
        from cutora.ui.theme.tokens import current, lighten_hex
        err = current().err
        grad.setColorAt(0.0, QColor(lighten_hex(err, 0.18)))
        grad.setColorAt(1.0, QColor(err))
        painter.fillPath(path, QBrush(grad))
        painter.setPen(QPen(QColor(0, 0, 0, 80), 1))
        painter.drawPath(path)

    def _show_hover_time(self, real_x, event):
        hover_sec = real_x / self.pixels_per_second
        if self.duration > 0:
            hover_sec = max(0, min(hover_sec, self.duration / 1000.0))
        if self._last_hover_sec is None or abs(hover_sec - self._last_hover_sec) > 0.02:
            QToolTip.showText(event.globalPosition().toPoint(), self._format_time_label(hover_sec), self)
            self._last_hover_sec = hover_sec

    def _normalize_segments(self, track_obj=None):
        if not self.task_data:
            return []
        segs = self.task_data.get('segments', [])
        indexed = list(enumerate(segs))
        if track_obj is not None:
            tl = _tl_of(self.task_data)
            subs_tracks = [
                t for t in (getattr(tl, "tracks", None) or [])
                if getattr(t, "type", None) in ("subs", "subtitle")
            ] if tl is not None else []
            if len(subs_tracks) > 1:
                tid = str(getattr(track_obj, "id", "") or "")
                is_first = (track_obj is subs_tracks[0])
                indexed = [
                    (idx, seg) for idx, seg in indexed
                    if isinstance(seg, dict) and (
                        str(seg.get("_track_id") or "") == tid
                        or (is_first and not seg.get("_track_id"))
                    )
                ]
        indexed.sort(key=lambda x: x[1].get('start', 0))
        min_dur = 0.05
        prev_end = None
        overlap_fixed = 0
        end_fixed = 0
        layout = []
        for idx, seg in indexed:
            start = seg.get('start')
            end = seg.get('end')
            if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
                continue
            if start < 0: start = 0
            if end < 0: end = 0
            if end < start:
                end = start + min_dur
                end_fixed += 1
            if prev_end is not None and start < prev_end:
                start = prev_end
                overlap_fixed += 1
                if end <= start:
                    end = start + min_dur
                    end_fixed += 1
            sx = max(0, start * self.pixels_per_second)
            sw = max(1, (end - start) * self.pixels_per_second)
            ex = sx + sw
            layout.append({'idx': idx, 'start': start, 'end': end, 'sx': sx, 'sw': sw, 'ex': ex})
            prev_end = end
        stats = (len(segs), overlap_fixed, end_fixed)
        if stats != self._last_norm_stats:
            print(f"[Timeline] segments={stats[0]} overlap_fixed={stats[1]} end_fixed={stats[2]}")
            self._last_norm_stats = stats
        return layout

    def paintEvent(self, event):
        _bump("timeline.paint")
        if self._painting:
            return
        self._painting = True
        painter = None
        try:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.translate(-self.scroll_x, 0)

            dirty = event.rect() if event is not None else QRect(0, 0, self.width(), self.height())
            visible_left = float(self.scroll_x + dirty.x())
            visible_right = float(visible_left + max(1, dirty.width()))
            visible_top = float(dirty.y())
            visible_bot = float(dirty.y() + max(1, dirty.height()))
            painter.setClipRect(QRectF(
                visible_left, visible_top,
                max(1.0, visible_right - visible_left),
                max(1.0, visible_bot - visible_top)))

            h = self.height()
            from cutora.ui.theme.tokens import current
            tok = current()
            painter.fillRect(
                QRect(int(visible_left), int(visible_top),
                      int(visible_right - visible_left),
                      int(visible_bot - visible_top)),
                QColor(tok.bg0))
            # === NLE Gen-2: 按 timeline.tracks 遍历绘制 ===
            has_media = bool(self.task_data) and self.duration > 0
            tracks = self._track_positions()
            rows = tracks.get("rows", [])
            content_h = int(tracks.get("content_h") or 0)
            painter.save()
            painter.setClipRect(QRect(0, self.RULER_H, self.width(), max(0, h - self.RULER_H)))
            try:
                if content_h and visible_bot > content_h:
                    painter.fillRect(
                        QRect(int(visible_left), content_h,
                              int(visible_right - visible_left),
                              int(visible_bot - content_h)),
                        QColor(tok.bg1),
                    )

                # 绘制空轨道背景（只画脏矩形 ∩ 视口）
                for (t_obj, row_y, row_h) in rows:
                    if row_y + row_h < visible_top or row_y > visible_bot:
                        continue
                    t_type = getattr(t_obj, "type", "")
                    disp = self._TRACK_DISPLAY.get(t_type, {"label": t_type.capitalize()})
                    label = disp.get("label", t_type)
                    tr_rect = QRectF(
                        visible_left, row_y,
                        max(1.0, visible_right - visible_left), row_h)
                    self._draw_empty_track(painter, tr_rect, label)

                # Domain separator line
                sep_y = tracks.get("domain_sep_y", 0)
                painter.setPen(QPen(QColor(255, 255, 255, 30), 1))
                painter.drawLine(
                    int(visible_left), int(sep_y + self.DOMAIN_SEP // 2),
                    int(visible_right), int(sep_y + self.DOMAIN_SEP // 2))

                # Video lane stays on the empty-track fill (bg2). Do not paint
                # track_video across the whole width — that colour showed through
                # every gap between clips.

                if not has_media:
                    self._paint_drop_hint(painter)
                if self.task_data:
                    painter.setFont(timeline_label_font())

                    # === 按 Track 实例逐行绘制内容 ===
                    for (t_obj, row_y, row_h) in rows:
                        if row_y + row_h < visible_top or row_y > visible_bot:
                            continue
                        t_type = getattr(t_obj, "type", "")
                        t_visible = getattr(t_obj, "visible", True)
                        if not t_visible:
                            continue

                        painter.save()
                        try:
                            if t_type in ("subs", "subtitle"):
                                self._paint_subs_track(painter, row_y, row_h, visible_left, visible_right, track_obj=t_obj)
                            elif t_type == "audio":
                                self._paint_audio_track(painter, t_obj, row_y, row_h, visible_left, visible_right)
                            elif t_type == "text":
                                self._paint_text_track(painter, t_obj, row_y, row_h, visible_left, visible_right)
                            elif t_type == "image":
                                self._paint_image_track(painter, t_obj, row_y, row_h, visible_left, visible_right)
                            elif t_type == "adjust":
                                self._paint_adjust_track(painter, t_obj, row_y, row_h, visible_left, visible_right)
                            elif t_type == "video":
                                self._paint_video_track(painter, t_obj, row_y, row_h, visible_left, visible_right)
                        finally:
                            painter.restore()

                    # === 裁剪阴影 + 手柄（底层遮罩）===
                    self._draw_trim_overlay(painter)
                    self._draw_trim_end_overlay(painter)

                    # Q169：轨道头正在拖轨道 → 插入线横穿整条时间线
                    drag_ly = getattr(self, "_track_drag_line_y", None)
                    if drag_ly is not None:
                        painter.setPen(QPen(QColor(self._theme().accent_hi), 2))
                        painter.drawLine(int(visible_left), int(drag_ly), int(visible_right), int(drag_ly))
            finally:
                painter.restore()

            # 顶部刻度条固定常驻绘制在最上层（无论滚动或面板高度如何变化，始终可见）
            painter.fillRect(
                QRect(int(visible_left), 0, max(1, int(visible_right - visible_left)), self.RULER_H),
                QColor(tok.bg0),
            )
            self._draw_ruler(painter)
            if not self.task_data:
                return

            # Playhead（绘制在阴影之上，保证播放游标始终可见）
            px = (self.position / 1000.0) * self.pixels_per_second
            self._draw_playhead_marker(painter, px)
            painter.setPen(QPen(QColor(self._theme().err), 2)); painter.drawLine(int(px), 0, int(px), h)
            painter.setPen(QColor(self._theme().fg0))
            painter.drawText(int(px + 6), 12, self._format_time_label(self.position / 1000.0))

            guide = getattr(self, "_snap_guide_t", None)
            if guide is not None:
                gx = float(guide) * self.pixels_per_second
                guide_c = QColor(self._theme().accent)
                painter.setPen(QPen(guide_c, 1))
                painter.drawLine(int(round(gx)), 0, int(round(gx)), h)

            # Rubberband
            if self.mode == 'rubberband':
                painter.setPen(QPen(QColor(255, 255, 255, 150), 1, Qt.PenStyle.DashLine))
                painter.setBrush(QColor(255, 255, 255, 30))
                painter.drawRect(self.rubber_band_rect)

            # Q115：从外面拖素材进来时的落点提示
            self._paint_drop_hint(painter)
        except Exception as exc:
            tb = exc.__traceback__
            while tb is not None and tb.tb_next is not None:
                tb = tb.tb_next
            top = ""
            if tb is not None:
                fr = tb.tb_frame
                top = "%s:%s:%s" % (
                    os.path.basename(fr.f_code.co_filename),
                    fr.f_code.co_name, tb.tb_lineno)
            key = (type(exc).__name__, top)
            n = _paint_err_counts.get(key, 0) + 1
            _paint_err_counts[key] = n
            if n == 1:
                _log.exception("PaintError")
            elif n in (10, 100, 1000) or n % 5000 == 0:
                _log.error("PaintError %s x%s (%s)", key[0], n, key[1])
        finally:
            self._painting = False
            if painter is not None:
                painter.end()

    def _store_intro_drag_state(self, clip_obj):
        """在 mousePress 时存储开场视频 clip 的初始状态，供 release 时计算 ripple。"""
        self._intro_drag_orig_in_t = float(getattr(clip_obj, "in_t", 0.0))
        self._intro_drag_orig_out_t = float(getattr(clip_obj, "out_t", 0.0))
        self._intro_drag_orig_eff_dur = float(getattr(clip_obj, "effective_duration", 0.0))
        self._intro_drag_is_intro = bool(getattr(clip_obj, "is_intro_video_leadin", False))
        track = self._drag_source_track
        if track is not None:
            self._snapshot_clip(track, clip_obj)

    def _publish_selection(self):
        clips = list(self._clip_sel)
        if self.selected_video_clip is not None:
            tr, clip = self.selected_video_clip
            key = (str(getattr(tr, "id", "")), self._clip_index_of(tr, clip))
            if key[1] >= 0 and key not in clips:
                clips.insert(0, key)
        if self.selected_audio_idx is not None:
            ac_list = self._get_audio_clip_list()
            if 0 <= self.selected_audio_idx < len(ac_list):
                clip, _path = ac_list[self.selected_audio_idx]
                tr = self._track_of_clip(clip)
                key = (str(getattr(tr, "id", "")), self._clip_index_of(tr, clip))
                if key[1] >= 0 and key not in clips:
                    clips.append(key)
        self.selection.replace(clips=clips, segments=list(self.selected_indices))

    def _reset_selection(self, keep_ctrl=False):
        """Clear all seven selection channels.

        keep_ctrl leaves ``_clip_sel`` and ``_ov_multi`` so a Ctrl-click can toggle
        clips and overlays together without dropping the rest of the multi-select.
        """
        if keep_ctrl and self.selected_overlay is not None:
            name, idx = self.selected_overlay
            rows = (self.task_data or {}).get(name) or []
            if name and isinstance(idx, int) and 0 <= idx < len(rows):
                cur_ov = rows[idx]
                if not any(o is cur_ov for o in (self._ov_multi or [])):
                    self._ov_multi = list(self._ov_multi or []) + [cur_ov]
        self.selected_indices.clear()
        self.selected_overlay = None
        self.selected_video_clip = None
        self.selected_audio_idx = None
        self.selected_transition = None
        self.selected_seam = None
        if not keep_ctrl:
            self._clip_sel = []
            self._ov_multi = []
        self.selection.clear(emit=False)

    def _select_video_clip(self, track, clip, ctrl=False, keep_group=False, linked=True):
        tid = str(getattr(track, "id", "") or "")
        cidx = self._clip_index_of(track, clip)
        key = (tid, cidx)
        if keep_group and not ctrl and key in self._clip_sel:
            # 点在已选的一条上 → 保留当前选择（多选整组拖，或 Alt+点后的 J/L 单条）
            self._reset_selection(keep_ctrl=True)
        elif ctrl:
            self._reset_selection(keep_ctrl=True)
            if cidx >= 0:
                grp = self._group_keys_for(clip)        # Q154：锁住的组按整组加减
                grp_ovs = self._group_overlays_for(clip)
                if key in self._clip_sel:
                    drop = list(grp or [key])
                    if linked:
                        drop = self._expand_av_link_keys(drop)
                    drop = set(drop)
                    self._clip_sel = [k for k in self._clip_sel if k not in drop]
                    if grp_ovs:
                        self._ov_multi = [o for o in (self._ov_multi or []) if not any(o is g for g in grp_ovs)]
                else:
                    add = list(grp or [key])
                    if linked:
                        add = self._expand_av_link_keys(add)
                    for k in add:
                        if k not in self._clip_sel:
                            self._clip_sel.append(k)
                    for o in grp_ovs:
                        if not any(x is o for x in (self._ov_multi or [])):
                            self._ov_multi.append(o)
        else:
            self._reset_selection()
            if cidx >= 0:
                keys = self._expand_group_keys([key])
                self._clip_sel = self._expand_av_link_keys(keys) if linked else keys
        self.selected_video_clip = (track, clip)
        self.selected_track = track
        self._publish_selection()

    def _select_audio_index(self, track, clip, idx, ctrl=False, keep_group=False, linked=True):
        tid = str(getattr(track, "id", "") or "")
        cidx = self._clip_index_of(track, clip)
        key = (tid, cidx)
        if keep_group and not ctrl and key in self._clip_sel:
            self._reset_selection(keep_ctrl=True)
        elif ctrl:
            self._reset_selection(keep_ctrl=True)
            if cidx >= 0:
                grp = self._group_keys_for(clip)        # Q154：锁住的组按整组加减
                if key in self._clip_sel:
                    drop = list(grp or [key])
                    if linked:
                        drop = self._expand_av_link_keys(drop)
                    drop = set(drop)
                    self._clip_sel = [k for k in self._clip_sel if k not in drop]
                else:
                    add = list(grp or [key])
                    if linked:
                        add = self._expand_av_link_keys(add)
                    for k in add:
                        if k not in self._clip_sel:
                            self._clip_sel.append(k)
        else:
            self._reset_selection()
            if cidx >= 0:
                keys = self._expand_group_keys([key])
                self._clip_sel = self._expand_av_link_keys(keys) if linked else keys
        self.selected_audio_idx = idx
        self.selected_track = track
        self._publish_selection()

    def _select_overlay_item(self, track, list_name, idx, ctrl=False, keep_group=False):
        rows = (self.task_data or {}).get(list_name) or []
        ov = rows[idx] if 0 <= idx < len(rows) else None
        members = [o for _n, o in self._sel_overlays()]
        if ov is not None and ctrl:
            self._reset_selection(keep_ctrl=True)
            # Q145：Ctrl+点文字 / 图片 / 调整图层 = 加入 / 移出多选（同时保留已选视频片段）
            if any(o is ov for o in members):
                members = [o for o in members if o is not ov]
            else:
                members.append(ov)
            self._ov_multi = members
        elif ov is not None and keep_group and (len(members) > 1 or self._clip_sel) and any(o is ov for o in members):
            self._reset_selection(keep_ctrl=True)
            self._ov_multi = members
        else:
            self._reset_selection()
            if ov is not None and _CG.is_locked_member(ov):
                self._clip_sel = self._group_keys_for(ov)
                self._ov_multi = self._group_overlays_for(ov)
            else:
                self._ov_multi = []
        self.selected_overlay = (list_name, idx)
        if ctrl and ov is not None and not any(o is ov for o in self._ov_multi):
            # 刚被移出多选：主选中换成组里最后一个（组空了就不选）
            self.selected_overlay = None
            if self._ov_multi:
                last = self._ov_multi[-1]
                for name in ("text_overlays", "image_overlays"):
                    lst = (self.task_data or {}).get(name) or []
                    j = next((k for k, o in enumerate(lst) if o is last), -1)
                    if j >= 0:
                        self.selected_overlay = (name, j)
                        break
        self.selected_track = track
        self._publish_selection()
        self._ov_group_begin(ov)

    def _arm_transition(self, track, trans):
        self._reset_selection()
        self.selected_transition = (track, trans)
        self.selected_track = track
        self._publish_selection()

    def _select_subtitle_index(self, clicked_idx, ctrl=False, keep_group=False):
        """Exclusive subtitle selection. keep_group retains a multi-select
        when the click is inside it (so the group can still be dragged)."""
        if ctrl:
            kept = set(self.selected_indices)
            self._reset_selection()
            self.selected_indices.update(kept)
            if clicked_idx in self.selected_indices:
                self.selected_indices.remove(clicked_idx)
            else:
                self.selected_indices.add(clicked_idx)
        elif keep_group and clicked_idx in self.selected_indices:
            kept = set(self.selected_indices)
            self._reset_selection()
            self.selected_indices.update(kept)
        else:
            self._reset_selection()
            self.selected_indices.add(clicked_idx)
        self.selected_track = self._resolve_track_obj("subs")
        self._publish_selection()

    def select_all_focused(self):
        tr = self.selected_track
        t_type = getattr(tr, "type", None) if tr is not None else None
        segs = (self.task_data or {}).get("segments") or []
        if t_type in (None, "subs") or (self.selected_indices and t_type == "subs"):
            self.selected_indices = set(range(len(segs)))
            self.selected_video_clip = None
            self._clip_sel = []
        elif t_type in ("video", "audio") and tr is not None:
            tid = str(getattr(tr, "id", ""))
            clips = getattr(tr, "clips", []) or []
            self._clip_sel = [(tid, i) for i in range(len(clips))]
            self.selected_indices.clear()
            if t_type == "video" and clips:
                self.selected_video_clip = (tr, clips[0])
            elif t_type == "audio" and clips:
                ac_list = self._get_audio_clip_list()
                self.selected_audio_idx = 0 if ac_list else None
        self._publish_selection()
        self.update()

    def clear_selection(self):
        self._reset_selection()
        self._hover_seam = None
        self.update()

    def set_snap_enabled(self, on: bool) -> None:
        self.snap_enabled = bool(on)
        try:
            from cutora.core.history import _load_app_settings, _save_app_settings
            data = _load_app_settings() or {}
            data["timeline_snap"] = self.snap_enabled
            data["timeline_snap_kinds"] = dict(getattr(self, "snap_kinds", {}) or {})
            _save_app_settings(data)
        except Exception:
            pass
        if not self.snap_enabled:
            self._snap_guide_t = None
        self.update()

    def set_snap_kind(self, kind: str, on: bool) -> None:
        kinds = dict(getattr(self, "snap_kinds", None) or {})
        kinds[str(kind)] = bool(on)
        self.snap_kinds = kinds
        self._snap_edge_cache = None
        try:
            from cutora.core.history import _load_app_settings, _save_app_settings
            data = _load_app_settings() or {}
            data["timeline_snap_kinds"] = dict(kinds)
            _save_app_settings(data)
        except Exception:
            pass
        self.update()

    def set_tool(self, name: str) -> None:
        from cutora.ui.timeline.tools import TOOLS, TOOL_SELECT
        self.tool = name if name in TOOLS else TOOL_SELECT
        self.tool_changed.emit(self.tool)
        self.update()

    def _razor_targets(self) -> list:
        out = []
        if self.selected_video_clip is not None:
            tr, clip = self.selected_video_clip
            kind = "audio" if getattr(tr, "type", None) == "audio" else "video"
            out.append({"kind": kind, "clip": clip, "track": tr})
        if self.selected_audio_idx is not None:
            ac = self._get_audio_clip_list()
            if 0 <= self.selected_audio_idx < len(ac):
                clip, _path = ac[self.selected_audio_idx]
                tr = self._track_of_clip(clip)
                out.append({"kind": "audio", "clip": clip, "track": tr})
        ov = self.selected_overlay
        if ov is not None:
            name, idx = ov
            kind = "text" if name == "text_overlays" else "image"
            out.append({"kind": kind, "idx": int(idx)})
        for i in sorted(self.selected_indices):
            out.append({"kind": "subtitle", "idx": int(i)})
        return out

    def _razor_command_params(self) -> dict:
        t = float(self.position or 0) / 1000.0
        clips, overlays, subtitles = [], [], []
        seen_clips = set()
        if self.selected_video_clip is not None:
            tr, clip = self.selected_video_clip
            bag = list(getattr(tr, "clips", []) or [])
            if clip in bag:
                clips.append({"track_id": str(getattr(tr, "id", "")), "clip_index": bag.index(clip)})
                seen_clips.add(id(clip))
        if self.selected_audio_idx is not None:
            ac = self._get_audio_clip_list()
            if 0 <= self.selected_audio_idx < len(ac):
                clip, _path = ac[self.selected_audio_idx]
                tr = self._track_of_clip(clip)
                bag = list(getattr(tr, "clips", []) or []) if tr is not None else []
                if clip in bag and id(clip) not in seen_clips:
                    clips.append({"track_id": str(getattr(tr, "id", "")), "clip_index": bag.index(clip)})
        ov = self.selected_overlay
        if ov is not None:
            name, idx = ov
            kind = "text" if name == "text_overlays" else "image"
            overlays.append({"kind": kind, "idx": int(idx)})
        for i in sorted(self.selected_indices):
            subtitles.append({"idx": int(i)})
        return {"time": t, "clips": clips, "overlays": overlays, "subtitles": subtitles}

    def razor_at_playhead(self) -> int:
        payload = self._razor_command_params()
        if not (payload.get("clips") or payload.get("overlays") or payload.get("subtitles")):
            return 0
        if self._bus_execute("timeline.razor", payload):
            self.update()
            return 1
        return 0

    def _update_hover_edge(self, event):
        real_x = event.pos().x() + self.scroll_x
        y = event.pos().y()
        handle_hit = self._hit_transition_handle(real_x, y)
        if handle_hit is not None:
            self.setCursor(Qt.CursorShape.SizeHorCursor)
            return
        prev_seam = getattr(self, "_hover_seam", None)
        seam_hit = self._hit_seam_at(real_x, y)
        if seam_hit is not None and seam_hit[0] == "seam":
            new_seam = (seam_hit[1], int(seam_hit[2]), int(seam_hit[3]), float(seam_hit[4]))
            self._hover_seam = new_seam
            self._hover_edge = None
            self.unsetCursor()
            if new_seam != prev_seam:
                self.update()
            return
        if prev_seam is not None:
            self._hover_seam = None
        prev = self._hover_edge
        hit = None
        for (t_obj, row_y, row_h) in self._track_positions().get("rows", []):
            if y < row_y or y > row_y + row_h:
                continue
            t_type = getattr(t_obj, "type", "")
            if t_type in ("video", "audio"):
                for clip_obj in getattr(t_obj, "clips", []) or []:
                    start_t = float(getattr(clip_obj, "start_t", 0.0))
                    eff = float(getattr(clip_obj, "effective_duration", 0.0) or 0.0)
                    if eff <= 0:
                        eff = max(0.0, float(getattr(clip_obj, "out_t", 0.0))
                                  - float(getattr(clip_obj, "in_t", 0.0)))
                    sx = start_t * self.pixels_per_second
                    sw = eff * self.pixels_per_second
                    if t_type == "audio":
                        tid = str(getattr(t_obj, "id", "") or "")
                        cidx = self._clip_index_of(t_obj, clip_obj)
                        is_sel = (tid, cidx) in (getattr(self, "_clip_sel", None) or [])
                        if is_sel:
                            fade_side = self._audio_fade_hit(sx, sw, real_x)
                            if fade_side == "in":
                                hit = ("clip", clip_obj, "fade_in")
                            elif fade_side == "out":
                                hit = ("clip", clip_obj, "fade_out")
                    if hit is None:
                        if abs(real_x - sx) <= self.HANDLE_PX and sw >= 1:
                            hit = ("clip", clip_obj, "left")
                        elif abs(real_x - (sx + sw)) <= self.HANDLE_PX and sw >= 1:
                            hit = ("clip", clip_obj, "right")
                        elif sx <= real_x <= sx + sw:
                            hit = ("clip", clip_obj, "body")
                    if hit:
                        break
            elif t_type == "text":
                for _i, ov in self._get_overlays_for_track(t_obj, "text_overlays"):
                    sx = float(ov.get("start") or 0.0) * self.pixels_per_second
                    sw = (float(ov.get("end") or 0.0) - float(ov.get("start") or 0.0)) * self.pixels_per_second
                    if sx <= real_x <= sx + sw:
                        hit = ("clip", ov, "body")
                        break
            elif t_type in ("image", "adjust"):
                for _i, ov in self._get_overlays_for_track(t_obj, "image_overlays"):
                    sx = float(ov.get("start") or 0.0) * self.pixels_per_second
                    sw = (float(ov.get("end") or 0.0) - float(ov.get("start") or 0.0)) * self.pixels_per_second
                    if sx <= real_x <= sx + sw:
                        hit = ("clip", ov, "body")
                        break
            elif t_type in ("subs", "subtitle"):
                segs = list((self.task_data or {}).get("segments") or [])
                for item in self._normalize_segments():
                    idx = item.get("idx")
                    if not (0 <= int(idx) < len(segs)):
                        continue
                    if item["sx"] <= real_x <= item["ex"]:
                        hit = ("clip", segs[int(idx)], "body")
                        break
            if hit:
                break
        if hit is None:
            word = self._hit_word_edge(real_x, event.pos().y())
            if word is not None:
                hit = ("word", word[0], word[2])
        self._hover_edge = hit
        if hit and hit[2] in ("left", "right", "fade_in", "fade_out"):
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif prev:
            self.unsetCursor()
        if hit != prev:
            self.update()

    def _snap_active(self, event) -> bool:
        if not getattr(self, "snap_enabled", True):
            return False
        if getattr(self, "_snap_hold_off", False):
            return False
        return True

    def _snap_skip_keys(self):
        skip = set()
        if self.selected_video_clip is not None:
            tr, clip = self.selected_video_clip
            skip.add(("clip", str(getattr(tr, "id", "")),
                      self._clip_index_of(tr, clip)))
        if self.selected_audio_idx is not None:
            ac_list = self._get_audio_clip_list()
            if 0 <= self.selected_audio_idx < len(ac_list):
                clip, _path = ac_list[self.selected_audio_idx]
                tr = self._track_of_clip(clip)
                skip.add(("clip", str(getattr(tr, "id", "")),
                          self._clip_index_of(tr, clip)))
        for tid, idx in getattr(self, "_clip_sel", []) or []:
            skip.add(("clip", str(tid), int(idx)))
        if self._active_resize_idx is not None:
            skip.add(("seg", int(self._active_resize_idx)))
        for idx in self.selected_indices or []:
            skip.add(("seg", int(idx)))
        if self.selected_overlay is not None:
            skip.add(("ov", self.selected_overlay[0], int(self.selected_overlay[1])))
        return skip

    def _snap_edges(self):
        if self._snap_edge_cache is not None:
            return self._snap_edge_cache
        tl = _tl_of(self.task_data)
        clips_by_track = []
        if tl is not None:
            for tr in getattr(tl, "tracks", []) or []:
                clips_by_track.append((
                    str(getattr(tr, "id", "")),
                    list(getattr(tr, "clips", []) or []),
                ))
        segs = list((self.task_data or {}).get("segments") or [])
        overlays = [
            ("text_overlays", list((self.task_data or {}).get("text_overlays") or [])),
            ("image_overlays", list((self.task_data or {}).get("image_overlays") or [])),
        ]
        playhead = float(self.position or 0) / 1000.0
        kinds = getattr(self, "snap_kinds", None) or {}
        want_playhead = bool(kinds.get("playhead", True))
        want_marker = bool(kinds.get("marker", True))
        skip = self._snap_skip_keys()
        silence_times = []
        if kinds.get("silence", True):
            silence_times = self._waveform_silence_targets(skip)
        edges = snap_mod.collect_edges(
            clips_by_track=clips_by_track if kinds.get("clip", True) else [],
            segments=segs if kinds.get("clip", True) else [],
            overlays=overlays if kinds.get("clip", True) else [],
            playhead_s=playhead if want_playhead else None,
            markers=snap_mod.marker_times(self.task_data) if want_marker else None,
            in_point=(self.task_data or {}).get("in_point"),
            out_point=(self.task_data or {}).get("out_point"),
            skip=skip,
            silence_times=silence_times,
        )
        if not kinds.get("silence", True):
            edges = [e for e in edges if e.get("kind") != "silence"]
        if not kinds.get("clip", True):
            edges = [e for e in edges if not str(e.get("kind") or "").startswith(
                ("clip_", "seg_", "ov_"))]
        self._snap_edge_cache = edges
        return self._snap_edge_cache

    def _waveform_silence_targets(self, skip):
        """Silence start/end on audio clips, skipping the clip being dragged."""
        from cutora.audio.waveform import get_waveform_cache, silence_boundaries
        cache = get_waveform_cache()
        out = []
        tl = _tl_of(self.task_data)
        if tl is None:
            return out
        skip = set(skip or ())
        for tr in getattr(tl, "tracks", []) or []:
            if getattr(tr, "type", None) != "audio":
                continue
            tid = str(getattr(tr, "id", ""))
            for i, clip in enumerate(getattr(tr, "clips", []) or []):
                if ("clip", tid, i) in skip:
                    continue
                sid = getattr(clip, "source_id", None)
                if not sid:
                    continue
                peaks = cache.get_cached(str(sid))
                if peaks is None:
                    continue
                in_t = float(getattr(clip, "in_t", 0.0) or 0.0)
                out_t = float(getattr(clip, "out_t", in_t) or in_t)
                start_t = float(getattr(clip, "start_t", 0.0) or 0.0)
                speed = max(0.01, float(getattr(clip, "speed", 1.0) or 1.0))
                for src_t in silence_boundaries(peaks, 200):
                    if src_t < in_t - 1e-6 or src_t > out_t + 1e-6:
                        continue
                    tl_t = start_t + (src_t - in_t) / speed
                    out.append((tl_t, ("silence", tid, i, round(float(src_t), 4))))
        return out

    def _audio_fade_handles_ok(self, sw) -> bool:
        return float(sw) >= self.HANDLE_PX * 2 + 16

    def _audio_fade_hit(self, sx, sw, real_x):
        """'in' / 'out' if real_x is inside the fade handle, else None.

        Hit boxes sit fully inside the trim HANDLE_PX/2 box, then 8 px.
        """
        if not self._audio_fade_handles_ok(sw):
            return None
        half = self.HANDLE_PX / 2.0
        w = float(self.FADE_HANDLE_PX)
        if sx + half <= real_x <= sx + half + w:
            return "in"
        if sx + sw - half - w <= real_x <= sx + sw - half:
            return "out"
        return None

    def _paint_audio_fade_handles(self, painter, clip_obj, rect, is_sel):
        if not is_sel:
            return
        sw = float(rect.width())
        if not self._audio_fade_handles_ok(sw):
            return
        from cutora.ui.theme.tokens import RADIUS_SM
        sx = float(rect.left())
        row_y = float(rect.top())
        row_h = float(rect.height())
        pps = max(1e-6, float(self.pixels_per_second))
        fi = float(getattr(clip_obj, "fade_in_dur", 0.0) or 0.0)
        fo = float(getattr(clip_obj, "fade_out_dur", 0.0) or 0.0)
        shade = QColor(self._theme().fg0)
        shade.setAlpha(80)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(shade)
        if fi > 0:
            fw = min(sw, fi * pps)
            if fw > 0.5:
                painter.drawPolygon(QPolygonF([
                    QPointF(sx, row_y),
                    QPointF(sx + fw, row_y),
                    QPointF(sx, row_y + row_h),
                ]))
        if fo > 0:
            fw = min(sw, fo * pps)
            if fw > 0.5:
                painter.drawPolygon(QPolygonF([
                    QPointF(sx + sw, row_y),
                    QPointF(sx + sw - fw, row_y),
                    QPointF(sx + sw, row_y + row_h),
                ]))
        half = self.HANDLE_PX / 2.0
        w = float(self.FADE_HANDLE_PX)
        handle_color = QColor(self._theme().accent)
        handle_color.setAlpha(170)
        painter.setBrush(handle_color)
        painter.drawRoundedRect(QRectF(sx + half, row_y, w, row_h), RADIUS_SM, RADIUS_SM)
        painter.drawRoundedRect(
            QRectF(sx + sw - half - w, row_y, w, row_h), RADIUS_SM, RADIUS_SM)

    def _begin_audio_fade_drag(self, clip, side, sx, sw, clip_dur):
        self.mode = "fade_audio_in" if side == "in" else "fade_audio_out"
        self._fade_drag_clip = clip
        self._fade_drag_orig = (
            float(getattr(clip, "fade_in_dur", 0.0) or 0.0),
            float(getattr(clip, "fade_out_dur", 0.0) or 0.0),
        )
        self._fade_drag_sx = float(sx)
        self._fade_drag_sw = float(sw)
        self._fade_drag_dur = max(0.0, float(clip_dur))
        self.setCursor(Qt.CursorShape.SizeHorCursor)

    def _apply_audio_fade_live(self, real_x):
        clip = getattr(self, "_fade_drag_clip", None)
        if clip is None:
            return
        pps = max(1e-6, float(self.pixels_per_second))
        half = self.HANDLE_PX / 2.0
        max_f = 0.45 * self._fade_drag_dur
        if self.mode == "fade_audio_in":
            clip.fade_in_dur = max(0.0, min(max_f, (real_x - (self._fade_drag_sx + half)) / pps))
        else:
            clip.fade_out_dur = max(
                0.0,
                min(max_f, ((self._fade_drag_sx + self._fade_drag_sw - half) - real_x) / pps),
            )
        self.update()

    def _commit_audio_fade_drag(self):
        clip = getattr(self, "_fade_drag_clip", None)
        orig = getattr(self, "_fade_drag_orig", None)
        self._fade_drag_clip = None
        self._fade_drag_orig = None
        if clip is None or orig is None:
            return
        final_fi = float(getattr(clip, "fade_in_dur", 0.0) or 0.0)
        final_fo = float(getattr(clip, "fade_out_dur", 0.0) or 0.0)
        clip.fade_in_dur = orig[0]
        clip.fade_out_dur = orig[1]
        if abs(final_fi - orig[0]) < 1e-6 and abs(final_fo - orig[1]) < 1e-6:
            return
        tr = self._track_of_clip(clip)
        if tr is None:
            clip.fade_in_dur = final_fi
            clip.fade_out_dur = final_fo
            return
        ok = self._bus_execute("timeline.set_clip_fades", {
            "track_id": str(getattr(tr, "id", "")),
            "clip_index": self._clip_index_of(tr, clip),
            "fade_in_dur": final_fi,
            "fade_out_dur": final_fo,
        })
        if not ok:
            clip.fade_in_dur = orig[0]
            clip.fade_out_dur = orig[1]

    def _track_of_clip(self, clip):
        tl = _tl_of(self.task_data)
        if tl is None or clip is None:
            return None
        for tr in getattr(tl, "tracks", []) or []:
            if clip in (getattr(tr, "clips", []) or []):
                return tr
        return None

    def _clip_neighbors(self, track, clip):
        out = []
        if track is None:
            return out
        for c in getattr(track, "clips", []) or []:
            if c is clip:
                continue
            out.append(snap_mod.clip_span(c))
        return out

    def _seg_neighbors(self, idx, extra_skip=None):
        segs = list((self.task_data or {}).get("segments") or [])
        skip = set(extra_skip or ())
        skip.add(int(idx))
        out = []
        for i, s in enumerate(segs):
            if i in skip or not isinstance(s, dict):
                continue
            out.append((float(s.get("start") or 0.0), float(s.get("end") or 0.0)))
        return out

    def _apply_snap_move(self, start, end, neighbors, event):
        if not self._snap_active(event):
            self._snap_guide_t = None
            return start
        thr = snap_mod.threshold_sec(self.pixels_per_second)
        new_start, hit = snap_mod.snap_move(
            start, end, self._snap_edges(), thr, neighbors,
            min_dur=self.MIN_DUR)
        self._snap_guide_t = None if hit is None else float(hit["t"])
        return new_start

    def _apply_snap_trim(self, proposed, other, is_start, neighbors, event):
        if not self._snap_active(event):
            self._snap_guide_t = None
            return proposed
        thr = snap_mod.threshold_sec(self.pixels_per_second)
        snapped, hit = snap_mod.snap_trim(
            proposed, other, is_start, self._snap_edges(), thr, neighbors,
            min_dur=self.MIN_DUR)
        self._snap_guide_t = None if hit is None else float(hit["t"])
        return snapped

    def _clip_index_of(self, track, clip):
        for i, c in enumerate(getattr(track, "clips", []) or []):
            if c is clip:
                return i
        return -1

    def _snapshot_clip(self, track, clip):
        self._clip_drag_orig = {
            "track_id": getattr(track, "id", None),
            "clip_index": self._clip_index_of(track, clip),
            "in_t": float(getattr(clip, "in_t", 0.0)),
            "out_t": float(getattr(clip, "out_t", 0.0)),
            "start_t": float(getattr(clip, "start_t", 0.0)),
            "speed": float(getattr(clip, "speed", 1.0)),
            "is_intro": bool(getattr(clip, "is_intro_video_leadin", False)),
        }

    def _restore_clip_orig(self):
        orig = getattr(self, "_clip_drag_orig", None)
        if not orig:
            return None
        tl = _tl_of(self.task_data)
        if tl is None:
            return None
        for tr in getattr(tl, "tracks", []) or []:
            if getattr(tr, "id", None) != orig.get("track_id"):
                continue
            clips = getattr(tr, "clips", []) or []
            i = int(orig.get("clip_index", -1))
            if 0 <= i < len(clips):
                c = clips[i]
                c.in_t = orig["in_t"]
                c.out_t = orig["out_t"]
                c.start_t = orig["start_t"]
                c.speed = orig["speed"]
                return tr, c
        return None

    def _track_is_audio(self, tr) -> bool:
        return getattr(tr, "type", None) == "audio"

    def _begin_av_linked_trim(self, clip, side, *, audio=False):
        """Snapshot AV partners currently in the selection for same-edge live trim."""
        from cutora.model.av_link import clip_timeline_span, partners
        self._av_trim_side = side
        self._av_trim_primary_audio = bool(audio)
        self._av_trim_primary_span = clip_timeline_span(clip, audio=bool(audio))
        if bool(getattr(clip, "is_intro_video_leadin", False)):
            self._av_trim_partners = None
            return
        index = getattr(self, "_av_link_index", None) or {}
        partner_ids = {id(c) for _t, c in partners(index, clip)}
        rows = []
        for tr, c in self._sel_clips():
            if c is clip or id(c) not in partner_ids:
                continue
            rows.append({
                "track": tr, "clip": c, "audio": self._track_is_audio(tr),
                "in_t": float(getattr(c, "in_t", 0.0)),
                "out_t": float(getattr(c, "out_t", 0.0)),
                "start_t": float(getattr(c, "start_t", 0.0)),
                "speed": float(getattr(c, "speed", 1.0) or 1.0),
            })
        self._av_trim_partners = rows or None

    def _apply_av_trim_partners_live(self, clip):
        from cutora.model.av_link import apply_edge_delta, clip_timeline_span
        rows = self._av_trim_partners or []
        orig = self._av_trim_primary_span
        if not rows or not orig:
            return
        start, end = clip_timeline_span(clip, audio=bool(self._av_trim_primary_audio))
        if str(self._av_trim_side) in ("right", "end"):
            delta = end - orig[1]
        else:
            delta = start - orig[0]
        for row in rows:
            c = row["clip"]
            c.in_t = row["in_t"]
            c.out_t = row["out_t"]
            c.start_t = row["start_t"]
            apply_edge_delta(
                c, self._av_trim_side, delta,
                audio=row["audio"], min_dur=self.MIN_DUR)

    def _av_trim_partner_finals(self):
        items = []
        for row in self._av_trim_partners or []:
            tr, c = row["track"], row["clip"]
            bag = list(getattr(tr, "clips", []) or [])
            if c not in bag:
                continue
            items.append({
                "track_id": str(getattr(tr, "id", "")),
                "clip_index": bag.index(c),
                "in_t": float(getattr(c, "in_t", 0.0)),
                "out_t": float(getattr(c, "out_t", 0.0)),
                "start_t": float(getattr(c, "start_t", 0.0)),
                "speed": float(getattr(c, "speed", 1.0) or 1.0),
            })
        return items

    def _restore_av_trim_partners(self):
        for row in self._av_trim_partners or []:
            c = row["clip"]
            c.in_t = row["in_t"]
            c.out_t = row["out_t"]
            c.start_t = row["start_t"]
            c.speed = row["speed"]

    def _clear_av_trim(self):
        self._av_trim_partners = None
        self._av_trim_side = None
        self._av_trim_primary_span = None
        self._av_trim_primary_audio = False

    def _rebuild_av_link_index(self):
        from cutora.model.av_link import index_link_groups
        tl = _tl_of(self.task_data)
        self._av_link_index = index_link_groups(tl) if tl is not None else {}

    def _bus_execute(self, name, params):
        try:
            from cutora.core.commands import get_bus
            get_bus().execute(name, params, source="ui")
            if str(name or "").startswith(("timeline.", "audio.")):
                self._rebuild_av_link_index()
            return True
        except Exception as exc:
            _log.error("command %s failed: %s", name, exc)
            win = self.window()
            bar = getattr(win, "statusBar", None) if win is not None else None
            if callable(bar):
                bar().showMessage("命令失败：%s（%s）" % (name, exc), 4000)
            return False

    def _bus_execute_result(self, name, params):
        try:
            from cutora.core.commands import get_bus
            out = get_bus().execute(name, params, source="ui")
            if str(name or "").startswith(("timeline.", "audio.")):
                self._rebuild_av_link_index()
            return out
        except Exception as exc:
            _log.error("command %s failed: %s", name, exc)
            win = self.window()
            bar = getattr(win, "statusBar", None) if win is not None else None
            if callable(bar):
                bar().showMessage("命令失败：%s（%s）" % (name, exc), 4000)
            return None

    def _alt_duplicate_live(self, track, clip):
        """In-memory clone for Alt-drag preview. Not a command; commit later."""
        if track is None or clip is None:
            return track, clip
        bag = list(getattr(track, "clips", []) or [])
        if clip not in bag:
            return track, clip
        clone = clip.clone()
        import uuid as _uuid
        clone.uid = _uuid.uuid4().hex          # Q154：Alt 复制出来的是新片段，不带进原来的组
        clone.group = {}
        clone.link_group_id = _uuid.uuid4().hex  # Q204a：副本新 gid，避免三角链接
        track.clips.append(clone)
        return track, clone

    def _maybe_alt_copy_live(self, real_x, kind) -> bool:
        """Return False while Alt-copy is waiting for the drag threshold."""
        if not getattr(self, "_alt_copy_pending", False):
            return True
        start_x = (self._video_drag_start_x if kind == "video"
                   else self._audio_drag_start_x)
        if abs(float(real_x) - float(start_x)) < float(self.ALT_COPY_PX):
            return False
        self._alt_copy_pending = False
        if kind == "video" and self.selected_video_clip is not None:
            tr, clip = self.selected_video_clip
            tr, clone = self._alt_duplicate_live(tr, clip)
            self.selected_video_clip = (tr, clone)
            self._drag_target_track = tr
            self._alt_copy_did = True
        elif kind == "audio" and self.selected_audio_idx is not None:
            ac_list = self._get_audio_clip_list()
            if 0 <= self.selected_audio_idx < len(ac_list):
                clip, _path = ac_list[self.selected_audio_idx]
                tr = self._track_of_clip(clip)
                tr, clone = self._alt_duplicate_live(tr, clip)
                ac_list = self._get_audio_clip_list()
                for ai, (c, _) in enumerate(ac_list):
                    if c is clone:
                        self.selected_audio_idx = ai
                        break
                self._drag_target_track = tr
                self._alt_copy_did = True
        return True

    def _commit_alt_copy(self):
        """Drop the live clone and emit one duplicate_clips at the final time."""
        src = getattr(self, "_alt_copy_src", None) or {}
        final_start = None
        clone = None
        dest = None
        if self.selected_video_clip is not None:
            dest, clone = self.selected_video_clip
            final_start = float(getattr(clone, "start_t", 0.0) or 0.0)
        elif self.selected_audio_idx is not None:
            ac_list = self._get_audio_clip_list()
            if 0 <= self.selected_audio_idx < len(ac_list):
                clone = ac_list[self.selected_audio_idx][0]
                dest = self._track_of_clip(clone)
                final_start = float(getattr(clone, "start_t", 0.0) or 0.0)
        if clone is not None and dest is not None:
            bag = list(getattr(dest, "clips", []) or [])
            if clone in bag:
                dest.clips.remove(clone)
        tid = str(src.get("track_id") or "")
        cidx = int(src.get("clip_index", -1))
        if not tid or cidx < 0 or final_start is None:
            return
        self._bus_execute("timeline.duplicate_clips", {
            "clips": [{"track_id": tid, "clip_index": cidx}],
            "time": final_start,
        })

    def _commit_clip_drag(self):
        """Restore pre-drag clip, then one command so undo is the mouse-up."""
        if getattr(self, "_alt_copy_did", False):
            self._commit_alt_copy()
            self._alt_copy_did = False
            self._alt_copy_pending = False
            self._alt_copy_src = None
            return
        self._alt_copy_pending = False
        self._alt_copy_src = None
        orig = getattr(self, "_clip_drag_orig", None)
        if not orig or int(orig.get("clip_index", -1)) < 0:
            return
        clip = None
        if self.selected_video_clip is not None:
            clip = self.selected_video_clip[1]
        elif self.selected_audio_idx is not None:
            ac_list = self._get_audio_clip_list()
            if 0 <= self.selected_audio_idx < len(ac_list):
                clip = ac_list[self.selected_audio_idx][0]
        if clip is None:
            return
        final = {
            "in_t": float(getattr(clip, "in_t", 0.0)),
            "out_t": float(getattr(clip, "out_t", 0.0)),
            "start_t": float(getattr(clip, "start_t", 0.0)),
            "speed": float(getattr(clip, "speed", 1.0)),
        }
        dest = self._drag_target_track
        dest_id = getattr(dest, "id", None) if dest is not None else orig["track_id"]
        changed = any(
            abs(final[k] - float(orig[k])) > 1e-6
            for k in ("in_t", "out_t", "start_t", "speed"))
        track_changed = bool(dest_id) and dest_id != orig["track_id"]
        partner_items = self._av_trim_partner_finals()
        if not changed and not track_changed:
            self._restore_av_trim_partners()
            self._clear_av_trim()
            return
        self._restore_clip_orig()
        self._restore_av_trim_partners()
        if orig.get("is_intro"):
            ok = self._bus_execute("timeline.ripple_intro", {
                "in_t": final["in_t"],
                "out_t": final["out_t"],
                "start_t": final["start_t"],
                "intro_duration": max(0.01, final["out_t"] - final["in_t"]),
            })
            self._clear_av_trim()
            if not ok:
                self._restore_clip_orig()
            return
        if track_changed:
            ok = self._bus_execute("timeline.move_clip_to_track", {
                "track_id": orig["track_id"],
                "clip_index": orig["clip_index"],
                "dest_track_id": dest_id,
                "start": final["start_t"],
            })
            self._clear_av_trim()
            if not ok:
                self._restore_clip_orig()
                return
            if self.selected_video_clip is not None and dest is not None:
                self.selected_video_clip = (dest, clip)
                self.selected_track = dest
            elif self.selected_audio_idx is not None:
                new_list = self._get_audio_clip_list()
                for i, (c, _) in enumerate(new_list):
                    if c is clip:
                        self.selected_audio_idx = i
                        break
                self.selected_track = dest
            return
        trim_changed = any(
            abs(final[k] - float(orig[k])) > 1e-6
            for k in ("in_t", "out_t", "speed"))
        if trim_changed:
            primary = {
                "track_id": orig["track_id"],
                "clip_index": orig["clip_index"],
                "in_t": final["in_t"],
                "out_t": final["out_t"],
                "start_t": final["start_t"],
                "speed": final["speed"],
            }
            if partner_items:
                ok = self._bus_execute("timeline.trim_clips", {
                    "clips": [primary] + partner_items,
                })
            else:
                ok = self._bus_execute("timeline.trim_clip", primary)
            if not ok:
                self._restore_clip_orig()
                self._restore_av_trim_partners()
            self._clear_av_trim()
            return
        ok = self._bus_execute("timeline.move_clips", {
            "clips": [{
                "track_id": orig["track_id"],
                "clip_index": orig["clip_index"],
                "start": final["start_t"],
            }],
        })
        self._clear_av_trim()
        if not ok:
            self._restore_clip_orig()

    def _restore_segments_orig(self):
        segs = (self.task_data or {}).get("segments") or []
        for idx, orig in (self.original_segments_state or {}).items():
            if 0 <= int(idx) < len(segs) and isinstance(segs[int(idx)], dict):
                seg = segs[int(idx)]
                seg["start"] = orig["start"]
                seg["end"] = orig["end"]
                seg["words"] = copy.deepcopy(orig.get("words") or [])

    def _reset_seg_from_orig(self, idx):
        orig = (self.original_segments_state or {}).get(idx)
        segs = (self.task_data or {}).get("segments") or []
        if orig is None or not (0 <= idx < len(segs)) or not isinstance(segs[idx], dict):
            return None
        seg = segs[idx]
        seg["start"] = orig["start"]
        seg["end"] = orig["end"]
        seg["words"] = copy.deepcopy(orig.get("words") or [])
        return seg

    def _commit_segment_drag(self):
        """Restore pre-drag segments, then one command so undo is the mouse-up."""
        segs = (self.task_data or {}).get("segments") or []
        origs = self.original_segments_state or {}
        if not origs:
            return
        mode = self.mode
        if mode in ("resize_left", "resize_right"):
            idx = self._active_resize_idx
            if idx is None or not (0 <= idx < len(segs)):
                return
            final_start = float(segs[idx].get("start") or 0.0)
            final_end = float(segs[idx].get("end") or 0.0)
            orig = origs.get(idx) or {}
            if (abs(final_start - float(orig.get("start") or 0.0)) < 1e-6
                    and abs(final_end - float(orig.get("end") or 0.0)) < 1e-6):
                return
            self._restore_segments_orig()
            if not self._bus_execute("subtitle.trim_segment", {
                "seg_id": int(idx),
                "start": final_start,
                "end": final_end,
            }):
                self._restore_segments_orig()
            return
        if mode == "move_multi":
            items = []
            changed = False
            for idx, orig in origs.items():
                if not (0 <= int(idx) < len(segs)):
                    continue
                start = float(segs[int(idx)].get("start") or 0.0)
                if abs(start - float(orig.get("start") or 0.0)) > 1e-6:
                    changed = True
                items.append({"seg_id": int(idx), "start": start})
            if not changed or not items:
                return
            self._restore_segments_orig()
            if not self._bus_execute("subtitle.move_segments", {"segments": items}):
                self._restore_segments_orig()
            return
        if mode in ("retime_left", "retime_right"):
            pivot = getattr(self, "_retime_pivot", None)
            scale = getattr(self, "_retime_scale", None)
            if pivot is None or scale is None or abs(float(scale) - 1.0) < 1e-6:
                return
            ids = [int(i) for i in origs.keys()]
            self._restore_segments_orig()
            if not self._bus_execute("subtitle.retime_segments", {
                "seg_ids": ids,
                "pivot": float(pivot),
                "scale": float(scale),
            }):
                self._restore_segments_orig()

    def mousePressEvent(self, event):
        self._snap_edge_cache = None
        from cutora.ui.timeline.tools import TOOL_RAZOR
        if getattr(self, "tool", "select") == TOOL_RAZOR and event.button() == Qt.MouseButton.LeftButton:
            if self.razor_at_playhead():
                event.accept()
                return
        # === 裁剪手柄优先拦截（在标尺区域的任意 Y 位置均可触发）===
        if isinstance(self.task_data, dict) and self.task_data:
            widget_x = event.pos().x()
            y = event.pos().y()
            # Q157：标尺上按在播放头头部（±7 px）= 抓住播放头拖（比裁剪手柄 / 标记优先，
            # 播放头停在 0 或裁剪点上时也能抓到）。按住不松一直拖，松手才请求全分辨率帧。
            if (event.button() == Qt.MouseButton.LeftButton and y <= self.RULER_H + 8
                    and abs(widget_x - self.playhead_screen_x()) <= 7):
                self._reset_selection()
                self._publish_selection()
                self.drag_start_x = widget_x + self.scroll_x
                self.mode = 'seek'
                self._scrub_to(widget_x + self.scroll_x)
                event.accept()
                return
            # 手柄命中：鼠标 X 在手柄橙色区域内（±12px 容差），Y 在标尺内
            trim_px = self.trim_start_sec * self.pixels_per_second - self.scroll_x
            if abs(widget_x - trim_px) <= 12 and y <= self.RULER_H + 8:
                self._dragging_trim = True
                self._trim_drag_start_x = widget_x + self.scroll_x   # real_x
                self._trim_drag_start_sec = self.trim_start_sec
                self.setCursor(Qt.CursorShape.SizeHorCursor)
                event.accept()
                return
            # === 裁剪尾手柄命中检测 ===
            if self.trim_end_sec > 0:
                trim_end_px = self.trim_end_sec * self.pixels_per_second - self.scroll_x
                if abs(widget_x - trim_end_px) <= 12 and y <= self.RULER_H + 8:
                    self._dragging_trim_end = True
                    self._trim_end_drag_start_x = widget_x + self.scroll_x
                    self._trim_end_drag_start_sec = self.trim_end_sec
                    self.setCursor(Qt.CursorShape.SizeHorCursor)
                    event.accept()
                    return
            hit_mk = self._hit_marker(widget_x, y)
            if hit_mk is not None:
                self._dragging_marker = hit_mk
                self._marker_drag_orig_t = float(hit_mk.get("t") or 0.0)
                event.accept()
                return
            if event.button() == Qt.MouseButton.LeftButton and y <= self.RULER_H:
                self._reset_selection()
                self._publish_selection()
                self.drag_start_x = widget_x + self.scroll_x
                self.mode = 'seek'
                self._scrub_to(widget_x + self.scroll_x)
                event.accept()
                return
        if not isinstance(self.task_data, dict) or not self.task_data:
            event.ignore()
            return
        real_x = event.pos().x() + self.scroll_x
        y = event.pos().y()
        content_h = int(self.get_content_height() or 0)
        if content_h and y > content_h:
            self._reset_selection()
            self._publish_selection()
            self.selected_track = None
            self.item_selected.emit("", None)
            self.update()
            event.accept()
            return
        self.drag_start_x = real_x
        self._drag_source_track = None
        self._drag_target_track = None

        if event.button() == Qt.MouseButton.LeftButton:
            mt = self._hit_matte_band(real_x, y)
            if mt is not None:
                # Q152-D：点 AI 抠像覆盖条上的黄段 → 跳到那段开头（片段照常选中，不 return）
                self.seek_request.emit(int(round(max(0.0, mt) * 1000.0)))
            kf_hit = self._hit_keyframe(real_x, y)
            if kf_hit is not None:
                self._dragging_keyframe = dict(kf_hit)
                self._kf_drag_orig_t = float(kf_hit["t"])
                event.accept()
                self.update()
                return

        word_hit = self._hit_word_edge(real_x, y)
        if word_hit is not None and self.can_edit_track("subs"):
            seg_i, wi, edge = word_hit
            segs = list((self.task_data or {}).get("segments") or [])
            if 0 <= seg_i < len(segs) and isinstance(segs[seg_i], dict):
                words = segs[seg_i].get("words") or []
                if 0 <= wi < len(words) and isinstance(words[wi], dict):
                    self._select_subtitle_index(seg_i)
                    self.mode = "resize_word_left" if edge == "left" else "resize_word_right"
                    orig_t = float(words[wi].get("end") if edge == "right"
                                   else words[wi].get("start") or 0.0)
                    self._word_drag_orig = {
                        "seg_id": seg_i,
                        "word_index": wi,
                        "edge": edge,
                        "t": orig_t,
                        "words": copy.deepcopy(words),
                    }
                    self.setCursor(Qt.CursorShape.SizeHorCursor)
                    self.update()
                    event.accept()
                    return

        segs = self.task_data.get('segments', [])
        tracks = self._track_positions()
        # NLE Gen-2: 动态查找轨道位置（支持多条字幕轨道）
        subs_track_at_y = None
        subs_row = None
        for t_obj_s, row_y_s, row_h_s in tracks.get("rows", []):
            if getattr(t_obj_s, "type", "") in ("subs", "subtitle") and (row_y_s <= y <= row_y_s + row_h_s):
                subs_track_at_y = t_obj_s
                subs_row = (row_y_s, row_h_s)
                break
        if subs_row is None:
            subs_row = self._row_for_type(tracks, "subs")
        layout = self._normalize_segments(track_obj=subs_track_at_y)
        subs_top = subs_row[0] if subs_row else -999
        subs_bottom = (subs_row[0] + subs_row[1]) if subs_row else -999
        clicked_idx = -1
        hit_mode = "none"

        handle_hit = self._hit_transition_handle(real_x, y)
        if handle_hit is not None:
            t_obj, trans_obj, side = handle_hit
            self._arm_transition(t_obj, trans_obj)
            self.mode = "resize_transition"
            self._trans_drag_track = t_obj
            self._trans_drag_obj = trans_obj
            self._trans_drag_side = side
            self._trans_orig_start = float(getattr(trans_obj, "start_t", 0.0) or 0.0)
            self._trans_orig_dur = float(getattr(trans_obj, "duration", 0.0) or 0.0)
            cover, lo, hi = self._transition_seam_bounds(t_obj, trans_obj)
            self._trans_drag_seam = cover
            self._trans_drag_lo = lo
            self._trans_drag_hi = hi
            self.setCursor(Qt.CursorShape.SizeHorCursor)
            self.update()
            self.item_selected.emit("transition", trans_obj)
            self.setFocus()
            return
        tape = self._hit_transition_tape(real_x, y)
        if tape is not None:
            t_obj, trans_obj = tape
            self._arm_transition(t_obj, trans_obj)
            self.update()
            self.item_selected.emit("transition", trans_obj)
            self.setFocus()
            return
        hit_seam = self._hit_seam_at(real_x, y)
        if hit_seam is not None:
            if hit_seam[0] == "transition":
                t_obj, trans_obj = hit_seam[1], hit_seam[2]
                self._arm_transition(t_obj, trans_obj)
                self.update()
                self.item_selected.emit("transition", trans_obj)
                self.setFocus()
                return
            self._select_seam(hit_seam[1], hit_seam[2], hit_seam[3], hit_seam[4])
            return
        self.selected_seam = None

        _ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        _alt = bool(event.modifiers() & Qt.KeyboardModifier.AltModifier) and not _ctrl
        _linked = not _alt

        # Video clip hit test（遍历所有 video 轨道）
        tl = _tl_of(self.task_data) if self.task_data else None
        if tl is not None:
            for (t_obj, row_y, row_h) in tracks.get("rows", []):
                if getattr(t_obj, "type", None) != "video":
                    continue
                if not getattr(t_obj, "visible", True) or getattr(t_obj, "locked", False):
                    continue
                if not (row_y <= y <= row_y + row_h):
                    continue
                for clip_obj in reversed(getattr(t_obj, "clips", []) or []):
                    start_t = float(getattr(clip_obj, "start_t", 0.0))
                    eff_dur = float(getattr(clip_obj, "effective_duration", 0.0))
                    if eff_dur <= 0:
                        eff_dur = max(0.0, float(getattr(clip_obj, "out_t", 0.0)) - float(getattr(clip_obj, "in_t", 0.0)))
                    sx = start_t * self.pixels_per_second
                    sw = eff_dur * self.pixels_per_second
                    can_resize = not _CG.is_repeat(clip_obj)      # Q154：重复份不能单独裁
                    if sw < self.HANDLE_PX * 2 and can_resize:
                        if (sx - self.HANDLE_PX / 2) <= real_x <= (sx + sw + self.HANDLE_PX / 2):
                            right_center = sx + sw
                            left_center = sx
                            self._select_video_clip(t_obj, clip_obj, ctrl=_ctrl, keep_group=True, linked=_linked)
                            self._drag_source_track = t_obj
                            self._drag_target_track = t_obj
                            self.mode = 'resize_video_right' if abs(real_x - right_center) < abs(real_x - left_center) else 'resize_video_left'
                            self._video_drag_start_x = real_x
                            self._video_drag_orig_start = start_t
                            self._store_intro_drag_state(clip_obj)
                            self._begin_av_linked_trim(clip_obj, "right" if self.mode == "resize_video_right" else "left")
                            self.update()
                            self.item_selected.emit('video_clip', clip_obj)
                            self.setFocus()
                            return
                    else:
                        right_rect = QRectF(sx + sw - self.HANDLE_PX, row_y, self.HANDLE_PX, row_h)
                        left_rect = QRectF(sx, row_y, self.HANDLE_PX, row_h)
                        if not can_resize:
                            right_rect = left_rect = QRectF()
                        if right_rect.contains(real_x, y):
                            self._select_video_clip(t_obj, clip_obj, ctrl=_ctrl, keep_group=True, linked=_linked)
                            self._drag_source_track = t_obj
                            self._drag_target_track = t_obj
                            self.mode = 'resize_video_right'
                            self._video_drag_start_x = real_x
                            self._video_drag_orig_start = start_t
                            self._store_intro_drag_state(clip_obj)
                            self._begin_av_linked_trim(clip_obj, "right")
                            self.update()
                            self.item_selected.emit('video_clip', clip_obj)
                            self.setFocus()
                            return
                        if left_rect.contains(real_x, y):
                            self._select_video_clip(t_obj, clip_obj, ctrl=_ctrl, keep_group=True, linked=_linked)
                            self._drag_source_track = t_obj
                            self._drag_target_track = t_obj
                            self.mode = 'resize_video_left'
                            self._video_drag_start_x = real_x
                            self._video_drag_orig_start = start_t
                            self._store_intro_drag_state(clip_obj)
                            self._begin_av_linked_trim(clip_obj, "left")
                            self.update()
                            self.item_selected.emit('video_clip', clip_obj)
                            self.setFocus()
                            return
                    if sx <= real_x <= sx + sw:
                        tid = str(getattr(t_obj, "id", ""))
                        cidx = self._clip_index_of(t_obj, clip_obj)
                        self._alt_copy_pending = _alt
                        self._alt_copy_did = False
                        self._alt_copy_src = {
                            "track_id": tid, "clip_index": cidx,
                        } if self._alt_copy_pending else None
                        self._select_video_clip(t_obj, clip_obj, ctrl=_ctrl, keep_group=True, linked=_linked)
                        if not self._alt_copy_pending:
                            self._clip_group_begin(clip_obj, ctrl=_ctrl)
                        self._drag_source_track = t_obj
                        self._drag_target_track = t_obj
                        self.mode = 'move_video'
                        self._video_drag_start_x = real_x
                        self._video_drag_orig_start = start_t
                        self._snapshot_clip(t_obj, clip_obj)
                        self._store_intro_drag_state(clip_obj)
                        self.update()
                        self.item_selected.emit('video_clip', clip_obj)
                        self.setFocus()
                        return

        # Audio hit test（按每条音频轨道独立检测，Audio1/Audio2/Audio3 均可点中）
        if tl is not None and self.can_edit_track("audio"):
            for (t_obj, row_y, row_h) in tracks.get("rows", []):
                if getattr(t_obj, "type", None) != "audio":
                    continue
                if not getattr(t_obj, "visible", True) or getattr(t_obj, "locked", False):
                    continue
                if not (row_y <= y <= row_y + row_h):
                    continue
                # 该轨道之前的全局音频 clip 数量
                global_offset = 0
                for t in tl.tracks:
                    if t is t_obj:
                        break
                    if getattr(t, "type", None) == "audio":
                        global_offset += len(getattr(t, "clips", []))
                track_clips = getattr(t_obj, "clips", []) or []
                # 从该轨道内最后一个 clip 往前检测（与绘制顺序一致，后绘制的在上层）
                for local_i in range(len(track_clips) - 1, -1, -1):
                    clip_obj = track_clips[local_i]
                    idx = global_offset + local_i
                    start = float(getattr(clip_obj, "start_t", 0.0))
                    in_t = float(getattr(clip_obj, "in_t", 0.0))
                    out_t = float(getattr(clip_obj, "out_t", in_t))
                    try:
                        end = start + max(0.0, float(clip_obj.effective_duration))
                    except Exception:
                        end = start + max(0.0, out_t - in_t)
                    sx = start * self.pixels_per_second
                    sw = (end - start) * self.pixels_per_second
                    left_center = sx
                    right_center = sx + sw
                    if sw < self.HANDLE_PX * 2:
                        if (sx - self.HANDLE_PX / 2) <= real_x <= (sx + sw + self.HANDLE_PX / 2):
                            self._select_audio_index(t_obj, clip_obj, idx, ctrl=_ctrl, keep_group=True, linked=_linked)
                            self._drag_source_track = t_obj
                            self._drag_target_track = t_obj
                            self.mode = 'resize_audio_right' if abs(real_x - right_center) < abs(real_x - left_center) else 'resize_audio_left'
                            self._snapshot_clip(t_obj, clip_obj)
                            self._begin_av_linked_trim(
                                clip_obj, "right" if self.mode == "resize_audio_right" else "left",
                                audio=True)
                            self.update()
                            self.item_selected.emit('audio', idx)
                            self.setFocus()
                            return
                    else:
                        right_rect = QRectF(sx + sw - self.HANDLE_PX / 2, row_y, self.HANDLE_PX, row_h)
                        left_rect = QRectF(sx - self.HANDLE_PX / 2, row_y, self.HANDLE_PX, row_h)
                        if right_rect.contains(real_x, y):
                            self._select_audio_index(t_obj, clip_obj, idx, ctrl=_ctrl, keep_group=True, linked=_linked)
                            self._drag_source_track = t_obj
                            self._drag_target_track = t_obj
                            self.mode = 'resize_audio_right'
                            self._snapshot_clip(t_obj, clip_obj)
                            self._begin_av_linked_trim(clip_obj, "right", audio=True)
                            self.update()
                            self.item_selected.emit('audio', idx)
                            self.setFocus()
                            return
                        if left_rect.contains(real_x, y):
                            self._select_audio_index(t_obj, clip_obj, idx, ctrl=_ctrl, keep_group=True, linked=_linked)
                            self._drag_source_track = t_obj
                            self._drag_target_track = t_obj
                            self.mode = 'resize_audio_left'
                            self._snapshot_clip(t_obj, clip_obj)
                            self._begin_av_linked_trim(clip_obj, "left", audio=True)
                            self.update()
                            self.item_selected.emit('audio', idx)
                            self.setFocus()
                            return
                    tid = str(getattr(t_obj, "id", "") or "")
                    is_sel = (
                        self.selected_audio_idx == idx
                        or (tid, local_i) in (getattr(self, "_clip_sel", None) or [])
                    )
                    if is_sel:
                        fade_side = self._audio_fade_hit(sx, sw, real_x)
                        if fade_side:
                            self._select_audio_index(t_obj, clip_obj, idx, ctrl=_ctrl, keep_group=True, linked=_linked)
                            self._begin_audio_fade_drag(
                                clip_obj, fade_side, sx, sw, max(0.0, end - start))
                            self.update()
                            self.item_selected.emit('audio', idx)
                            self.setFocus()
                            return
                    if sx <= real_x <= sx + sw:
                        tid = str(getattr(t_obj, "id", ""))
                        cidx = self._clip_index_of(t_obj, clip_obj)
                        self._alt_copy_pending = _alt
                        self._alt_copy_did = False
                        self._alt_copy_src = {
                            "track_id": tid, "clip_index": cidx,
                        } if self._alt_copy_pending else None
                        self._select_audio_index(t_obj, clip_obj, idx, ctrl=_ctrl, keep_group=True, linked=_linked)
                        if not self._alt_copy_pending:
                            self._clip_group_begin(clip_obj, ctrl=_ctrl)
                        self._drag_source_track = t_obj
                        self._drag_target_track = t_obj
                        self.mode = 'move_audio'
                        self._audio_drag_start_x = real_x
                        self._audio_drag_orig_start = start
                        self._audio_drag_orig_end = end
                        self._snapshot_clip(t_obj, clip_obj)
                        self.item_selected.emit('audio', idx)
                        self.update()
                        self.setFocus()
                        return
                break  # 只处理包含 y 的那一条音频轨道

        # Overlay hit test (text/image tracks) — 多轨道支持
        # 遍历所有同类型轨道行，只检测属于该轨道的 overlay
        for list_name, track_type_key, edit_key in [('text_overlays', 'text', 'text'), ('image_overlays', 'image', 'images'),
                                                    ('image_overlays', 'adjust', 'images')]:
            if not self.can_edit_track(edit_key):
                continue
            # 遍历所有该类型的轨道行
            for (t_obj, row_y_t, row_h_t) in tracks.get("rows", []):
                if getattr(t_obj, "type", "") != track_type_key:
                    continue
                ov_top = row_y_t
                ov_bottom = ov_top + row_h_t
                if not (ov_top <= y <= ov_bottom):
                    continue
                # 只取属于这条轨道的 overlay
                my_ovs = self._get_overlays_for_track(t_obj, list_name)
                for idx, ov in reversed(my_ovs):
                    sx = ov['start'] * self.pixels_per_second
                    sw = (ov['end'] - ov['start']) * self.pixels_per_second
                    left_center = sx
                    right_center = sx + sw
                    if sw < self.HANDLE_PX * 2:
                        if (sx - self.HANDLE_PX / 2) <= real_x <= (sx + sw + self.HANDLE_PX / 2):
                            mode = 'resize_overlay_right' if abs(real_x - right_center) < abs(real_x - left_center) else 'resize_overlay_left'
                            self._select_overlay_item(t_obj, list_name, idx)
                            self._drag_source_track = t_obj
                            self._drag_target_track = t_obj
                            self.mode = mode
                            self.update()
                            self.item_selected.emit('overlay', (list_name, idx))
                            self.setFocus()
                            return
                    else:
                        right_rect = QRectF(sx + sw - self.HANDLE_PX / 2, ov_top, self.HANDLE_PX, row_h_t)
                        left_rect = QRectF(sx - self.HANDLE_PX / 2, ov_top, self.HANDLE_PX, row_h_t)
                        if right_rect.contains(real_x, y):
                            self._select_overlay_item(t_obj, list_name, idx)
                            self._drag_source_track = t_obj
                            self._drag_target_track = t_obj
                            self.mode = 'resize_overlay_right'
                            self.update()
                            self.item_selected.emit('overlay', (list_name, idx))
                            self.setFocus()
                            return
                        if left_rect.contains(real_x, y):
                            self._select_overlay_item(t_obj, list_name, idx)
                            self._drag_source_track = t_obj
                            self._drag_target_track = t_obj
                            self.mode = 'resize_overlay_left'
                            self.update()
                            self.item_selected.emit('overlay', (list_name, idx))
                            self.setFocus()
                            return
                    if sx <= real_x <= sx + sw:
                        _ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
                        self._select_overlay_item(t_obj, list_name, idx, ctrl=_ctrl, keep_group=True)
                        self._ov_group_ctrl = _ctrl
                        self._drag_source_track = t_obj
                        self._drag_target_track = t_obj
                        self.mode = 'move_overlay'
                        self._ov_drag_start_x = real_x
                        self._ov_drag_orig_start = ov['start']
                        self._ov_drag_orig_end = ov['end']
                        self.item_selected.emit('overlay', (list_name, idx))
                        self.update()
                        self.setFocus()
                        return

        if subs_top <= y <= subs_bottom and self.can_edit_track("subs"):
            # 0. Handle hit test (right -> left)
            for item in reversed(layout):
                sx = item['sx']
                sw = item['sw']
                left_center = sx
                right_center = sx + sw
                if sw < self.HANDLE_PX * 2:
                    if (sx - self.HANDLE_PX / 2) <= real_x <= (sx + sw + self.HANDLE_PX / 2):
                        hit_mode = "resize_right" if abs(real_x - right_center) < abs(real_x - left_center) else "resize_left"
                        clicked_idx = item['idx']
                        break
                else:
                    subs_h_val = subs_row[1] if subs_row else self.TRACK_H
                    right_rect = QRectF(sx + sw - self.HANDLE_PX / 2, subs_top, self.HANDLE_PX, subs_h_val)
                    left_rect = QRectF(sx - self.HANDLE_PX / 2, subs_top, self.HANDLE_PX, subs_h_val)
                    if right_rect.contains(real_x, y):
                        hit_mode = "resize_right"
                        clicked_idx = item['idx']
                        break
                    if left_rect.contains(real_x, y):
                        hit_mode = "resize_left"
                        clicked_idx = item['idx']
                        break

            # 1. Body hit test (move)
            if hit_mode == "none":
                for item in reversed(layout):
                    if item['sx'] <= real_x <= item['sx'] + item['sw']:
                        hit_mode = "move"
                        clicked_idx = item['idx']
                        break

        if hit_mode in ["resize_right", "resize_left"] and clicked_idx != -1:
            self._select_subtitle_index(clicked_idx)
            self.mode = hit_mode
            self._active_resize_idx = clicked_idx
            self.item_selected.emit('sub', clicked_idx)
            self.prepare_retime()
            self.update()
            return
        
        # 0. Check per-segment resize handles (right first, then left)
        if subs_top <= y <= subs_bottom:
            for item in layout:
                sx = item['sx']
                sw = item['sw']
                left_center = sx
                right_center = sx + sw
                if sw < self.HANDLE_PX * 2:
                    if (sx - self.HANDLE_PX / 2) <= real_x <= (sx + sw + self.HANDLE_PX / 2):
                        mode = 'retime_right' if abs(real_x - right_center) < abs(real_x - left_center) else 'retime_left'
                    else:
                        continue
                else:
                    right_rect = QRectF(sx + sw - self.HANDLE_PX / 2, subs_top, self.HANDLE_PX,
                                        subs_bottom - subs_top)
                    left_rect = QRectF(sx - self.HANDLE_PX / 2, subs_top, self.HANDLE_PX,
                                       subs_bottom - subs_top)
                    if right_rect.contains(real_x, y):
                        mode = 'retime_right'
                    elif left_rect.contains(real_x, y):
                        mode = 'retime_left'
                    else:
                        continue
                clicked_idx = item['idx']
                self._select_subtitle_index(clicked_idx)
                self.mode = mode
                self.item_selected.emit('sub', clicked_idx)
                self.prepare_retime()
                self.update()
                return

        # 1. Check for Retime Handles (if ANY selection exists)（编辑保护）
        if len(self.selected_indices) > 0 and self.can_edit_track("subs"):
            # Calc bounds again (simplified)
            sel_rects = [QRectF(item['sx'], subs_top, item['sw'], subs_bottom - subs_top)
                         for item in layout if item['idx'] in self.selected_indices]
            if sel_rects:
                min_x = min(r.x() for r in sel_rects)
                max_x = max(r.x() + r.width() for r in sel_rects)
                min_y = subs_top
                max_y = subs_bottom
            else:
                min_x = max_x = min_y = max_y = 0
            
            handle_w = 10
            # Left Handle Clicked?
            if min_y <= y <= max_y and min_x - handle_w <= real_x <= min_x:
                kept = set(self.selected_indices)
                self._reset_selection()
                self.selected_indices.update(kept)
                self._publish_selection()
                self.mode = 'retime_left'
                self.prepare_retime()
                return
            # Right Handle Clicked?
            if min_y <= y <= max_y and max_x <= real_x <= max_x + handle_w:
                kept = set(self.selected_indices)
                self._reset_selection()
                self.selected_indices.update(kept)
                self._publish_selection()
                self.mode = 'retime_right'
                self.prepare_retime()
                return

        # 2. Check for Segment Click
        if subs_top <= y <= subs_bottom:
            for item in reversed(layout):
                if item['sx'] <= real_x <= item['sx'] + item['sw']:
                    if clicked_idx == -1:
                        clicked_idx = item['idx']
                    break
        
        if clicked_idx != -1:
            ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
            self._select_subtitle_index(clicked_idx, ctrl=ctrl, keep_group=not ctrl)
            self.mode = 'move_multi'
            self.item_selected.emit('sub', clicked_idx)
            # Store initial states for all selected items for relative moving
            self.prepare_retime() # Re-use this dict for moving too
            self.update()
            return

        # 3. Click on Empty Space -> Rubberband or Seek（编辑保护：subs 锁定时不允许框选）
        ctrl_empty = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if ctrl_empty:
            self._reset_selection(keep_ctrl=True)
        else:
            self._reset_selection()
        self._publish_selection()
        self.selected_track = self._pos_to_track(y)
        if getattr(self.selected_track, "type", "") in ("subs", "subtitle"):
            self.item_selected.emit("track", self.selected_track)
        if (ctrl_empty or (subs_top <= y <= subs_bottom)) and self.can_edit_track("subs"):
            # Start rubberband
            self.mode = 'rubberband'
            self.rubber_band_rect = QRect(event.pos(), QSize(0,0))
            self.update()
        else:
            self.mode = 'seek'
            self.seek_request.emit(int(real_x/self.pixels_per_second*1000))

    def prepare_retime(self):
        # Snapshot current state
        # 修复：必须深拷贝 words，否则计算单词重定位时会丢失原始基准
        self.original_segments_state = {}
        for idx in self.selected_indices:
            seg = self.task_data['segments'][idx]
            self.original_segments_state[idx] = {
                'start': seg['start'], 
                'end': seg['end'],
                'words': copy.deepcopy(seg.get('words', [])) # 关键：备份单词
            }

    def mouseMoveEvent(self, event):
        if not isinstance(self.task_data, dict) or not self.task_data:
            event.ignore()
            return
        if self.mode in ('none', None) or self.mode == 'none':
            self._update_hover_edge(event)
        if self.mode in (
            "resize_video_left", "resize_video_right",
            "resize_audio_left", "resize_audio_right",
        ):
            clip = self.selected_video_clip[1] if self.selected_video_clip else None
            if clip is not None and self._clip_is_offline(clip):
                return
        real_x = event.pos().x() + self.scroll_x
        max_time = self.duration / 1000.0 if self.duration and self.duration > 0 else None
        if self._dragging_marker is not None:
            self._dragging_marker["t"] = max(0.0, real_x / self.pixels_per_second)
            self.update()
            return
        if self._dragging_keyframe is not None:
            hit = self._dragging_keyframe
            host = hit.get("host")
            path = hit.get("path")
            t_from = float(hit.get("t") or 0.0)
            t_to = max(0.0, real_x / max(1e-6, self.pixels_per_second) - self._host_start_t(host))
            if abs(t_to - t_from) > 1e-4:
                from cutora.render.keyframe import move_point
                if move_point(host, path, t_from, t_to):
                    hit["t"] = t_to
            self.update()
            return

        if self.mode in ("resize_word_left", "resize_word_right"):
            orig = getattr(self, "_word_drag_orig", None)
            if orig:
                from cutora.subtitle.word_trim import apply_word_trim
                segs = list((self.task_data or {}).get("segments") or [])
                idx = int(orig.get("seg_id", -1))
                if 0 <= idx < len(segs) and isinstance(segs[idx], dict):
                    proposed = real_x / max(1e-6, self.pixels_per_second)
                    if self._snap_active(event):
                        proposed = self._word_snap_t(
                            proposed, segs[idx], orig.get("word_index"),
                            extra=snap_mod.marker_times(self.task_data))
                    segs[idx]["words"] = apply_word_trim(
                        orig.get("words") or [],
                        orig.get("word_index"),
                        orig.get("edge"),
                        proposed,
                        seg_start=float(segs[idx].get("start") or 0.0),
                        seg_end=float(segs[idx].get("end") or 0.0),
                    )
                    self.update()
            return

        # === 裁剪头手柄悬浮光标 + 拖动 ===
        if self._dragging_trim:
            self._trim_push_undo("裁剪开头")
            delta_px = real_x - self._trim_drag_start_x
            new_sec = self._trim_drag_start_sec + delta_px / self.pixels_per_second
            max_sec = (self.duration / 1000.0 - 0.1) if self.duration > 0 else 0.0
            self.trim_start_sec = max(0.0, min(max_sec, new_sec))
            # 把裁剪点同步写入 task
            if isinstance(self.task_data, dict):
                self.task_data["trim_start_sec"] = self.trim_start_sec
            self.update()
            return

        # === 裁剪尾手柄拖动 ===
        if self._dragging_trim_end:
            self._trim_push_undo("裁剪结尾")
            delta_px = real_x - self._trim_end_drag_start_x
            new_sec = self._trim_end_drag_start_sec + delta_px / self.pixels_per_second
            min_sec = self.trim_start_sec + 0.1  # 不可拖到裁剪头之前
            self.trim_end_sec = max(min_sec, new_sec)
            if isinstance(self.task_data, dict):
                self.task_data["trim_end_sec"] = self.trim_end_sec
            self.update()
            return

        # 悬浮在裁剪手柄附近时显示调整光标
        trim_px = self.trim_start_sec * self.pixels_per_second - self.scroll_x
        trim_end_px = self.trim_end_sec * self.pixels_per_second - self.scroll_x if self.trim_end_sec > 0 else -999
        _on_trim_handle = (abs(event.pos().x() - trim_px) <= 12 and event.pos().y() <= self.RULER_H + 8)
        _on_trim_end_handle = (self.trim_end_sec > 0 and abs(event.pos().x() - trim_end_px) <= 12 and event.pos().y() <= self.RULER_H + 8)
        if (_on_trim_handle or _on_trim_end_handle
                or self.mode in ("fade_audio_in", "fade_audio_out")):
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        else:
            self.unsetCursor()
        # === Transition Tape 边缘拉拽（对边不动，松手才落命令）===
        if self.mode == "resize_transition" and getattr(self, "_trans_drag_obj", None) is not None:
            self._apply_transition_handle_live(real_x)
            trans_obj = self._trans_drag_obj
            self.update()
            self.item_selected.emit("transition", trans_obj)
            return

        if self.mode in ("fade_audio_in", "fade_audio_out"):
            self._apply_audio_fade_live(real_x)
            return

        # === Video clip 拖拽/拉伸 ===
        # T22: 默认边缘 = 修剪（改 out_t / in_t，speed 不变）。变速只在 tool==speed。
        # 开场视频例外：右拉伸改 out_t，左拉伸改 in_t（保持 speed=1.0）
        if self.mode in ['resize_video_right', 'resize_video_left'] and self.selected_video_clip is not None:
            from cutora.ui.timeline.tools import trim_clip_edge, TOOL_SPEED
            _tr, clip_obj = self.selected_video_clip
            is_intro = getattr(self, "_intro_drag_is_intro", False)
            start_t = float(getattr(clip_obj, "start_t", 0.0))
            in_t = float(getattr(clip_obj, "in_t", 0.0))
            out_t = float(getattr(clip_obj, "out_t", in_t))
            src_dur = max(0.01, out_t - in_t)
            speed = float(getattr(clip_obj, "speed", 1.0))
            eff_dur = src_dur / max(0.01, speed)
            end_t = start_t + eff_dur
            new_time = real_x / self.pixels_per_second
            if self.mode == 'resize_video_right':
                if is_intro:
                    # 开场视频：右拉伸 → 直接改 out_t（裁切源素材结尾）
                    new_end = max(new_time, start_t + self.MIN_DUR)
                    if max_time is not None:
                        new_end = min(new_end, max_time)
                    new_end = self._apply_snap_trim(
                        new_end, start_t, False,
                        self._clip_neighbors(_tr, clip_obj), event)
                    new_eff_dur = max(self.MIN_DUR, new_end - start_t)
                    # 获取源素材完整时长作为上限
                    main_win = self.window()
                    full_dur = 9999.0
                    if hasattr(main_win, "tasks") and main_win.curr_idx >= 0:
                        iv_state = main_win.tasks[main_win.curr_idx].get("_intro_video_state", {})
                        full_dur = float(iv_state.get("full_duration", 9999.0))
                    new_out = min(in_t + new_eff_dur, full_dur)
                    new_out = max(in_t + self.MIN_DUR, new_out)
                    clip_obj.out_t = new_out
                else:
                    new_end = max(new_time, start_t + self.MIN_DUR)
                    if max_time is not None:
                        new_end = min(new_end, max_time)
                    new_end = self._apply_snap_trim(
                        new_end, start_t, False,
                        self._clip_neighbors(_tr, clip_obj), event)
                    trim_clip_edge(
                        clip_obj, "right", new_end,
                        speed_mode=(getattr(self, "tool", "select") == TOOL_SPEED),
                        min_dur=self.MIN_DUR)
            else:
                if is_intro:
                    # 开场视频：左拉伸 → 直接改 in_t（裁切源素材开头），start_t 也改变
                    new_start = min(new_time, end_t - self.MIN_DUR)
                    new_start = max(0, new_start)
                    new_start = self._apply_snap_trim(
                        new_start, end_t, True,
                        self._clip_neighbors(_tr, clip_obj), event)
                    delta_timeline = new_start - start_t
                    # speed 恒为 1.0，source delta = timeline delta
                    new_in = in_t + delta_timeline
                    new_in = max(0.0, new_in)
                    new_in = min(out_t - self.MIN_DUR, new_in)
                    clip_obj.start_t = new_start
                    clip_obj.in_t = new_in
                else:
                    # 普通 clip：左拉伸 → 裁切起点
                    new_start = min(new_time, end_t - self.MIN_DUR)
                    new_start = max(0, new_start)
                    new_start = self._apply_snap_trim(
                        new_start, end_t, True,
                        self._clip_neighbors(_tr, clip_obj), event)
                    delta_timeline = new_start - start_t
                    delta_source = delta_timeline * max(0.01, speed)
                    clip_obj.start_t = new_start
                    clip_obj.in_t = in_t + delta_source
            self._apply_av_trim_partners_live(clip_obj)
            self.update()
            # 同步属性面板
            self.item_selected.emit('video_clip', clip_obj)
        elif self.mode == 'move_video' and self.selected_video_clip is not None:
            if not self._maybe_alt_copy_live(real_x, "video"):
                return
            _tr, clip_obj = self.selected_video_clip
            delta = (real_x - self._video_drag_start_x) / self.pixels_per_second
            new_start = max(0, self._video_drag_orig_start + delta)
            orig_dur = max(
                self.MIN_DUR,
                float(getattr(clip_obj, "effective_duration", 0.0) or 0.0)
                or (float(getattr(clip_obj, "out_t", 0.0))
                    - float(getattr(clip_obj, "in_t", 0.0))))
            new_start = self._apply_snap_move(
                new_start, new_start + orig_dur,
                self._clip_neighbors(_tr, clip_obj), event)
            clip_obj.start_t = new_start
            if self._clip_group_orig and not getattr(self, "_alt_copy_did", False):
                # Q145：多选整组一起移（同一时间差，不换轨）
                self._clip_group_apply(new_start - self._video_drag_orig_start)
                self.update()
                return
            # 开场视频拖拽时不切换 track
            is_intro = getattr(self, "_intro_drag_is_intro", False)
            if not is_intro:
                target_track = self._pos_to_track(event.pos().y())
                if target_track is not None and getattr(target_track, "type", None) == "video" and self.can_edit_track(target_track):
                    self._drag_target_track = target_track
            self.update()
        elif self.mode in ['resize_audio_right', 'resize_audio_left'] and self.selected_audio_idx is not None:
            from cutora.ui.timeline.tools import TOOL_SPEED, trim_clip_edge
            ac_list = self._get_audio_clip_list()
            if 0 <= self.selected_audio_idx < len(ac_list):
                clip_obj, _ = ac_list[self.selected_audio_idx]
                start = float(getattr(clip_obj, "start_t", 0.0))
                try:
                    end = start + max(0.0, float(clip_obj.effective_duration))
                except Exception:
                    in_t = float(getattr(clip_obj, "in_t", 0.0))
                    out_t = float(getattr(clip_obj, "out_t", in_t))
                    end = start + max(0.0, out_t - in_t)
                new_time = real_x / self.pixels_per_second
                tr = self._track_of_clip(clip_obj)
                speed_mode = (
                    self.mode == "resize_audio_right"
                    and getattr(self, "tool", "select") == TOOL_SPEED)
                if self.mode == 'resize_audio_right':
                    new_end = max(new_time, start + self.MIN_DUR)
                    if max_time is not None:
                        new_end = min(new_end, max_time)
                    new_end = self._apply_snap_trim(
                        new_end, start, False,
                        self._clip_neighbors(tr, clip_obj), event)
                    trim_clip_edge(
                        clip_obj, "right", new_end,
                        speed_mode=speed_mode, min_dur=self.MIN_DUR)
                else:
                    new_start = min(new_time, end - self.MIN_DUR)
                    new_start = max(0, new_start)
                    new_start = self._apply_snap_trim(
                        new_start, end, True,
                        self._clip_neighbors(tr, clip_obj), event)
                    trim_clip_edge(
                        clip_obj, "left", new_start,
                        speed_mode=False, min_dur=self.MIN_DUR)
                self._apply_av_trim_partners_live(clip_obj)
                self.update()
        elif self.mode == 'move_audio' and self.selected_audio_idx is not None:
            if not self._maybe_alt_copy_live(real_x, "audio"):
                return
            ac_list = self._get_audio_clip_list()
            if 0 <= self.selected_audio_idx < len(ac_list):
                clip_obj, _ = ac_list[self.selected_audio_idx]
                delta = (real_x - self._audio_drag_start_x) / self.pixels_per_second
                new_start = max(0, self._audio_drag_orig_start + delta)
                orig_dur = max(self.MIN_DUR, self._audio_drag_orig_end - self._audio_drag_orig_start)
                new_end = new_start + orig_dur
                if max_time is not None:
                    if new_end > max_time:
                        shift = new_end - max_time
                        new_start = max(0, new_start - shift)
                        new_end = max_time
                tr = self._track_of_clip(clip_obj)
                new_start = self._apply_snap_move(
                    new_start, new_start + orig_dur,
                    self._clip_neighbors(tr, clip_obj), event)
                clip_obj.start_t = new_start
                if self._clip_group_orig and not getattr(self, "_alt_copy_did", False):
                    self._clip_group_apply(new_start - self._audio_drag_orig_start)
                    self.update()
                    return
                target_track = self._pos_to_track(event.pos().y())
                if target_track is not None and getattr(target_track, "type", None) == "audio" and self.can_edit_track(target_track):
                    self._drag_target_track = target_track
                # out_t - in_t = 时长保持不变，只移动 start_t
                self.update()
        elif self.mode in ['resize_overlay_right', 'resize_overlay_left'] and self.selected_overlay is not None:
            list_name, idx = self.selected_overlay
            ov_list = self.task_data.get(list_name, [])
            if 0 <= idx < len(ov_list):
                ov = ov_list[idx]
                new_time = real_x / self.pixels_per_second
                if self.mode == 'resize_overlay_right':
                    ov['end'] = max(new_time, ov['start'] + self.MIN_DUR)
                    ov['end'] = self._apply_snap_trim(
                        ov['end'], ov['start'], False, [], event)
                else:
                    new_start = min(new_time, ov['end'] - self.MIN_DUR)
                    new_start = max(0, new_start)
                    ov['start'] = self._apply_snap_trim(
                        new_start, ov['end'], True, [], event)
                self.update()
        elif self.mode == 'move_overlay' and self.selected_overlay is not None:
            list_name, idx = self.selected_overlay
            ov_list = self.task_data.get(list_name, [])
            if 0 <= idx < len(ov_list):
                ov = ov_list[idx]
                delta = (real_x - self._ov_drag_start_x) / self.pixels_per_second
                new_start = max(0, self._ov_drag_orig_start + delta)
                dur = max(self.MIN_DUR, self._ov_drag_orig_end - self._ov_drag_orig_start)
                new_start = self._apply_snap_move(
                    new_start, new_start + dur, [], event)
                ov['start'] = new_start
                ov['end'] = new_start + dur
                if self._ov_group_orig:
                    self._ov_group_apply(new_start - self._ov_drag_orig_start)
                    self.update()
                    return
                target_track = self._pos_to_track(event.pos().y())
                if target_track is not None and self.can_edit_track(target_track):
                    if list_name == "text_overlays" and getattr(target_track, "type", None) == "text":
                        self._drag_target_track = target_track
                    elif list_name == "image_overlays" and getattr(target_track, "type", None) == (
                            "adjust" if ov.get("type") == "adjust" else "image"):
                        self._drag_target_track = target_track
                self.update()
        elif self.mode in ['resize_right', 'resize_left'] and self._active_resize_idx is not None:
            idx = self._active_resize_idx
            orig = (self.original_segments_state or {}).get(idx)
            seg = self._reset_seg_from_orig(idx)
            if seg is not None and orig is not None:
                new_time = real_x / self.pixels_per_second
                if self.mode == 'resize_right':
                    new_start = float(orig['start'])
                    new_end = max(new_time, new_start + self.MIN_DUR)
                    new_end = self._apply_snap_trim(
                        new_end, new_start, False,
                        self._seg_neighbors(idx), event)
                else:
                    new_end = float(orig['end'])
                    new_start = min(new_time, new_end - self.MIN_DUR)
                    new_start = max(0, new_start)
                    new_start = self._apply_snap_trim(
                        new_start, new_end, True,
                        self._seg_neighbors(idx), event)
                apply_trim(seg, new_start, new_end)
            self.update()
        elif self.mode == 'move_multi':
            time_delta = (real_x - self.drag_start_x) / self.pixels_per_second
            origs = self.original_segments_state or {}
            proposed = []
            for idx in self.selected_indices:
                orig = origs.get(idx)
                if orig is None:
                    continue
                ns = max(0.0, float(orig['start']) + time_delta)
                ne = ns + max(self.MIN_DUR, float(orig['end']) - float(orig['start']))
                proposed.append((idx, orig, ns, ne))
            if proposed:
                gmin = min(p[2] for p in proposed)
                gmax = max(p[3] for p in proposed)
                neighbors = self._seg_neighbors(
                    proposed[0][0], extra_skip=self.selected_indices)
                snapped_min = self._apply_snap_move(
                    gmin, gmax, neighbors, event)
                adj = snapped_min - gmin
            else:
                adj = 0.0
            for idx, orig, ns, _ne in proposed:
                seg = self._reset_seg_from_orig(idx)
                if seg is None:
                    continue
                apply_move(seg, max(0.0, ns + adj))
            self.update()
            
        elif self.mode in ['retime_left', 'retime_right']:
            orig_starts = [self.original_segments_state[i]['start'] for i in self.selected_indices]
            orig_ends = [self.original_segments_state[i]['end'] for i in self.selected_indices]
            min_s = min(orig_starts)
            max_e = max(orig_ends)
            original_duration = max_e - min_s
            if original_duration <= 0.1:
                return
            mouse_time = real_x / self.pixels_per_second
            neighbors = self._seg_neighbors(
                next(iter(self.selected_indices)), extra_skip=self.selected_indices)
            if self.mode == 'retime_right':
                mouse_time = self._apply_snap_trim(
                    mouse_time, min_s, False, neighbors, event)
                new_duration = max(0.1, mouse_time - min_s)
                scale = new_duration / original_duration
                pivot = min_s
            else:
                mouse_time = self._apply_snap_trim(
                    mouse_time, max_e, True, neighbors, event)
                new_duration = max(0.1, max_e - mouse_time)
                scale = new_duration / original_duration
                pivot = max_e
            self._retime_pivot = pivot
            self._retime_scale = scale
            for idx in self.selected_indices:
                seg = self._reset_seg_from_orig(idx)
                if seg is None:
                    continue
                apply_retime(seg, pivot, scale)
            self.update()

        elif self.mode == 'seek' and (event.buttons() & Qt.MouseButton.LeftButton):
            # Q145：按住空白处 / 标尺拖 = 擦洗（期间预览半分辨率）
            self._scrub_to(real_x)
        elif self.mode == 'rubberband':
            # Update rect (in visual coords, not scroll coords)
            start_pos = QPoint(int(self.drag_start_x - self.scroll_x), self.rubber_band_rect.y())
            curr_pos = event.pos()
            self.rubber_band_rect = QRect(start_pos, curr_pos).normalized()
            
            # Select items inside rect
            # Convert rect back to world coords
            world_rect = QRectF(self.rubber_band_rect)
            world_rect.translate(self.scroll_x, 0)
            
            self.selected_indices.clear()
            self._clip_sel = []
            layout = self._normalize_segments()
            tracks = self._track_positions()
            _sr = self._row_for_type(tracks, "subs")
            subs_top = _sr[0] if _sr else -999
            subs_h_val = _sr[1] if _sr else self.TRACK_H
            for item in layout:
                item_rect = QRectF(item['sx'], subs_top, item['sw'], subs_h_val)
                if world_rect.intersects(item_rect):
                    self.selected_indices.add(item['idx'])
            for (t_obj, row_y, row_h) in tracks.get("rows", []):
                t_type = getattr(t_obj, "type", "")
                if t_type not in ("video", "audio"):
                    continue
                tid = str(getattr(t_obj, "id", ""))
                for i, clip_obj in enumerate(getattr(t_obj, "clips", []) or []):
                    start_t = float(getattr(clip_obj, "start_t", 0.0))
                    eff = float(getattr(clip_obj, "effective_duration", 0.0) or 0.0)
                    sx = start_t * self.pixels_per_second
                    sw = max(1.0, eff * self.pixels_per_second)
                    clip_rect = QRectF(sx, row_y, sw, row_h)
                    if world_rect.intersects(clip_rect):
                        self._clip_sel.append((tid, i))
            # Q145：框选也框文字 / 图片
            self._ov_multi = []
            for (t_obj, row_y, row_h) in tracks.get("rows", []):
                t_type = getattr(t_obj, "type", "")
                name = {"text": "text_overlays", "image": "image_overlays", "adjust": "image_overlays"}.get(t_type)
                if name is None:
                    continue
                for _i, ov in self._get_overlays_for_track(t_obj, name):
                    sx = float(ov.get("start") or 0.0) * self.pixels_per_second
                    sw = max(1.0, (float(ov.get("end") or 0.0) - float(ov.get("start") or 0.0))
                             * self.pixels_per_second)
                    if world_rect.intersects(QRectF(sx, row_y, sw, row_h)):
                        self._ov_multi.append(ov)
            self._publish_selection()
            self.update()
        
        if self.mode == 'none':
            self._show_hover_time(real_x, event)

    def mouseReleaseEvent(self, event):
        if self._dragging_marker is not None:
            rec = self._dragging_marker
            orig = self._marker_drag_orig_t
            final_t = float(rec.get("t") or 0.0)
            rec["t"] = orig
            self._dragging_marker = None
            self._marker_drag_orig_t = None
            if orig is not None and abs(final_t - float(orig)) > 1e-4:
                if not self._bus_execute("marker.move", {
                    "id": rec.get("id"), "t": final_t,
                }):
                    rec["t"] = orig
            self.update()
            return
        if self._dragging_keyframe is not None:
            hit = self._dragging_keyframe
            orig = self._kf_drag_orig_t
            final_t = float(hit.get("t") or 0.0)
            host = hit.get("host")
            path = hit.get("path")
            self._dragging_keyframe = None
            self._kf_drag_orig_t = None
            if orig is not None and abs(final_t - float(orig)) > 1e-4:
                from cutora.render.keyframe import move_point
                # Restore so the command snapshot sees the original t.
                move_point(host, path, final_t, orig)
                params = self._kf_cmd_params(hit)
                params["t_from"] = float(orig)
                params["t_to"] = float(final_t)
                if not self._bus_execute("keyframe.move", params):
                    move_point(host, path, orig, final_t)
            self.update()
            return

        # 裁剪头手柄拖动结束
        if self._dragging_trim:
            self._dragging_trim = False
            self._trim_capture_undo()
            self.unsetCursor()
            self.update()
            return

        # 裁剪尾手柄拖动结束
        if self._dragging_trim_end:
            self._dragging_trim_end = False
            self._trim_capture_undo()
            self.unsetCursor()
            self.update()
            return

        if self.mode in ("resize_word_left", "resize_word_right"):
            self._commit_word_drag()
            self.mode = "none"
            self._word_drag_orig = None
            self._snap_guide_t = None
            self._snap_edge_cache = None
            self.unsetCursor()
            self.update()
            return

        if self.mode in (
            'resize_left', 'resize_right', 'move_multi',
            'retime_left', 'retime_right',
        ):
            self._commit_segment_drag()
            self.mode = 'none'
            self.rubber_band_rect = QRect()
            self._active_resize_idx = None
            self._retime_pivot = None
            self._retime_scale = None
            self._snap_guide_t = None
            self._snap_edge_cache = None
            self.update()
            return

        if self.mode in ("fade_audio_in", "fade_audio_out"):
            self._commit_audio_fade_drag()
            self.mode = "none"
            self.unsetCursor()
            self.update()
            return

        if self.mode in (
            'resize_video_right', 'resize_video_left', 'move_video',
            'resize_audio_right', 'resize_audio_left', 'move_audio',
        ):
            group_done = False
            if self.mode in ('move_video', 'move_audio') and self._clip_group_orig:
                if self.mode == 'move_video' and self.selected_video_clip is not None:
                    p_tr, p_clip = self.selected_video_clip
                    p_orig = self._video_drag_orig_start
                else:
                    p_clip, p_tr, p_orig = None, None, None
                    ac = self._get_audio_clip_list()
                    if self.selected_audio_idx is not None and 0 <= self.selected_audio_idx < len(ac):
                        p_clip, _path = ac[self.selected_audio_idx]
                        p_tr = self._track_of_clip(p_clip)
                        p_orig = self._audio_drag_orig_start
                if p_clip is not None:
                    moved = abs(float(getattr(p_clip, "start_t", 0.0)) - float(p_orig)) > 1e-6
                    group_done = self._clip_group_commit(p_tr, p_clip, p_orig)
                    self._alt_copy_pending = False
                    self._alt_copy_src = None
                    if group_done and not moved and not self._clip_group_ctrl and not self._group_keys_for(p_clip):
                        # 点了组里的一条、没拖：收成单选（和画布一样）；Ctrl+点是在加减多选，不收
                        if self.mode == 'move_video':
                            self._select_video_clip(p_tr, p_clip)
                        else:
                            self._select_audio_index(p_tr, p_clip, self.selected_audio_idx)
            self._clip_group_orig = None
            if not group_done:
                self._commit_clip_drag()
            else:
                self._clear_av_trim()
            self.item_dragged.emit()
            self._intro_drag_is_intro = False
            self._clip_drag_orig = None
            self._drag_source_track = None
            self._drag_target_track = None
            self.mode = 'none'
            self.rubber_band_rect = QRect()
            self._active_resize_idx = None
            self._snap_guide_t = None
            self._snap_edge_cache = None
            self.update()
            return

        if self.mode == 'move_overlay' and self.selected_overlay is not None:
            list_name, idx = self.selected_overlay
            ov_list = self.task_data.get(list_name, [])
            if 0 <= idx < len(ov_list):
                ov = ov_list[idx]
                target_track = self._drag_target_track
                source_track = self._drag_source_track
                if target_track is not None and source_track is not None and target_track is not source_track:
                    if list_name == "text_overlays" and getattr(target_track, "type", None) == "text":
                        ov["_track_id"] = getattr(target_track, "id", None)
                        self.selected_track = target_track
                    elif list_name == "image_overlays" and getattr(target_track, "type", None) == (
                            "adjust" if ov.get("type") == "adjust" else "image"):
                        ov["_track_id"] = getattr(target_track, "id", None)
                        self.selected_track = target_track

        if self.mode in ('move_overlay', 'resize_overlay_right', 'resize_overlay_left'):
            # Q145：时间线上拖文字 / 图片以前撤销不了
            _ov_changed = self._ov_drag_commit(
                "移动文字 / 图片" if self.mode == 'move_overlay' else "裁剪文字 / 图片")
            if (not _ov_changed and self.mode == 'move_overlay'
                    and not self._ov_group_ctrl and len(self._ov_multi or []) > 1):
                # 点了多选里的一条、没拖：收成单选（和片段、画布一样）
                self._ov_multi = []
                self._publish_selection()
            self._ov_group_ctrl = False
            if _ov_changed:
                self._after_edit()
            else:
                self.update()

        if self.mode == 'seek':
            self._scrub_end(event.pos().x() + self.scroll_x)

        if self.mode == "resize_transition":
            self._commit_transition_handle_drag()
            self.mode = "none"
            self.unsetCursor()
            self.update()
            return

        self._drag_source_track = None
        self._drag_target_track = None
        self.mode = 'none'
        self.rubber_band_rect = QRect()
        self._active_resize_idx = None
        self._snap_guide_t = None
        self._snap_edge_cache = None
        self.update()

    def _rename_marker_dialog(self, rec):
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(
            self, "重命名标记", "名称", text=str(rec.get("name") or ""))
        if not ok:
            return
        self._bus_execute("marker.rename", {
            "id": rec.get("id"), "name": name,
        })

    def _recolor_marker_dialog(self, rec):
        from PySide6.QtWidgets import QColorDialog
        cur = QColor(str(rec.get("color") or "#F5B700"))
        color = QColorDialog.getColor(cur, self, "标记颜色")
        if not color.isValid():
            return
        self._bus_execute("marker.set_color", {
            "id": rec.get("id"), "color": color.name(),
        })

    def mouseDoubleClickEvent(self, event):
        if not isinstance(self.task_data, dict) or not self.task_data:
            event.ignore()
            return
        hit_mk = self._hit_marker(event.pos().x(), event.pos().y())
        if hit_mk is not None:
            self._rename_marker_dialog(hit_mk)
            return
        real_x = event.pos().x() + self.scroll_x
        y = event.pos().y()
        tracks = self._track_positions()
        subs_row = self._row_for_type(tracks, "subs")
        subs_top = subs_row[0] if subs_row else -999
        subs_h = subs_row[1] if subs_row else 40
        subs_bottom = subs_top + subs_h

        # 旧逻辑使用固定 y 范围(70~110)，在滚动/缩放/布局变化后会导致双击失效。
        # 这里使用真实字幕轨道的位置判断。（编辑保护：locked 时不可双击编辑）
        if subs_top <= y <= subs_bottom and self.can_edit_track("subs"):
            layout = self._normalize_segments()
            for item in reversed(layout):
                if item['sx'] <= real_x <= item['sx'] + item['sw']:
                    self.text_edit_request.emit(item['idx'])
                    return

    def contextMenuEvent(self, event):
        if not isinstance(self.task_data, dict) or not self.task_data:
            return super().contextMenuEvent(event)
        widget_x = event.pos().x()
        real_x = widget_x + self.scroll_x
        kf_hit = self._hit_keyframe(real_x, event.pos().y())
        if kf_hit is not None:
            self._keyframe_context_menu(kf_hit, event.globalPos())
            return
        hit_mk = self._hit_marker(widget_x, event.pos().y())
        if hit_mk is not None:
            menu = QMenu(self)
            act_ren = menu.addAction("重命名")
            act_col = menu.addAction("改颜色")
            act_del = menu.addAction("删除标记")
            chosen = menu.exec(event.globalPos())
            if chosen == act_ren:
                self._rename_marker_dialog(hit_mk)
            elif chosen == act_col:
                self._recolor_marker_dialog(hit_mk)
            elif chosen == act_del:
                self._bus_execute("marker.remove", {"ids": [hit_mk.get("id")]})
            return
        trim_px = self.trim_start_sec * self.pixels_per_second - self.scroll_x
        # 如果右键点击在裁剪手柄附近（±14px），显示"重置裁剪起点"菜单
        if abs(widget_x - trim_px) <= 14:
            menu = QMenu(self)
            act_reset = menu.addAction("⏮  重置裁剪起点为 0")
            chosen = menu.exec(event.globalPos())
            if chosen == act_reset:
                self.trim_start_sec = 0.0
                if isinstance(self.task_data, dict):
                    self.task_data["trim_start_sec"] = 0.0
                self.update()
            return
        # 如果右键点击在裁剪尾手柄附近（±14px），显示"重置裁剪终点"菜单
        if self.trim_end_sec > 0:
            trim_end_px = self.trim_end_sec * self.pixels_per_second - self.scroll_x
            if abs(widget_x - trim_end_px) <= 14:
                menu = QMenu(self)
                act_reset = menu.addAction("⏭  重置裁剪终点为主视频时长")
                chosen = menu.exec(event.globalPos())
                if chosen == act_reset:
                    # 找到主视频轨道的时长
                    main_dur = 0.0
                    tl = _tl_of(self.task_data)
                    if tl:
                        for tr in tl.tracks:
                            if getattr(tr, "is_main_video", False):
                                for clip in getattr(tr, "clips", []) or []:
                                    in_t = float(getattr(clip, "in_t", 0.0))
                                    out_t = float(getattr(clip, "out_t", in_t))
                                    main_dur = max(main_dur, out_t - in_t)
                    if main_dur <= 0:
                        main_dur = float(self.task_data.get("duration", 0.0)) if isinstance(self.task_data, dict) else 0.0
                    if main_dur > 0:
                        self.trim_end_sec = main_dur
                        if isinstance(self.task_data, dict):
                            self.task_data["trim_end_sec"] = main_dur
                    self.update()
                return
        real_x = event.pos().x() + self.scroll_x
        y = event.pos().y()
        tracks = self._track_positions()
        trans_hit = self._hit_transition_tape(real_x, y)
        if trans_hit is None:
            seam_probe = self._hit_seam_at(real_x, y)
            if seam_probe is not None and seam_probe[0] == "transition":
                trans_hit = (seam_probe[1], seam_probe[2])
        if trans_hit is not None:
            t_obj, trans_obj = trans_hit
            self._arm_transition(t_obj, trans_obj)
            self.item_selected.emit("transition", trans_obj)
            menu = QMenu(self)
            act_del = menu.addAction("删除转场")
            if getattr(t_obj, "locked", False):
                act_del.setEnabled(False)
            chosen = menu.exec(event.globalPos())
            if chosen == act_del:
                self._delete_selected_transition()
            return
        seam_hit = self._hit_seam_at(real_x, y)
        if seam_hit is not None and seam_hit[0] == "seam":
            track, left_i, right_i, seam_t = seam_hit[1], seam_hit[2], seam_hit[3], seam_hit[4]
            self._select_seam(track, left_i, right_i, seam_t)
            menu = QMenu(self)
            add_menu = menu.addMenu("添加转场…")
            from cutora.render.anim_engine import (
                AUDIO_TRANSITION_LIBRARY, STANDALONE_TRANSITION_LIBRARY,
                audio_transition_duration,
            )
            domain = "audio" if getattr(track, "domain", "visual") == "audio" else "visual"
            lib = AUDIO_TRANSITION_LIBRARY if domain == "audio" else STANDALONE_TRANSITION_LIBRARY
            chosen_preset = None
            for lab, preset in lib:
                add_menu.addAction(lab).setData(preset)
            picked = menu.exec(event.globalPos())
            if picked is not None and picked.data():
                dur = audio_transition_duration(picked.data()) if domain == "audio" else 0.35
                self._add_transition_to_seam(
                    track, left_i, right_i, picked.data(), domain, dur)
            return
        hit_clip = None
        hit_track = None
        for (t_obj, row_y, row_h) in tracks.get("rows", []):
            if getattr(t_obj, "type", None) != "video":
                continue
            if not (row_y <= y <= row_y + row_h):
                continue
            for clip_obj in reversed(getattr(t_obj, "clips", []) or []):
                start_t = float(getattr(clip_obj, "start_t", 0.0))
                eff_dur = float(getattr(clip_obj, "effective_duration", 0.0))
                if eff_dur <= 0:
                    in_t = float(getattr(clip_obj, "in_t", 0.0))
                    out_t = float(getattr(clip_obj, "out_t", in_t))
                    eff_dur = max(0.0, out_t - in_t)
                sx = start_t * self.pixels_per_second
                sw = eff_dur * self.pixels_per_second
                rect = QRectF(sx, row_y, sw, row_h)
                if rect.contains(real_x, y):
                    hit_clip = clip_obj
                    hit_track = t_obj
                    break
            break
        if hit_clip is None:
            for (t_obj, row_y, row_h) in tracks.get("rows", []):
                if getattr(t_obj, "type", None) != "audio":
                    continue
                if not (row_y <= y <= row_y + row_h):
                    continue
                for clip_obj in reversed(getattr(t_obj, "clips", []) or []):
                    start = float(getattr(clip_obj, "start_t", 0.0))
                    in_t = float(getattr(clip_obj, "in_t", 0.0))
                    out_t = float(getattr(clip_obj, "out_t", in_t))
                    sx = start * self.pixels_per_second
                    sw = max(0.0, out_t - in_t) * self.pixels_per_second
                    if QRectF(sx, row_y, sw, row_h).contains(real_x, y):
                        hit_clip = clip_obj
                        hit_track = t_obj
                        break
                break
            if hit_clip is not None:
                self._popup_audio_clip_menu(event, hit_track, hit_clip)
                return

        # ── 检测图片轨 (`image_overlays`) / 文本轨 (`text_overlays`) 上的覆层条右键菜单 ──
        if hit_clip is None:
            hit_ov_info = None
            for list_name, track_type_key in (
                ("image_overlays", "image"),
                ("image_overlays", "adjust"),
                ("text_overlays", "text"),
            ):
                for (t_obj, row_y_t, row_h_t) in tracks.get("rows", []):
                    if getattr(t_obj, "type", "") != track_type_key:
                        continue
                    if not (row_y_t <= y <= row_y_t + row_h_t):
                        continue
                    my_ovs = self._get_overlays_for_track(t_obj, list_name)
                    for idx, ov in reversed(my_ovs):
                        sx = float(ov.get("start") or 0.0) * self.pixels_per_second
                        sw = max(2.0, (float(ov.get("end") or 0.0) - float(ov.get("start") or 0.0)) * self.pixels_per_second)
                        if sx <= real_x <= sx + sw:
                            hit_ov_info = (t_obj, list_name, idx, ov)
                            break
                    if hit_ov_info is not None:
                        break
                if hit_ov_info is not None:
                    break

            if hit_ov_info is not None:
                ov_track, list_name, ov_idx, hit_ov = hit_ov_info
                self._select_overlay_item(ov_track, list_name, ov_idx, keep_group=True)
                self.item_selected.emit("overlay", (list_name, ov_idx))
                self.update()
                menu = QMenu(self)
                group_acts = self._group_menu(menu, ov_track, hit_ov)
                win_top = self.window()
                from cutora.ui.ai_studio_room import (
                    get_overlay_image_path,
                    has_rmbg_original,
                    open_quick_ai_creator,
                    restore_rmbg_original_ui,
                    run_rmbg_cutout_ui,
                )
                is_img_ov = (list_name == "image_overlays") and (hit_ov.get("type") != "adjust")
                act_rmbg_ov = None
                act_restore_ov = None
                ai_ov_acts = {}
                if is_img_ov:
                    target_ovs = [
                        o for o in (self._ov_multi or [hit_ov])
                        if isinstance(o, dict) and o.get("type") != "adjust"
                    ] or [hit_ov]
                    act_rmbg_ov = menu.addAction("✂️ AI 一键抠图去背景 (RMBG-2.0 发丝级透明抠图)")
                    if any(has_rmbg_original(o) for o in target_ovs):
                        act_restore_ov = menu.addAction("↩️ 还原抠图前原图")
                    menu.addSeparator()
                    for _alab, _acap in (
                        ("🖼️ AI 参考改图 / 极速生图 · FLUX.2…", "flux2_klein"),
                        ("🚀 AI 4× 真实感超分放大 · Nomos2…", "nomos2_4x"),
                        ("🎬 AI 将此图片转为动态视频 · LTX-2.5 I2V…", "ltx25_i2v_t2v"),
                        ("✨ 打开 AI 创作与精修胶囊 (21项全集)…", "rmbg2_sam2_matte"),
                    ):
                        ai_ov_acts[menu.addAction(_alab)] = _acap
                    menu.addSeparator()
                act_del_ov = menu.addAction("删除此图层\tDelete")
                chosen_ov = menu.exec(event.globalPos())
                if chosen_ov is None:
                    return
                if chosen_ov in group_acts:
                    group_acts[chosen_ov]()
                    return
                if is_img_ov and chosen_ov == act_rmbg_ov:
                    run_rmbg_cutout_ui(win=win_top, overlays=target_ovs, task=self.task_data, parent=self)
                    return
                if is_img_ov and act_restore_ov is not None and chosen_ov == act_restore_ov:
                    restore_rmbg_original_ui(win=win_top, overlays=target_ovs)
                    return
                if chosen_ov in ai_ov_acts:
                    ov_img_path = get_overlay_image_path(hit_ov)
                    open_quick_ai_creator(
                        win=win_top,
                        video_path=ov_img_path,
                        initial_cap_id=ai_ov_acts[chosen_ov],
                        parent=self,
                    )
                    return
                if chosen_ov == act_del_ov:
                    if win_top is not None and hasattr(win_top, "delete_selected_overlay"):
                        win_top.delete_selected_overlay()
                    else:
                        ovs = self.task_data.get(list_name, [])
                        if 0 <= ov_idx < len(ovs):
                            ovs.pop(ov_idx)
                            self.selected_overlay = None
                            self._after_edit()
                return

        is_thumb = hit_clip is not None and bool(getattr(hit_clip, "is_thumbnail_leadin", False))
        is_intro_vid = hit_clip is not None and bool(getattr(hit_clip, "is_intro_video_leadin", False))
        if not is_thumb and not is_intro_vid:
            if hit_clip is not None:
                # 右键点到没选中的那条：先把它选上（锁住的组会整组选上），菜单才对得上号
                tid_hit = str(getattr(hit_track, "id", ""))
                if (tid_hit, self._clip_index_of(hit_track, hit_clip)) not in self._clip_sel:
                    self._select_video_clip(hit_track, hit_clip)
                    self.update()
                menu = QMenu(self)
                group_acts = self._group_menu(menu, hit_track, hit_clip)
                # ── 置顶：AI 抠图 (RMBG-2.0) 与 视频点选抠像 (SAM 2.1) ──
                from cutora.ui.ai_studio_room import has_rmbg_original, restore_rmbg_original_ui, run_rmbg_cutout_ui
                act_rmbg_clip = menu.addAction("✂️ AI 一键抠图去背景 (RMBG-2.0 发丝级图片/当前帧抠图)")
                act_restore_clip = None
                if has_rmbg_original(hit_clip):
                    act_restore_clip = menu.addAction("↩️ 还原抠图前原图")
                act_sam2_clip = menu.addAction("🎯 AI 视频点选追踪抠像 (SAM 2.1 动态人物抠像)")
                menu.addSeparator()
                act_fill = menu.addAction("填满画布（cover）")
                act_fit = menu.addAction("完整适配（contain）")
                menu.addSeparator()
                act_proj = menu.addAction("按项目尺寸")
                act_src = menu.addAction("按源尺寸")
                menu.addSeparator()
                act_copy_mat = menu.addAction("复制这条素材（原时间）")
                act_copy_style = menu.addAction("复制这条的属性")
                act_copy_all = menu.addAction("复制这条的全部属性")
                act_paste_layer = menu.addAction("粘贴到这条时间线（按复制时的方式）")
                menu.addSeparator()
                ai_menu = menu.addMenu("✨ AI 创作与精修卡牌（直接生成上轨）")
                ai_cap_acts = {}
                for _alab, _acap in (
                    ("✂️ 发丝级抠图与视频抠像 · RMBG-2.0 / SAM 2.1…", "rmbg2_sam2_matte"),
                    ("🎬 图/文生视频 · LTX-2.5 / Flow / Grok…", "ltx25_i2v_t2v"),
                    ("🖼️ 极速生图 / 参考图改图 · FLUX.2…", "flux2_klein"),
                    ("📐 局部蒙版重绘 / 竖屏扩 16:9 改视频…", "ltx_in_outpaint"),
                    ("🎙️ 无痕去话筒 / 去人物 / 去杂物 · SAM 2.1…", "ltx25_cleanplate"),
                    (None, None),
                    ("💎 画质修复（去伪影/去噪点）· LTX Restore…", "ltx25_restore"),
                    ("🎯 视频去运动模糊 / 失焦锐化 · LTX Deblur…", "ltx25_deblur"),
                    ("✨ 放大补细节精修 · LTX Refine…", "ltx25_refine"),
                    ("🚀 4× 摄影级真实超分 · Nomos2…", "nomos2_4x"),
                    ("🌅 SDR 转 10bit HDR 高动态范围…", "ltx25_sdr2hdr"),
                    ("🎨 黑白/褪色老视频自然上色 · Colorize…", "ltx25_colorize"),
                    ("🛡️ 光流运动补偿护脸时域降噪 · Denoise…", "temporal_denoise"),
                    (None, None),
                    ("☀️ 三维人像重打光 · Simone v6h…", "simone_relight_v6h"),
                    ("💡 面部阴影补光与去频闪 · Fill+Flicker…", "portrait_fill_flicker"),
                    ("🎬 智能电影调色与 3D LUT 生成 · Autograde…", "autograde_lut"),
                    (None, None),
                    ("🎵 在当前游标生成背景配乐 (MusicGen BGM)…", "musicgen_bgm"),
                    ("🔊 在当前游标生成立体声效/掌声 (Stable Audio SFX)…", "stable_audio_sfx"),
                    ("🗣️ 吸取本片原声克隆配音 (Chatterbox / XTTS-v2)…", "chatterbox_clone"),
                    ("🎧 录音棚人声深度降噪 / 四轨伴奏分离 (Resemble / Demucs)…", "resemble_demucs_audio"),
                ):
                    if _alab is None:
                        ai_menu.addSeparator()
                    else:
                        ai_cap_acts[ai_menu.addAction(_alab)] = _acap
                ai_menu.addSeparator()
                act_open_ai_deck = ai_menu.addAction("🏛️ 进入 AI 艺术馆卡牌工坊（查看本时间线全套 21 张卡牌）")
                chosen = menu.exec(event.globalPos())
                if chosen is not None and chosen in group_acts:
                    group_acts[chosen]()
                    return
                if chosen == act_rmbg_clip:
                    win_top = self.window()
                    tl_obj = _tl_of(self.task_data)
                    run_rmbg_cutout_ui(
                        win=win_top,
                        clip_obj=hit_clip,
                        tl_obj=tl_obj,
                        task=self.task_data,
                        parent=self,
                    )
                    return
                if act_restore_clip is not None and chosen == act_restore_clip:
                    win_top = self.window()
                    tl_obj = _tl_of(self.task_data)
                    restore_rmbg_original_ui(win=win_top, clip_obj=hit_clip, tl_obj=tl_obj)
                    return
                if chosen == act_sam2_clip:
                    win_top = self.window()
                    canvas_w = getattr(win_top, "video_canvas", None) if win_top is not None else None
                    if canvas_w is not None and hasattr(canvas_w, "_activate_sam2_video_matte_from_canvas"):
                        canvas_w._activate_sam2_video_matte_from_canvas()
                    return
                if chosen in ai_cap_acts or chosen == act_open_ai_deck:
                    win_top = self.window()
                    tl_obj = _tl_of(self.task_data)
                    src_obj = (getattr(tl_obj, "sources", None) or {}).get(getattr(hit_clip, "source_id", None)) if tl_obj else None
                    clip_path = str(getattr(src_obj, "path", "") or getattr(hit_clip, "path", "") or getattr(hit_clip, "src_path", "") or "")
                    if chosen == act_open_ai_deck:
                        if win_top is not None and hasattr(win_top, "room_host"):
                            win_top.room_host.enter("ai_studio")
                    else:
                        try:
                            from cutora.ui.ai_studio_room import open_quick_ai_creator
                            open_quick_ai_creator(
                                win=win_top,
                                video_path=clip_path,
                                initial_cap_id=ai_cap_acts[chosen],
                                parent=self,
                            )
                        except Exception:
                            pass
                    return
                if chosen == act_copy_mat:
                    self._copy_hit_clip(hit_track, hit_clip, "material")
                    return
                if chosen == act_copy_style:
                    self._copy_hit_clip(hit_track, hit_clip, "style")
                    return
                if chosen == act_copy_all:
                    self._copy_hit_clip(hit_track, hit_clip, "all")
                    return
                if chosen == act_paste_layer:
                    self._paste_copied_layers()
                    return
                if chosen == act_fill:
                    hit_clip.fit_mode = "fill"
                    hit_clip.size_mode = "project"
                elif chosen == act_fit:
                    hit_clip.fit_mode = "fit"
                    hit_clip.size_mode = "project"
                elif chosen == act_proj:
                    hit_clip.size_mode = "project"
                elif chosen == act_src:
                    hit_clip.size_mode = "source"
                self.update()
                return
            target = None
            for (t_obj, row_y, row_h) in tracks.get("rows", []):
                if row_y <= y <= row_y + row_h:
                    target = t_obj
                    break
            win = self.window()
            if hasattr(win, "popup_track_menu"):
                win.popup_track_menu(event.globalPos(), target)
                return
            return super().contextMenuEvent(event)

        menu = QMenu(self)

        if is_thumb:
            act_remove = menu.addAction("撤销缩略图")
            act_duration = menu.addAction("修改缩略图时长")
            act_apply_text = menu.addAction("批量应用缩略图文本格式到其他视频缩略图")
            chosen = menu.exec(event.globalPos())
            if chosen is None:
                return
            main_win = self.window()
            if chosen == act_remove and hasattr(main_win, "remove_thumbnail_for_current_task"):
                main_win.remove_thumbnail_for_current_task()
            elif chosen == act_duration and hasattr(main_win, "edit_thumbnail_duration_for_current_task"):
                main_win.edit_thumbnail_duration_for_current_task()
            elif chosen == act_apply_text and hasattr(main_win, "apply_thumbnail_text_style_to_all"):
                main_win.apply_thumbnail_text_style_to_all()

        elif is_intro_vid:
            act_remove_iv = menu.addAction("撤销开场视频")
            menu.addSeparator()
            act_edit_dur = menu.addAction("修改开场视频时长")
            act_edit_settings = menu.addAction("修改转场设置")
            menu.addSeparator()
            act_apply_all = menu.addAction("将当前开场视频设置应用到所有任务")
            chosen = menu.exec(event.globalPos())
            if chosen is None:
                return
            main_win = self.window()
            if chosen == act_remove_iv and hasattr(main_win, "remove_intro_video_for_current_task"):
                main_win.remove_intro_video_for_current_task()
            elif chosen == act_edit_dur and hasattr(main_win, "edit_intro_video_duration"):
                main_win.edit_intro_video_duration()
            elif chosen == act_edit_settings and hasattr(main_win, "edit_intro_video_settings"):
                main_win.edit_intro_video_settings()
            elif chosen == act_apply_all and hasattr(main_win, "apply_intro_video_settings_to_all"):
                main_win.apply_intro_video_settings_to_all()

    def _popup_audio_clip_menu(self, event, hit_track, hit_clip):
        """Q204a：音频 clip 右键。只放当时有的项：解绑 / 重新绑定、淡化、删除、复制。"""
        from cutora.model.av_link import partners
        ac = self._get_audio_clip_list()
        aidx = -1
        for i, (c, _) in enumerate(ac):
            if c is hit_clip:
                aidx = i
                break
        tid_hit = str(getattr(hit_track, "id", ""))
        if (tid_hit, self._clip_index_of(hit_track, hit_clip)) not in self._clip_sel:
            self._select_audio_index(hit_track, hit_clip, aidx)
            self.update()
        menu = QMenu(self)
        act_unlink = menu.addAction("解绑音视频")
        act_relink = menu.addAction("重新绑定音视频")
        menu.addSeparator()
        act_fade = menu.addAction("淡化 0.3s")
        act_clear_fade = menu.addAction("清除淡化")
        act_edge = menu.addAction("在说话边缘加 30 ms 淡")
        act_curve = menu.addAction("添加速度曲线")
        act_curve_clear = menu.addAction("清除速度曲线")
        act_vocals = menu.addAction("只留说话")
        act_novocals = menu.addAction("只留伴奏")
        hit_silence = self._silence_at_x(event.pos().x() + self.scroll_x)
        act_silence = None
        if hit_silence is not None:
            menu.addSeparator()
            act_silence = menu.addAction("删除此段静音")
        menu.addSeparator()
        denoise_menu = menu.addMenu("降噪…")
        denoise_acts = {}
        for _lab, _preset in (
            ("关闭", "off"), ("仅去噪", "denoise"), ("播客", "podcast"),
            ("YouTube", "youtube"), ("大厅", "hall"),
        ):
            denoise_acts[denoise_menu.addAction(_lab)] = _preset
        menu.addSeparator()
        ai_audio_menu = menu.addMenu("✨ AI 音频生成与精修卡牌（直接落位时间线）")
        ai_audio_acts = {}
        for _alab, _acap in (
            ("🎵 在时间线直接生成情绪背景配乐 · MusicGen BGM…", "musicgen_bgm"),
            ("🔊 在时间线直接生成立体声效/观众掌声 · Stable Audio SFX…", "stable_audio_sfx"),
            ("🎙️ 吸取本条原声克隆配音 · Chatterbox 零样本情感克隆…", "chatterbox_clone"),
            ("🌍 吸取本条原声跨语言配音 · XTTS-v2 (17国语种)…", "xtts_v2_clone"),
            ("🎧 录音棚深度降噪修复 & Demucs 四轨人声伴奏分离…", "resemble_demucs_audio"),
        ):
            ai_audio_acts[ai_audio_menu.addAction(_alab)] = _acap
        menu.addSeparator()
        act_copy = menu.addAction("复制")
        act_del = menu.addAction("删除")
        chosen = menu.exec(event.globalPos())
        if chosen is None:
            return
        if chosen in ai_audio_acts:
            win_top = self.window()
            tl_obj = _tl_of(self.task_data)
            src_obj = (getattr(tl_obj, "sources", None) or {}).get(getattr(hit_clip, "source_id", None)) if tl_obj else None
            clip_path = str(getattr(src_obj, "path", "") or getattr(hit_clip, "path", "") or "")
            try:
                from cutora.ui.ai_studio_room import open_quick_ai_creator
                open_quick_ai_creator(
                    win=win_top,
                    video_path=clip_path,
                    initial_cap_id=ai_audio_acts[chosen],
                    parent=self,
                )
            except Exception:
                pass
            return
        if chosen == act_unlink:
            self._rebuild_av_link_index()
            clips = list(self._sel_clips())
            seen = {id(c) for _t, c in clips}
            for tr, c in list(clips):
                for p_tr, p_c in partners(self._av_link_index or {}, c):
                    if id(p_c) not in seen:
                        clips.append((p_tr, p_c))
                        seen.add(id(p_c))
            refs = self._group_refs(clips)
            if refs:
                self._bus_execute("timeline.unlink_clips", {"clips": refs})
            self.update()
            return
        if chosen == act_relink:
            sel = list(self._sel_clips())
            videos = [(t, c) for t, c in sel if getattr(t, "type", None) == "video"]
            audios = [(t, c) for t, c in sel if getattr(t, "type", None) == "audio"]
            if len(videos) != 1 or len(audios) != 1:
                win = self.window()
                bar = getattr(win, "statusBar", None) if win is not None else None
                if callable(bar):
                    bar().showMessage("重新绑定需要选中 1 个视频 + 1 个音频", 3000)
                return
            refs = self._group_refs([videos[0], audios[0]])
            if refs:
                self._bus_execute("timeline.link_clips", {"clips": refs})
            self.update()
            return
        if chosen == act_curve or chosen == act_curve_clear:
            from cutora.audio.speed_curve import default_curve
            if chosen == act_curve_clear:
                hit_clip.speed_curve = []
            else:
                span = max(0.0, float(getattr(hit_clip, "out_t", 0) or 0) - float(getattr(hit_clip, "in_t", 0) or 0))
                hit_clip.speed_curve = default_curve(span)
            self.update()
            return
        if chosen == act_vocals or chosen == act_novocals:
            self._bus_execute("audio.isolate", {
                "mode": "vocals" if chosen == act_vocals else "novocals",
                "scope": "current",
                "track_id": tid_hit,
                "clip_index": self._clip_index_of(hit_track, hit_clip),
            })
            self.update()
            return
        if chosen == act_edge:
            self._bus_execute("audio.auto_fades", {"dur": 0.03, "scope": "current"})
            self.update()
            return
        if chosen == act_fade or chosen == act_clear_fade:
            fi = 0.3 if chosen == act_fade else 0.0
            fo = 0.3 if chosen == act_fade else 0.0
            cidx = self._clip_index_of(hit_track, hit_clip)
            self._bus_execute("timeline.set_clip_fades", {
                "track_id": str(getattr(hit_track, "id", "")),
                "clip_index": cidx,
                "fade_in_dur": fi,
                "fade_out_dur": fo,
            })
            self.update()
            if aidx >= 0:
                self.item_selected.emit("audio", aidx)
            return
        if act_silence is not None and chosen == act_silence:
            self._bus_execute("audio.remove_silences", {
                "min_dur": 0.8,
                "scope": "current",
                "start": float(hit_silence.get("start") or 0.0),
                "end": float(hit_silence.get("end") or 0.0),
            })
            self.update()
            return
        if chosen in denoise_acts:
            self._bus_execute("audio.denoise", {
                "preset": denoise_acts[chosen],
                "strength": "mid",
                "scope": "clip",
                "track_id": tid_hit,
                "clip_index": self._clip_index_of(hit_track, hit_clip),
            })
            self.update()
            return
        if chosen == act_copy:
            self.copy_selection()
            return
        if chosen == act_del:
            self.delete_selection()

    def _group_menu(self, menu, hit_track, hit_clip) -> dict:
        """Q154：片段 / 调整图层右键菜单顶部的预合成那一段。返回 {QAction: 回调}。"""
        acts = {}
        tl = _tl_of(self.task_data)
        if tl is None:
            return acts
        grp = _CG.group_of(hit_clip)
        sel_clips = [(tr, c) for tr, c in self._sel_clips() if not _CG.is_repeat(c)]
        sel_ovs = [(ln, ov) for ln, ov in self._sel_overlays() if not _CG.is_repeat(ov)]
        sel = [c for _t, c in sel_clips] + [ov for _ln, ov in sel_ovs]
        sel_gids = {_CG.gid_of(x) for x in sel}
        all_groups = _CG.groups(tl)
        names = {gid: _CG.display_name(g, i) for i, (gid, g) in enumerate(all_groups.items())}
        if grp is None or len(sel_gids) > 1 or (len(sel) > 1 and "" in sel_gids):
            a = menu.addAction("预合成片段（打组）\tCtrl+G")
            a.setEnabled(bool(sel))
            acts[a] = self.group_selection
        if grp is not None:
            gid = grp["id"]
            sub = menu.addMenu("预合成 · %s" % names.get(gid, "组"))
            a = sub.addAction("解除预合成\tCtrl+Shift+G")
            acts[a] = self.ungroup_selection
            a = sub.addAction("解锁组内编辑" if grp.get("locked", True) else "锁定组内编辑（整组联动）")
            acts[a] = lambda g=gid, on=not grp.get("locked", True): self.set_group_meta(g, locked=on)
            a = sub.addAction("循环播放铺满到视频结尾")
            a.setCheckable(True)
            a.setChecked(bool(grp.get("loop_fill")))
            acts[a] = lambda g=gid, on=not grp.get("loop_fill"): self.set_group_meta(g, loop_fill=on)
            a = sub.addAction("重命名组…")
            acts[a] = lambda g=gid, cur=grp.get("name", ""): self._rename_group_dialog(g, cur)
            cm = sub.addMenu("组颜色")
            for col in _CG.GROUP_COLORS:
                ca = cm.addAction(col)
                ca.setCheckable(True)
                ca.setChecked(col == grp.get("color"))
                from PySide6.QtGui import QPixmap
                pm = QPixmap(12, 12)
                pm.fill(QColor(col))
                ca.setIcon(pm)
                acts[ca] = lambda g=gid, c=col: self.set_group_meta(g, color=c)
            if not grp.get("repeat"):
                sub.addSeparator()
                a = sub.addAction("从组中分离（选中的这几条）")
                acts[a] = self.detach_selection
        others = [(g, n) for g, n in names.items() if g != (grp or {}).get("id")]
        if others and sel:
            jm = menu.addMenu("加入预合成组")
            for g, n in others:
                a = jm.addAction(n)
                acts[a] = lambda gg=g: self.join_group(gg)
        if acts:
            menu.addSeparator()
        return acts

    def _rename_group_dialog(self, gid, cur):
        from PySide6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(self, "预合成组改名", "组名（留空 = 自动编号）", text=str(cur or ""))
        if ok:
            self.set_group_meta(gid, name=text.strip())

    # =========================================================
    # Phase C: 拖拽素材到时间线
    # =========================================================
    def _drop_supported_exts(self):
        from cutora.ui.timeline_drop import supported_exts
        return supported_exts()

    def _pos_below_tracks(self, pos_y) -> bool:
        """落点在所有轨道下面的空白处 —— 那就是「给我新开一条轨道」。"""
        rows = (self._track_positions() or {}).get("rows", []) or []
        if not rows:
            return True
        last_bottom = max(row_y + row_h for (_t, row_y, row_h) in rows)
        return pos_y > last_bottom

    def _pos_to_domain(self, pos_y):
        """根据鼠标 y 坐标判断落在 visual 域还是 audio 域。"""
        tracks = self._track_positions()
        sep_y = tracks.get("domain_sep_y", 9999)
        return "audio" if pos_y > sep_y else "visual"

    def _pos_to_track(self, pos_y):
        """根据鼠标 y 坐标判断落在哪个 Track 上，返回 Track 对象或 None。"""
        tracks = self._track_positions()
        for (t_obj, row_y, row_h) in tracks.get("rows", []):
            if row_y <= pos_y <= row_y + row_h:
                return t_obj
        return None

    def _x_to_time(self, x):
        """将 widget 像素 x 坐标转换为时间线秒数。"""
        return max(0.0, (x + self.scroll_x) / self.pixels_per_second)

    def dragEnterEvent(self, event):
        from cutora.ui.panels.transition_panel import TRANSITION_MIME
        if event.mimeData().hasFormat(TRANSITION_MIME):
            event.acceptProposedAction()
            return
        if event.mimeData().hasUrls():
            # 检查是否有受支持的文件类型
            for url in event.mimeData().urls():
                ext = os.path.splitext(url.toLocalFile())[1].lower()
                if ext in self._drop_supported_exts():
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dragMoveEvent(self, event):
        from cutora.ui.panels.transition_panel import TRANSITION_MIME
        if event.mimeData().hasFormat(TRANSITION_MIME):
            event.acceptProposedAction()
            return
        if event.mimeData().hasUrls():
            pos = event.position() if hasattr(event, "position") else event.posF()
            self._set_drop_hint(pos.x(), pos.y())
            event.acceptProposedAction()
        else:
            self._clear_drop_hint()
            event.ignore()

    def dragLeaveEvent(self, event):
        self._clear_drop_hint()
        try:
            event.accept()
        except AttributeError:
            pass

    # --- Q115：拖进来的时候画一条落点线 + 高亮目标轨 -------------------
    def _set_drop_hint(self, x, y):
        track = self._pos_to_track(y)
        hint = (float(x), getattr(track, "id", None), bool(self._pos_below_tracks(y)))
        if getattr(self, "_drop_hint", None) != hint:
            self._drop_hint = hint
            self.update()

    def _clear_drop_hint(self):
        if getattr(self, "_drop_hint", None) is not None:
            self._drop_hint = None
            self.update()

    def _paint_drop_hint(self, painter):
        """落点竖线 + 目标轨道整行高亮；落在空白处就画一条「要新开轨道」的虚线。"""
        hint = getattr(self, "_drop_hint", None)
        if not hint:
            return
        x, track_id, below = hint
        t = self._theme()
        accent = QColor(t.accent)
        rows = (self._track_positions() or {}).get("rows", []) or []
        painter.save()
        if track_id is not None and not below:
            for (t_obj, row_y, row_h) in rows:
                if getattr(t_obj, "id", None) != track_id:
                    continue
                glow = QColor(accent)
                glow.setAlpha(28)
                painter.fillRect(QRectF(0, row_y, self.width(), row_h), QBrush(glow))
                edge = QColor(accent)
                edge.setAlpha(120)
                painter.setPen(QPen(edge, 1))
                painter.drawRect(QRectF(0.5, row_y + 0.5,
                                        self.width() - 1, row_h - 1))
                break
        elif below and rows:
            last_bottom = max(row_y + row_h for (_t, row_y, row_h) in rows)
            pen = QPen(QColor(accent), 1, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawLine(QPointF(0, last_bottom + 6),
                             QPointF(self.width(), last_bottom + 6))
        painter.setPen(QPen(QColor(accent), 2))
        painter.drawLine(QPointF(x, 0), QPointF(x, self.height()))
        painter.restore()

    def dropEvent(self, event):
        from cutora.ui.panels.transition_panel import TRANSITION_MIME
        if event.mimeData().hasFormat(TRANSITION_MIME):
            try:
                raw = bytes(event.mimeData().data(TRANSITION_MIME)).decode("utf-8")
                payload = json.loads(raw) if raw else {}
            except Exception as exc:
                self._status("不能添加转场：拖放数据无效（%s）" % exc)
                event.ignore()
                return
            drop_pos = event.position() if hasattr(event, "position") else event.posF()
            real_x = drop_pos.x() + self.scroll_x
            y = drop_pos.y()
            hit = self._hit_seam_at(real_x, y)
            if hit is None or hit[0] != "seam":
                self._status("请把转场拖到接缝上")
                event.ignore()
                return
            track, left_i, right_i, _t = hit[1], hit[2], hit[3], hit[4]
            self._select_seam(track, left_i, right_i, _t)
            ok = self._add_transition_to_seam(
                track, left_i, right_i,
                payload.get("preset") or "",
                payload.get("domain") or "visual",
                float(payload.get("duration") or 0.35),
            )
            if ok:
                event.acceptProposedAction()
            else:
                event.ignore()
            return
        if not event.mimeData().hasUrls():
            event.ignore()
            return
        from cutora.ui.timeline_drop import sort_for_drop
        self._clear_drop_hint()
        drop_pos = event.position() if hasattr(event, 'position') else event.posF()
        drop_time = self._x_to_time(drop_pos.x())
        domain = self._pos_to_domain(drop_pos.y())
        target_track = self._pos_to_track(drop_pos.y())
        new_track = self._pos_below_tracks(drop_pos.y())
        supported = self._drop_supported_exts()
        paths = []
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if not path:
                continue
            if os.path.splitext(path)[1].lower() not in supported:
                continue
            paths.append(path)
        if not paths:
            self._status("这些文件时间线收不了（支持视频 / 音频 / 图片 / 文本 / 字幕）")
            event.ignore()
            return
        paths = sort_for_drop(paths)
        # 目标轨道必须在 emit 之前就位 —— 以前是 emit 完才赋值，
        # handler 拿到的永远是上一次的落点（等于落轨从来没生效过）。
        self._last_drop_target_track = target_track
        self.files_dropped_on_timeline.emit({
            "paths": paths,
            "time": float(drop_time),
            "domain": domain,
            "track": target_track,
            "new_track": bool(new_track),
        })
        event.acceptProposedAction()

    def _copy_selected_layers(self) -> bool:
        """Ctrl+C：把选中的视频 / 音频 / 文本 / 图片收进共用剪贴板，时间原样保留。"""
        if not isinstance(self.task_data, dict):
            return False
        locs = []
        seen = set()

        def add_clip(track, clip):
            if track is None or clip is None:
                return
            idx = self._clip_index_of(track, clip)
            if idx < 0:
                return
            key = ("clip", str(getattr(track, "id", "") or ""), idx)
            if key in seen:
                return
            seen.add(key)
            locs.append({"where": "clip", "track_id": key[1], "clip_index": idx})

        tl = _tl_of(self.task_data)
        by_id = {}
        if tl is not None:
            by_id = {str(getattr(tr, "id", "") or ""): tr for tr in getattr(tl, "tracks", []) or []}
        for tid, idx in getattr(getattr(self, "selection", None), "clips", []) or []:
            tr = by_id.get(str(tid))
            clips = getattr(tr, "clips", None) or [] if tr is not None else []
            if 0 <= int(idx) < len(clips):
                add_clip(tr, clips[int(idx)])
        if self.selected_video_clip is not None:
            add_clip(self.selected_video_clip[0], self.selected_video_clip[1])
        if self.selected_audio_idx is not None:
            ac = self._get_audio_clip_list()
            if 0 <= self.selected_audio_idx < len(ac):
                clip = ac[self.selected_audio_idx][0]
                add_clip(self._track_of_clip(clip), clip)
        ov = self.selected_overlay
        if ov is not None:
            list_name, idx = ov
            rows = self.task_data.get(list_name) or []
            if 0 <= int(idx) < len(rows):
                locs.append({"where": "overlay", "list": list_name, "index": int(idx)})
        if not locs:
            return False
        return self._arm_layer_pack(locs, "material")

    def _copy_hit_clip(self, track, clip, mode: str) -> bool:
        if track is None or clip is None:
            return False
        idx = self._clip_index_of(track, clip)
        if idx < 0:
            return False
        return self._arm_layer_pack(
            [{"where": "clip", "track_id": str(getattr(track, "id", "") or ""), "clip_index": idx}],
            mode)

    def _arm_layer_pack(self, locators, mode: str) -> bool:
        from cutora.batch import layer_transfer as lt
        from cutora.batch.monitor_model import set_clipboard
        pack = lt.pack_locators(self.task_data, locators, mode)
        if not pack:
            self._tell_timeline("没有可复制的图层")
            return False
        set_clipboard(pack)
        self._layer_paste_armed = True
        self._tell_timeline(lt.describe_copy(pack) + "。换一条视频后 Ctrl+V，或到中央监视器里批量粘贴")
        return True

    def _paste_copied_layers(self, *, quiet_mismatch: bool = False) -> bool:
        from cutora.batch import layer_transfer as lt
        from cutora.batch.monitor_model import get_clipboard
        pack = get_clipboard()
        if not isinstance(self.task_data, dict) or not isinstance(pack, dict) or pack.get("kind") != "layers":
            self._layer_paste_armed = False
            if not quiet_mismatch:
                self._tell_timeline("剪贴板里没有图层。先在时间线或中央监视器里复制一层")
            return False
        win = self.window()
        pushed = False
        if win is not None and hasattr(win, "push_undo"):
            win.push_undo("粘贴图层", scope="task")
            pushed = True
        totals = lt.apply_pack(self.task_data, pack)
        if int(totals.get("added") or 0) == 0 and int(totals.get("styled") or 0) == 0:
            mgr = getattr(win, "history_mgr", None) if win is not None else None
            if pushed and mgr is not None and hasattr(mgr, "pop_last"):
                mgr.pop_last()
            self._tell_timeline(lt.describe_apply(1, totals))
            return True
        if win is not None and hasattr(win, "capture_undo_after"):
            win.capture_undo_after()
        if win is not None and hasattr(win, "mark_dirty"):
            win.mark_dirty()
        self._tell_timeline(lt.describe_apply(1, totals))
        self.update()
        return True

    def _tell_timeline(self, text: str) -> None:
        win = self.window()
        bar = getattr(win, "statusBar", None) if win is not None else None
        if callable(bar):
            bar().showMessage(text, 5000)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_N and not event.isAutoRepeat():
            self._snap_hold_off = True
            return
        if event.key() == Qt.Key.Key_Space:
            event.ignore()
            return
        if not self.task_data:
            return QWidget.keyPressEvent(self, event)
        if self._ops_key(event):          # Q145 第二期：时间线快捷键
            return
        if event.key() == Qt.Key.Key_Escape:
            self.clear_selection()
            return
        if event.key() == Qt.Key.Key_Z:
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.zoom_to_selection()
            elif not (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
                self.zoom_fit_all()
            else:
                return QWidget.keyPressEvent(self, event)
            return
        if event.key() in (Qt.Key.Key_Comma, Qt.Key.Key_Period):
            step = -1 if event.key() == Qt.Key.Key_Comma else 1
            self._nudge_selection_frames(step)
            return
        if event.key() in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete) and getattr(self, "selected_seam", None):
            self._join_selected_seam()
            return
        if (event.modifiers() & Qt.KeyboardModifier.ControlModifier
                and event.key() == Qt.Key.Key_A):
            self.select_all_focused()
            return
        if (event.modifiers() & Qt.KeyboardModifier.ControlModifier
                and event.key() == Qt.Key.Key_C
                and self.selected_indices):
            segs = self.task_data.get("segments") or []
            items = []
            for i in sorted(self.selected_indices):
                if 0 <= i < len(segs) and isinstance(segs[i], dict):
                    items.append(copy.deepcopy(segs[i]))
            self._clipboard_segments = items
            self._layer_paste_armed = False
            return
        if (event.modifiers() & Qt.KeyboardModifier.ControlModifier
                and event.key() == Qt.Key.Key_C
                and self._copy_selected_layers()):
            return
        if (event.modifiers() & Qt.KeyboardModifier.ControlModifier
                and event.key() == Qt.Key.Key_V
                and getattr(self, "_layer_paste_armed", False)):
            if self._paste_copied_layers(quiet_mismatch=True):
                return
        if (event.modifiers() & Qt.KeyboardModifier.ControlModifier
                and event.key() == Qt.Key.Key_V
                and getattr(self, "_clipboard_segments", None)):
            t = self.position / 1000.0
            self._bus_execute("subtitle.duplicate_segments", {
                "items": self._clipboard_segments,
                "time": t,
            })
            self.update()
            return
        # === Delete 键删除选中的 subtitle segment ===
        if event.key() == Qt.Key.Key_Delete and len(self.selected_indices) > 0:
            if self.can_edit_track("subs"):
                ids = sorted(int(i) for i in self.selected_indices)
                if ids and self._bus_execute(
                        "subtitle.delete_segments", {"seg_ids": ids}):
                    self.selected_indices.clear()
                    self._publish_selection()
                    self.update()
                    return
                return
        # === Delete 键删除选中的 audio clip ===
        if event.key() == Qt.Key.Key_Delete and self.selected_audio_idx is not None:
            if self.can_edit_track("audio"):
                ac_list = self._get_audio_clip_list()
                if 0 <= self.selected_audio_idx < len(ac_list):
                    clip_obj, _ = ac_list[self.selected_audio_idx]
                    tr = self._track_of_clip(clip_obj)
                    cidx = self._clip_index_of(tr, clip_obj) if tr is not None else -1
                    deleted = False
                    if tr is not None and cidx >= 0:
                        deleted = bool(self._bus_execute(
                            "timeline.delete_clip",
                            {"track_id": str(getattr(tr, "id", "")), "clip_index": cidx},
                        ))
                    if not deleted:
                        tl = _tl_of(self.task_data)
                        if tl is not None:
                            for t in tl.tracks:
                                if hasattr(t, "type") and t.type == "audio" and clip_obj in t.clips:
                                    t.clips.remove(clip_obj)
                                    break
                    self._reset_selection()
                    self._publish_selection()
                    self._after_edit()
                    return
        # === Ctrl+B 切刀：对所有选中片段（视频/文本/图片/音频/字幕）===
        if (event.modifiers() & Qt.KeyboardModifier.ControlModifier
                and event.key() == Qt.Key.Key_B):
            if self.razor_at_playhead():
                return
        if (event.modifiers() & Qt.KeyboardModifier.ControlModifier
                and event.key() == Qt.Key.Key_C
                and self.selected_audio_idx is not None):
            if self.can_edit_track("audio"):
                ac_list = self._get_audio_clip_list()
                if 0 <= self.selected_audio_idx < len(ac_list):
                    clip_obj, _ = ac_list[self.selected_audio_idx]
                    self._clipboard_audio_clip = clip_obj.clone()
                    return
        if (event.modifiers() & Qt.KeyboardModifier.ControlModifier
                and event.key() == Qt.Key.Key_V
                and self._clipboard_audio_clip is not None):
            tl = _tl_of(self.task_data)
            if tl is None:
                return
            cursor_pos = self.mapFromGlobal(QCursor.pos())
            target_track = self._pos_to_track(cursor_pos.y())
            if target_track is None or getattr(target_track, "type", None) != "audio" or not self.can_edit_track(target_track):
                if self.selected_track is not None and getattr(self.selected_track, "type", None) == "audio" and self.can_edit_track(self.selected_track):
                    target_track = self.selected_track
                else:
                    target_track = next((t for t in tl.tracks if getattr(t, "type", None) == "audio"), None)
            if target_track is None:
                return
            new_clip = self._clipboard_audio_clip.clone()
            new_clip.start_t = self.position / 1000.0
            target_track.clips.append(new_clip)
            self.selected_track = target_track
            new_list = self._get_audio_clip_list()
            for i, (c, _) in enumerate(new_list):
                if c is new_clip:
                    self.selected_audio_idx = i
                    break
            self.update()
            return

        if (event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace)
                and getattr(self, "selected_transition", None) is not None):
            self._delete_selected_transition()
            return

        # === Video clip 键盘操作 ===
        if self.selected_video_clip is not None:
            v_track, v_clip = self.selected_video_clip
            if getattr(v_track, "locked", False):
                pass  # 锁定轨道不处理
            elif event.key() == Qt.Key.Key_Delete:
                idx = self._clip_index_of(v_track, v_clip)
                if idx >= 0:
                    ok = self._bus_execute("timeline.delete_clip", {
                        "track_id": str(getattr(v_track, "id", "") or ""),
                        "clip_index": int(idx),
                    })
                    if not ok:
                        return
                self.selected_video_clip = None
                self.selected_transition = None
                self._publish_selection()
                self.update()
                return
            elif (event.modifiers() & Qt.KeyboardModifier.ControlModifier
                  and event.key() == Qt.Key.Key_B):
                # Q145：切刀只走 timeline.razor（能撤销、守锁定）；它没切成就是切不了，
                # 不再绕过命令总线直接 split_at
                self._status("播放头不在选中片段里，或轨道已锁定")
                return
            elif (event.modifiers() & Qt.KeyboardModifier.ControlModifier
                  and event.key() == Qt.Key.Key_C):
                keys = list(self.selection.clips) or [
                    (str(getattr(v_track, "id", "")),
                     self._clip_index_of(v_track, v_clip))]
                self._clipboard_clips = [
                    {"track_id": tid, "clip_index": idx} for tid, idx in keys]
                return
            elif (event.modifiers() & Qt.KeyboardModifier.ControlModifier
                  and event.key() == Qt.Key.Key_V
                  and getattr(self, "_clipboard_clips", None)):
                self._bus_execute("timeline.duplicate_clips", {
                    "clips": self._clipboard_clips,
                    "time": self.position / 1000.0,
                })
                self.update()
                return

        if not self.selected_overlay:
            return QWidget.keyPressEvent(self, event)
        list_name, idx = self.selected_overlay
        if list_name is None or idx is None:
            return QWidget.keyPressEvent(self, event)
        ov_list = self.task_data.get(list_name, [])
        if idx < 0 or idx >= len(ov_list):
            return QWidget.keyPressEvent(self, event)
        # 编辑保护：对应轨道 locked/hidden 时禁止操作
        _ov_track_key = "text" if list_name == "text_overlays" else "images"
        if not self.can_edit_track(_ov_track_key):
            return QWidget.keyPressEvent(self, event)
        if event.key() == Qt.Key.Key_Delete:
            del ov_list[idx]
            self.selected_overlay = None
            self.update()
            return
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if event.key() == Qt.Key.Key_C:
                self._clipboard_overlay = (list_name, copy.deepcopy(ov_list[idx]))
                return
            if event.key() == Qt.Key.Key_V and self._clipboard_overlay:
                clip_list, ov = self._clipboard_overlay
                if clip_list != list_name:
                    return
                new_ov = copy.deepcopy(ov)
                tl = _tl_of(self.task_data)
                cursor_pos = self.mapFromGlobal(QCursor.pos())
                target_track = self._pos_to_track(cursor_pos.y())
                if target_track is None or not self.can_edit_track(target_track):
                    target_track = self.selected_track
                if target_track is not None:
                    expected_type = "text" if list_name == "text_overlays" else (
                        "adjust" if ov.get("type") == "adjust" else "image")
                    if getattr(target_track, "type", None) != expected_type:
                        target_track = None
                if target_track is None and tl is not None:
                    tid = ov.get("_track_id")
                    if tid:
                        for t in tl.tracks:
                            if getattr(t, "id", None) == tid:
                                target_track = t
                                break
                    if target_track is None:
                        for t in tl.tracks:
                            if getattr(t, "type", None) == ("text" if list_name == "text_overlays" else (
                                    "adjust" if ov.get("type") == "adjust" else "image")):
                                target_track = t
                                break
                if target_track is not None:
                    new_ov["_track_id"] = getattr(target_track, "id", None)
                now = self.position / 1000.0
                dur = max(self.MIN_DUR, new_ov['end'] - new_ov['start'])
                new_ov['start'] = now
                new_ov['end'] = now + dur
                new_ov['id'] = int(time.time() * 1000)
                ov_list.append(new_ov)
                self.selected_overlay = (list_name, len(ov_list) - 1)
                self.selected_track = target_track
                # === 批量复制（第 8/8 部分）===
                # 将同一 overlay 副本插入到其他开启 enable_batch 的同类型轨道
                self._batch_paste_overlay(list_name, new_ov)
                self.update()
                return
        return QWidget.keyPressEvent(self, event)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key.Key_N:
            self._snap_hold_off = False
            return
        return QWidget.keyReleaseEvent(self, event)
